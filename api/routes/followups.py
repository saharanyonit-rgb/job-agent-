from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.database import models as m
from core.api.deps import get_db

router = APIRouter(prefix="/api/followups", tags=["followups"])


@router.get("")
def list_followups(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.query(m.Followup).order_by(m.Followup.id.desc()).limit(100).all()
    return [
        {"id": f.id, "application_id": f.application_id, "status": f.status, "message": f.message, "scheduled_for": f.scheduled_for.isoformat() if f.scheduled_for else None}
        for f in rows
    ]


@router.post("/process")
def process(db: Session = Depends(get_db)) -> dict:
    from core.followups.engine import FollowupEngine

    engine = FollowupEngine(db)
    plans = engine.plan_pending()
    engine.record_plans(plans)
    sent = engine.process_followups()
    return {"planned": len(plans), "sent": sent}