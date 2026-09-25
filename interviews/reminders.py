from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_agent.database import models as m
from job_agent.interviews.service import InterviewService


class ReminderService:
    def __init__(self, db: Session, intervals_minutes: list[int] | None = None):
        self.db = db
        self.interviews = InterviewService(db)
        self.intervals = intervals_minutes or [7 * 24 * 60, 24 * 60, 120, 30]
        self.sent_history: set[tuple[int, int]] = set()

    def due_reminders(self, now: datetime | None = None) -> list[tuple[m.Interview, int]]:
        now = now or datetime.utcnow()
        due: list[tuple[m.Interview, int]] = []
        for interview in self.interviews.list_interviews():
            if not interview.interview_date or interview.status != "SCHEDULED":
                continue
            delta = interview.interview_date - now
            for interval in self.intervals:
                if 0 <= delta.total_seconds() <= interval * 60 + 300:
                    if (interview.id, interval) not in self.sent_history:
                        due.append((interview, interval))
        return due

    def send_due(self, notifications: Any, now: datetime | None = None) -> list[dict[str, Any]]:
        sent: list[dict[str, Any]] = []
        for interview, interval in self.due_reminders(now):
            hours = interval / 60
            label = f"{int(hours)}h" if hours < 48 else f"{int(hours/24)}d"
            message = (
                f"Interview reminder ({label}): {interview.role} at {interview.company} "
                f"on {interview.interview_date.strftime('%Y-%m-%d %H:%M')} "
                f"({interview.timezone or 'UTC'})"
            )
            if interview.meeting_url:
                message += f"\nMeeting: {interview.meeting_url}"
            notifications.notify(
                "Interview reminder",
                message,
                priority="NORMAL",
                dedup_key=f"interview-reminder:{interview.id}:{interval}",
            )
            self.sent_history.add((interview.id, interval))
            sent.append({"interview_id": interview.id, "interval": label, "message": message})
        return sent