from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_agent.database import models as m

# Lifecycle: UNKNOWN -> LEARNING -> PRACTICED -> PROJECT_EVIDENCE -> VERIFIED_SKILL
SKILL_LIFECYCLE = ["UNKNOWN", "LEARNING", "PRACTICED", "PROJECT_EVIDENCE", "VERIFIED_SKILL"]

VALID_STATUSES = set(SKILL_LIFECYCLE)

# Skills whose resume presence alone is treated as "practiced" (e.g. languages,
# frameworks mentioned in projects/skills sections). Verifiable only with project
# evidence or interviewer feedback.
_STATUS_BY_EVIDENCE = {
    "resume": "PRACTICED",
    "candidate_confirmed": "PROJECT_EVIDENCE",
    "project_evidence": "PROJECT_EVIDENCE",
    "interview_feedback": "VERIFIED_SKILL",
    "assessment": "VERIFIED_SKILL",
}


class CandidateSkillService:
    def __init__(self, db: Session):
        self.db = db

    def list(self, candidate_id: int) -> list[m.CandidateSkill]:
        return self.db.execute(
            select(m.CandidateSkill)
            .where(m.CandidateSkill.candidate_id == candidate_id)
            .order_by(m.CandidateSkill.name)
        ).scalars().all()

    def verified_names(self, candidate_id: int) -> set[str]:
        rows = self.db.execute(
            select(m.CandidateSkill.name).where(
                m.CandidateSkill.candidate_id == candidate_id,
                m.CandidateSkill.status == "VERIFIED_SKILL",
            )
        ).scalars().all()
        return set(rows)

    def known_names(self, candidate_id: int) -> set[str]:
        rows = self.db.execute(
            select(m.CandidateSkill.name).where(
                m.CandidateSkill.candidate_id == candidate_id,
                m.CandidateSkill.status.in_(SKILL_LIFECYCLE),
            )
        ).scalars().all()
        return set(rows)

    def get(self, candidate_id: int, name: str) -> m.CandidateSkill | None:
        return self.db.execute(
            select(m.CandidateSkill).where(
                m.CandidateSkill.candidate_id == candidate_id,
                m.CandidateSkill.name == name,
            )
        ).scalar_one_or_none()

    def upsert(
        self,
        candidate_id: int,
        name: str,
        status: str | None = None,
        proficiency: float | None = None,
        source: str = "system",
        evidence: str | None = None,
        verified: bool | None = None,
    ) -> m.CandidateSkill:
        if status is not None and status not in VALID_STATUSES:
            raise ValueError(f"Invalid skill status: {status}")
        row = self.get(candidate_id, name)
        if row is None:
            effective_status = status or _infer_status(source)
            row = m.CandidateSkill(
                candidate_id=candidate_id,
                name=name,
                status=effective_status,
                proficiency=proficiency,
                source=source,
                evidence=evidence,
                verified=bool(verified or effective_status == "VERIFIED_SKILL"),
            )
            self.db.add(row)
            self.db.flush()
            return row
        if status is not None:
            row.status = status
        if proficiency is not None:
            row.proficiency = proficiency
        if source:
            row.source = source
        if evidence:
            row.evidence = evidence
        if verified is not None:
            row.verified = verified
        elif row.status == "VERIFIED_SKILL":
            row.verified = True
        self.db.commit()
        self.db.refresh(row)
        return row

    def set_status(self, candidate_id: int, name: str, status: str, evidence: str | None = None) -> m.CandidateSkill:
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid skill status: {status}")
        return self.upsert(candidate_id, name, status=status, source="agent", evidence=evidence, verified=status == "VERIFIED_SKILL")

    def advance(self, candidate_id: int, name: str, evidence: str | None = None) -> m.CandidateSkill:
        """Move a skill one step forward in the lifecycle."""
        row = self.get(candidate_id, name)
        if row is None:
            return self.upsert(candidate_id, name, status="LEARNING", source="agent", evidence=evidence)
        idx = SKILL_LIFECYCLE.index(row.status)
        if idx < len(SKILL_LIFECYCLE) - 1:
            return self.set_status(candidate_id, name, SKILL_LIFECYCLE[idx + 1], evidence=evidence)
        return row

    def sync_from_facts(self, candidate_id: int, skills: list[Any], source: str, evidence: str | None = None) -> int:
        """Reconcile the candidate_skills table from a facts['skills'] payload.

        Returns the number of skills upserted/updated.
        """
        count = 0
        for raw in skills:
            name, status = _parse_skill_entry(raw)
            if status == "UNKNOWN":
                status = _infer_status(source)
            promoted = self.upsert(candidate_id, name, status=status, source=source, evidence=evidence or name)
            count += 1
            if promoted.proficiency is None:
                pass
        self.db.commit()
        return count

    def upsert_by_fact(self, candidate_id: int, fact: m.CandidateFact) -> None:
        """Derive candidate_skills rows from a 'skills' CandidateFact row."""
        import json

        try:
            values = json.loads(fact.value) if fact.value else []
        except (json.JSONDecodeError, TypeError):
            values = []
        if not isinstance(values, list):
            return
        self.sync_from_facts(candidate_id, values, fact.source, fact.evidence)


def _parse_skill_entry(raw: Any) -> tuple[str, str]:
    """Accepts either a bare name or {'name': ..., 'proficiency': ...}."""
    if isinstance(raw, dict):
        name = raw.get("name") or ""
        proficiency = raw.get("proficiency")
        status = raw.get("status")
        if isinstance(status, str) and status in VALID_STATUSES:
            return name, status
        if isinstance(proficiency, (int, float)) and proficiency >= 0.8:
            return name, "PROJECT_EVIDENCE"
        return name, "UNKNOWN"
    return str(raw), "UNKNOWN"


def _infer_status(source: str) -> str:
    return _STATUS_BY_EVIDENCE.get(source, "UNKNOWN")