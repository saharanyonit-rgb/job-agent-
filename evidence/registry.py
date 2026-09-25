from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_agent.database import models as m


class EvidenceRegistry:
    """Phase 10: legacy system for traceable claims.

    Every claim the agent makes about the candidate, an application, or an
    outcome can be recorded with its source, confidence, and supporting detail
    so the "no fabricated facts" invariant holds end-to-end.
    """

    def __init__(self, db: Session):
        self.db = db

    def add(
        self,
        entity_type: str,
        entity_id: int | None,
        claim: str,
        source: str,
        confidence: float = 0.0,
        detail: dict[str, Any] | None = None,
    ) -> m.Evidence:
        row = m.Evidence(
            entity_type=entity_type,
            entity_id=entity_id,
            claim=claim,
            source=source,
            confidence=confidence,
            detail=detail or {},
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def for_entity(self, entity_type: str, entity_id: int) -> list[m.Evidence]:
        return self.db.execute(
            select(m.Evidence)
            .where(m.Evidence.entity_type == entity_type, m.Evidence.entity_id == entity_id)
            .order_by(m.Evidence.created_at.desc())
        ).scalars().all()

    def latest(self, entity_type: str) -> list[m.Evidence]:
        return self.db.execute(
            select(m.Evidence)
            .where(m.Evidence.entity_type == entity_type)
            .order_by(m.Evidence.created_at.desc())
            .limit(20)
        ).scalars().all()

    def to_dict(self, entity_type: str, entity_id: int) -> list[dict[str, Any]]:
        return [
            {
                "id": e.id,
                "claim": e.claim,
                "source": e.source,
                "confidence": e.confidence,
                "detail": e.detail,
                "created_at": e.created_at.isoformat(),
            }
            for e in self.for_entity(entity_type, entity_id)
        ]