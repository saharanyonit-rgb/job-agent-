from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.analytics.learning import SkillGapAnalyzer
from core.candidates.profile import CandidateProfileService
from core.database import models as m


class CareerPlanService:
    """Phase 9: a career improvement plan driven by verified skills and
    evidence-grounded gaps. Follows LEARN -> BUILD -> PROVE -> APPLY."""

    def __init__(self, db: Session):
        self.db = db

    def generate(self, candidate_id: int) -> dict[str, Any]:
        profile = CandidateProfileService(self.db).build(candidate_id)
        gaps = SkillsService(self.db).gaps(candidate_id)
        verified = profile.verified_skill_names()
        known = {s.name.lower() for s in profile.skills}

        strengths = sorted(profile.skill_names(minimum="PRACTICED"))[:10]
        critical = [g for g in gaps if g["priority"] == "HIGH"]
        moderate = [g for g in gaps if g["priority"] == "MEDIUM"]
        low = [g for g in gaps if g["priority"] == "LOW"]

        plan: dict[str, Any] = {
            "generated_on": date.today().isoformat(),
            "target_roles": profile.facts.value("preferred_roles") or [],
            "strengths": strengths,
            "gaps": gaps,
            "priority_areas": [g["skill"] for g in critical + moderate],
            "actions": self._actions(critical, moderate, verified, known),
            "weekly_plan": {
                "week_1": self._week_actions(critical, "week_1"),
                "week_2": self._week_actions(critical + moderate, "week_2"),
                "week_3": self._week_actions(critical + moderate, "week_3"),
                "week_4": self._week_actions(critical + moderate + low, "week_4"),
            },
            "apply_now_focus": self._apply_focus(critical),
        }
        self._persist(candidate_id, plan)
        self._sync_learning_goals(candidate_id, critical + moderate)
        return plan

    def latest(self, candidate_id: int) -> m.CareerPlan | None:
        return self.db.execute(
            select(m.CareerPlan)
            .where(m.CareerPlan.candidate_id == candidate_id)
            .order_by(m.CareerPlan.generated_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    def last_plan(self, candidate_id: int) -> m.CareerPlan | None:
        return self.latest(candidate_id)

    def last_gaps(self, candidate_id: int) -> list[dict[str, Any]]:
        return [g for g in SkillsService(self.db).gaps(candidate_id)]

    def learning_goals(self, candidate_id: int) -> list[m.LearningGoal]:
        return self.db.execute(
            select(m.LearningGoal)
            .where(m.LearningGoal.candidate_id == candidate_id)
            .order_by(
                m.LearningGoal.priority.desc(),
                m.LearningGoal.created_at.desc(),
            )
        ).scalars().all()

    def _actions(
        self,
        critical: list[dict[str, Any]],
        moderate: list[dict[str, Any]],
        verified: set[str],
        known: set[str],
    ) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        for gap in critical + moderate:
            skill = gap["skill"]
            actions.append(
                {
                    "skill": skill,
                    "stage": "LEARN",
                    "step": f"Learn the fundamentals of {skill} (documented course/tutorial).",
                }
            )
            actions.append(
                {
                    "skill": skill,
                    "stage": "BUILD",
                    "step": f"Build a small project using {skill} and log it under has_projects / portfolio.",
                }
            )
            actions.append(
                {
                    "skill": skill,
                    "stage": "PROVE",
                    "step": f"Add evidence for {skill} (project link, home-lab, or interview feedback) to verify it.",
                }
            )
            actions.append(
                {
                    "skill": skill,
                    "stage": "APPLY",
                    "step": f"Re-run matching for roles requiring {skill} once it is verified.",
                }
            )
        return actions[:12]

    def _week_actions(self, gaps: list[dict[str, Any]], week: str) -> list[str]:
        if not gaps:
            return ["Focus on applying for your strong matches and collecting responses."]
        if week == "week_1":
            return [f"LEARN: foundational course/tutorial on {g['skill']}" for g in gaps[:3]]
        if week == "week_2":
            return [f"BUILD: prototype using {g['skill']}" for g in gaps[:3]]
        if week == "week_3":
            return [f"PROVE: capture verified evidence for {g['skill']}" for g in gaps[:2]]
        return [f"APPLY: target roles that reward verified {g['skill']}" for g in gaps[:2]]

    def _apply_focus(self, critical: list[dict[str, Any]]) -> str:
        if not critical:
            return "Keep applying to strong matches while you maintain current applications."
        return f"While learning {', '.join(g['skill'] for g in critical[:2])}, keep applying to roles emphasizing your verified strengths."

    def _persist(self, candidate_id: int, plan: dict[str, Any]) -> None:
        self._replace(candidate_id, m.CareerPlan, {"plan": plan})
        self.db.add(m.CareerPlan(candidate_id=candidate_id, period="CURRENT", plan=plan))
        self.db.commit()

    def _sync_learning_goals(self, candidate_id: int, gaps: list[dict[str, Any]]) -> None:
        for gap in gaps:
            existing = self.db.execute(
                select(m.LearningGoal).where(
                    m.LearningGoal.candidate_id == candidate_id,
                    m.LearningGoal.skill == gap["skill"],
                )
            ).scalar_one_or_none()
            if existing is None:
                self.db.add(
                    m.LearningGoal(
                        candidate_id=candidate_id,
                        skill=gap["skill"],
                        priority=gap["priority"],
                        status="UNKNOWN",
                        plan={
                            "stage": "LEARN",
                            "why": gap["analysis"]["evidence_of_gap"],
                        },
                    )
                )
            else:
                existing.priority = gap["priority"]
        self.db.commit()

    def _replace(self, candidate_id: int, model: type, fields: dict[str, Any]) -> None:
        rows = self.db.query(model).filter(model.candidate_id == candidate_id).all()
        for row in rows:
            self.db.delete(row)
        self.db.flush()


class SkillsService:
    """Thin wrapper exposing skill gaps for this module and the API."""

    def __init__(self, db: Session):
        self.db = db

    def gaps(self, candidate_id: int) -> list[dict[str, Any]]:
        return SkillGapAnalyzer(self.db).analyze(candidate_id)