from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .agents import MeetingAgents
from .llm import DeepSeekClient
from .retrieval import index_segments
from .storage import Store
from .transcribe import Segment, choose_transcriber


class MeetingPipeline:
    def __init__(self, store: Store, client: DeepSeekClient):
        self.store = store
        self.agents = MeetingAgents(client)

    def execute(self, meeting_id: str, job_id: str) -> None:
        meeting = self.store.meeting(meeting_id)
        if meeting is None:
            raise ValueError(f"meeting {meeting_id} does not exist")
        started = time.perf_counter()
        source = Path(meeting["source_path"])

        self.store.append_event(job_id, "transcribe", "started", {"source_type": meeting["source_type"]})
        segments = choose_transcriber(source).transcribe(source)
        source_version = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        self.store.save_transcript(meeting_id, [segment.as_dict() for segment in segments], source_version)
        self.store.append_event(job_id, "transcribe", "succeeded", {"segments": len(segments), "source_version": source_version})

        self.store.append_event(job_id, "index", "started")
        index_segments(self.store, meeting_id, segments)
        self.store.append_event(job_id, "index", "succeeded", {"chunks": len(segments)})

        self.store.append_event(job_id, "agents", "started", {"agents": ["summary", "actions", "critic"]})
        bundle = self.agents.run(segments)
        report = {
            "meeting_id": meeting_id,
            "title": meeting["title"],
            "summary": bundle.summary,
            "actions": bundle.actions,
            "review": bundle.review,
            "agent_traces": [trace.__dict__ for trace in bundle.traces],
            "metrics": {"processing_ms": int((time.perf_counter() - started) * 1000), "segment_count": len(segments), "fallback_agents": sum(trace.fallback for trace in bundle.traces)},
        }
        self.store.save_report(meeting_id, report)
        self.store.append_event(job_id, "agents", "succeeded", {"fallback_agents": report["metrics"]["fallback_agents"]})

