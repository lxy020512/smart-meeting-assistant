from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database: Path
    model_base_url: str
    model_name: str
    model_api_key: str
    model_timeout: float
    model_retries: int
    worker_poll_seconds: float
    worker_lease_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.getenv("MEETING_DATA_DIR", "./data")).resolve()
        return cls(
            data_dir=data_dir,
            database=Path(os.getenv("MEETING_DB", str(data_dir / "meetings.sqlite3"))).resolve(),
            model_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            model_name=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            model_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            model_timeout=float(os.getenv("MODEL_TIMEOUT_SECONDS", "45")),
            model_retries=max(0, int(os.getenv("MODEL_RETRIES", "2"))),
            worker_poll_seconds=float(os.getenv("WORKER_POLL_SECONDS", "0.5")),
            worker_lease_seconds=max(30, int(os.getenv("WORKER_LEASE_SECONDS", "300"))),
        )

    def prepare(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "uploads").mkdir(parents=True, exist_ok=True)

