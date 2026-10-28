from __future__ import annotations

import re
from typing import Any

from core.ai.provider import LLMProvider

_SKILL_TOKENS = [
    "python", "java", "javascript", "typescript", "c#", "csharp", ".net", "go", "rust",
    "sql", "postgres", "mysql", "mongodb", "redis", "kafka", "azure", "aws", "gcp",
    "kubernetes", "k8s", "docker", "terraform", "ci/cd", "gitlab", "jenkins",
    "devops", "sre", "microservices", "event-driven", "rest", "graphql", "grpc",
    "react", "angular", "vue", "node.js", "next.js", "fastapi", "django", "flask",
    "spring", "hibernate", "llm", "rag", "genai", "chatgpt", "langchain", "pytorch",
    "tensorflow", "mlops", "airflow", "spark", "hadoop", "dbt",
    "architect", "architecture", "solution design", "technical leadership",
    "analytics", "tableau", "power bi", "excel", "etl", "data modeling",
    "security", "oauth", "jwt", "saml", "encryption", "series 7", "cfa",
    "investor relations", "fund accounting", "fundraising", "equity research",
    "private equity", "venture capital", "ibd", "wealth management",
]


class RuleBasedProvider(LLMProvider):
    """Deterministic, dependency-free provider used when no API keys are set.

    It performs regex/normalization-based structured output and scoring so the
    full system works offline while keeping output honest (never invented).
    """

    name = "rule_based"

    def generate(self, prompt: str, system: str | None = None, **kwargs: Any) -> str:
        return self._answer(prompt)

    def structured_output(self, prompt: str, system: str | None = None, **kwargs: Any) -> dict[str, Any]:
        return self._extract_structured(prompt)

    def classify(self, text: str, labels: list[str], system: str | None = None) -> tuple[str, float]:
        lowered = text.lower()
        for label in labels:
            for keyword in _CLASSIFIER_KEYWORDS.get(label, []):
                if keyword in lowered:
                    return label, 0.85
        return labels[-1], 0.3

    def extract(self, text: str, schema: dict[str, Any], system: str | None = None) -> dict[str, Any]:
        return self._extract_fields(text, list(schema))

    def score(self, prompt: str, system: str | None = None) -> float:
        return self._extract_score(prompt)

    # --- helpers ---

    def _answer(self, prompt: str) -> str:
        return "Rule-based provider: deterministic response."

    def _extract_structured(self, prompt: str) -> dict[str, Any]:
        # Look for JSON objects in the prompt (commonly present in extraction tasks)
        text = prompt
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            import json

            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
        return {}

    def _extract_fields(self, text: str, keys: list[str]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        lowered = text.lower()
        for key in keys:
            lower_key = key.lower()
            if lower_key in ("skills", "technologies"):
                out[key] = [s for s in _SKILL_TOKENS if re.search(rf"\b{re.escape(s)}\b", lowered)]
            elif lower_key in ("years_of_experience", "experience"):
                m = re.search(r"(\d{1,2})(?:\+)?\s*(?:years?|yrs)", lowered)
                out[key] = float(m.group(1)) if m else None
            elif lower_key == "name":
                for line in text.splitlines():
                    line = line.strip()
                    if line and not any(t in lowered for t in ["summary", "experience", "skills"]):
                        out[key] = line
                        break
            elif lower_key == "email":
                m = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
                out[key] = m.group(0) if m else None
            elif lower_key == "phone":
                m = re.search(r"(?:\+?\d[\d\s().-]{8,}\d)", text)
                out[key] = m.group(0).strip() if m else None
            elif lower_key == "location":
                m = re.search(r"(?:located? in|based in|resident of)\s+([A-Za-z ,-]+)", text, re.I)
                out[key] = m.group(1).strip() if m else None
        return out

    def _extract_score(self, prompt: str) -> float:
        # If the prompt contains an explicit score, surface it. Otherwise derive from tokens.
        import json

        m = re.search(r"overall[_ ]?match[:\s]+(\d{1,3})", prompt, re.I)
        if m:
            return float(max(0, min(100, int(m.group(1)))))
        start = prompt.find("{")
        end = prompt.rfind("}")
        if start != -1 and end != -1:
            try:
                data = json.loads(prompt[start : end + 1])
                if "overall_match" in data:
                    return float(data["overall_match"])
            except json.JSONDecodeError:
                pass
        return 0.0


_CLASSIFIER_KEYWORDS: dict[str, list[str]] = {
    "APPLICATION_RECEIVED": [
        "received your application", "application received", "thank you for applying",
        "we received your resume", "application has been received",
    ],
    "APPLICATION_ACKNOWLEDGED": [
        "acknowledge your application", "acknowledgement of your application",
        "confirmation of your application", "application confirmation", "we acknowledge",
    ],
    "REJECTION": [
        "we regret to inform", "unfortunately", "won't be moving forward",
        "will not be moving forward", "position has been filled", "not selected",
    ],
    "INTERVIEW_INVITATION": [
        "interview", "schedule a time to meet", "would like to invite", "invite you to",
        "zoom", "google meet", "meet.google", "msteams", "calendly",
    ],
    "INTERVIEW_RESCHEDULE": [
        "reschedule", "rescheduling", "move our", "time change",
    ],
    "INTERVIEW_CANCELLED": [
        "cancel", "cancelled", "canceled",
    ],
    "ASSESSMENT": [
        "assessment", "take-home", "hackerrank", "test", "coding challenge", "assignment",
    ],
    "RECRUITER_RESPONSE": [
        "questions about your application", "would like to learn more", "quick call",
        "let's talk about your application", "wanted to reach out",
    ],
    "NEXT_ROUND_INVITATION": [
        "next round", "second round", "final round", "advance to the next",
        "moving forward to the next", "successful in moving forward",
    ],
    "HIRING_FREEZE_ROLE_CLOSED": [
        "hiring freeze", "role has been closed", "role is now closed",
        "position on hold", "position is no longer available", "frozen",
    ],
    "SUSPICIOUS_EMAIL": [
        "verify your account", "confirm your bank", "wire transfer", "urgent security alert",
        "update your payment details",
    ],
    "OFFER": [
        "offer", "we are pleased", "congratulations", "salary package",
    ],
    "FOLLOW_UP": [
        "reminder", "checking in", "following up",
    ],
}