from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_agent.database import models as m
from job_agent.core.config import get_settings

TERMINAL = {"REJECTED", "OFFER", "ACCEPTED", "WITHDRAWN", "EXPIRED"}
PROGRESSED = {
    "ACKNOWLEDGED": 1,
    "RECEIVED": 1,
    "RECRUITER_RESPONSE": 2,
    "ASSESSMENT": 3,
    "INTERVIEW_REQUESTED": 4,
    "INTERVIEW_SCHEDULED": 4,
    "INTERVIEW_COMPLETED": 5,
    "OFFER": 6,
    "ACCEPTED": 7,
}


class OutcomeAnalyzer:
    """Phase 7: honest, sample-minimum outcome statistics.

    Never reports percentages from tiny samples; below the configured minimum
    it returns INSUFFICIENT_DATA so users don't draw wrong conclusions.
    """

    def __init__(self, db: Session):
        self.db = db
        self.min_sample = max(1, get_settings().outcome_min_sample)

    def compute(self, candidate_id: int | None = None) -> dict[str, Any]:
        apps = self._applications(candidate_id)
        total = len(apps)
        submitted = [a for a in apps if a.submitted_at or a.status not in {"DISCOVERED", "VERIFIED", "MATCHED", "READY", "PREPARING"}]
        submitted = [a for a in apps if self._was_submitted(a)]

        responded = [a for a in submitted if self._response(a)]
        rejected = [a for a in submitted if a.status == "REJECTED"]
        offers = [a for a in submitted if a.status in {"OFFER", "ACCEPTED"}]
        interviews = [a for a in submitted if self._reached_interview(a)]

        n = len(submitted)
        funnel = self._funnel(submitted)

        analysis: dict[str, Any] = {
            "sample": {
                "total_applications": total,
                "submitted": n,
                "sample_adequate": n >= self.min_sample,
                "minimum_sample": self.min_sample,
            },
            "funnel": funnel,
            "rates": self._honest_rates(submitted, responded, interviews, offers, rejected, n),
        }
        if n >= self.min_sample:
            analysis["patterns"] = self._patterns(submitted, rejected, offers)
            analysis["response_time_days"] = self._response_time(submitted)
        else:
            analysis["patterns"] = []
            analysis["response_time_days"] = None
            analysis["insufficient_data"] = "Not enough submitted applications to compute reliable patterns."

        analysis["role_breakdown"] = self._role_breakdown(submitted)
        return analysis

    # --- internals ---

    def _applications(self, candidate_id: int | None) -> list[m.Application]:
        q = select(m.Application).join(m.Job).order_by(m.Application.created_at)
        if candidate_id:
            q = q.where(m.Application.candidate_id == candidate_id)
        return self.db.execute(q).scalars().all()

    def _was_submitted(self, a: m.Application) -> bool:
        return a.submitted_at is not None or a.status in {
            "SUBMITTED", "ACKNOWLEDGED", "RECEIVED", "RECRUITER_RESPONSE", "ASSESSMENT",
            "INTERVIEW_REQUESTED", "INTERVIEW_SCHEDULED", "INTERVIEW_COMPLETED", "OFFER",
            "ACCEPTED", "REJECTED", "EXPIRED", "HANDOFF_REQUIRED", "FOLLOW_UP",
        }

    def _response(self, a: m.Application) -> bool:
        for ev in a.events:
            if ev.event_type in {
                "APPLICATION_RECEIVED", "APPLICATION_ACKNOWLEDGED", "REJECTION", "OFFER",
                "INTERVIEW_INVITATION", "RECRUITER_RESPONSE", "NEXT_ROUND_INVITATION",
                "ASSESSMENT", "HIRING_FREEZE_ROLE_CLOSED",
            }:
                return True
        return a.status in {"ACKNOWLEDGED", "RECEIVED", "RECRUITER_RESPONSE", "ASSESSMENT", "OFFER", "ACCEPTED", "REJECTED", "EXPIRED"}

    def _reached_interview(self, a: m.Application) -> bool:
        return a.status in {"INTERVIEW_REQUESTED", "INTERVIEW_SCHEDULED", "INTERVIEW_COMPLETED", "OFFER", "ACCEPTED"}

    def _funnel(self, apps: list[m.Application]) -> dict[str, int]:
        counts: dict[str, int] = {"SUBMITTED": 0, "ACKNOWLEDGED": 0, "RECRUITER_RESPONSE": 0, "INTERVIEW": 0, "OFFER": 0, "ACCEPTED": 0}
        depth = {"SUBMITTED": 0, "ACKNOWLEDGED": 1, "RECEIVED": 1, "RECRUITER_RESPONSE": 2, "ASSESSMENT": 3, "INTERVIEW_REQUESTED": 4, "INTERVIEW_SCHEDULED": 4, "INTERVIEW_COMPLETED": 5, "OFFER": 6, "ACCEPTED": 7}
        for a in apps:
            d = depth.get(a.status, 0)
            counts["SUBMITTED"] += 1
            if d >= 1:
                counts["ACKNOWLEDGED"] += 1
            if d >= 2:
                counts["RECRUITER_RESPONSE"] += 1
            if d >= 4:
                counts["INTERVIEW"] += 1
            if d >= 6:
                counts["OFFER"] += 1
            if d >= 7:
                counts["ACCEPTED"] += 1
        return counts

    def _honest_rates(self, submitted: list[m.Application], responded: list[m.Application], interviews: list[m.Application], offers: list[m.Application], rejected: list[m.Application], n: int) -> dict[str, Any]:
        def rate(dividend: int) -> dict[str, Any]:
            if n < self.min_sample:
                return {"value": None, "status": "INSUFFICIENT_DATA"}
            return {"value": round(100 * dividend / n, 1), "status": "OK"}

        return {
            "response_rate": rate(len(responded)),
            "interview_rate": rate(len(interviews)),
            "offer_rate": rate(len(offers)),
            "rejection_rate": rate(len(rejected)),
        }

    def _response_time(self, apps: list[m.Application]) -> dict[str, Any] | None:
        deltas = []
        for a in apps:
            if not (a.submitted_at and a.events):
                continue
            resp = None
            for ev in a.events:
                if ev.event_type in {
                    "APPLICATION_RECEIVED", "APPLICATION_ACKNOWLEDGED", "REJECTION", "OFFER",
                    "INTERVIEW_INVITATION", "RECRUITER_RESPONSE",
                } and ev.created_at:
                    resp = ev.created_at
                    break
            if resp:
                deltas.append((resp - a.submitted_at).total_seconds() / 86400)
        if not deltas:
            return None
        return {"mean_days": round(sum(deltas) / len(deltas), 1), "median_days": round(sorted(deltas)[len(deltas) // 2], 1), "n": len(deltas)}

    def _patterns(self, apps: list[m.Application], rejected: list[m.Application], offers: list[m.Application]) -> list[dict[str, Any]]:
        """Honest, weakly-worded observations grounded in evidence."""
        patterns: list[dict[str, Any]] = []
        skills_in_rejected = self._skills_of(rejected)
        skills_in_offers = self._skills_of(offers)
        if skills_in_rejected:
            top = skills_in_rejected[0]
            patterns.append({
                "type": "skill_in_rejections",
                "observation": f"'{top[0]}' appeared as a requirement in the most rejected applications.",
                "contributing_factor": "A missing/weak skill is a possible contributing factor.",
                "confidence": "LOW" if len(rejected) < self.min_sample else "MEDIUM",
            })
        if skills_in_offers and not skills_in_rejected:
            top = skills_in_offers[0]
            patterns.append({
                "type": "skill_in_offers",
                "observation": f"'{top[0]}' was present in applications that advanced furthest.",
                "contributing_factor": None,
                "confidence": "LOW" if len(offers) < self.min_sample else "MEDIUM",
            })
        companies = {}
        for a in rejected:
            job = a.job if hasattr(a, "job") else None
            company = getattr(a, "company", None)
            key = job.company if job else company
            companies[key] = companies.get(key, 0) + 1
        repeat = [k for k, v in companies.items() if v >= 2]
        if repeat:
            patterns.append({
                "type": "repeat_rejections_per_company",
                "observation": f"Multiple rejections from {', '.join(repeat[:3])}.",
                "contributing_factor": "Possibly a company-level fit or pipeline issue; focus on role-specific fit.",
                "confidence": "MEDIUM",
            })
        return patterns

    def _skills_of(self, apps: list[m.Application]) -> list[tuple[str, int]]:
        counter: dict[str, int] = {}
        for a in apps:
            job = self.db.get(m.Job, a.job_id) if a.job_id else None
            for s in (job.skills if job else []) or []:
                key = str(s).lower()
                counter[key] = counter.get(key, 0) + 1
        return sorted(counter.items(), key=lambda kv: kv[1], reverse=True)

    def _role_breakdown(self, apps: list[m.Application]) -> list[dict[str, Any]]:
        buckets: dict[str, list[m.Application]] = {}
        for a in apps:
            job = self.db.get(m.Job, a.job_id) if a.job_id else None
            role = (job.normalized_title or job.title or "Unknown") if job else "Unknown"
            buckets.setdefault(role, []).append(a)
        out = []
        for role, rows in buckets.items():
            n = len(rows)
            rec = {"role": role, "applications": n}
            if n >= self.min_sample:
                rec["interview_rate"] = round(100 * sum(1 for r in rows if self._reached_interview(r)) / n, 1)
                rec["sample_adequate"] = True
            else:
                rec["interview_rate"] = None
                rec["sample_adequate"] = False
            out.append(rec)
        return sorted(out, key=lambda r: r["applications"], reverse=True)