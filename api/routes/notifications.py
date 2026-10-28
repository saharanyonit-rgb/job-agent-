from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.database import models as m
from core.api.deps import get_db

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("")
def list_notifications(unread_only: bool = False, db: Session = Depends(get_db)) -> list[dict]:
    q = db.query(m.Notification).order_by(m.Notification.created_at.desc()).limit(50)
    if unread_only:
        q = q.filter(m.Notification.read.is_(False))
    return [
        {"id": n.id, "priority": n.priority, "title": n.title, "body": n.body, "read": n.read, "created_at": n.created_at.isoformat()}
        for n in q.all()
    ]


@router.post("/{notification_id}/read")
def mark_read(notification_id: int, db: Session = Depends(get_db)) -> dict:
    n = db.get(m.Notification, notification_id)
    if n:
        n.read = True
        db.commit()
    return {"ok": True}