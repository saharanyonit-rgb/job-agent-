from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from job_agent.api.deps import get_db
from job_agent.core.observability import ObservabilityService

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("")
def audit(limit: int = 100, db: Session = Depends(get_db)) -> dict:
    obs = ObservabilityService(db)
    return {"entries": obs.audit_trail(limit=limit)}