"""Download a pinned public Mem-Gallery subset, without model calls."""

import hashlib
import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from threading import Lock

DATASET_ID = "Ethan-Bei/Mem-Gallery"


def split_for(topic: str) -> str:
    return "holdout" if int(hashlib.sha256(topic.encode()).hexdigest(), 16) % 5 == 0 else "dev"


def download_subset(
    metadata_path: Path, output: Path, topics: list[str], max_bytes: int = 200 * 1024 * 1024
) -> dict:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    revision = metadata["sha"]
    if metadata.get("id") != DATASET_ID or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("expected pinned public Mem-Gallery metadata")
    files = sorted(item["rfilename"] for item in metadata["siblings"])
    source_metadata = {item["rfilename"]: item for item in metadata["siblings"]}
    available = {PurePosixPath(name).stem for name in files if name.startswith("data/dialog/")}
    if not topics or not set(topics) <= available:
        raise ValueError("topics must identify existing complete conversations")
    selected = [
        name
        for name in files
        if any(
            name == f"data/dialog/{topic}.json" or name.startswith(f"data/image/{topic}/")
            for topic in topics
        )
    ]
    if sum(source_metadata[name].get("size", 0) for name in selected) > max_bytes:
        raise ValueError("subset exceeds explicit download byte budget")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "dataset": DATASET_ID,
        "revision": revision,
        "license_declared": metadata.get("cardData", {}).get("license"),
        "topics": {topic: split_for(topic) for topic in sorted(topics)},
        "files": [],
    }
    consumed = 0
    counter_lock = Lock()

    def account(size: int):
        nonlocal consumed
        with counter_lock:
            if consumed + size > max_bytes:
                raise ValueError("subset exceeds explicit download byte budget")
            consumed += size

    def download_file(name: str) -> dict:
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe dataset path")
        target = output.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Downloads are pinned by immutable repository revision. Record file hashes too.
        if not target.exists():
            temporary = target.with_suffix(target.suffix + ".part")
            url = f"https://huggingface.co/datasets/{DATASET_ID}/resolve/{revision}/{name}"
            try:
                with (
                    urllib.request.urlopen(url, timeout=60) as response,
                    temporary.open("wb") as out,
                ):
                    while chunk := response.read(1024 * 1024):
                        account(len(chunk))
                        out.write(chunk)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        else:
            account(target.stat().st_size)
        with target.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        expected = source_metadata[name]
        if "size" in expected and target.stat().st_size != expected["size"]:
            raise ValueError("downloaded file size differs from repository metadata")
        if expected.get("lfs", {}).get("sha256"):
            if digest != expected["lfs"]["sha256"]:
                raise ValueError("downloaded file hash differs from repository metadata")
        elif expected.get("blobId"):
            git_hash = hashlib.sha1(f"blob {target.stat().st_size}\0".encode())
            with target.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    git_hash.update(chunk)
            if git_hash.hexdigest() != expected["blobId"]:
                raise ValueError("downloaded Git blob differs from repository metadata")
        return {"path": name, "bytes": target.stat().st_size, "sha256": digest}

    with ThreadPoolExecutor(max_workers=4) as pool:
        manifest["files"] = list(pool.map(download_file, selected))
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest
