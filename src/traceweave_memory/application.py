import copy
from datetime import UTC, datetime

from traceweave_memory.domain import (
    MAX_MEDIA_BYTES,
    Evidence,
    MediaError,
    lexical_tokens,
    stable_hash,
    text_view,
)
from traceweave_memory.ports import Media, Repository


def inspect_images(
    content: str | list[dict], media: Media
) -> tuple[int, dict[int, tuple[bytes, str]]]:
    images = {}
    if not isinstance(content, str):
        for index, part in enumerate(content):
            if part["type"] == "image_url":
                images[index] = media.decode(part["image_url"]["url"])
    return sum(len(raw) for raw, _ in images.values()), images


class MemoryService:
    def __init__(self, repository: Repository, media: Media):
        self.repository = repository
        self.media = media

    def add(self, payload: dict) -> dict:
        # Validate all media/timestamps before writing any evidence or media.
        prepared = []
        total = 0
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        for message in payload["messages"]:
            size, images = inspect_images(message["content"], self.media)
            total += size
            if total > MAX_MEDIA_BYTES:
                raise MediaError("Add images exceed 30 MiB decoded")
            timestamp = message.get("timestamp")
            try:
                created = (
                    (
                        datetime.fromtimestamp(timestamp / 1000, UTC)
                        .isoformat()
                        .replace("+00:00", "Z")
                    )
                    if timestamp is not None
                    else now
                )
            except (OverflowError, OSError, ValueError) as exc:
                raise MediaError("timestamp is outside the supported UTC range") from exc
            prepared.append((message["content"], size, images, created, message["role"], timestamp))

        records = []
        for position, (content, size, images, created, role, timestamp) in enumerate(prepared):
            stored = copy.deepcopy(content)
            for index, (raw, subtype) in images.items():
                stored[index] = {
                    "type": "image_url",
                    "media_ref": self.media.put(payload["user_id"], raw, subtype),
                }
            evidence_id = "mem_" + stable_hash(
                [payload["user_id"], payload["request_id"], position]
            )
            records.append(
                Evidence(evidence_id, stored, created, size, role=role, source_timestamp=timestamp)
            )
        self.repository.add(
            payload["user_id"],
            payload["request_id"],
            payload["session_id"],
            stable_hash(payload),
            records,
        )
        return {
            "success": True,
            **{key: payload[key] for key in ("request_id", "user_id", "session_id")},
        }

    def search(self, payload: dict) -> dict:
        size, _ = inspect_images(payload["query"], self.media)
        if size > MAX_MEDIA_BYTES:
            raise MediaError("query images exceed the local 30 MiB decoded limit")
        # B0 validates but does not interpret image-only queries; no fabricated caption.
        tokens = lexical_tokens(text_view(payload["query"]))
        candidates = self.repository.search(payload["user_id"], tokens, payload["top_k"])
        results = []
        used = 0
        for record in candidates:
            if used + record.media_bytes > MAX_MEDIA_BYTES:
                continue
            content = copy.deepcopy(record.content)
            if not isinstance(content, str):
                for index, part in enumerate(content):
                    if part["type"] == "image_url":
                        content[index] = {
                            "type": "image_url",
                            "image_url": {
                                "url": self.media.get(payload["user_id"], part["media_ref"])
                            },
                        }
            results.append(
                {
                    "id": record.id,
                    "content": content,
                    "score": record.score,
                    "created_at": record.created_at,
                }
            )
            used += record.media_bytes
        return {"data": results}
