from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from app.config import Settings
from app.llm import DeepSeekClient
from app.pipeline import MeetingPipeline
from app.storage import Store


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a deterministic meeting report replay")
    parser.add_argument("--input", type=Path, help="txt/json transcript; omit to use built-in Chinese sample")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        source = args.input or root / "sample.txt"
        if not args.input:
            source.write_text("李宁负责整理用户反馈，周三前完成。\n会议决定先做小范围灰度发布。", encoding="utf-8")
        config = Settings(root, root / "replay.sqlite3", "", "", "", 0.1, 0, 0.1, 30)
        store = Store(config.database)
        meeting_id, job_id = store.create_meeting(source.stem, str(source), source.suffix.lstrip("."))
        MeetingPipeline(store, DeepSeekClient(config)).execute(meeting_id, job_id)
        print(json.dumps(store.report(meeting_id), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

