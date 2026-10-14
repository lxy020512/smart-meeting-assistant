from __future__ import annotations

import time
from pathlib import Path

from app.config import Settings
from app.llm import DeepSeekClient
from app.pipeline import MeetingPipeline
from app.storage import Store


def main() -> None:
    with __import__("tempfile").TemporaryDirectory() as folder:
        root = Path(folder)
        source = root / "benchmark.txt"
        source.write_text("王芳负责整理预算，周五前完成。\n会议决定采用灰度发布。", encoding="utf-8")
        settings = Settings(root, root / "benchmark.sqlite3", "", "", "", 0.1, 0, 0.1, 30)
        store = Store(settings.database)
        pipeline = MeetingPipeline(store, DeepSeekClient(settings))
        durations = []
        for index in range(5):
            meeting_id, job_id = store.create_meeting(f"bench-{index}", str(source), "txt")
            started = time.perf_counter(); pipeline.execute(meeting_id, job_id); durations.append((time.perf_counter() - started) * 1000)
        print(f"runs={len(durations)} average_ms={sum(durations) / len(durations):.2f} max_ms={max(durations):.2f}")


if __name__ == "__main__":
    main()

