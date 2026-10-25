from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from job_agent.ai.service import chain
from job_agent.analytics.outcomes import OutcomeAnalyzer
from job_agent.career.agent import CareerAgent
from job_agent.career.plan import CareerPlanService
from job_agent.candidates.profile import CandidateProfileService
from job_agent.candidates.skills import CandidateSkillService
from job_agent.api.deps import get_db
from job_agent.database import models as m

router = APIRouter(prefix="/api/career", tags=["career"])

DEFAULT_CANDIDATE_ID = 1

# In-memory conversation store (use Redis in production)
_conversations: dict[str, list[dict[str, Any]]] = {}


class AskRequest(BaseModel):
    question: str | None = None
    question_id: str | None = None


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    response: str
    conversation_id: str
    sources: list[dict[str, Any]] = []


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


# --- Conversational Chat Interface ---

def _gather_context(db: Session, candidate_id: int) -> dict[str, Any]:
    """Collect all relevant data for the LLM to answer naturally."""
    candidate = db.get(m.Candidate, candidate_id)
    if not candidate:
        return {}
    
    profile = CandidateProfileService(db).build(candidate_id)
    plan = CareerPlanService(db).latest(candidate_id)
    gaps = CareerPlanService(db).last_gaps(candidate_id)
    outcomes = OutcomeAnalyzer(db).compute(candidate_id)
    
    # Recent applications with job details
    apps = db.query(m.Application, m.Job).join(
        m.Job, m.Application.job_id == m.Job.id
    ).filter(
        m.Application.candidate_id == candidate_id
    ).order_by(m.Application.created_at.desc()).limit(10).all()
    
    # Upcoming interviews
    interviews = db.query(m.Interview).join(m.Application).filter(
        m.Application.candidate_id == candidate_id,
        m.Interview.status == "SCHEDULED"
    ).order_by(m.Interview.interview_date.asc()).limit(5).all()
    
    return {
        "candidate": {
            "name": candidate.name,
            "email": candidate.email,
            "current_title": candidate.current_title,
            "years_experience": candidate.years_of_experience,
            "location": candidate.location,
            "preferred_roles": candidate.preferred_roles or [],
            "remote_preference": candidate.remote_preference,
            "notice_period": candidate.notice_period,
        },
        "profile": {
            "verified_skills": list(profile.verified_skill_names()),
            "all_skills": [s.name for s in profile.skills],
            "unavailable": profile.unavailable,
        },
        "gaps": [{"skill": g["skill"], "priority": g["priority"], "why": g["analysis"]["evidence_of_gap"]} for g in gaps],
        "plan": plan.plan if plan else None,
        "funnel": outcomes.get("funnel", {}),
        "rates": outcomes.get("rates", {}),
        "recent_apps": [
            {
                "company": job.company if job else "Unknown",
                "title": job.title if job else "Unknown",
                "status": app.status,
                "match_score": app.match_score,
                "applied_at": app.submitted_at.isoformat() if app.submitted_at else None,
            }
            for app, job in apps
        ],
        "upcoming_interviews": [
            {
                "company": i.company,
                "role": i.role,
                "date": i.interview_date.isoformat() if i.interview_date else None,
                "round": i.round,
            }
            for i in interviews
        ],
    }


def _build_system_prompt() -> str:
    return """You are a supportive career coach AI. You have access to the candidate's real data (profile, applications, interviews, skill gaps, career plan).

Guidelines:
- Speak naturally, like a human career coach would
- Be encouraging but honest - never fabricate or exaggerate
- Reference specific data points (e.g., "I see you've applied to 3 roles at MockCorp")
- If data is missing, say so: "I don't have enough info about X yet"
- Give actionable, specific next steps
- Keep responses concise (2-4 sentences typically)
- Use "you" and "your" - make it personal"""


def _build_user_prompt(message: str, context: dict[str, Any], history: list[dict[str, Any]]) -> str:
    history_str = "\n".join([f"{h['role']}: {h['content']}" for h in history[-6:]])  # Last 6 turns
    return f"""CONVERSATION HISTORY:
{history_str if history else "(none)"}

CANDIDATE DATA:
{context}

USER MESSAGE: {message}

Respond as a helpful career coach. Reference specific facts from their data."""


@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, db: Session = Depends(get_db)) -> ChatResponse:
    conv_id = payload.conversation_id or str(uuid4())
    history = _conversations.get(conv_id, [])
    
    # Add user message
    history.append({"role": "user", "content": payload.message, "timestamp": datetime.utcnow().isoformat()})
    
    # Get candidate
    candidate = db.query(m.Candidate).order_by(m.Candidate.id).limit(1).first()
    if not candidate:
        return ChatResponse(
            response="I don't see a candidate profile yet. Please run `python -m job_agent seed` or import your resume first.",
            conversation_id=conv_id,
        )
    
    # Gather context
    context = _gather_context(db, candidate.id)
    
    # Generate response via LLM
    try:
        system = _build_system_prompt()
        user = _build_user_prompt(payload.message, context, history)
        
        llm_response = chain.generate(user, system=system)
        response_text = llm_response.strip()
    except Exception as e:
        # Fallback to structured agent
        agent = CareerAgent(db)
        structured = agent.ask(question=payload.message)
        response_text = structured.get("answer", "I'm having trouble accessing your data right now.")
    
    # Add assistant response
    history.append({"role": "assistant", "content": response_text, "timestamp": datetime.utcnow().isoformat()})
    _conversations[conv_id] = history[-20:]  # Keep last 20 turns
    
    # Track sources used
    sources = [
        {"type": "profile", "verified_skills": len(context.get("profile", {}).get("verified_skills", []))},
        {"type": "applications", "count": len(context.get("recent_apps", []))},
        {"type": "interviews", "count": len(context.get("upcoming_interviews", []))},
        {"type": "gaps", "count": len(context.get("gaps", []))},
        {"type": "plan", "exists": context.get("plan") is not None},
    ]
    
    return ChatResponse(
        response=response_text,
        conversation_id=conv_id,
        sources=sources,
    )


@router.get("/chat/history/{conversation_id}")
def chat_history(conversation_id: str) -> dict:
    return {"conversation_id": conversation_id, "history": _conversations.get(conversation_id, [])}


@router.delete("/chat/{conversation_id}")
def clear_chat(conversation_id: str) -> dict:
    if conversation_id in _conversations:
        del _conversations[conversation_id]
    return {"cleared": True}