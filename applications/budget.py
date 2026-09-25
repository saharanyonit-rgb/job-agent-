"""Low-confidence application budget (Phase hardening).

Hard fail-closed policy gate. `MAX_LOW_CONFIDENCE_APPLICATIONS` is not a log
line — it is enforced before document preparation / submission.

Definition of a *low-confidence application* (deterministic):

  * belongs to the candidate under evaluation, and
  * actually reached submission (`submitted_at` is set), and
  * carries recorded evidence confidence below
    `policy.min_evidence_confidence` — as expressed by either
      - `Application.evidence_confidence < min_evidence_confidence`, or
      - an `ApplicationEvent` of type ``LOW_CONFIDENCE`` recorded for it,
        or a handoff reason that names missing/low evidence.

Counting semantics:

  * window           = rolling ``low_confidence_window_hours`` (default 24h);
                       aligned to UTC midnight when ``align_to_utc_day`` is set
                       so behaviour "resets" each day.
  * exclusion        = the current application is never counted.
  * reach            = only applications that actually reached submission are
                       counted (failed / preparing / aborted never count).
  * limit exceeded   = decision is ``HANDOFF_REQUIRED`` (fail closed). It is
                       never merely logged.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_agent.database import models as m

LOW_CONFIDENCE_EVENT_TYPES = {"LOW_CONFIDENCE", "NEED_MORE_EVIDENCE"}
EVIDENCE_HANDOFF_MARKERS = ("evidence", "low-confidence", "low confidence", "unconfirmed")


def low_confidence_window_start(now: datetime | None = None, *, window_hours: int = 24, align_to_utc_day: bool = False) -> datetime:
    """Start of the counting window, inclusive. UTC-based to stay deterministic."""
    now = now or datetime.utcnow()
    if align_to_utc_day:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if start > now - timedelta(hours=window_hours):
            return start
        return now - timedelta(hours=window_hours)
    return now - timedelta(hours=window_hours)


def is_low_confidence_application(application: m.Application, min_evidence_confidence: float = 0.8) -> bool:
    """Deterministic predicate for what counts as a low-confidence application."""
    if application.evidence_confidence is not None:
        if application.evidence_confidence < min_evidence_confidence:
            return True
    handlers = [
        (e.event_type or "") for e in (application.events or [])
    ]
    if any(t in LOW_CONFIDENCE_EVENT_TYPES for t in handlers):
        return True
    handoff_reason = (application.handoff_reason or "").lower()
    if handoff_reason and any(marker in handoff_reason for marker in EVIDENCE_HANDOFF_MARKERS):
        return True
    return False


class LowConfidenceBudget:
    """Counts and gates on low-confidence applications reaching submission."""

    def __init__(self, db: Session):
        self.db = db

    def qualifying(
        self,
        candidate_id: int,
        *,
        exclude_application_id: int | None = None,
        window_hours: int = 24,
        align_to_utc_day: bool = False,
        min_evidence_confidence: float = 0.8,
        now: datetime | None = None,
    ) -> list[m.Application]:
        """Applications that count towards the budget (window + reached submission)."""
        start = low_confidence_window_start(now, window_hours=window_hours, align_to_utc_day=align_to_utc_day)
        rows = (
            self.db.execute(
                select(m.Application).where(
                    m.Application.candidate_id == candidate_id,
                    m.Application.submitted_at.isnot(None),
                    m.Application.submitted_at >= start,
                )
            )
        ).scalars().all()
        if exclude_application_id is not None:
            rows = [a for a in rows if a.id != exclude_application_id]
        return [a for a in rows if is_low_confidence_application(a, min_evidence_confidence)]

    def count(self, candidate_id: int, *, exclude_application_id: int | None = None, window_hours: int = 24, align_to_utc_day: bool = False, min_evidence_confidence: float = 0.8, now: datetime | None = None) -> int:
        return len(
            self.qualifying(
                candidate_id,
                exclude_application_id=exclude_application_id,
                window_hours=window_hours,
                align_to_utc_day=align_to_utc_day,
                min_evidence_confidence=min_evidence_confidence,
                now=now,
            )
        )

    def decide(
        self,
        candidate_id: int,
        *,
        exclude_application_id: int | None = None,
        max_low_confidence_applications: int = 5,
        window_hours: int = 24,
        align_to_utc_day: bool = False,
        min_evidence_confidence: float = 0.8,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Fail-closed decision for an application about to be prepared/submitted."""
        current_count = self.count(
            candidate_id,
            exclude_application_id=exclude_application_id,
            window_hours=window_hours,
            align_to_utc_day=align_to_utc_day,
            min_evidence_confidence=min_evidence_confidence,
            now=now,
        )
        exceeded = current_count >= max_low_confidence_applications
        return {
            "action": "HANDOFF_REQUIRED" if exceeded else "PROCEED",
            "decision": "NEED_MORE_EVIDENCE" if exceeded else "READY_FOR_SUBMISSION",
            "count": current_count,
            "limit": max_low_confidence_applications,
            "exceeded": exceeded,
            "reasons": (
                [f"low-confidence application budget exceeded: {current_count} >= {max_low_confidence_applications} in window"]
                if exceeded
                else []
            ),
            "window_hours": window_hours,
            "window_start": low_confidence_window_start(now, window_hours=window_hours, align_to_utc_day=align_to_utc_day).isoformat(),
        }

    def idempotency_key(self, candidate_id: int, application_id: int, window_hours: int = 24) -> str:
        """Deterministic key for a single budget decision (safe retries)."""
        payload = f"low-confidence-budget|{candidate_id}|{application_id}|{window_hours}"
        return hashlib.sha256(payload.encode()).hexdigest()