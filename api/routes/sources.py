from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.api.deps import get_db
from core.discovery.service import DiscoveryService
from core.core.observability import ObservabilityService

router = APIRouter(prefix="/api/sources", tags=["sources"])


@router.get("/status")
def source_status(db: Session = Depends(get_db)) -> dict:
    return {"sources": DiscoveryService(db).source_health()}


@router.get("/providers")
def providers_status() -> dict:
    from core.integrations.matrix import provider_status

    return provider_status()


@router.get("/health")
def status(db: Session = Depends(get_db)) -> dict:
    return ObservabilityService(db).summary()