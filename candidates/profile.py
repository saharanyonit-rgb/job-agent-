from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.candidates.models import CandidateFactCollection, FactStatus
from core.candidates.service import CandidateService
from core.candidates.skills import CandidateSkillService
from core.database import models as m


@dataclass
class SkillProfile:
    name: str
    status: str
    proficiency: float | None = None
    verified: bool = False
    source: str = "unknown"
    evidence: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "proficiency": self.proficiency,
            "verified": self.verified,
            "source": self.source,
            "evidence": self.evidence,
        }


@dataclass
class CandidateProfile:
    candidate_id: int
    facts: CandidateFactCollection
    skills: list[SkillProfile] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)

    def skill_names(self, minimum: str = "LEARNING") -> set[str]:
        order = {"UNKNOWN": 0, "LEARNING": 1, "PRACTICED": 2, "PROJECT_EVIDENCE": 3, "VERIFIED_SKILL": 4}
        floor = order[minimum]
        return {s.name for s in self.skills if order.get(s.status, 0) >= floor}

    def verified_skill_names(self) -> set[str]:
        return {s.name for s in self.skills if s.verified}

    def everything_known(self) -> bool:
        return not self.unavailable

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "facts": self.facts.to_dict(),
            "skills": [s.to_dict() for s in self.skills],
            "unavailable": self.unavailable,
        }


def _value(coll: CandidateFactCollection, key: str) -> Any:
    f = coll.get(key)
    return f.value if f and f.status == FactStatus.KNOWN else None


class CandidateProfileService:
    """Builds the evidence-grounded candidate intelligence profile (Phase 1).

    Never invents facts: anything not known is reported as unavailable so
    downstream engines can return INSUFFICIENT_DATA instead of guessing.
    """

    def __init__(self, db: Session):
        self.db = db

    def build(self, candidate_id: int) -> CandidateProfile:
        fact_coll = CandidateService(self.db).get_facts(candidate_id)
        profile = CandidateProfile(candidate_id=candidate_id, facts=fact_coll)

        skill_rows = CandidateSkillService(self.db).list(candidate_id)
        for row in skill_rows:
            profile.skills.append(
                SkillProfile(
                    name=row.name,
                    status=row.status,
                    proficiency=row.proficiency,
                    verified=row.verified,
                    source=row.source,
                    evidence=row.evidence,
                )
            )

        required_keys = [
            "location", "work_authorization", "years_of_experience",
            "current_title", "current_ctc", "expected_ctc", "employment_type",
        ]
        for key in required_keys:
            if _value(fact_coll, key) is None:
                profile.unavailable.append(key)
        if not profile.skill_names():
            profile.unavailable.append("skills")

        # Reflect structured fields that are not facts onto the candidate row.
        self._sync_structured_fields(candidate_id, fact_coll)
        return profile

    def _sync_structured_fields(self, candidate_id: int, coll: CandidateFactCollection) -> None:
        candidate = self.db.get(m.Candidate, candidate_id)
        if candidate is None:
            return
        mapping = {
            "languages": "languages",
            "preferred_industries": "preferred_industries",
            "excluded_roles": "excluded_roles",
            "portfolio_links": "portfolio_links",
            "achievements": "achievements",
            "preferred_roles": "preferred_roles",
            "availability": "availability",
        }
        changed = False
        for key, attr in mapping.items():
            val = _value(coll, key)
            if val is not None:
                setattr(candidate, attr, val if isinstance(val, list) else [val])
                changed = True
        if changed:
            self.db.commit()