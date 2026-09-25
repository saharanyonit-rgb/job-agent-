from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from job_agent.candidates.models import CandidateFactCollection
from job_agent.candidates.service import CandidateService
from job_agent.database import models as m
from job_agent.discovery.service import DiscoveryService
from job_agent.jobs.service import JobService
from job_agent.matching.engine import MatchingEngine
from job_agent.applications.service import ApplicationService
from job_agent.core.observability import ObservabilityService, runtime_status
from job_agent.core.logging_config import get_logger
from job_agent.core.errors import HandoffRequiredError

log = get_logger(__name__)


class JobAgentOrchestrator:
    """Central orchestrator wiring agents into the end-to-end pipeline."""

    def __init__(self, db: Session):
        self.db = db
        self.candidates = CandidateService(db)
        self.jobs = JobService(db)
        self.discovery = DiscoveryService(db, self.jobs)
        self.applications = ApplicationService(db)
        self.matcher = MatchingEngine()
        self.obs = ObservabilityService(db)

    # --- pipeline stages ---

    def discover(self) -> dict[str, Any]:
        runtime_status.task_start("discover")
        result = self.discovery.run_discovery()
        runtime_status.cycle_complete("discover", result)
        return result

    def verify(self, limit: int | None = None) -> dict[str, Any]:
        runtime_status.task_start("verify")
        jobs = self.jobs.jobs_to_verify() if limit is None else self.jobs.jobs_to_verify()[:limit]
        states = {"ACTIVE": 0, "REMOVED": 0, "EXPIRED": 0, "UNKNOWN": 0, "REJECTED": 0}
        for job in jobs:
            if job.verification_status == "ACTIVE":
                continue
            res = self.jobs.verify_job(job)
            states[res.status] = states.get(res.status, 0) + 1
        result = {"checked": len(jobs), "states": states}
        runtime_status.cycle_complete("verify", result)
        return result

    def dedupe(self) -> dict[str, Any]:
        runtime_status.task_start("dedupe")
        canonical = self.jobs.dedupe()
        result = {"canonical_jobs": len(canonical)}
        runtime_status.cycle_complete("dedupe", result)
        return result

    def match_all(self, candidate_id: int) -> dict[str, Any]:
        runtime_status.task_start("match_all")
        jobs = self.jobs.eligible_jobs()
        matched = 0
        results: list[dict[str, Any]] = []
        for job in jobs:
            app, score = self.applications.match_job(candidate_id, job)
            matched += 1
            results.append({"job_id": job.id, "title": job.title, "company": job.company, "score": app.match_score, "status": app.status})
        outcome = {"evaluated": matched, "applications": results}
        runtime_status.cycle_complete("match_all", outcome)
        return outcome

    def prepare_documents(self, app: m.Application, facts: CandidateFactCollection, ai: Any = None) -> dict[str, Any]:
        from job_agent.documents.generator import DocumentGenerator

        from job_agent.ai.service import chain

        gen = DocumentGenerator(ai=ai or chain)
        job = self.db.get(m.Job, app.job_id)
        original = self.candidates.latest_resume(app.candidate_id)
        assert job is not None
        assert original is not None
        payload = gen.tailor_resume(facts, job, original)
        resume_version = self.candidates.create_version(
            app.candidate_id,
            original,
            payload,
            name=f"resume_{job.company.lower()}_{job.normalized_title[:12]}",
        )
        cover_letter = gen.cover_letter(facts, job)
        app.resume_id = resume_version.id
        app.cover_letter_id = resume_version.id
        self.db.commit()
        return {
            "resume_version": resume_version.filename,
            "resume_id": resume_version.id,
            "cover_letter": cover_letter,
        }

    def prepare_and_submit_applications(self, candidate_id: int, dry_run: bool = False) -> dict[str, Any]:
        """Match, prepare documents, validate, and submit per policy. This is the
        vertical slice behind AU'TONOMOUS and PREPARE_AND_APPROVE modes."""
        from job_agent.browser.manager import HttpFormBackend
        from job_agent.browser.service import ApplicationSubmitter
        from job_agent.browser.mapper import SENSITIVE_FIELDS

        self.match_all(candidate_id)
        facts = self.candidates.get_facts(candidate_id)
        prefs = self.candidates.get_preferences(candidate_id)
        facts_dict = dict(facts.facts)

        apps = self.applications.list_applications(candidate_id=candidate_id)
        ready = [a for a in apps if a.status == "MATCHED"]
        ready = sorted(ready, key=lambda a: (a.match_score or 0), reverse=True)

        summary = {
            "evaluated": len(apps),
            "matched": len(ready),
            "submitted": [],
            "handoffs": [],
            "failures": [],
            "skipped": [],
        }

        for app in ready:
            job = self.db.get(m.Job, app.job_id)
            assert job is not None

            # Phase 3/16: strategy gate before any document work.
            strategy = app.strategy or {}
            decision = strategy.get("decision")
            strat_class = strategy.get("strategy_class") or app.strategy_class or "POSSIBLE_MATCH"
            if decision == "SKIP":
                self.applications.pipeline.record_event(
                    app,
                    "STRATEGY_SKIP",
                    source="strategy",
                    evidence="; ".join(strategy.get("reasons", [])),
                    confidence=0.9,
                )
                summary["skipped"].append({"job_id": job.id, "reason": "strategy: " + strat_class})
                continue
            if decision in ("NEED_MORE_EVIDENCE", "HANDOFF_REQUIRED"):
                self.applications.mark_handoff(app, f"strategy: {strat_class} requires human review")
                summary["handoffs"].append({"job_id": job.id, "reasons": [strat_class]})
                continue

            # Anti-spam/duplicate gates (Phase 16).
            strategy_engine = self.applications.strategy
            company_count = strategy_engine.company_application_count(candidate_id, job.company, exclude_application_id=app.id)
            role_count = strategy_engine.role_application_count(candidate_id, job.normalized_title or job.title, exclude_application_id=app.id)
            hard_failures = (app.match_explanation or {}).get("hard_requirement_failures") or []
            if hard_failures:
                summary["skipped"].append({"job_id": job.id, "reason": f"hard requirement failures: {hard_failures[:3]}"})
                self.applications.pipeline.record_event(app, "HARD_FAILURE_SKIP", source="strategy", evidence="; ".join(hard_failures[:5]), confidence=1.0)
                continue

            # Fail-closed low-confidence application budget gate.
            # Checked BEFORE any document preparation or submission work.
            budget_gate = self.applications.policy.evaluate_low_confidence_budget(
                candidate_id,
                exclude_application_id=app.id,
            )
            if budget_gate["exceeded"]:
                self.applications.mark_handoff(app, "; ".join(budget_gate["reasons"]))
                self.applications.pipeline.record_event(
                    app,
                    "LOW_CONFIDENCE_BUDGET_HANDOFF",
                    source="policy",
                    evidence="; ".join(budget_gate["reasons"]),
                    confidence=0.0,
                    detail={"budget": {"count": budget_gate["count"], "limit": budget_gate["limit"]}},
                )
                summary["handoffs"].append(
                    {"job_id": job.id, "reasons": budget_gate["reasons"], "decision": "NEED_MORE_EVIDENCE"}
                )
                continue

            try:
                self.applications.advance_to_ready(app)
                self.applications.pipeline.transition(app, "PREPARING", {})
                docs = self.prepare_documents(app, facts)
                resume = self.db.get(m.Resume, docs["resume_id"])
                self.applications.pipeline.transition(app, "APPLYING", {})
            except Exception as exc:
                log.error("Preparation failed for job %s: %s", job.id, exc)
                summary["failures"].append({"job_id": job.id, "step": "prepare", "error": str(exc)})
                try:
                    self.applications.mark_handoff(app, f"prepare_failed: {exc}")
                except Exception:
                    pass
                continue

            # Pre-submission validation
            validation = self.applications.validate_before_submit(app)
            if not validation["passed"]:
                self.applications.mark_handoff(app, "validation_failed: " + "; ".join(validation["errors"]))
                summary["failures"].append({"job_id": job.id, "step": "validation", "errors": validation["errors"]})
                continue

            # Policy decision (now includes strategy class + hard failures + limits)
            decision = self.applications.decide(
                app,
                hard_failures=hard_failures,
                company_application_count=company_count,
                same_role_application_count=role_count,
                last_company_application_at=strategy_engine.last_company_application_at(candidate_id, job.company),
            )
            if decision["action"] in ("SKIP", "DISCOVERY_ONLY", "HANDOFF_REQUIRED"):
                self.applications.mark_handoff(app, "; ".join(decision["reasons"]))
                summary["handoffs"].append({"job_id": job.id, "reasons": decision["reasons"]})
                continue

            if decision["action"] == "READY_FOR_SUBMISSION":
                self.applications.pipeline.transition(app, "READY", decision)
                summary["handoffs"].append({"job_id": job.id, "reasons": ["awaiting approval"]})
                continue

            # SUBMIT path
            if dry_run:
                summary["submitted"].append({"job_id": job.id, "dry_run": True})
                continue

            submitter = ApplicationSubmitter(backend_kind="http")
            outcome = submitter.apply(
                application_url=job.application_url or job.url or "",
                facts={k: f.value for k, f in facts_dict.items()},
                files={"resume": resume.path if resume else ""},
            )

            if outcome.handoff_required:
                self.applications.mark_handoff(app, outcome.handoff_reason)
                self.obs.record_error("browser", "handoff", outcome.handoff_reason, {"job_id": job.id})
                summary["handoffs"].append({"job_id": job.id, "reason": outcome.handoff_reason})
                continue

            if not outcome.has_evidence:
                self.applications.pipeline.transition(app, "FAILED", {"reason": "no confirmation evidence"})
                summary["failures"].append({"job_id": job.id, "step": "submit", "reason": "no confirmation evidence"})
                continue

            self.applications.submit(app, outcome.evidencing | {"application_id": outcome.application_id})
            summary["submitted"].append(
                {
                    "job_id": job.id,
                    "job": job.title,
                    "company": job.company,
                    "application_id": outcome.application_id,
                    "evidence": bool(outcome.evidencing),
                }
            )

        return summary

    def cycle(self, candidate_id: int, dry_run: bool = False) -> dict[str, Any]:
        """One full autonomous loop iteration. One failing source never blocks the rest."""
        results = {}
        for task, fn in [
            ("discover", self.discover),
            ("dedupe", self.dedupe),
            ("verify", self.verify),
        ]:
            try:
                results[task] = fn()
            except Exception as exc:
                log.error("cycle stage %s failed: %s", task, exc)
                self.obs.record_error("orchestrator", task, str(exc))
                results[task] = {"error": str(exc)}
        results["match"] = self.match_all(candidate_id)
        results["applications"] = self.prepare_and_submit_applications(candidate_id, dry_run=dry_run)
        return results

    def run_daily_career_loop(self, candidate_id: int, dry_run: bool = False) -> dict[str, Any]:
        """Phase 14: the full closed loop with outcome analysis, skill gaps,
        career plan and a persisted daily report."""
        from job_agent.career.daily import DailyCareerLoop

        return DailyCareerLoop(self.db, orchestrator=self).run(candidate_id, dry_run=dry_run)