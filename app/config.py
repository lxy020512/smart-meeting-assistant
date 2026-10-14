from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


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
    asr_backend: str = "funasr"
    asr_device: str = "cpu"
    asr_url: str = ""
    semantic_enabled: bool = False
    embed_model: str = "BAAI/bge-small-zh-v1.5"
    embedded_worker: bool = True
    max_upload_mb: int = 200
    agent_chunk_chars: int = 5000
    api_token: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(os.getenv("MODEL_ENV_FILE") or ".env", override=False)
        data_dir = Path(os.getenv("MEETING_DATA_DIR", "./data")).resolve()
        return cls(
            data_dir=data_dir,
            database=Path(os.getenv("MEETING_DB", str(data_dir / "meetings.sqlite3"))).resolve(),
            model_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            model_name=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            model_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            model_timeout=float(os.getenv("MODEL_TIMEOUT_SECONDS", "45")),
            model_retries=min(3, max(0, int(os.getenv("MODEL_RETRIES", "2")))),
            worker_poll_seconds=float(os.getenv("WORKER_POLL_SECONDS", "0.5")),
            worker_lease_seconds=max(30, int(os.getenv("WORKER_LEASE_SECONDS", "300"))),
            asr_backend=os.getenv("ASR_BACKEND", "funasr"),
            asr_device=os.getenv("ASR_DEVICE", "cpu"),
            asr_url=os.getenv("ASR_URL", ""),
            semantic_enabled=os.getenv("SEMANTIC_ENABLED", "false").lower() == "true",
            embed_model=os.getenv("EMBED_MODEL", "BAAI/bge-small-zh-v1.5"),
            embedded_worker=os.getenv("EMBEDDED_WORKER", "true").lower() == "true",
            max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "200")),
            agent_chunk_chars=max(1000, int(os.getenv("AGENT_CHUNK_CHARS", "5000"))),
            api_token=os.getenv("API_TOKEN", ""),
        )

    def prepare(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "uploads").mkdir(parents=True, exist_ok=True)

