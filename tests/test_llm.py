from pathlib import Path

from app.config import Settings
from app.llm import DeepSeekClient, _json_text


def test_json_parser_accepts_fenced_response() -> None:
    assert _json_text("```json\n{\"ok\": true}\n```") == {"ok": True}


def test_client_falls_back_without_secret(tmp_path: Path) -> None:
    config = Settings(tmp_path, tmp_path / "x.sqlite3", "https://example.invalid/v1", "test", "", 0.1, 1, 0.1, 30)
    value, trace = DeepSeekClient(config).ask_json("test", "system", "user", ("summary",))
    assert value is None
    assert trace.fallback is True

