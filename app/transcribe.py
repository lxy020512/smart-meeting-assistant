from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class Segment:
    speaker: str
    start_ms: int
    end_ms: int
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "speaker": self.speaker,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": self.text,
        }


class Transcriber(Protocol):
    def transcribe(self, path: Path) -> list[Segment]: ...


def normalize_segments(raw: list[dict[str, Any]]) -> list[Segment]:
    result: list[Segment] = []
    for index, item in enumerate(raw):
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        start = max(0, int(item.get("start_ms", item.get("start", 0))))
        end = max(start + 1, int(item.get("end_ms", item.get("end", start + 1000))))
        speaker = str(item.get("speaker") or f"说话人{index % 2 + 1}")
        result.append(Segment(speaker=speaker, start_ms=start, end_ms=end, text=text))
    if not result:
        raise ValueError("transcript contains no non-empty segments")
    return result


class TextOrJsonTranscriber:
    """Deterministic adapter for demos, tests, and recorded transcript exports."""

    def transcribe(self, path: Path) -> list[Segment]:
        suffix = path.suffix.lower()
        if suffix == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            raw = payload.get("segments", payload) if isinstance(payload, dict) else payload
            if not isinstance(raw, list):
                raise ValueError("JSON transcript must be a list or contain segments")
            return normalize_segments(raw)
        if suffix in {".txt", ".md", ".srt", ".vtt"}:
            lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            return normalize_segments([{"text": line, "start_ms": i * 5000, "end_ms": (i + 1) * 5000} for i, line in enumerate(lines)])
        raise ValueError(f"unsupported demo transcript format: {suffix}")


class FasterWhisperTranscriber:
    """Optional local ASR adapter; the dependency is loaded only when audio is submitted."""

    def __init__(self, model_name: str = "small", device: str = "cpu"):
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("install the optional 'asr' extra to transcribe audio") from exc
        self.model = WhisperModel(model_name, device=device, compute_type="int8" if device == "cpu" else "float16")

    def transcribe(self, path: Path) -> list[Segment]:
        pieces, _ = self.model.transcribe(str(path), vad_filter=True, word_timestamps=False)
        return normalize_segments([
            {"speaker": "说话人1", "start_ms": int(piece.start * 1000), "end_ms": int(piece.end * 1000), "text": piece.text}
            for piece in pieces
        ])


def choose_transcriber(path: Path) -> Transcriber:
    if path.suffix.lower() in {".txt", ".md", ".json", ".srt", ".vtt"}:
        return TextOrJsonTranscriber()
    return FasterWhisperTranscriber()

