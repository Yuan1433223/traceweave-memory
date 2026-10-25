import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("data/service")
    api_key: str = field(default="", repr=False)
    auth_mode: str = "bearer"
    max_body_bytes: int = 64 * 1024 * 1024

    def __post_init__(self):
        if self.auth_mode not in {"bearer", "token", "x-api-key"}:
            raise ValueError("TW_AUTH_MODE must be bearer, token or x-api-key")
        if self.max_body_bytes <= 0:
            raise ValueError("TW_MAX_BODY_MIB must be positive")

    @classmethod
    def from_env(cls):
        return cls(
            data_dir=Path(os.environ.get("TW_DATA_DIR", "data/service")),
            api_key=os.environ.get("TW_API_KEY", ""),
            auth_mode=os.environ.get("TW_AUTH_MODE", "bearer").lower(),
            max_body_bytes=int(os.environ.get("TW_MAX_BODY_MIB", "64")) * 1024 * 1024,
        )
