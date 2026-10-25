import base64
import binascii
import hashlib
import io
import os
import re
import tempfile
import warnings
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from traceweave_memory.domain import MAX_IMAGE_BYTES, MediaError, MediaUnavailable

DATA_URI = re.compile(r"\Adata:image/(jpeg|png|webp);base64,([A-Za-z0-9+/=]+)\Z")
FORMATS = {"jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}


def decode_image(url: str) -> tuple[bytes, str]:
    match = DATA_URI.fullmatch(url)
    if not match:
        raise MediaError("image must be a JPEG, PNG or WebP Base64 data URI")
    subtype, encoded = match.groups()
    if len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
        raise MediaError("image exceeds 10 MiB decoded")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise MediaError("invalid image Base64") from exc
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise MediaError("image is empty or exceeds 10 MiB decoded")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as picture:
                if picture.format != FORMATS[subtype]:
                    raise MediaError("image bytes do not match declared MIME type")
                picture.verify()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as exc:
        raise MediaError("image cannot be decoded safely") from exc
    return raw, subtype


class FileMedia:
    decode = staticmethod(decode_image)

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, scope: str, digest: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise MediaUnavailable("invalid stored media reference")
        namespace = hashlib.sha256(scope.encode()).hexdigest()
        return self.root / namespace / digest

    def put(self, scope: str, raw: bytes, subtype: str) -> dict:
        digest = hashlib.sha256(raw).hexdigest()
        target = self._path(scope, digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() == raw:
            return {"digest": digest, "subtype": subtype, "size": len(raw)}
        # Always atomically replace: an interrupted/corrupt orphan is repaired on retry.
        fd, name = tempfile.mkstemp(dir=target.parent, prefix=".writing-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.replace(name, target)
            except PermissionError:
                # Windows may hold the winner's destination during a duplicate write.
                # It is safe to reuse only after checking the full expected bytes.
                if not target.exists() or target.read_bytes() != raw:
                    raise
        finally:
            Path(name).unlink(missing_ok=True)
        return {"digest": digest, "subtype": subtype, "size": len(raw)}

    def get(self, scope: str, ref: dict) -> str:
        try:
            raw = self._path(scope, ref["digest"]).read_bytes()
        except OSError as exc:
            raise MediaUnavailable("stored media is unavailable") from exc
        if len(raw) != ref["size"] or hashlib.sha256(raw).hexdigest() != ref["digest"]:
            raise MediaUnavailable("stored media integrity check failed")
        return f"data:image/{ref['subtype']};base64,{base64.b64encode(raw).decode('ascii')}"
