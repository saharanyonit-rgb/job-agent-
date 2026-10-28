from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.applications.pipeline import ApplicationPipeline
from core.database import models as m
from core.matching.engine import MatchResult

# Final strategy classes that can be assigned to an application.
STRATEGY_CLASSES = [
    "STRONG_MATCH",
    "POSSIBLE_MATCH",
    "WEAK_MATCH",
    "HARD_REQUIREMENT_FAILURE",
    "HIGH_RISK",
    "HANDOFF_REQUIRED",
]

# Decisions produced by the strategy engine.
DECISIONS = [
    "APPLY",
    "PREPARE",
    "SKIP",
    "NEED_MORE_EVIDENCE",
    "HANDOFF_REQUIRED",
]

EFFORT_LEVELS = ["LOW", "MEDIUM", "HIGH"]


@dataclass
class ApplicationStrategyResult:
    application_id: int
    strategy_class: str
    decision: str
    reasons: list[str] = field(default_factory=list)
    risk: str = "LOW"
    effort: str = "LOW"
    advantages: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    missing_requirements: list[str] = field(default_factory=list)
    resume_customization: dict[str, Any] = field(default_factory=dict)
    cover_letter_required: bool = True
    human_approval_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "application_id": self.application_id,
            "strategy_class": self.strategy_class,
            "decision": self.decision,
            "reasons": self.reasons,
            "risk": self.risk,
            "effort": self.effort,
            "advantages": self.advantages,
            "weaknesses": self.weaknesses,
            "missing_requirements": self.missing_requirements,
            "resume_customization": self.resume_customization,
            "cover_letter_required": self.cover_letter_required,
            "human_approval_required": self.human_approval_required,
        }


# Anti-spam limits count applications actually engaged (submitted onward),
# so matched-but-unsent jobs don't inflate the per-company/role counters.
ACTIVE_APPLICATION_STATUSES = [
    "SUBMITTED", "ACKNOWLEDGED", "RECEIVED", "RECRUITER_RESPONSE", "ASSESSMENT",
    "INTERVIEW_REQUESTED", "INTERVIEW_SCHEDULED", "INTERVIEW_COMPLETED", "OFFER",
    "ACCEPTED", "FOLLOW_UP", "HANDOFF_REQUIRED", "FAILED",
]


