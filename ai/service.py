from __future__ import annotations

from typing import Any

from core.ai.fallback import RuleBasedProvider
from core.ai.provider import LLMProvider
from core.ai.providers import AnthropicProvider, OllamaProvider, OpenAIProvider
from core.config import get_settings
from core.logging_config import get_logger

log = get_logger(__name__)


class AIProviderChain:
    """Tries configured providers in order; falls through on failure."""

    def __init__(self, providers: list[LLMProvider] | None = None):
        self.providers = providers or build_provider_chain()

    def generate(self, prompt: str, system: str | None = None, **kwargs: Any) -> str:
        last: Exception | None = None
        for provider in self.providers:
            try:
                return provider.generate(prompt, system=system, **kwargs)
            except Exception as exc:  # pragma: no cover - provider dependent
                last = exc
                log.warning("Provider %s failed: %s", provider.name, exc)
        raise RuntimeError(f"All AI providers failed: {last}")

    def structured_output(self, prompt: str, system: str | None = None, **kwargs: Any) -> dict[str, Any]:
        last: Exception | None = None
        for provider in self.providers:
            try:
                return provider.structured_output(prompt, system=system, **kwargs)
            except Exception as exc:
                last = exc
                log.warning("Provider %s structured output failed: %s", provider.name, exc)
        raise RuntimeError(f"All AI providers failed: {last}")

    def classify(self, text: str, labels: list[str], system: str | None = None) -> tuple[str, float]:
        last: Exception | None = None
        for provider in self.providers:
            try:
                return provider.classify(text, labels, system=system)
            except Exception as exc:
                last = exc
                log.warning("Provider %s classify failed: %s", provider.name, exc)
        raise RuntimeError(f"All AI providers failed: {last}")

    def extract(self, text: str, schema: dict[str, Any], system: str | None = None) -> dict[str, Any]:
        last: Exception | None = None
        for provider in self.providers:
            try:
                return provider.extract(text, schema, system=system)
            except Exception as exc:
                last = exc
                log.warning("Provider %s extract failed: %s", provider.name, exc)
        raise RuntimeError(f"All AI providers failed: {last}")

    def score(self, prompt: str, system: str | None = None) -> float:
        last: Exception | None = None
        for provider in self.providers:
            try:
                return provider.score(prompt, system=system)
            except Exception as exc:
                last = exc
                log.warning("Provider %s score failed: %s", provider.name, exc)
        raise RuntimeError(f"All AI providers failed: {last}")


def build_provider_chain() -> list[LLMProvider]:
    settings = get_settings()
    order = [name.strip().lower() for name in settings.llm_providers.split(",") if name.strip()]
    providers: list[LLMProvider] = []
    for name in order:
        if name == "openai" and settings.openai_api_key:
            providers.append(OpenAIProvider(settings.openai_api_key, settings.openai_model, settings.openai_base_url))
        elif name == "anthropic" and settings.anthropic_api_key:
            providers.append(AnthropicProvider(settings.anthropic_api_key, settings.anthropic_model))
        elif name == "ollama" and settings.ollama_base_url:
            providers.append(OllamaProvider(settings.ollama_base_url, settings.ollama_model))
        elif name == "rule_based":
            providers.append(RuleBasedProvider())
    providers.append(RuleBasedProvider())
    seen: set[str] = set()
    unique: list[LLMProvider] = []
    for p in providers:
        if p.name not in seen:
            seen.add(p.name)
            unique.append(p)
    return unique


chain = AIProviderChain()