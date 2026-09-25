from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from job_agent.api.deps import DiscreteAgents, get_db
from job_agent.database import models as m

router = APIRouter(prefix="/api/emails", tags=["emails"])


@router.post("/process")
def process(adapter: str = "mock", db: Session = Depends(get_db)) -> dict:
    return DiscreteAgents(db).monitor_email(adapter=adapter)


@router.get("")
def list_emails(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.query(m.EmailMessage).order_by(m.EmailMessage.received_at.desc()).limit(100).all()
    return [
        {
            "id": r.id,
            "from_name": r.from_name,
            "from_address": r.from_address,
            "subject": r.subject,
            "classification": r.classification,
            "confidence": r.classification_confidence,
            "extracted": r.extracted,
            "received_at": r.received_at.isoformat(),
        }
        for r in rows
    ]