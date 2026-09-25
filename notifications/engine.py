from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from job_agent.database import models as m

PRIORITIES = {"CRITICAL", "NORMAL", "LOW"}


class NotificationEngine:
    def __init__(self, db: Session):
        self.db = db

    def notify(
        self,
        title: str,
        body: str | None = None,
        priority: str = "NORMAL",
        candidate_id: int | None = None,
        channel: str = "inbox",
        dedup_key: str | None = None,
    ) -> m.Notification:
        """Create a notification, returning an existing one with the same
        dedup_key so repeated delivery (e.g. retried handoffs) never spams
        the inbox."""
        if priority not in PRIORITIES:
            priority = "NORMAL"
        if dedup_key:
            existing = (
                self.db.query(m.Notification)
                .filter(m.Notification.dedup_key == dedup_key)
                .order_by(m.Notification.created_at.desc())
                .first()
            )
            if existing:
                return existing
        row = m.Notification(
            candidate_id=candidate_id,
            priority=priority,
            channel=channel,
            title=title,
            body=body,
            dedup_key=dedup_key,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def unread(self, candidate_id: int | None = None) -> list[m.Notification]:
        query = self.db.query(m.Notification).filter(m.Notification.read.is_(False))
        if candidate_id:
            query = query.filter(m.Notification.candidate_id == candidate_id)
        return query.order_by(m.Notification.created_at.desc()).all()

    def mark_read(self, notification_id: int) -> None:
        row = self.db.get(m.Notification, notification_id)
        if row:
            row.read = True
            self.db.commit()