class ApplicationStrategyEngine:
    """Phase 3: decides whether an application is worth pursuing, and how.

    The strategy is recorded on the Application (strategy_class / strategy / risk)
    and as an ApplicationStrategy row for traceability. It never replaces the
    human: anything risky or uncertain goes to HANDOFF_REQUIRED/NEED_MORE_EVIDENCE.
    """

    def __init__(self, db: Session):
        self.db = db
        self.pipeline = ApplicationPipeline(db)

    def evaluate(
        self,
        application: m.Application,
        match: MatchResult,
        job: m.Job,
        *,
        existing_for_company: int = 0,
        existing_for_role: int = 0,
    ) -> ApplicationStrategyResult:
        reasons: list[str] = []
        advantages: list[str] = []
        weaknesses: list[str] = []

        strategy_class = match.fit_classification
        if strategy_class not in STRATEGY_CLASSES:
            strategy_class = "WEAK_MATCH" if match.overall_match < 60 else "POSSIBLE_MATCH"

        risk = match.application_risk

        if match.matched_skills:
            advantages.append(f"Matched skills: {', '.join(match.matched_skills[:6])}")
        if match.hard_requirement_failures:
            weaknesses.extend(match.hard_requirement_failures)
        if match.missing_requirements:
            for req in match.missing_requirements[:5]:
                if req not in weaknesses:
                    weaknesses.append(req)

        custom_skills = sorted(set(match.matched_skills))[:8]
        resume_customization = {
            "highlight_skills": custom_skills,
            "missing_requirements": match.missing_requirements,
        }

        # Decide.
        decision = "PREPARE"
        human_approval = False
        if strategy_class == "HARD_REQUIREMENT_FAILURE":
            decision = "SKIP"
            reasons.append(strategy_class)
        elif strategy_class == "HIGH_RISK":
            decision = "NEED_MORE_EVIDENCE"
            human_approval = True
            reasons.append("High risk: unconfirmed facts should be verified by the candidate")
        elif strategy_class == "WEAK_MATCH":
            decision = "SKIP"
            reasons.append("Weak match; deferred to keep focus on strong fits")
        elif strategy_class in ("STRONG_MATCH", "POSSIBLE_MATCH"):
            reasons.append(strategy_class)
            if risk == "LOW":
                decision = "APPLY"
            else:
                decision = "NEED_MORE_EVIDENCE"
                human_approval = True

        effort = self._effort(job, match)
        if existing_for_company > 0:
            reasons.append(f"Already applied to {existing_for_company} role(s) at this company")
        if existing_for_role > 0:
            reasons.append(f"Already applied to {existing_for_role} similar role(s)")

        cover_letter_required = True
        result = ApplicationStrategyResult(
            application_id=application.id,
            strategy_class=strategy_class,
            decision=decision,
            reasons=reasons or [strategy_class],
            risk=risk,
            effort=effort,
            advantages=advantages,
            weaknesses=weaknesses,
            missing_requirements=match.missing_requirements,
            resume_customization=resume_customization,
            cover_letter_required=cover_letter_required,
            human_approval_required=human_approval,
        )
        self._persist(application, result)
        return result

    def _effort(self, job: m.Job, match: MatchResult) -> str:
        missing_skills = sum(1 for s in (match.missing_requirements or []) if "skill" in s)
        if missing_skills >= 3 or (job.screening_questions if hasattr(job, "screening_questions") else None):
            return "HIGH"
        if missing_skills >= 1:
            return "MEDIUM"
        return "LOW"

    def _persist(self, application: m.Application, result: ApplicationStrategyResult) -> None:
        application.strategy_class = result.strategy_class
        application.strategy = result.to_dict()
        application.risk_level = result.risk
        application.human_approval_required = result.human_approval_required
        row = m.ApplicationStrategy(
            application_id=application.id,
            strategy_class=result.strategy_class,
            decision=result.decision,
            reasons=result.reasons,
            risk=result.risk,
            effort=result.effort,
            advantages=result.advantages,
            weaknesses=result.weaknesses,
            missing_requirements=result.missing_requirements,
            resume_customization=result.resume_customization,
            cover_letter_required=result.cover_letter_required,
            human_approval_required=result.human_approval_required,
        )
        for existing in list(application.strategies):
            self.db.delete(existing)
        self.db.add(row)
        self.db.commit()
        self.db.refresh(application)

    # --- contextual counters used by the orchestrator ---

    def company_application_count(self, candidate_id: int, company: str, exclude_application_id: int | None = None) -> int:
        rows = self.db.execute(
            select(m.Application)
            .join(m.Job)
            .where(
                m.Application.candidate_id == candidate_id,
                m.Job.company == company,
                m.Application.status.in_(ACTIVE_APPLICATION_STATUSES),
            )
        ).all()
        return len([r for r in rows if exclude_application_id is None or r[0].id != exclude_application_id])

    def role_application_count(self, candidate_id: int, normalized_title: str, exclude_application_id: int | None = None) -> int:
        rows = self.db.execute(
            select(m.Application)
            .join(m.Job)
            .where(
                m.Application.candidate_id == candidate_id,
                m.Job.normalized_title == normalized_title,
                m.Application.status.in_(ACTIVE_APPLICATION_STATUSES),
            )
        ).all()
        return len([r for r in rows if exclude_application_id is None or r[0].id != exclude_application_id])

    def last_company_application_at(self, candidate_id: int, company: str) -> datetime | None:
        row = self.db.execute(
            select(m.Application.submitted_at)
            .join(m.Job)
            .where(m.Application.candidate_id == candidate_id, m.Job.company == company)
            .order_by(m.Application.submitted_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        return row

    def existing_application_for_job(self, candidate_id: int, job_id: int) -> m.Application | None:
        return self.db.execute(
            select(m.Application).where(
                m.Application.candidate_id == candidate_id,
                m.Application.job_id == job_id,
            )
        ).scalar_one_or_none()