from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from job_agent.analytics.outcomes import OutcomeAnalyzer
from job_agent.career.plan import CareerPlanService
from job_agent.candidates.profile import CandidateProfileService
from job_agent.api.deps import JobAgentOrchestrator, get_db
from job_agent.database import models as m

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

DEFAULT_CANDIDATE_ID = 1


@router.get("")
def dashboard(db: Session = Depends(get_db)) -> dict:
    return _build(db)


def _build(db: Session) -> dict:
    counts = {s: db.query(m.Application).filter(m.Application.status == s).count() for s in [
        "DISCOVERED", "VERIFIED", "MATCHED", "READY", "PREPARING", "APPLYING",
        "SUBMITTED", "ACKNOWLEDGED", "RECEIVED", "RECRUITER_RESPONSE", "ASSESSMENT",
        "INTERVIEW_REQUESTED", "INTERVIEW_SCHEDULED", "INTERVIEW_COMPLETED", "REJECTED",
        "OFFER", "ACCEPTED", "WITHDRAWN", "EXPIRED", "CLOSED", "HANDOFF_REQUIRED", "FAILED",
    ]}
    total = sum(counts.values())
    pipeline = {
        "DISCOVERED": db.query(m.Job).filter(m.Job.status == "DISCOVERED").count(),
        "VERIFIED": db.query(m.Job).filter(m.Job.status == "VERIFIED").count(),
        "APPLIED": db.query(m.Application).filter(m.Application.submitted_at.isnot(None)).count(),
        "RESPONSE": db.query(m.EmailMessage).filter(m.EmailMessage.classification != "OTHER").count(),
        "INTERVIEW": db.query(m.Interview).count(),
        "OFFER": db.query(m.Application).filter(m.Application.status == "OFFER").count(),
    }
    upcoming = db.query(m.Interview).filter(m.Interview.status == "SCHEDULED").order_by(m.Interview.interview_date.asc()).limit(5).all()
    handoffs = db.query(m.Application).filter(m.Application.status == "HANDOFF_REQUIRED").all()
    notifications = db.query(m.Notification).filter(m.Notification.read.is_(False)).count()
    today_submitted = db.query(m.Application).filter(m.Application.submitted_at.isnot(None)).count()

    # Phase 12: career health, funnel, learning, insights
    candidate = db.query(m.Candidate).order_by(m.Candidate.id).limit(1).first()
    career = _career_sections(db, candidate.id if candidate else None)
    outcomes = OutcomeAnalyzer(db).compute(candidate.id if candidate else None)

    return {
        "total_applications": total,
        "active_applications": total - counts.get("REJECTED", 0) - counts.get("CLOSED", 0) - counts.get("WITHDRAWN", 0) - counts.get("EXPIRED", 0),
        "applications": counts,
        "pipeline": pipeline,
        "interviews": counts.get("INTERVIEW_SCHEDULED", 0),
        "assessments": counts.get("ASSESSMENT", 0),
        "offers": counts.get("OFFER", 0) + counts.get("ACCEPTED", 0),
        "rejections": counts.get("REJECTED", 0),
        "upcoming_interviews": [
            {
                "company": i.company,
                "role": i.role,
                "round": i.round,
                "date": i.interview_date.strftime("%Y-%m-%d") if i.interview_date else None,
                "time": i.interview_date.strftime("%H:%M") if i.interview_date else None,
                "meeting_url": i.meeting_url,
            }
            for i in upcoming
        ],
        "action_required": [
            {
                "application_id": h.id,
                "job": (db.get(m.Job, h.job_id).title if h.job_id else None),
                "company": (db.get(m.Job, h.job_id).company if h.job_id else None),
                "reason": h.handoff_reason,
            }
            for h in handoffs
        ],
        "unread_notifications": notifications,
        "today_activity": {
            "applications_submitted": today_submitted,
            "interviews_detected": db.query(m.Interview).count(),
        },
        "career_health": career["career_health"],
        "funnel": outcomes["funnel"],
        "rates": outcomes["rates"],
        "learning": career["learning"],
        "insights": career["insights"],
    }


def _career_sections(db: Session, candidate_id: int | None) -> dict:
    empty = {"career_health": {"status": "INSUFFICIENT_DATA", "verified_skills": 0, "total_skills": 0, "critical_gaps": 0, "evidence_quality": None}, "learning": [], "insights": []}
    if candidate_id is None:
        return empty

    profile = CandidateProfileService(db).build(candidate_id)
    verified = len(profile.verified_skill_names())
    total = len(profile.skills)
    plan_svc = CareerPlanService(db)
    try:
        gaps = plan_svc.last_gaps(candidate_id)
    except Exception:
        gaps = []
    critical = [g for g in gaps if g["priority"] == "HIGH"]
    latest = plan_svc.latest(candidate_id)
    insights_payload = latest.plan.get("weekly_plan") if latest and latest.plan else None
    insights = []
    if critical:
        insights.append({"type": "critical_gap", "text": f"Learn {critical[0]['skill']} (critical demand gap) while continuing to apply."})
    if insights_payload:
        insights.append({"type": "plan", "week_1": insights_payload.get("week_1", []), "week_2": insights_payload.get("week_2", [])})
    return {
        "career_health": {
            "status": "KNOWN" if total else "INSUFFICIENT_DATA",
            "verified_skills": verified,
            "total_skills": total,
            "critical_gaps": len(critical),
            "evidence_quality": round(100.0 * verified / max(total, 1), 1),
        },
        "learning": [
            {"skill": g["skill"], "priority": g["priority"],
             "demand_count": g["demand_count"],
             "appeared_in_rejections": g["appeared_in_rejections"],
             "appeared_in_interviews": g["appeared_in_interviews"],
             "why": g["analysis"]["evidence_of_gap"]}
            for g in gaps[:10]
        ],
        "insights": insights,
    }