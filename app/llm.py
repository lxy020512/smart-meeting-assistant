from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings


class ModelCallError(RuntimeError):
    pass


@dataclass(frozen=True)
class CallTrace:
    agent: str
    attempts: int
    latency_ms: int
    fallback: bool
    error: str | None = None


def _json_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.S | re.I)
    if fenced:
        cleaned = fenced.group(1).strip()
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise ValueError("model response must be a JSON object")
    return value


class DeepSeekClient:
    """Minimal provider adapter with explicit reliability semantics."""

    def __init__(self, settings: Settings, transport: httpx.Client | None = None):
        self.settings = settings
        self.http = transport or httpx.Client(timeout=settings.model_timeout)

    def ask_json(self, agent: str, system: str, user: str, required: tuple[str, ...]) -> tuple[dict[str, Any] | None, CallTrace]:
        if not self.settings.model_api_key:
            return None, CallTrace(agent, 0, 0, True, "DEEPSEEK_API_KEY is not configured")
        endpoint = self.settings.model_base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.settings.model_name,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        started = time.perf_counter()
        last_error: str | None = None
        attempts = 0
        for attempts in range(1, self.settings.model_retries + 2):
            try:
                response = self.http.post(endpoint, headers={"Authorization": f"Bearer {self.settings.model_api_key}"}, json=payload)
                response.raise_for_status()
                body = response.json()
                text = body["choices"][0]["message"]["content"]
                parsed = _json_text(text)
                missing = [key for key in required if key not in parsed]
                if missing:
                    raise ValueError(f"missing required fields: {', '.join(missing)}")
                latency = int((time.perf_counter() - started) * 1000)
                return parsed, CallTrace(agent, attempts, latency, False)
            except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempts <= self.settings.model_retries:
                    time.sleep(min(2 ** (attempts - 1), 4))
        latency = int((time.perf_counter() - started) * 1000)
        return None, CallTrace(agent, attempts, latency, True, last_error)

