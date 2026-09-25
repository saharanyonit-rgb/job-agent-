from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from job_agent.api.deps import JobAgentOrchestrator, get_db
from job_agent.applications.policy import ApplicationPolicy, PolicyService, MODES

router = APIRouter(prefix="/api/settings", tags=["settings"])


class PolicyIn(BaseModel):
    mode: str | None = None
    minimum_match: float | None = None
    minimum_salary: float | None = None
    allowed_locations: list[str] | None = None
    allowed_roles: list[str] | None = None
    excluded_companies: list[str] | None = None
    max_daily_applications: int | None = None
    require_verified: bool | None = None
    min_evidence_confidence: float | None = None
    max_hard_failures: int | None = None
    max_low_confidence_applications: int | None = None
    low_confidence_window_hours: int | None = None
    low_confidence_window_align_utc_day: bool | None = None


@router.get("/policy")
def get_policy(db: Session = Depends(get_db)) -> dict:
    return PolicyService(db).get().to_dict()


@router.put("/policy")
def update_policy(body: PolicyIn, db: Session = Depends(get_db)) -> dict:
    if body.mode and body.mode not in MODES:
        raise HTTPException(400, f"mode must be one of {MODES}")
    try:
        policy = PolicyService(db).update(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return policy.to_dict()


@router.get("/sources")
def get_statuses(db: Session = Depends(get_db)) -> dict:
    from job_agent.discovery.base import register_default_adapters

    register_default_adapters()
    from job_agent.discovery.base import registry

    return {"sources": registry.statuses()}