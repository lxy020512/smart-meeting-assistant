from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class Segment:
    speaker: str
    start_ms: int
    end_ms: int
    text: str
    timing_known: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "speaker": self.speaker,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": self.text,
            "timing_known": self.timing_known,
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
        speaker = str(item.get("speaker") or "未标注")
        known = bool(item.get("timing_known", "start_ms" in item or "start" in item))
        result.append(Segment(speaker=speaker, start_ms=start, end_ms=end, text=text, timing_known=known))
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
        if suffix in {".srt", ".vtt"}:
            text = path.read_text(encoding="utf-8-sig")
            pattern = r"(?P<start>\d{1,2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(?P<end>\d{1,2}:\d{2}:\d{2}[,.]\d{3})[^\n]*\n(?P<text>.*?)(?=\n\s*\n|\Z)"
            def milliseconds(value):
                hour, minute, second, ms = map(int, re.split(r"[:,.]", value))
                return ((hour*60+minute)*60+second)*1000+ms
            return normalize_segments([{"text": re.sub(r"<[^>]+>", "", m["text"]).replace("\n", " "), "start_ms": milliseconds(m["start"]), "end_ms": milliseconds(m["end"])} for m in re.finditer(pattern, text, re.S)])
        if suffix in {".txt", ".md"}:
            lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            return normalize_segments([{"text": line, "timing_known": False} for line in lines])
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
            {"speaker": "未标注", "start_ms": int(piece.start * 1000), "end_ms": int(piece.end * 1000), "text": piece.text}
            for piece in pieces
        ])


class FunASRTranscriber:
    """Optional Chinese ASR + VAD + speaker labeling adapter."""

    def __init__(self, device: str = "cpu"):
        try:
            from funasr import AutoModel
        except ImportError as exc:
            raise RuntimeError("install FunASR separately or choose ASR_BACKEND=faster-whisper") from exc
        self.model = AutoModel(
            model=os.getenv("FUNASR_MODEL", "paraformer-zh"),
            vad_model=os.getenv("FUNASR_VAD_MODEL", "fsmn-vad"),
            punc_model=os.getenv("FUNASR_PUNC_MODEL", "ct-punc-c"),
            spk_model=os.getenv("FUNASR_SPK_MODEL", "cam++"),
            device=device,
            disable_update=True,
        )

    def transcribe(self, path: Path) -> list[Segment]:
        result = self.model.generate(input=str(path), batch_size_s=300, sentence_timestamp=True)
        if not result:
            raise ValueError("FunASR returned no result")
        item = result[0] if isinstance(result, list) else result
        raw = item.get("sentence_info") or item.get("segments") or []
        if not raw and item.get("text"):
            raw = [{"text": item["text"], "start": 0, "end": 1, "timing_known": False}]
        normalized = []
        for index, sentence in enumerate(raw):
            stamp = sentence.get("timestamp") or []
            start = sentence.get("start", stamp[0][0] if stamp else 0)
            end = sentence.get("end", stamp[-1][-1] if stamp else 1)
            speaker = sentence.get("spk", sentence.get("speaker"))
            label = f"说话人{speaker}" if speaker is not None else "未标注"
            normalized.append({"speaker": label, "start_ms": int(start), "end_ms": int(end), "text": sentence.get("text", ""), "timing_known": sentence.get("timing_known", "start" in sentence or bool(stamp))})
        return normalize_segments(normalized)


class RemoteASRTranscriber:
    def __init__(self, endpoint: str):
        self.endpoint = endpoint

    def transcribe(self, path: Path) -> list[Segment]:
        import httpx
        with path.open("rb") as audio:
            response = httpx.post(self.endpoint, files={"file": (path.name, audio)}, timeout=300)
        response.raise_for_status()
        payload = response.json()
        return normalize_segments(payload.get("segments", []) if isinstance(payload, dict) else payload)


@lru_cache(maxsize=4)
def audio_transcriber(backend, device, asr_url):
    if backend == "remote":
        if not asr_url:
            raise RuntimeError("ASR_URL is required for ASR_BACKEND=remote")
        return RemoteASRTranscriber(asr_url)
    if backend == "funasr":
        return FunASRTranscriber(device)
    if backend == "faster-whisper":
        return FasterWhisperTranscriber(device=device)
    raise ValueError("unknown ASR backend")


def choose_transcriber(path: Path, backend: str = "funasr", device: str = "cpu", asr_url: str = "") -> Transcriber:
    if path.suffix.lower() in {".txt", ".md", ".json", ".srt", ".vtt"}:
        return TextOrJsonTranscriber()
    return audio_transcriber(backend, device, asr_url)

