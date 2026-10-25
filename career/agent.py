from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from job_agent.analytics.outcomes import OutcomeAnalyzer
from job_agent.career.plan import CareerPlanService
from job_agent.candidates.profile import CandidateProfileService
from job_agent.database import models as m

_QUESTION_CATALOG: list[dict[str, Any]] = [
    {"question_id": "what_should_i_do_today", "prompt": "What should I do today?", "fallback": False},
    {"question_id": "why_no_interviews", "prompt": "Why am I not getting interviews?", "fallback": False},
    {"question_id": "what_skills_to_learn", "prompt": "What skills should I learn?", "fallback": False},
    {"question_id": "am_i_sending_enough_applications", "prompt": "Am I sending enough applications?", "fallback": False},
    {"question_id": "how_is_my_funnel", "prompt": "How is my application funnel looking?", "fallback": False},
    {"question_id": "why_am_i_getting_rejected", "prompt": "Why am I getting rejected?", "fallback": False},
    {"question_id": "what_improvement_plan", "prompt": "What is my improvement plan?", "fallback": False},
    {"question_id": "what_is_my_career_health", "prompt": "What is my career health?", "fallback": False},
    {"question_id": "should_i_apply_to_this_job", "prompt": "Should I apply to this job?", "fallback": False},
    {"question_id": "which_companies_to_prioritize", "prompt": "Which companies should I prioritize?", "fallback": False},
    {"question_id": "am_i_ready_for_interviews", "prompt": "Am I ready for interviews?", "fallback": False},
    {"question_id": "what_evidence_do_i_have", "prompt": "What evidence do I have?", "fallback": False},
    {"question_id": "summary", "prompt": "Give me a career summary.", "fallback": True},
]

_CATALOG_BY_ID: dict[str, dict[str, Any]] = {e["question_id"]: e for e in _QUESTION_CATALOG}


