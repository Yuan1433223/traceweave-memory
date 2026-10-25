"""Evidence and deterministic lexical rules; no framework or database imports."""

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass

MIB = 1024 * 1024
MAX_IMAGE_BYTES = 10 * MIB
MAX_MEDIA_BYTES = 30 * MIB


class MemoryErrorBase(Exception):
    """Expected service failure with a safe, content-free message."""


class WriteConflict(MemoryErrorBase):
    pass


class MediaError(MemoryErrorBase):
    pass


class MediaUnavailable(MemoryErrorBase):
    pass


def stable_hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def lexical_tokens(text: str) -> list[str]:
    """Unicode words plus CJK uni/bigrams; numbers and source text remain intact."""
    text = unicodedata.normalize("NFKC", text).casefold()
    tokens = re.findall(r"[^\W_]+", text, flags=re.UNICODE)
    result = []
    for token in tokens:
        for part in re.findall(r"[\u3400-\u9fff]+|[^\u3400-\u9fff]+", token):
            if re.fullmatch(r"[\u3400-\u9fff]+", part):
                result.extend(part)
                result.extend(part[i : i + 2] for i in range(len(part) - 1))
            else:
                result.append(part)
    return result


def text_view(content: str | list[dict]) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(part["text"] for part in content if part["type"] == "text")


@dataclass(frozen=True)
class Evidence:
    id: str
    content: str | list[dict]
    created_at: str
    media_bytes: int
    score: float = 0.0
    role: str = "user"
    source_timestamp: int | None = None
