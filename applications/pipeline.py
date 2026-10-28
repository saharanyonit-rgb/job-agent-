from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from core.database import models as m

VALID_TRANSITIONS: dict[str, set[str]] = {
    "DISCOVERED": {"VERIFIED", "REJECTED", "CLOSED"},
    "VERIFIED": {"MATCHED", "REJECTED", "CLOSED", "DUPLICATE"},
    "MATCHED": {"READY", "REJECTED", "CLOSED"},
    "READY": {"PREPARING", "APPLYING", "REJECTED", "WITHDRAWN", "HANDOFF_REQUIRED"},
    "PREPARING": {"APPLYING", "HANDOFF_REQUIRED", "REJECTED"},
    "APPLYING": {"SUBMITTED", "FAILED", "HANDOFF_REQUIRED", "READY"},
    "SUBMITTED": {
        "RECEIVED",
        "ACKNOWLEDGED",
        "ASSESSMENT",
        "INTERVIEW_REQUESTED",
        "RECRUITER_RESPONSE",
        "REJECTED",
        "FAILED",
        "HANDOFF_REQUIRED",
        "EXPIRED",
    },
    "ACKNOWLEDGED": {
        "RECEIVED",
        "RECRUITER_RESPONSE",
        "ASSESSMENT",
        "INTERVIEW_REQUESTED",
        "OFFER",
        "REJECTED",
        "EXPIRED",
        "HANDOFF_REQUIRED",
    },
    "RECEIVED": {"ASSESSMENT", "INTERVIEW_REQUESTED", "RECRUITER_RESPONSE", "REJECTED", "OFFER", "FOLLOW_UP", "EXPIRED"},
    "RECRUITER_RESPONSE": {"ASSESSMENT", "INTERVIEW_REQUESTED", "OFFER", "REJECTED", "FOLLOW_UP", "CLOSED"},
    "ASSESSMENT": {"INTERVIEW_REQUESTED", "REJECTED", "OFFER", "FOLLOW_UP"},
    "INTERVIEW_REQUESTED": {"INTERVIEW_SCHEDULED", "REJECTED", "CLOSED"},
    "INTERVIEW_SCHEDULED": {"INTERVIEW_COMPLETED", "REJECTED", "OFFER", "CLOSED"},
    "INTERVIEW_COMPLETED": {"OFFER", "REJECTED", "CLOSED"},
    "OFFER": {"ACCEPTED", "REJECTED", "WITHDRAWN", "CLOSED"},
    "ACCEPTED": {"CLOSED", "WITHDRAWN"},
    "REJECTED": {"CLOSED"},
    "WITHDRAWN": {"CLOSED"},
    "EXPIRED": {"CLOSED"},
    "CLOSED": set(),
    "HANDOFF_REQUIRED": {"APPLYING", "READY", "REJECTED", "CLOSED"},
    "FAILED": {"APPLYING", "PREPARING", "HANDOFF_REQUIRED", "CLOSED"},
    "DUPLICATE": {"CLOSED"},
    "FOLLOW_UP": {"RECEIVED", "INTERVIEW_REQUESTED", "REJECTED", "CLOSED"},
}

ALL_STATUSES = sorted({s for s, t in VALID_TRANSITIONS.items()} | {t for ts in VALID_TRANSITIONS.values() for t in ts})

TERMINAL = {"CLOSED", "REJECTED", "WITHDRAWN", "DUPLICATE", "ACCEPTED", "EXPIRED"}

# Email-class driven outcome events that carry evidence and confidence.
ACKNOWLEDGED_LABELS = {"APPLICATION_RECEIVED", "APPLICATION_ACKNOWLEDGED"}
REJECTION_LABELS = {"REJECTION"}
OFFER_LABELS = {"OFFER"}
RESPONSE_LABELS = {"RECRUITER_RESPONSE"}
FREEZE_LABELS = {"HIRING_FREEZE_ROLE_CLOSED"}


class InvalidTransition(Exception):
    def __init__(self, current: str, target: str):
        super().__init__(f"Invalid application transition {current} -> {target}")
        self.current = current
        self.target = target


def _event(
    application: m.Application,
    to_status: str,
    event_type: str | None = None,
    source: str | None = None,
    evidence: str | None = None,
    confidence: float | None = None,
    detail: dict[str, Any] | None = None,
) -> m.ApplicationEvent:
    return m.ApplicationEvent(
        application_id=application.id,
        to_status=to_status,
        event_type=event_type,
        source=source,
        evidence=evidence,
        confidence=confidence,
        detail=detail or {},
    )


class ApplicationPipeline:
    def __init__(self, db: Session):
        self.db = db

    def transition(
        self,
        application: m.Application,
        to_status: str,
        detail: dict[str, Any] | None = None,
        force: bool = False,
        *,
        event_type: str | None = None,
        source: str | None = None,
        evidence: str | None = None,
        confidence: float | None = None,
    ) -> m.Application:
        if application.status == to_status:
            return application
        allowed = VALID_TRANSITIONS.get(application.status, set())
        if to_status not in allowed and not force:
            raise InvalidTransition(application.status, to_status)
        application.status = to_status
        application.updated_at = datetime.utcnow()
        self.db.add(
            _event(
                application,
                to_status,
                event_type=event_type or to_status,
                source=source or "system",
                evidence=evidence,
                confidence=confidence,
                detail=detail or {},
            )
        )
        self.db.commit()
        self.db.refresh(application)
        return application

    def record_event(
        self,
        application: m.Application,
        event_type: str,
        source: str,
        evidence: str | None = None,
        confidence: float | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Record an event without changing status (e.g. handoff/'soft' signals)."""
        self.db.add(
            _event(
                application,
                application.status,
                event_type=event_type,
                source=source,
                evidence=evidence,
                confidence=confidence,
                detail=detail,
            )
        )
        self.db.commit()
        self.db.refresh(application)