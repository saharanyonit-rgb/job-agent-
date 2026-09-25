from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from job_agent.analytics.outcomes import OutcomeAnalyzer
from job_agent.career.agent import CareerAgent
from job_agent.career.daily import DailyCareerLoop
from job_agent.career.plan import CareerPlanService
from job_agent.candidates.profile import CandidateProfileService
from job_agent.candidates.skills import CandidateSkillService
from job_agent.api.deps import get_db
from job_agent.database import models as m

router = APIRouter(prefix="/api/career", tags=["career"])

DEFAULT_CANDIDATE_ID = 1


class AskRequest(BaseModel):
    question: str | None = None
    question_id: str | None = None


class SkillUpdateRequest(BaseModel):
    name: str
    status: str
    evidence: str | None = None


class InterviewOutcomeRequest(BaseModel):
    outcome: str
    next_step: str | None = None
    interviewer: str | None = None


class RunLoopRequest(BaseModel):
    dry_run: bool = False


@router.get("/profile")
def profile(db: Session = Depends(get_db)) -> dict:
    candidate = db.query(m.Candidate).order_by(m.Candidate.id).limit(1).first()
    if candidate is None:
        raise HTTPException(status_code=404, detail="No candidate profile found")
    built = CandidateProfileService(db).build(candidate.id)
    return {
        "candidate_id": candidate.id,
        "profile": built.to_dict(),
        "structured": {
            "languages": candidate.languages or [],
            "preferred_industries": candidate.preferred_industries or [],
            "excluded_roles": candidate.excluded_roles or [],
            "preferred_roles": candidate.preferred_roles or [],
            "portfolio_links": candidate.portfolio_links or [],
            "achievements": candidate.achievements or [],
            "availability": candidate.availability,
        },
    }


@router.get("/skills")
def skills(db: Session = Depends(get_db)) -> dict:
    candidate = db.query(m.Candidate).order_by(m.Candidate.id).limit(1).first()
    if candidate is None:
        raise HTTPException(status_code=404, detail="No candidate profile found")
    rows = CandidateSkillService(db).list(candidate.id)
    return {
        "skills": [
            {"id": r.id, "name": r.name, "status": r.status, "proficiency": r.proficiency,
             "verified": r.verified, "source": r.source, "evidence": r.evidence}
            for r in rows
        ]
    }


@router.post("/skills/update")
def update_skill(payload: SkillUpdateRequest, db: Session = Depends(get_db)) -> dict:
    candidate = db.query(m.Candidate).order_by(m.Candidate.id).limit(1).first()
    if candidate is None:
        raise HTTPException(status_code=404, detail="No candidate profile found")
    row = CandidateSkillService(db).set_status(candidate.id, payload.name, payload.status, payload.evidence)
    return {"id": row.id, "name": row.name, "status": row.status, "verified": row.verified}


@router.get("/skill-gaps")
def skill_gaps(db: Session = Depends(get_db)) -> dict:
    candidate = db.query(m.Candidate).order_by(m.Candidate.id).limit(1).first()
    if candidate is None:
        raise HTTPException(status_code=404, detail="No candidate profile found")
    gaps = CareerPlanService(db).last_gaps(candidate.id)
    return {"gaps": gaps, "status": "INSUFFICIENT_DATA" if not gaps else "KNOWN"}


@router.post("/plan")
def generate_plan(db: Session = Depends(get_db)) -> dict:
    candidate = db.query(m.Candidate).order_by(m.Candidate.id).limit(1).first()
    if candidate is None:
        raise HTTPException(status_code=404, detail="No candidate profile found")
    plan = CareerPlanService(db).generate(candidate.id)
    return {"plan": plan}


@router.get("/plan")
def get_plan(db: Session = Depends(get_db)) -> dict:
    candidate = db.query(m.Candidate).order_by(m.Candidate.id).limit(1).first()
    if candidate is None:
        raise HTTPException(status_code=404, detail="No candidate profile found")
    row = CareerPlanService(db).latest(candidate.id)
    if row is None:
        raise HTTPException(status_code=404, detail="No plan generated yet; POST /api/career/plan first")
    return {"generated_at": row.generated_at.isoformat(), "period": row.period, "plan": row.plan}


@router.get("/goals")
def learning_goals(db: Session = Depends(get_db)) -> dict:
    candidate = db.query(m.Candidate).order_by(m.Candidate.id).limit(1).first()
    if candidate is None:
        raise HTTPException(status_code=404, detail="No candidate profile found")
    goals = CareerPlanService(db).learning_goals(candidate.id)
    return {"goals": [{"id": g.id, "skill": g.skill, "status": g.status, "priority": g.priority, "plan": g.plan} for g in goals]}


@router.get("/questions")
def questions() -> dict:
    return {"questions": CareerAgent.catalog()}


@router.post("/ask")
def ask(payload: AskRequest, db: Session = Depends(get_db)) -> dict:
    return CareerAgent(db).ask(question_id=payload.question_id, question=payload.question)


@router.post("/run-loop")
def run_daily_loop(payload: RunLoopRequest, db: Session = Depends(get_db)) -> dict:
    from job_agent.api.deps import JobAgentOrchestrator

    return JobAgentOrchestrator(db).run_daily_career_loop(DEFAULT_CANDIDATE_ID, dry_run=payload.dry_run)


@router.get("/daily-report")
def latest_daily_report(db: Session = Depends(get_db)) -> dict:
    row = db.query(m.DailyReport).order_by(m.DailyReport.generated_at.desc()).first()
    if row is None:
        raise HTTPException(status_code=404, detail="No daily report yet; run the daily loop first")
    return {"report_id": row.id, "generated_at": row.generated_at.isoformat(), "report": row.report}


@router.get("/analytics")
def analytics(db: Session = Depends(get_db)) -> dict:
    candidate = db.query(m.Candidate).order_by(m.Candidate.id).limit(1).first()
    return OutcomeAnalyzer(db).compute(candidate.id if candidate else None)


@router.post("/interviews/{interview_id}/outcome")
def interview_outcome(interview_id: int, payload: InterviewOutcomeRequest, db: Session = Depends(get_db)) -> dict:
    from job_agent.interviews.service import InterviewService

    interview = db.get(m.Interview, interview_id)
    if interview is None:
        raise HTTPException(status_code=404, detail="Interview not found")
    row = InterviewService(db).record_outcome(interview, payload.outcome, payload.next_step, payload.interviewer)
    return {"id": row.id, "status": row.status, "outcome": row.outcome, "next_step": row.next_step, "interviewer": row.interviewer_name}