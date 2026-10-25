from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from job_agent.applications.pipeline import ApplicationPipeline
from job_agent.applications.policy import PolicyService
from job_agent.applications.strategy import ApplicationStrategyEngine
from job_agent.applications.validation import validate_application
from job_agent.candidates.profile import CandidateProfileService
from job_agent.candidates.service import CandidateService
from job_agent.database import models as m
from job_agent.matching.engine import MatchingEngine
from job_agent.core.logging_config import get_logger

log = get_logger(__name__)


class ApplicationService:
    def __init__(self, db: Session):
        self.db = db
        self.pipeline = ApplicationPipeline(db)
        self.policy = PolicyService(db)
        self.candidates = CandidateService(db)
        self.matcher = MatchingEngine()
        self.strategy = ApplicationStrategyEngine(db)

    def match_job(self, candidate_id: int, job: m.Job) -> tuple[m.Application | None, dict[str, Any]]:
        facts = self.candidates.get_facts(candidate_id)
        prefs = self.candidates.get_preferences(candidate_id)
        profile = CandidateProfileService(self.db).build(candidate_id)
        result = self.matcher.match(
            facts,
            prefs,
            job,
            verified_skills=profile.verified_skill_names(),
            profile_unavailable=profile.unavailable,
        )
        existing = self.db.execute(
            select(m.Application).where(
                m.Application.candidate_id == candidate_id, m.Application.job_id == job.id
            )
        ).scalar_one_or_none()
        if existing:
            application = existing
        else:
            application = m.Application(candidate_id=candidate_id, job_id=job.id, status="DISCOVERED")
            self.db.add(application)
            self.db.flush()
        application.match_score = result.overall_match
        application.match_explanation = result.to_dict()
        self.db.commit()
        self.db.refresh(application)

        if application.status in ("DISCOVERED",):
            if job.verification_status == "ACTIVE":
                self.pipeline.transition(application, "VERIFIED", {"verification": job.verification_status})
            if result.overall_match >= self.policy.get().minimum_match and job.verification_status == "ACTIVE":
                self.pipeline.transition(application, "MATCHED", {"score": result.overall_match})
                self._evaluate_strategy(application, result, job, candidate_id)
        elif application.status == "VERIFIED" and result.overall_match >= self.policy.get().minimum_match:
            self.pipeline.transition(application, "MATCHED", {"score": result.overall_match})
            self._evaluate_strategy(application, result, job, candidate_id)
        elif application.status == "MATCHED" and not application.strategy:
            self._evaluate_strategy(application, result, job, candidate_id)
        return application, result.to_dict()

    def _evaluate_strategy(self, application: m.Application, result: Any, job: m.Job, candidate_id: int) -> None:
        self.strategy.evaluate(
            application,
            result,
            job,
            existing_for_company=self.strategy.company_application_count(candidate_id, job.company),
            existing_for_role=self.strategy.role_application_count(candidate_id, job.normalized_title or job.title),
        )

    def advance_to_ready(self, application: m.Application) -> m.Application:
        if application.status == "MATCHED":
            return self.pipeline.transition(application, "READY")
        return application

    def decide(
        self,
        application: m.Application,
        handoff_reasons: list[str] | None = None,
        *,
        hard_failures: list[str] | None = None,
        evidence_confidence: float | None = None,
        company_application_count: int = 0,
        same_role_application_count: int = 0,
        last_company_application_at=None,
    ) -> dict[str, Any]:
        job = self.db.get(m.Job, application.job_id)
        assert job is not None

        # Fail-closed low-confidence budget gate BEFORE document prep/submission.
        gate = self.policy.evaluate_low_confidence_budget(
            application.candidate_id,
            exclude_application_id=application.id,
        )
        if gate["exceeded"]:
            return {
                "action": "HANDOFF_REQUIRED",
                "decision": "NEED_MORE_EVIDENCE",
                "reasons": gate["reasons"],
                "policy": self.policy.get().to_dict(),
                "budget": {"count": gate["count"], "limit": gate["limit"]},
            }

        return self.policy.decide(
            application.match_score or 0,
            job,
            handoff_reasons,
            strategy_class=application.strategy_class,
            hard_failures=hard_failures or (application.match_explanation or {}).get("hard_requirement_failures"),
            evidence_confidence=evidence_confidence,
            company_application_count=company_application_count,
            same_role_application_count=same_role_application_count,
            last_company_application_at=last_company_application_at,
            candidate_id=application.candidate_id,
            exclude_application_id=application.id,
        )

    def validate_before_submit(self, application: m.Application) -> dict[str, Any]:
        candidate = self.db.get(m.Candidate, application.candidate_id)
        job = self.db.get(m.Job, application.job_id)
        resume = self.db.get(m.Resume, application.resume_id) if application.resume_id else None
        cover = self.db.get(m.Resume, application.cover_letter_id) if application.cover_letter_id else None
        form_answers = {
            "first_name": (candidate.name or "").split()[0] if candidate and candidate.name else "",
            "last_name": (candidate.name or "").split()[-1] if candidate and len((candidate.name or "").split()) > 1 else "",
            "email": candidate.email if candidate else "",
            "phone": candidate.phone if candidate else "",
            "notice_period": candidate.notice_period if candidate else "",
            "expected_salary": candidate.expected_ctc if candidate else "",
            "job_title": job.title if job else "",
        }
        return validate_application(
            candidate={
                "name": candidate.name if candidate else None,
                "email": candidate.email if candidate else None,
            },
            job={"title": job.title if job else None, "company": job.company if job else None},
            resume_path=resume.path if resume else None,
            cover_letter_path=cover.path if cover else None,
            form_answers=form_answers,
            screening_answers=application.screening_answers or [],
        )

    def submit(self, application: m.Application, evidence: dict[str, Any], *, evidence_confidence: float | None = None) -> m.Application:
        application.submitted_at = datetime.utcnow()
        application.application_id_external = evidence.get("application_id")
        application.confirmation_evidence = evidence
        if evidence_confidence is not None:
            application.evidence_confidence = float(evidence_confidence)
        return self.pipeline.transition(application, "SUBMITTED", evidence)

    def mark_handoff(self, application: m.Application, reason: str) -> m.Application:
        application.handoff_reason = reason
        return self.pipeline.transition(application, "HANDOFF_REQUIRED", {"reason": reason})

    def counts(self, candidate_id: int | None = None) -> dict[str, int]:
        q = select(m.Application.status, func.count()).group_by(m.Application.status)
        rows = self.db.execute(q).all()
        return {status: count for status, count in rows}

    def list_applications(self, candidate_id: int | None = None, status: str | None = None) -> list[m.Application]:
        q = select(m.Application)
        if candidate_id:
            q = q.where(m.Application.candidate_id == candidate_id)
        if status:
            q = q.where(m.Application.status == status)
        q = q.order_by(m.Application.updated_at.desc())
        return list(self.db.execute(q).scalars().all())