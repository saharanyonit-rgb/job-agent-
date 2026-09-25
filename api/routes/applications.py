from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from job_agent.api.deps import ApplicationService, JobAgentOrchestrator, get_db
from job_agent.database import models as m
from job_agent.applications.pipeline import InvalidTransition

router = APIRouter(prefix="/api/applications", tags=["applications"])

DEFAULT_CANDIDATE_ID = 1


class MatchIn(BaseModel):
    candidate_id: int = DEFAULT_CANDIDATE_ID


class MoveIn(BaseModel):
    to_status: str


@router.post("/match")
def match_all(body: MatchIn | None = None, db: Session = Depends(get_db)) -> dict:
    body = body or MatchIn()
    return JobAgentOrchestrator(db).match_all(body.candidate_id)


@router.post("/prepare")
def prepare_all(body: MatchIn | None = None, dry_run: bool = False, db: Session = Depends(get_db)) -> dict:
    body = body or MatchIn()
    return JobAgentOrchestrator(db).prepare_and_submit_applications(body.candidate_id, dry_run=dry_run)


@router.get("")
def list_applications(status: str | None = None, candidate_id: int = DEFAULT_CANDIDATE_ID, db: Session = Depends(get_db)) -> list[dict]:
    svc = ApplicationService(db)
    apps = svc.list_applications(candidate_id=candidate_id, status=status)
    return [_app_dict(a, db) for a in apps]


@router.get("/counts")
def counts(db: Session = Depends(get_db)) -> dict:
    return ApplicationService(db).counts()


@router.get("/today")
def today(db: Session = Depends(get_db)) -> dict:
    from datetime import datetime

    start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    submitted = db.query(m.Application).filter(m.Application.submitted_at >= start).count()
    events = (
        db.query(m.ApplicationEvent)
        .filter(m.ApplicationEvent.created_at >= start)
        .filter(m.ApplicationEvent.to_status.in_(["SUBMITTED"]))
        .count()
    )
    return {"submitted_today": submitted, "submit_events_today": events}


@router.get("/{application_id}")
def get_application(application_id: int, db: Session = Depends(get_db)) -> dict:
    app = db.get(m.Application, application_id)
    if not app:
        raise HTTPException(404, "application not found")
    return _app_dict(app, db)


@router.post("/{application_id}/transition")
def transition(application_id: int, body: MoveIn, db: Session = Depends(get_db)) -> dict:
    app = db.get(m.Application, application_id)
    if not app:
        raise HTTPException(404, "not found")
    svc = ApplicationService(db)
    try:
        svc.pipeline.transition(app, body.to_status)
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc))
    return _app_dict(app, db)


@router.post("/{application_id}/submit-manual")
def submit_manual(application_id: int, db: Session = Depends(get_db)) -> dict:
    app = db.get(m.Application, application_id)
    if not app:
        raise HTTPException(404, "not found")
    if app.status not in ("READY", "HANDOFF_REQUIRED", "FAILED"):
        raise HTTPException(409, f"cannot submit from {app.status}")
    orch = JobAgentOrchestrator(db)
    result = orch.prepare_and_submit_applications(app.candidate_id, dry_run=False)
    return result


def _app_dict(a: m.Application, db: Session) -> dict:
    job = db.get(m.Job, a.job_id)
    events = [{"status": e.to_status, "created_at": e.created_at.isoformat(), "detail": e.detail} for e in a.events]
    return {
        "id": a.id,
        "job_id": a.job_id,
        "job": job.title if job else None,
        "company": job.company if job else None,
        "status": a.status,
        "mode": a.mode,
        "match_score": a.match_score,
        "match_explanation": a.match_explanation,
        "application_id_external": a.application_id_external,
        "submitted_at": a.submitted_at.isoformat() if a.submitted_at else None,
        "confirmation_evidence": a.confirmation_evidence or {},
        "handoff_reason": a.handoff_reason,
        "validation_report": a.validation_report or {},
        "resume_id": a.resume_id,
        "events": events,
    }