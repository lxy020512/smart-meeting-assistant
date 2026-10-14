from pathlib import Path

from app.retrieval import index_segments, search
from app.storage import Store
from app.transcribe import Segment


def test_retrieval_returns_evidence_with_location(tmp_path: Path) -> None:
    store = Store(tmp_path / "search.sqlite3")
    index_segments(store, "meeting-a", [Segment("说话人2", 1200, 3000, "产品团队决定下周发布灰度版本")])
    results = search(store, "灰度发布")
    assert results and results[0]["meeting_id"] == "meeting-a"
    assert results[0]["start_ms"] == 1200

