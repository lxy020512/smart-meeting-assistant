from __future__ import annotations

import json
import math
import re
from collections import Counter
from typing import Any

from .storage import Store
from .transcribe import Segment


def tokenize(text: str) -> list[str]:
    text = text.lower()
    words = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text)
    chinese = [char for char in text if "\u4e00" <= char <= "\u9fff"]
    bigrams = ["".join(chinese[i : i + 2]) for i in range(len(chinese) - 1)]
    return words + bigrams


def index_segments(store: Store, meeting_id: str, segments: list[Segment]) -> None:
    chunks = [{"speaker": s.speaker, "start_ms": s.start_ms, "end_ms": s.end_ms, "text": s.text, "tokens": tokenize(s.text)} for s in segments]
    store.replace_chunks(meeting_id, chunks)


def search(store: Store, query: str, top_k: int = 5) -> list[dict[str, Any]]:
    query_tokens = tokenize(query)
    if not query_tokens:
        return []
    rows = store.all_chunks()
    document_frequency = Counter()
    parsed: list[tuple[Any, list[str]]] = []
    for row in rows:
        tokens = json.loads(row["tokens"])
        parsed.append((row, tokens))
        document_frequency.update(set(tokens))
    total = max(1, len(parsed))
    results = []
    for row, tokens in parsed:
        counts = Counter(tokens)
        score = 0.0
        for token in query_tokens:
            if token not in counts:
                continue
            idf = math.log((total + 1) / (document_frequency[token] + 1)) + 1
            score += idf * (1 + math.log(counts[token]))
        if query in row["text"]:
            score += 2.0
        if score:
            results.append({"meeting_id": row["meeting_id"], "speaker": row["speaker"], "start_ms": row["start_ms"], "end_ms": row["end_ms"], "text": row["text"], "score": round(score, 4)})
    return sorted(results, key=lambda item: item["score"], reverse=True)[:top_k]

