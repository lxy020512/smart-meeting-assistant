from pathlib import Path

from app.config import Settings
from app.queue import DurableWorker
from app.storage import Store


def test_worker_retries_then_marks_dead(tmp_path: Path) -> None:
    config = Settings(tmp_path, tmp_path / "queue.sqlite3", "", "", "", 0.1, 0, 0.01, 30)
    store = Store(config.database)
    meeting_id, job_id = store.create_meeting("失败回放", str(tmp_path / "missing.txt"), "txt")
    calls = []

    def fail(_meeting: str, _job: str, _token: str) -> None:
        calls.append(1)
        raise RuntimeError("expected failure")

    worker = DurableWorker(store, config, fail, max_attempts=2)
    assert worker.run_once() is True
    with store._db() as db:
        db.execute("UPDATE jobs SET available_at='2000-01-01' WHERE id=?", (job_id,))
    assert worker.run_once() is True
    assert len(calls) == 2
    assert store.job(job_id)["state"] == "dead"
    assert any(event["state"] == "dead" for event in store.events(job_id))

