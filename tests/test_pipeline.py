from pathlib import Path

from app.agents import MeetingAgents
from app.config import Settings
from app.llm import DeepSeekClient
from app.pipeline import MeetingPipeline
from app.storage import Store


def settings(tmp_path: Path) -> Settings:
    return Settings(tmp_path, tmp_path / "test.sqlite3", "https://invalid.local/v1", "test", "", 0.1, 1, 0.01, 30)


def test_pipeline_creates_report_without_model(tmp_path: Path) -> None:
    source = tmp_path / "meeting.txt"
    source.write_text("王芳负责整理评审结论，周五前完成。\n会议决定采用分阶段发布。", encoding="utf-8")
    config = settings(tmp_path)
    store = Store(config.database)
    meeting_id, job_id = store.create_meeting("离线回放", str(source), "txt")
    MeetingPipeline(store, DeepSeekClient(config)).execute(meeting_id, job_id)
    report = store.report(meeting_id)
    assert report is not None
    assert report["metrics"]["fallback_agents"] == 3
    assert report["summary"]["decisions"]
    assert store.transcript(meeting_id)


def test_action_fallback_keeps_evidence() -> None:
    from app.transcribe import Segment

    config = Settings(Path("."), Path("test.sqlite3"), "", "", "", 0.1, 0, 1, 30)
    actions, trace = MeetingAgents(DeepSeekClient(config)).action_agent([Segment("说话人1", 0, 1000, "王芳负责整理结论")])
    assert trace.fallback is True
    assert actions["events"][0]["evidence"]["quote"] == "王芳负责整理结论"

