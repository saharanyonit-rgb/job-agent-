from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_agent.analytics.outcomes import OutcomeAnalyzer
from job_agent.career.plan import CareerPlanService
from job_agent.candidates.profile import CandidateProfileService
from job_agent.core.observability import ObservabilityService, runtime_status
from job_agent.database import models as m


class DailyCareerLoop:
    """Phase 14: the closed loop, run once per day.

    discover -> dedupe -> verify -> match+strategy -> prepare/submit
            -> monitor email outcomes -> analyze outcomes -> skill gaps
            -> career plan -> daily report
    """

    def __init__(self, db: Session, orchestrator: Any = None):
        self.db = db
        self.orchestrator = orchestrator
        self.obs = ObservabilityService(db)

    def run(self, candidate_id: int, dry_run: bool = False) -> dict[str, Any]:
        runtime_status.task_start("daily_career_loop")
        profile = CandidateProfileService(self.db).build(candidate_id)

        pipeline: dict[str, Any] = {}
        orchestrator = self.orchestrator
        if orchestrator is not None:
            pipeline = self._run_pipeline(orchestrator, candidate_id, dry_run)
        else:
            pipeline = {"error": "no orchestrator provided"}

        outcomes = OutcomeAnalyzer(self.db).compute(candidate_id)
        plan = CareerPlanService(self.db).generate(candidate_id)
        gaps = self._gaps(candidate_id)

        report = self._build_report(pipeline, outcomes, gaps, profile, candidate_id)

        row = m.DailyReport(candidate_id=candidate_id, report=report, generated_at=datetime.utcnow())
        self.db.add(row)
        self.db.commit()

        self._write_evidence(candidate_id, report)
        runtime_status.cycle_complete("daily_career_loop", {"report_id": row.id})
        return {"report_id": row.id, "report": report}

    def _run_pipeline(self, orchestrator: Any, candidate_id: int, dry_run: bool) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for task, fn in [
            ("discover", orchestrator.discover),
            ("dedupe", orchestrator.dedupe),
            ("verify", orchestrator.verify),
            ("match", lambda: orchestrator.match_all(candidate_id)),
            ("applications", lambda: orchestrator.prepare_and_submit_applications(candidate_id, dry_run=dry_run)),
        ]:
            try:
                results[task] = fn()
            except Exception as exc:  # one failing source never blocks the loop
                results[task] = {"error": str(exc)}
                self.obs.record_error("daily_loop", task, str(exc))
        from job_agent.email_service.outcomes import EmailOutcomeService

        try:
            results["email_outcomes"] = EmailOutcomeService(self.db).process_latest()
        except Exception as exc:
            results["email_outcomes"] = {"error": str(exc)}
        try:
            results["interviews_linked"] = self._reprocess_interviews()
        except Exception as exc:
            results["interviews_linked"] = {"error": str(exc)}
        return results

    def _reprocess_interviews(self) -> int:
        """Re-run pending interview linking/preparation for scheduled interviews."""
        count = 0
        interviews = self.db.execute(
            select(m.Interview).where(m.Interview.status == "SCHEDULED")
        ).scalars().all()
        for interview in interviews:
            if interview.application_id:
                app = self.db.get(m.Application, interview.application_id)
                if app and app.status not in ("INTERVIEW_REQUESTED", "INTERVIEW_SCHEDULED"):
                    from job_agent.applications.pipeline import ApplicationPipeline

                    try:
                        ApplicationPipeline(self.db).transition(app, "INTERVIEW_REQUESTED", source="interview", confidence=0.9)
                        count += 1
                    except Exception:
                        pass
        return count

    def _gaps(self, candidate_id: int) -> list[dict[str, Any]]:
        from job_agent.analytics.learning import SkillGapAnalyzer

        return SkillGapAnalyzer(self.db).analyze(candidate_id)

    def _build_report(
        self,
        pipeline: dict[str, Any],
        outcomes: dict[str, Any],
        gaps: list[dict[str, Any]],
        profile: Any,
        candidate_id: int,
    ) -> dict[str, Any]:
        submitted = outcomes["funnel"]["SUBMITTED"]
        interview_count = outcomes["funnel"]["INTERVIEW"]
        offers = outcomes["funnel"]["OFFER"]
        critical = [g for g in gaps if g["priority"] == "HIGH"]

        recent = self.db.execute(
            select(m.Application).where(
                m.Application.candidate_id == candidate_id,
                m.Application.status.in_(["REJECTED", "OFFER", "ACCEPTED", "EXPIRED"]),
            )
            .order_by(m.Application.updated_at.desc())
            .limit(10)
        ).scalars().all()

        submitted_today = self.db.query(m.Application).filter(
            m.Application.submitted_at >= datetime.utcnow() - timedelta(days=1)
        ).count()

        evidence = self._evidence_counts(profile)
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "summary": {
                "candidate_id": candidate_id,
                "verified_skills": evidence["verified_skills"],
                "evidence_quality": evidence["quality"],
                "submitted_all_time": submitted,
                "submitted_last_24h": submitted_today,
                "interviews": interview_count,
                "offers": offers,
                "critical_gaps": len(critical),
                "action_required": critical[:3] or [],
            },
            "pipeline": {
                "discovered": pipeline.get("discover", {}).get("created", 0),
                "verify": pipeline.get("verify", {}).get("checked", 0),
                "matched": len(pipeline.get("match", {}).get("applications", [])),
                "submitted": len(pipeline.get("applications", {}).get("submitted", [])),
                "handoffs": len(pipeline.get("applications", {}).get("handoffs", [])),
                "email_events": pipeline.get("email_outcomes", []),
            },
            "funnel": outcomes["funnel"],
            "rates": outcomes["rates"],
            "recent_outcomes": [
                {
                    "status": a.status,
                    "company": (self.db.get(m.Job, a.job_id).company if a.job_id else None),
                    "role": (self.db.get(m.Job, a.job_id).title if a.job_id else None),
                    "reason": a.rejection_reason,
                    "strategy_class": a.strategy_class,
                    "updated_at": a.updated_at.isoformat() if a.updated_at else None,
                }
                for a in recent
            ],
            "skill_gaps": gaps[:10],
            "insights": outcomes.get("patterns", []),
            "recommended_action": self._recommendation(critical, submitted_today, interview_count, evidence["evidence_ready"]),
        }

    def _evidence_counts(self, profile: Any) -> dict[str, Any]:
        verified = len(profile.verified_skill_names())
        total = len(profile.skills)
        unavailable = profile.unavailable
        missing_facts = [k for k in unavailable if k not in ("skills", "years_of_experience", "location")]
        max(0.0, min(100.0, (verified / max(total, 1)) * 100))
        return {
            "verified_skills": verified,
            "total_skills": total,
            "evidence_ready": total > 0 and not missing_facts,
            "quality": round(100.0 * verified / max(total, 1), 1),
            "unavailable": unavailable,
        }

    def _recommendation(self, critical: list[dict[str, Any]], submitted_today: int, interviews: int, evidence_ready: bool) -> str:
        if not evidence_ready:
            return "Add portfolio/project evidence so matching can trust your skills."
        if critical:
            return f"Learn {critical[0]['skill']} (critical demand gap) while continuing to apply."
        if submitted_today == 0:
            return "Apply to today's strong matches."
        if interviews == 0:
            return "Review funnel: applications are being sent but not converting; refine targets."
        return "Maintain current pipeline; prepare for scheduled interviews."

    def _write_evidence(self, candidate_id: int, report: dict[str, Any]) -> None:
        from job_agent.evidence.registry import EvidenceRegistry

        registry = EvidenceRegistry(self.db)
        registry.add(
            entity_type="candidate",
            entity_id=candidate_id,
            claim="Daily career loop completed",
            source="daily_loop",
            confidence=1.0,
            detail={"report": report["summary"]},
        )