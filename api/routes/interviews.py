from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.api.deps import DiscreteAgents, get_db
from core.database import models as m

router = APIRouter(prefix="/api/interviews", tags=["interviews"])

DEFAULT_CANDIDATE_ID = 1


@router.get("")
def list_interviews(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.query(m.Interview).order_by(m.Interview.interview_date.desc()).all()
    return [_iv(i) for i in rows]


@router.get("/next")
def next_confirmed(db: Session = Depends(get_db)) -> dict:
    return DiscreteAgents(db).when_is_my_interview()


@router.post("/{interview_id}/prepare")
def prepare_interview(interview_id: int, candidate_id: int = DEFAULT_CANDIDATE_ID, db: Session = Depends(get_db)) -> dict:
    try:
        return DiscreteAgents(db).prepare_interview(interview_id, candidate_id)
    except ValueError:
        raise HTTPException(404, "interview not found")


def _iv(i: m.Interview) -> dict:
    return {
        "id": i.id,
        "company": i.company,
        "role": i.role,
        "round": i.round,
        "date": i.interview_date.strftime("%Y-%m-%d") if i.interview_date else None,
        "time": i.interview_date.strftime("%H:%M") if i.interview_date else None,
        "timezone": i.timezone,
        "meeting_url": i.meeting_url,
        "status": i.status,
        "source": i.source,
        "prepared": bool(i.preparation),
    }