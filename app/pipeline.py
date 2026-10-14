from __future__ import annotations

import hashlib
import time
from pathlib import Path

from .agents import MeetingAgents, replay_events, valid_evidence
from .retrieval import EvidenceIndex
from .transcribe import Segment, choose_transcriber


def segment_chunks(segments, max_chars):
    groups, current, chars, offset = [], [], 0, 0
    for segment in segments:
        if current and chars + len(segment.text) > max_chars:
            groups.append((offset, current))
            offset += len(current)
            current, chars = [], 0
        if len(segment.text) > max_chars:
            raise ValueError("a single transcript segment exceeds the configured context limit")
        current.append(segment)
        chars += len(segment.text)
    if current:
        groups.append((offset, current))
    if len(groups) > 64:
        raise ValueError("meeting exceeds 64 context windows; increase chunk budget")
    return groups


class MeetingPipeline:
    def __init__(self, store, client, settings=None, index=None):
        self.store, self.settings, self.agents = store, settings, MeetingAgents(client)
        self.index = index or EvidenceIndex(store, settings)

    def execute(self, meeting_id, job_id, token=None):
        meeting = self.store.meeting(meeting_id)
        if not meeting:
            raise ValueError("meeting not found")
        started = time.perf_counter()
        source = Path(meeting["source_path"])
        with source.open("rb") as stream:
            source_hash = hashlib.file_digest(stream, "sha256").hexdigest()

        def stage(name, operation):
            cached = self.store.checkpoint(job_id, name)
            if cached and cached.get("source_hash") == source_hash:
                self.store.append_event(job_id, name, "reused", token=token)
                return cached["result"]
            self.store.append_event(job_id, name, "started", token=token)
            before = time.perf_counter()
            try:
                value = operation()
                self.store.save_checkpoint(job_id, name, {"source_hash": source_hash, "result": value}, token)
                self.store.append_event(job_id, name, "succeeded", {"elapsed_ms": round((time.perf_counter()-before)*1000)}, token)
                return value
            except Exception as exc:
                self.store.append_event(job_id, name, "failed", {"error_type": type(exc).__name__}, token)
                raise

        def transcribe():
            config = self.settings
            adapter = choose_transcriber(source, config.asr_backend, config.asr_device, config.asr_url) if config else choose_transcriber(source)
            return [s.as_dict() for s in adapter.transcribe(source)]

        raw_segments = stage("transcribe", transcribe)
        segments = [Segment(**s) for s in raw_segments]
        self.store.save_transcript(meeting_id, raw_segments, source_hash, job_id, token)
        index_mode = stage("index", lambda: self.index.index(meeting_id, segments, source_hash, job_id, token))
        groups = segment_chunks(segments, self.settings.agent_chunk_chars if self.settings else 5000)
        summaries, decisions, keywords, events, traces, issues = [], [], [], [], [], []
        for part, (offset, window) in enumerate(groups):
            def summary_call():
                value, trace = self.agents.summary_agent(window, offset)
                return {"output": value, "trace": trace.__dict__}

            summary_result = stage(f"summary:{part}", summary_call)
            value = summary_result["output"]
            summaries.append(value["summary"])
            keywords.extend(value["keywords"])
            traces.append(summary_result["trace"])
            for decision in value["decisions"]:
                if valid_evidence(decision["evidence"], segments):
                    decisions.append(decision)
                else:
                    issues.append("拒绝无效决议证据")
            known, _, _ = replay_events(events, segments)

            def action_call():
                value, trace = self.agents.action_agent(window, offset, known)
                return {"output": value, "trace": trace.__dict__}

            result = stage(f"actions:{part}", action_call)
            events.extend(result["output"]["events"])
            traces.append(result["trace"])
        actions, history, replay_issues = replay_events(events, segments)
        issues.extend(replay_issues)
        summary = {"summary": "\n".join(summaries), "decisions": decisions, "keywords": list(dict.fromkeys(keywords))[:20]}
        critic_issues, critic_approved = [], True
        for part, (offset, window) in enumerate(groups):
            relevant = [a for a in actions if offset <= a["evidence"]["segment_id"] < offset + len(window)]
            # Keep original ids by using the same global-id JSON protocol.
            def critic_call():
                value, trace = self.agents.critic_agent(window, {"summary": summaries[part], "decisions": [d for d in decisions if offset <= d["evidence"]["segment_id"] < offset+len(window)]}, relevant, offset)
                return {"output": value, "trace": trace.__dict__}

            result = stage(f"critic:{part}", critic_call)
            traces.append(result["trace"])
            critic_approved &= result["output"]["approved"]
            critic_issues.extend(result["output"]["issues"])
        review = {"approved": critic_approved and not issues, "issues": issues + critic_issues, "program_evidence_valid": not issues}
        report = {"meeting_id": meeting_id, "title": meeting["title"], "source_version": source_hash, "summary": summary, "actions": [a for a in actions if a["status"] == "active"], "cancelled_actions": [a for a in actions if a["status"] == "cancelled"], "task_history": history, "review": review, "agent_traces": traces, "metrics": {"processing_ms": round((time.perf_counter()-started)*1000), "segment_count": len(segments), "context_windows": len(groups), "fallback_agents": sum(t["fallback"] for t in traces), "prompt_tokens": sum(t["prompt_tokens"] for t in traces), "completion_tokens": sum(t["completion_tokens"] for t in traces), "retrieval_mode": index_mode}}
        self.store.save_report(meeting_id, report, job_id, token)
        self.store.append_event(job_id, "report", "succeeded", report["metrics"], token)
