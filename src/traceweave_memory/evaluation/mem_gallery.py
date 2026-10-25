"""Strict local adapter for the actual released Mem-Gallery JSON structure."""

import base64
import io
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from PIL import Image

from traceweave_memory.adapters.media import decode_image
from traceweave_memory.domain import stable_hash


@dataclass(frozen=True)
class GalleryMemory:
    writes: list[dict]
    # Labels never enter writes or production Search.
    source_ids_by_evidence: dict[str, set[str]]


def image_content(root: Path, path: str) -> dict:
    relative = PurePosixPath(path.replace("\\", "/"))
    if path.startswith("../image/"):
        relative = PurePosixPath("data/image") / str(relative)[len("../image/") :]
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("unsupported or unsafe image reference")
    full_path = root.resolve().joinpath(*relative.parts).resolve()
    if not full_path.is_relative_to(root.resolve()):
        raise ValueError("image reference escapes dataset root")
    raw = full_path.read_bytes()
    with Image.open(io.BytesIO(raw)) as picture:
        subtype = {"JPEG": "jpeg", "PNG": "png", "WEBP": "webp"}.get(picture.format)
    if not subtype:
        raise ValueError("unsupported actual image format")
    # Published filename extensions do not always match the bytes. Preserve bytes,
    # declare the actual type, and keep strict HTTP MIME validation unchanged.
    uri = f"data:image/{subtype};base64,{base64.b64encode(raw).decode()}"
    decode_image(uri)
    return {"type": "image_url", "image_url": {"url": uri}}


def prepare_memory(document: dict, root: Path, scope: str) -> GalleryMemory:
    writes, aliases, seen = [], {}, set()
    for session in document["multi_session_dialogues"]:
        session_id = str(session["session_id"])
        # Source dates have day precision; UTC midnight is an explicit local convention.
        stamp = int(
            datetime.strptime(session["date"], "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000
        )
        for turn in session["dialogues"]:
            round_id = str(turn["round"])
            if round_id in seen:
                raise ValueError("duplicate source round id")
            seen.add(round_id)
            paths = turn.get("input_image", [])
            captions = turn.get("image_caption", [])
            image_ids = turn.get("image_id", [])
            if len(paths) != len(image_ids) or (captions and len(captions) != len(paths)):
                raise ValueError("image, caption and source-id alignment mismatch")
            user_parts = []
            if turn.get("user"):
                user_parts.append({"type": "text", "text": turn["user"]})
            for index, path in enumerate(paths):
                if captions and captions[index]:
                    user_parts.append({"type": "text", "text": captions[index]})
                user_parts.append(image_content(root, path))
            messages = []
            if user_parts:
                messages.append({"role": "user", "timestamp": stamp, "content": user_parts})
            if turn.get("assistant"):
                messages.append(
                    {"role": "assistant", "timestamp": stamp, "content": turn["assistant"]}
                )
            if not messages:
                raise ValueError("empty source turn")
            request_id = f"{session_id}:{round_id}"
            writes.append(
                {
                    "user_id": scope,
                    "session_id": session_id,
                    "request_id": request_id,
                    "messages": messages,
                }
            )
            for position, item in enumerate(messages):
                evidence_id = "mem_" + stable_hash([scope, request_id, position])
                labels = {round_id}
                if item["role"] == "user":
                    labels.update(str(value) for value in image_ids)
                aliases[evidence_id] = labels
    return GalleryMemory(writes, aliases)


def prepare_query(question: dict, root: Path, scope: str, top_k: int) -> dict:
    query = question["question"]
    if question.get("question_image"):
        query = [{"type": "text", "text": query}, image_content(root, question["question_image"])]
    return {"query": query, "user_id": scope, "top_k": top_k}
