from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .llm import CallTrace, DeepSeekClient
from .transcribe import Segment


def transcript_text(segments: list[Segment]) -> str:
    return "\n".join(f"[{s.start_ms / 1000:.1f}s] {s.speaker}: {s.text}" for s in segments)


def _string_list(value: Any) -> list[str]:
    return [str(item).strip() for item in value] if isinstance(value, list) else []


def _fallback_summary(segments: list[Segment]) -> dict[str, Any]:
    lines = [segment.text for segment in segments]
    decisions = [line for line in lines if any(token in line for token in ("决定", "确定", "同意", "采用", "通过"))]
    return {
        "summary": "；".join(lines[:4])[:600],
        "decisions": decisions[:5],
        "keywords": list(dict.fromkeys(re.findall(r"[\u4e00-\u9fff]{2,8}", " ".join(lines))))[:8],
    }


def _fallback_actions(segments: list[Segment]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    pattern = re.compile(r"(?P<owner>[^，。；,; ]{1,8})?(?:负责|需要|请|计划|安排)(?P<task>[^，。；,;]{2,50})")
    for segment in segments:
        for match in pattern.finditer(segment.text):
            owner = (match.group("owner") or segment.speaker).strip()
            task = match.group("task").strip()
            actions.append({"task": task, "owner": owner, "due": None, "evidence": segment.text, "start_ms": segment.start_ms})
    return actions[:20]


@dataclass
class AgentBundle:
    summary: dict[str, Any]
    actions: list[dict[str, Any]]
    review: dict[str, Any]
    traces: list[CallTrace]


class MeetingAgents:
    def __init__(self, client: DeepSeekClient):
        self.client = client

    def summary_agent(self, segments: list[Segment]) -> tuple[dict[str, Any], CallTrace]:
        raw, trace = self.client.ask_json(
            "summary",
            "你是中文会议纪要 Agent。只输出 JSON，不能编造原文没有的事实。",
            f"请从以下会议原文提取 summary、decisions、keywords 三个字段。原文：\n{transcript_text(segments)}",
            ("summary", "decisions", "keywords"),
        )
        if raw is None:
            return _fallback_summary(segments), trace
        return {"summary": str(raw.get("summary", "")), "decisions": _string_list(raw.get("decisions")), "keywords": _string_list(raw.get("keywords"))}, trace

    def action_agent(self, segments: list[Segment]) -> tuple[list[dict[str, Any]], CallTrace]:
        raw, trace = self.client.ask_json(
            "actions",
            "你是行动项提取 Agent。只输出 JSON，actions 必须是数组，每项包含 task、owner、due、evidence、start_ms。没有证据时不要臆测。",
            f"请从以下带时间的会议原文提取行动项：\n{transcript_text(segments)}",
            ("actions",),
        )
        if raw is None:
            return _fallback_actions(segments), trace
        result: list[dict[str, Any]] = []
        for item in raw.get("actions", []):
            if not isinstance(item, dict) or not str(item.get("task", "")).strip():
                continue
            result.append({"task": str(item["task"]).strip(), "owner": str(item.get("owner") or "待确认"), "due": item.get("due"), "evidence": str(item.get("evidence") or ""), "start_ms": int(item.get("start_ms") or 0)})
        return result[:30], trace

    def critic_agent(self, segments: list[Segment], summary: dict[str, Any], actions: list[dict[str, Any]]) -> tuple[dict[str, Any], CallTrace]:
        payload = {"summary": summary, "actions": actions, "transcript": [s.as_dict() for s in segments]}
        raw, trace = self.client.ask_json(
            "critic",
            "你是证据审校 Agent。只输出 JSON，检查每个行动项是否能在 transcript 中找到证据。",
            str(payload),
            ("approved", "issues"),
        )
        if raw is not None:
            return {"approved": bool(raw.get("approved")), "issues": _string_list(raw.get("issues"))}, trace
        source = " ".join(s.text for s in segments)
        issues = [f"行动项缺少原文证据：{item['task']}" for item in actions if item.get("evidence") and item["evidence"] not in source]
        return {"approved": not issues, "issues": issues}, trace

    def run(self, segments: list[Segment]) -> AgentBundle:
        summary, summary_trace = self.summary_agent(segments)
        actions, action_trace = self.action_agent(segments)
        review, review_trace = self.critic_agent(segments, summary, actions)
        return AgentBundle(summary, actions, review, [summary_trace, action_trace, review_trace])

