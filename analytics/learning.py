from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_agent.candidates.profile import CandidateProfileService
from job_agent.database import models as m


class SkillGapAnalyzer:
    """Phase 8: evidence-grounded skill gaps.

    A gap = a skill demanded by the candidate's target jobs that the candidate
    has NOT evidenced. Demand is counted from verified/active jobs and from
    actual application outcomes (rejections vs interviews).
    """

    def __init__(self, db: Session):
        self.db = db

    def analyze(self, candidate_id: int) -> list[dict[str, Any]]:
        candidate = self.db.get(m.Candidate, candidate_id)
        profile = CandidateProfileService(self.db).build(candidate_id)
        known = profile.verified_skill_names() | {s.name.lower() for s in profile.skills}

        demand: dict[str, dict[str, int]] = self._demand_from_jobs(candidate_id, candidate)
        target_ids = {j.id for j in self._target_jobs(candidate_id, candidate)}

        apps = self.db.execute(
            select(m.Application).where(m.Application.candidate_id == candidate_id)
        ).scalars().all()

        # second pass: attribute outcomes per skill for target-role applications only
        for a in apps:
            job = self.db.get(m.Job, a.job_id) if a.job_id else None
            if not job or job.id not in target_ids:
                continue
            outcome = self._outcome(a)
            for s in (job.skills or []):
                key = str(s).lower()
                entry = demand.setdefault(key, {"demand": 0, "interviews": 0, "rejections": 0, "applications": 0})
                entry["applications"] += 1
                if outcome in ("REJECTED", "EXPIRED"):
                    entry["rejections"] += 1
                elif outcome in ("INTERVIEWED", "OFFER", "ACCEPTED"):
                    entry["interviews"] += 1

        gaps: list[dict[str, Any]] = []
        for name, stats in demand.items():
            if name in known:
                continue
            kebab = name
            priority = self._priority(stats)
            gaps.append(
                {
                    "skill": kebab,
                    "demand_count": stats["demand"],
                    "application_count": stats["applications"],
                    "appeared_in_rejections": stats["rejections"],
                    "appeared_in_interviews": stats["interviews"],
                    "priority": priority,
                    "status": "UNKNOWN",
                    "analysis": {
                        "demanded_by": stats["demand"],
                        "evidence_of_gap": self._why(stats),
                    },
                }
            )
        gaps.sort(key=lambda g: ("HIGH" != g["priority"], "MEDIUM" != g["priority"], g["priority"] not in ("HIGH", "MEDIUM")))
        self._persist(candidate_id, gaps)
        return gaps

    def _target_jobs(self, candidate_id: int, candidate: m.Candidate | None) -> list[m.Job]:
        preferred_roles = (candidate.preferred_roles if candidate else None) or []
        q = select(m.Job).where(
            m.Job.verification_status == "ACTIVE",
            m.Job.status.notin_(["CLOSED", "EXPIRED"]),
        )
        jobs = self.db.execute(q).scalars().all()
        if preferred_roles:
            jobs = [
                j
                for j in jobs
                if any(pr.lower() in (j.normalized_title or j.title or "").lower() for pr in preferred_roles)
            ]
        return jobs

    def _demand_from_jobs(self, candidate_id: int, candidate: m.Candidate | None) -> dict[str, dict[str, int]]:
        demand: dict[str, dict[str, int]] = {}
        for job in self._target_jobs(candidate_id, candidate):
            for s in (job.skills or []):
                key = str(s).lower()
                demand.setdefault(key, {"demand": 0, "interviews": 0, "rejections": 0, "applications": 0})["demand"] += 1
        return demand

    def _outcome(self, a: m.Application) -> str:
        if a.status == "REJECTED":
            return "REJECTED"
        if a.status in {"OFFER", "ACCEPTED"}:
            return "OFFER"
        if a.status in {"INTERVIEW_REQUESTED", "INTERVIEW_SCHEDULED", "INTERVIEW_COMPLETED"}:
            return "INTERVIEWED"
        if a.status in {"EXPIRED"}:
            return "EXPIRED"
        return "PENDING"

    def _priority(self, stats: dict[str, int]) -> str:
        if stats["rejections"] >= 2 or (stats["rejections"] >= 1 and stats["demand"] >= 3):
            return "HIGH"
        if stats["demand"] >= 2:
            return "MEDIUM"
        return "LOW"

    def _why(self, stats: dict[str, int]) -> str:
        reasons = []
        if stats["demand"]:
            reasons.append(f"demanded by {stats['demand']} target job(s)")
        if stats["rejections"]:
            reasons.append(f"appeared in {stats['rejections']} rejection(s)")
        if stats["interviews"]:
            reasons.append(f"appeared in {stats['interviews']} interview(s)")
        return "; ".join(reasons) if reasons else "not evidenced by candidate"

    def _persist(self, candidate_id: int, gaps: list[dict[str, Any]]) -> None:
        self.db.execute(m.SkillGap.__table__.delete().where(m.SkillGap.candidate_id == candidate_id))
        for g in gaps:
            self.db.add(
                m.SkillGap(
                    candidate_id=candidate_id,
                    skill=g["skill"],
                    priority=g["priority"],
                    demand_count=g["demand_count"],
                    appeared_in_rejections=g["appeared_in_rejections"],
                    appeared_in_interviews=g["appeared_in_interviews"],
                    analysis=g["analysis"],
                )
            )
        self.db.commit()