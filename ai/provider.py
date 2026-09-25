from __future__ import annotations

import abc
import json
import re
from typing import Any


class LLMProvider(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    def generate(self, prompt: str, system: str | None = None, **kwargs: Any) -> str:
        """Free-form text generation."""

    def structured_output(self, prompt: str, system: str | None = None, **kwargs: Any) -> dict[str, Any]:
        """Generate a JSON object. Providers may override for native structured output."""
        raw = self.generate(prompt, system, **kwargs)
        return parse_json_strict(raw)

    def classify(self, text: str, labels: list[str], system: str | None = None) -> tuple[str, float]:
        """Return (label, confidence)."""
        prompt = (
            "Classify the following text into exactly one of these labels: "
            + ", ".join(labels)
            + ".\nReply with the label only.\n\nText:\n" + text[:4000]
        )
        raw = self.generate(prompt, system=system or "You are a precise classifier.")
        for label in labels:
            if label.lower() in raw.lower():
                return label, 0.9
        return labels[0], 0.1

    def extract(self, text: str, schema: dict[str, Any], system: str | None = None) -> dict[str, Any]:
        """Extract structured fields from text according to a JSON schema description."""
        prompt = (
            "Extract a JSON object from the text below. Return ONLY valid JSON, no prose.\n"
            f"Expected keys: {json.dumps(schema)}\n"
            "Use null for values that are not present. Never invent information.\n\nText:\n"
            + text[:8000]
        )
        return self.structured_output(prompt, system=system or "You extract exactly what is stated. Never fabricate.")

    def score(self, prompt: str, system: str | None = None) -> float:
        raw = self.generate(prompt, system=system or "You are a precise scorer.", max_tokens=10)
        m = re.search(r"-?\d+(\.\d+)?", raw)
        if not m:
            return 0.0
        return max(0.0, min(100.0, float(m.group(0))))


def parse_json_strict(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    # Strip markdown code fences
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1)
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start : end + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Provider returned invalid JSON: {exc}") from exc


def extract_json_objects(raw: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for m in re.finditer(r"\{[^{}]*\}", raw):
        try:
            objects.append(json.loads(m.group(0)))
        except json.JSONDecodeError:
            continue
    return objects