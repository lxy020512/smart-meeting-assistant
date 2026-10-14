from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel

from .config import Settings


@dataclass(frozen=True)
class CallTrace:
    agent: str
    attempts: int
    latency_ms: int
    fallback: bool
    error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0


def _json_text(text: str) -> dict[str, Any]:
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text.strip(), re.S | re.I)
    value = json.loads(match.group(1) if match else text)
    if not isinstance(value, dict):
        raise ValueError("response must be an object")
    return value


class DeepSeekClient:
    def __init__(self, settings: Settings, transport: httpx.Client | None = None, sleeper=time.sleep):
        self.settings = settings
        self.http = transport or httpx.Client(timeout=settings.model_timeout)
        self.sleeper = sleeper

    def close(self):
        self.http.close()

    def ask_json(self, agent, system, user, required=(), schema: type[BaseModel] | None = None):
        if not self.settings.model_api_key:
            return None, CallTrace(agent, 0, 0, True, "model_key_missing")
        if schema:
            system += "\n输出必须满足 JSON Schema：" + json.dumps(schema.model_json_schema(), ensure_ascii=False)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        payload = {"model": self.settings.model_name, "temperature": 0.1, "response_format": {"type": "json_object"}, "max_tokens": 4096, "messages": messages}
        started, last_error, prompt_tokens, completion_tokens = time.perf_counter(), None, 0, 0
        for attempt in range(1, self.settings.model_retries + 2):
            retryable = True
            try:
                response = self.http.post(self.settings.model_base_url.rstrip("/") + "/chat/completions", headers={"Authorization": f"Bearer {self.settings.model_api_key}"}, json=payload, timeout=self.settings.model_timeout)
                response.raise_for_status()
                body = response.json()
                usage = body.get("usage") or {}
                prompt_tokens += int(usage.get("prompt_tokens", 0))
                completion_tokens += int(usage.get("completion_tokens", 0))
                parsed = _json_text(body["choices"][0]["message"]["content"])
                if schema:
                    parsed = schema.model_validate(parsed).model_dump()
                elif any(key not in parsed for key in required):
                    raise ValueError("required field missing")
                return parsed, CallTrace(agent, attempt, int((time.perf_counter() - started) * 1000), False, None, prompt_tokens, completion_tokens)
            except httpx.HTTPStatusError as exc:
                last_error = f"http_{exc.response.status_code}"
                retryable = exc.response.status_code in {408, 429} or exc.response.status_code >= 500
            except httpx.TimeoutException:
                last_error = "timeout"
            except httpx.RequestError:
                last_error = "network_error"
            except (ValueError, KeyError, TypeError, IndexError):
                last_error = "invalid_structured_output"
                # Do not echo invalid content; it may contain sensitive source text.
                payload["messages"] = messages + [{"role": "user", "content": "上次输出格式不合规。请严格返回满足所给 Schema 的 JSON 对象。"}]
            if not retryable or attempt > self.settings.model_retries:
                break
            self.sleeper(min(2 ** (attempt - 1), 4))
        return None, CallTrace(agent, attempt, int((time.perf_counter() - started) * 1000), True, last_error, prompt_tokens, completion_tokens)
