"""OpenAI-compatible chat client.

OpenRouter and Ollama both expose `/chat/completions`, so a single client
covers both: switch by changing `base_url` + `model` in config.yaml.
"""

from __future__ import annotations

import json
import re
import time

import requests


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        temperature: float = 0.2,
        max_retries: int = 3,
        timeout: float = 90.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_retries = max_retries
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        """OpenRouter requires these headers for free-tier access."""
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        if "openrouter.ai" in self.base_url:
            h["Referer"] = "https://openrouter.ai"
            h["X-Title"] = "WhatsApp Form Automation"
        return h

    def chat(self, messages: list[dict], max_tokens: int = 1500) -> str:
        """Send a chat completion, retrying transient failures with backoff."""
        headers = self._headers()
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
        }
        url = f"{self.base_url}/chat/completions"
        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
                if resp.status_code in (429, 500, 502, 503) and attempt < self.max_retries:
                    wait = 2**attempt
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                choice = data["choices"][0]["message"]
                content = choice.get("content") or choice.get("reasoning")
                if not content:
                    raise LLMError(f"Empty completion from {self.model}")
                return content
            except (requests.RequestException, KeyError, ValueError) as exc:
                last_err = exc
                if attempt < self.max_retries:
                    time.sleep(2**attempt)
        raise LLMError(f"LLM request failed after {self.max_retries} attempts: {last_err}")

    def complete(self, system: str, user: str, max_tokens: int = 1500) -> str:
        return self.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=max_tokens,
        )


def extract_json(text: str):
    """Pull the first JSON object out of an LLM reply (models love to add prose)."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise LLMError(f"No JSON object found in reply: {text[:200]!r}")
    return json.loads(match.group(0))


def list_free_models(api_base: str = "https://openrouter.ai/api/v1") -> list[str]:
    """Query OpenRouter's public model list and return ids with zero cost."""
    resp = requests.get(f"{api_base.rstrip('/')}/models", timeout=30)
    resp.raise_for_status()
    free = []
    for model in resp.json().get("data", []):
        pricing = model.get("pricing") or {}
        try:
            if float(pricing.get("prompt", 1)) == 0 and float(pricing.get("completion", 1)) == 0:
                free.append(model["id"])
        except (TypeError, ValueError):
            continue
    return sorted(free)