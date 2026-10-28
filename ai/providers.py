from __future__ import annotations

from typing import Any

import httpx

from core.ai.provider import LLMProvider, parse_json_strict


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-4o-mini", base_url: str = "https://api.openai.com/v1"):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    def _chat(self, messages: list[dict[str, str]], max_tokens: int | None = None) -> str:
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        if max_tokens:
            payload["max_tokens"] = max_tokens
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def generate(self, prompt: str, system: str | None = None, **kwargs: Any) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self._chat(messages, max_tokens=kwargs.get("max_tokens"))

    def structured_output(self, prompt: str, system: str | None = None, **kwargs: Any) -> dict[str, Any]:
        system = system or "Return ONLY valid JSON. Never invent information."
        return parse_json_strict(self.generate(prompt, system=system, **kwargs))


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        self.api_key = api_key
        self.model = model

    def generate(self, prompt: str, system: str | None = None, **kwargs: Any) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": kwargs.get("max_tokens", 1024),
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        text = resp.json()["content"]
        if isinstance(text, list):
            text = "".join(block.get("text", "") for block in text)
        return text


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, base_url: str, model: str = "llama3.1"):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def generate(self, prompt: str, system: str | None = None, **kwargs: Any) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "stream": False}
        if kwargs.get("max_tokens"):
            payload["options"] = {"num_predict": kwargs["max_tokens"]}
        resp = httpx.post(
            f"{self.base_url}/api/chat",
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        body = resp.json()
        return body.get("message", {}).get("content") or body.get("response", "")