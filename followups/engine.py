from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.database import models as m


@dataclass
class FollowupPlan:
    application_id: int | None
    candidate_id: int
    scheduled_for: datetime | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "application_id": self.application_id,
            "candidate_id": self.candidate_id,
            "scheduled_for": self.scheduled_for.isoformat() if self.scheduled_for else None,
            "message": self.message,
        }


class FollowupEngine:
    """Plans polite follow-ups, avoiding spam (one message per window)."""

    RESPONSE_DEADLINE_HOURS = 120
    FOLLOWUP_CPU = 5

    def __init__(self, db: Session, now: datetime | None = None):
        self.db = db
        self._now = now or datetime.utcnow()

    def plan_pending(self) -> list[FollowupPlan]:
        """Applications submitted > deadline with no recruiter response."""
        plans: list[FollowupPlan] = []
        apps = (
            self.db.execute(
                select(m.Application).where(
                    m.Application.status.in_(["SUBMITTED", "RECEIVED"]),
                    m.Application.submitted_at.isnot(None),
                )
            )
            .scalars()
            .all()
        )
        for app in apps:
            assert app.submitted_at is not None
            elapsed = (self._now - app.submitted_at).total_seconds() / 3600
            if elapsed >= self.RESPONSE_DEADLINE_HOURS:
                existing = self.db.execute(
                    select(m.Followup).where(m.Followup.application_id == app.id)
                ).scalars().all()
                if len(existing) >= self.FOLLOWUP_CPU:
                    continue
                if any(f.status == "PENDING" for f in existing):
                    continue
                if existing:
                    last_contact: datetime | None = None
                    for f in existing:
                        stamp = f.sent_at or f.scheduled_for
                        if stamp and (last_contact is None or stamp > last_contact):
                            last_contact = stamp
                    if last_contact:
                        elapsed_since = (self._now - last_contact).total_seconds() / 3600
                        if elapsed_since < self.RESPONSE_DEADLINE_HOURS:
                            continue
                job = self.db.get(m.Job, app.job_id)
                message = (
                    f"Hello, I applied for the {job.title if job else 'role'} position "
                    f"at {job.company if job else 'your company'} some time ago and remain "
                    "very interested. I would appreciate any update on my application."
                )
                plans.append(
                    FollowupPlan(
                        application_id=app.id,
                        candidate_id=app.candidate_id,
                        scheduled_for=self._now,
                        message=message.strip(),
                    )
                )
        return plans

    def record_plans(self, plans: list[FollowupPlan]) -> list[m.Followup]:
        rows: list[m.Followup] = []
        for plan in plans:
            row = m.Followup(
                application_id=plan.application_id,
                candidate_id=plan.candidate_id,
                scheduled_for=plan.scheduled_for,
                message=plan.message,
                status="PENDING",
            )
            self.db.add(row)
            rows.append(row)
        self.db.commit()
        return rows

    def process_followups(self, max_send: int = 5) -> list[dict[str, Any]]:
        rows = (
            self.db.execute(
                select(m.Followup)
                .where(m.Followup.status == "PENDING")
                .order_by(m.Followup.scheduled_for.asc())
                .limit(max_send)
            )
            .scalars()
            .all()
        )
        sent: list[dict[str, Any]] = []
        for row in rows:
            row.status = "SENT"
            row.sent_at = self._now
            sent.append(
                {
                    "followup_id": row.id,
                    "application_id": row.application_id,
                    "message": row.message,
                }
            )
        self.db.commit()
        return sent