class CareerAgent:
    """Phase 13: grounded Q&A about the candidate's career.

    Answers only from stored evidence. Every answer carries `status`:
      KNOWN               - answered from data
      INSUFFICIENT_DATA   - too little data to answer safely
    """

    def __init__(self, db: Session):
        self.db = db
        self.outcomes = OutcomeAnalyzer(db)
        self.plan_svc = CareerPlanService(db)

    @staticmethod
    def catalog() -> list[dict[str, Any]]:
        """Grounded Q&A catalog. 'summary' is a fallback agent, flagged as such."""
        return [dict(entry) for entry in _QUESTION_CATALOG]

    @staticmethod
    def question_ids() -> list[str]:
        return [entry["question_id"] for entry in _QUESTION_CATALOG]

    def ask(self, question_id: str | None = None, question: str | None = None) -> dict[str, Any]:
        qid = question_id or self._guess_question(question or "")
        handlers = {
            "what_should_i_do_today": self._what_should_i_do_today,
            "why_no_interviews": self._why_no_interviews,
            "what_skills_to_learn": self._what_skills_to_learn,
            "am_i_sending_enough_applications": self._am_i_sending_enough,
            "how_is_my_funnel": self._how_is_my_funnel,
            "why_am_i_getting_rejected": self._why_am_i_getting_rejected,
            "what_improvement_plan": self._what_improvement_plan,
            "what_is_my_career_health": self._what_is_my_career_health,
            "should_i_apply_to_this_job": self._should_i_apply_to_this_job,
            "which_companies_to_prioritize": self._which_companies_to_prioritize,
            "am_i_ready_for_interviews": self._am_i_ready_for_interviews,
            "what_evidence_do_i_have": self._what_evidence,
            "summary": self._summary,
        }
        handler = handlers.get(qid, handlers["summary"])
        answer = handler()
        answer["question_id"] = qid
        return answer

    # --- answers ---

    def _summary(self) -> dict[str, Any]:
        candidate = self._candidate()
        if candidate is None:
            return {"status": "INSUFFICIENT_DATA", "answer": "No candidate profile found."}
        profile = CandidateProfileService(self.db).build(candidate.id)
        return {
            "status": "KNOWN",
            "answer": (
                f"Verified skills: {len(profile.verified_skill_names())} / {len(profile.skills)}, "
                f"target roles: {candidate.preferred_roles or 'not set'}."
            ),
        }

    def _what_should_i_do_today(self) -> dict[str, Any]:
        candidate = self._candidate()
        if candidate is None:
            return {"status": "INSUFFICIENT_DATA", "answer": "No candidate profile found."}
        pending = self._count_applications(candidate.id, {"PREPARING", "APPLYING", "SUBMITTED", "HANDOFF_REQUIRED"})
        new_jobs = self._count_new_jobs()
        steps = []
        if new_jobs:
            steps.append(f"Review {new_jobs} newly discovered/verified jobs and run matching.")
        if pending:
            steps.append(f"{pending} application(s) need attention (preparing/submitting/follow-up).")
        plan = self.plan_svc.last_plan(candidate.id)
        if plan:
            steps.append("Work on the current week's LEARN/BUILD/PROVE actions from your plan.")
        if not steps:
            steps.append("No immediate actions; consider adding a target role or uploading more portfolio evidence.")
        return {
            "status": "KNOWN",
            "answer": "Today: " + " ".join(steps),
        }

    def _why_no_interviews(self) -> dict[str, Any]:
        analysis = self._outcomes()
        n = analysis["sample"]["submitted"]
        if n < analysis["sample"]["minimum_sample"]:
            return {
                "status": "INSUFFICIENT_DATA",
                "answer": f"Insufficient evidence: only {n} submitted application(s); need {analysis['sample']['minimum_sample']}.",
            }
        inter = analysis["funnel"].get("INTERVIEW", 0)
        if inter >= 1:
            return {"status": "KNOWN", "answer": "You are getting interviews; see your funnel for where they stall."}
        top_gaps: list[str] = []
        candidate = self._candidate()
        if candidate:
            top_gaps = [g["skill"] for g in self.plan_svc.last_gaps(candidate.id) if g["priority"] == "HIGH"][:3]
        txt = "No interviews yet in the submitted sample. Possible contributing factors (not conclusions): missing skills, application volume below pipeline expectations, or response lag."
        if top_gaps:
            txt += f" Highest-demand skills you have not evidenced: {', '.join(top_gaps)}."
        return {"status": "KNOWN", "answer": txt}

    def _what_skills_to_learn(self) -> dict[str, Any]:
        candidate = self._candidate()
        if candidate is None:
            return {"status": "INSUFFICIENT_DATA", "answer": "No candidate profile found."}
        gaps = self.plan_svc.last_gaps(candidate.id)
        if not gaps:
            return {"status": "INSUFFICIENT_DATA", "answer": "No skill gaps detected yet; add target roles and applications first."}
        top = [g["skill"] for g in gaps if g["priority"] == "HIGH"][:5]
        return {"status": "KNOWN", "answer": f"Priority skills: {', '.join(top)}." if top else "No high-priority gaps; focus on medium gaps."}

    def _am_i_sending_enough(self) -> dict[str, Any]:
        analysis = self._outcomes()
        n = analysis["sample"]["submitted"]
        return {"status": "KNOWN" if n else "INSUFFICIENT_DATA", "answer": f"{n} submitted application(s) tracked."}

    def _how_is_my_funnel(self) -> dict[str, Any]:
        analysis = self._outcomes()
        if analysis["sample"]["submitted"] == 0:
            return {"status": "INSUFFICIENT_DATA", "answer": "No submitted applications yet."}
        funnel = analysis["funnel"]
        return {"status": "KNOWN", "answer": f"Funnel: sent {funnel['SUBMITTED']}, acknowledged {funnel['ACKNOWLEDGED']}, interviews {funnel['INTERVIEW']}, offers {funnel['OFFER']}."}

    def _why_am_i_getting_rejected(self) -> dict[str, Any]:
        pat = self._patterns()
        if pat is None:
            return {"status": "INSUFFICIENT_DATA", "answer": "Not enough rejected applications to identify patterns."}
        return {"status": "KNOWN", "answer": pat if pat else "No recurring rejection pattern detected yet."}

    def _what_improvement_plan(self) -> dict[str, Any]:
        candidate = self._candidate()
        if candidate is None:
            return {"status": "INSUFFICIENT_DATA", "answer": "No candidate profile found."}
        plan = self.plan_svc.last_plan(candidate.id)
        if not plan:
            return {"status": "INSUFFICIENT_DATA", "answer": "Generate a career plan first (run daily loop)."}
        return {"status": "KNOWN", "answer": f"CAREER_PLAN_GENERATED {plan.generated_at.date()}", "plan": plan.plan}

    def _what_is_my_career_health(self) -> dict[str, Any]:
        candidate = self._candidate()
        if candidate is None:
            return {"status": "INSUFFICIENT_DATA", "answer": "No candidate profile found."}
        profile = CandidateProfileService(self.db).build(candidate.id)
        gaps = self.plan_svc.last_gaps(candidate.id)
        analysis = self._outcomes()
        verified = len(profile.verified_skill_names())
        total_skills = len(profile.skills)
        critical = sum(1 for g in gaps if g["priority"] == "HIGH")
        texts = [
            f"{verified}/{total_skills} skills verified.",
            f"{critical} critical skill gap(s).",
            f"{analysis['funnel']['SUBMITTED']} submitted; {analysis['funnel']['INTERVIEW']} interviews.",
        ]
        return {"status": "KNOWN", "answer": "Career health: " + " ".join(texts)}

    def _should_i_apply_to_this_job(self) -> dict[str, Any]:
        # The active daily loop computes per-job strategies; this returns guidance.
        candidate = self._candidate()
        if candidate is None:
            return {"status": "INSUFFICIENT_DATA", "answer": "No candidate profile found."}
        return {"status": "KNOWN", "answer": "Run the daily loop to produce strategies per job; ask again after it finishes."}

    def _which_companies_to_prioritize(self) -> dict[str, Any]:
        candidate = self._candidate()
        if candidate is None:
            return {"status": "INSUFFICIENT_DATA", "answer": "No candidate profile found."}
        companies = self._company_demand(candidate.id)
        if not companies:
            return {"status": "INSUFFICIENT_DATA", "answer": "No verified jobs yet."}
        top = companies[:5]
        txt = ", ".join(f"{c}: {n}" for c, n in top)
        return {"status": "KNOWN", "answer": f"Companies with most verified jobs for your targets: {txt}."}

    def _am_i_ready_for_interviews(self) -> dict[str, Any]:
        candidate = self._candidate()
        if candidate is None:
            return {"status": "INSUFFICIENT_DATA", "answer": "No candidate profile found."}
        upcoming = self.db.execute(
            select(m.Interview)
            .where(m.Interview.status == "SCHEDULED")
            .order_by(m.Interview.interview_date.asc())
            .limit(10)
        ).scalars().all()
        n = len(upcoming)
        return {"status": "KNOWN", "answer": f"{n} scheduled interview(s)." if n else "No interviews scheduled yet."}

    def _what_evidence(self) -> dict[str, Any]:
        candidate = self._candidate()
        if candidate is None:
            return {"status": "INSUFFICIENT_DATA", "answer": "No candidate profile found."}
        profile = CandidateProfileService(self.db).build(candidate.id)
        prepared = [s for s in profile.skills if s.status == "VERIFIED_SKILL"]
        if not prepared:
            return {"status": "INSUFFICIENT_DATA", "answer": "No verified skills yet; add project evidence."}
        return {"status": "KNOWN", "answer": f"Verified skills: {', '.join(s.name for s in prepared[:6])}."}

    # --- helpers ---

    def _candidate(self) -> m.Candidate | None:
        return self.db.execute(select(m.Candidate).order_by(m.Candidate.id).limit(1)).scalar_one_or_none()

    def _outcomes(self) -> dict[str, Any]:
        candidate = self._candidate()
        return self.outcomes.compute(candidate.id if candidate else None)

    def _patterns(self) -> str | None:
        analysis = self._outcomes()
        if analysis["sample"]["submitted"] < analysis["sample"]["minimum_sample"]:
            return None
        return "; ".join(p["observation"] for p in analysis.get("patterns", [])[:2]) or None

    def _count_applications(self, candidate_id: int, statuses: set[str]) -> int:
        return (
            self.db.query(m.Application)
            .filter(m.Application.candidate_id == candidate_id, m.Application.status.in_(statuses))
            .count()
        )

    def _count_new_jobs(self) -> int:
        return self.db.query(m.Job).filter(m.Job.status.in_(["DISCOVERED", "VERIFIED"])).count()

    def _company_demand(self, candidate_id: int) -> list[tuple[str, int]]:
        rows = self.db.execute(
            select(m.Job.company, func.count(m.Job.id))
            .where(m.Job.verification_status == "ACTIVE")
            .group_by(m.Job.company)
            .order_by(func.count(m.Job.id).desc())
            .limit(10)
        ).all()
        return [(c, n) for c, n in rows]

    def _guess_question(self, question: str) -> str:
        q = question.lower()
        pairs = [
            ("what_should_i_do_today", ["today", "do now", "recommend"]),
            ("why_no_interviews", ["why", "no interview", "interview"]),
            ("what_skills_to_learn", ["skill", "learn", "gap"]),
            ("am_i_sending_enough_applications", ["enough", "how many", "volume"]),
            ("how_is_my_funnel", ["funnel", "conversion", "stage"]),
            ("why_am_i_getting_rejected", ["reject", "refuse"]),
            ("what_improvement_plan", ["plan", "improve", "improvement"]),
            ("what_is_my_career_health", ["health", "situation", "progress"]),
            ("should_i_apply_to_this_job", ["apply", "should i"]),
            ("which_companies_to_prioritize", ["company", "prioritiz", "where"]),
            ("am_i_ready_for_interviews", ["ready", "prepare"]),
            ("what_evidence_do_i_have", ["evidence", "proof", "verified"]),
        ]
        for qid, kw in pairs:
            if any(k in q for k in kw):
                return qid
        return "summary"