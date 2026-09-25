from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

ALLOWED_SOURCES = {
    "resume",
    "candidate_confirmed",
    "application_form",
    "email",
    "calendar",
    "external_source",
    "inference",
    "unknown",
}


class FactStatus(Enum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    HANDOFF_REQUIRED = "HANDOFF_REQUIRED"


@dataclass
class ProvenanceFact:
    key: str
    value: Any
    source: str = "unknown"
    verified: bool = False
    evidence: str | None = None

    def __post_init__(self) -> None:
        if self.source not in ALLOWED_SOURCES:
            raise ValueError(f"Invalid provenance source: {self.source}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "source": self.source,
            "verified": self.verified,
            "evidence": self.evidence,
        }

    @property
    def status(self) -> FactStatus:
        if self.value is None or self.value == "":
            return FactStatus.UNKNOWN
        if self.source == "inference" and not self.verified:
            return FactStatus.HANDOFF_REQUIRED
        return FactStatus.KNOWN


@dataclass
class CandidateFactCollection:
    facts: dict[str, ProvenanceFact] = field(default_factory=dict)

    def set(
        self,
        key: str,
        value: Any,
        source: str = "unknown",
        verified: bool = False,
        evidence: str | None = None,
    ) -> ProvenanceFact:
        fact = ProvenanceFact(key, value, source, verified, evidence)
        self.facts[key] = fact
        return fact

    def get(self, key: str) -> ProvenanceFact | None:
        return self.facts.get(key)

    def value(self, key: str) -> Any:
        fact = self.get(key)
        return fact.value if fact else None

    def is_known(self, key: str) -> bool:
        fact = self.get(key)
        return bool(fact and fact.status == FactStatus.KNOWN)

    def to_dict(self) -> dict[str, Any]:
        return {k: v.to_dict() for k, v in self.facts.items()}

    def missing(self, keys: list[str]) -> list[str]:
        return [k for k in keys if not self.is_known(k)]


# Special provenance: keep two facts separate even if they look conflicting.
# e.g. resume location (Bengaluru) vs candidate preference (Remote) are distinct keys.