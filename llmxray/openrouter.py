"""Minimal OpenRouter client built on urllib (no third-party deps)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class ChatResult:
    content: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    cost_usd: float | None
    finish_reason: str | None
    latency_ms: int
    raw: dict


class OpenRouterError(Exception):
    pass


class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        referer: str = "https://localhost/llm-xray",
        title: str = "llm-xray compare",
        timeout: int = 120,
        max_retries: int = 4,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.referer = referer
        self.title = title
        self.timeout = timeout
        self.max_retries = max_retries

    def chat(
        self,
        model: str,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> ChatResult:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            # Ask OpenRouter to include cost/usage in the response.
            "usage": {"include": True},
        }
        payload = json.dumps(body).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self.referer,
            "X-Title": self.title,
        }
        url = f"{self.base_url}/chat/completions"

        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            start = time.monotonic()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = json.loads(resp.read().decode("utf-8"))
                latency_ms = int((time.monotonic() - start) * 1000)
                return self._parse(raw, latency_ms)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")
                # Retry on rate limit / server errors; fail fast otherwise.
                if e.code in (429, 500, 502, 503, 504) and attempt < self.max_retries - 1:
                    last_err = OpenRouterError(f"HTTP {e.code}: {detail}")
                    time.sleep(self._backoff(attempt))
                    continue
                raise OpenRouterError(f"HTTP {e.code}: {detail}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                last_err = OpenRouterError(str(e))
                if attempt < self.max_retries - 1:
                    time.sleep(self._backoff(attempt))
                    continue
                raise OpenRouterError(str(e)) from e

        raise last_err or OpenRouterError("unknown error")

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(2.0 ** attempt, 30.0)

    @staticmethod
    def _parse(raw: dict, latency_ms: int) -> ChatResult:
        if "error" in raw and raw["error"]:
            msg = raw["error"]
            if isinstance(msg, dict):
                msg = msg.get("message", json.dumps(msg))
            raise OpenRouterError(str(msg))
        try:
            choice = raw["choices"][0]
            content = choice["message"]["content"]
            finish = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError) as e:
            raise OpenRouterError(f"Unexpected response shape: {json.dumps(raw)[:500]}") from e

        usage = raw.get("usage") or {}
        return ChatResult(
            content=content or "",
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            cost_usd=usage.get("cost"),
            finish_reason=finish,
            latency_ms=latency_ms,
            raw=raw,
        )
