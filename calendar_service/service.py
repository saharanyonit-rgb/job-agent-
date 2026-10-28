from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.database import models as m


class CalendarBackend(ABC):
    name = "base"
    integration_status = "UNVALIDATED"

    @abstractmethod
    def upsert_event(self, *, title: str, start: datetime, end: datetime, description: str | None = None, external_id: str | None = None, timezone: str | None = None) -> dict[str, Any]:
        ...

    @abstractmethod
    def fetch_events(self) -> list[dict[str, Any]]:
        ...


class MockCalendarBackend(CalendarBackend):
    name = "mock"
    integration_status = "VALIDATED"

    def __init__(self) -> None:
        self.events: dict[str, dict[str, Any]] = {}

    def upsert_event(self, *, title: str, start: datetime, end: datetime, description: str | None = None, external_id: str | None = None, timezone: str | None = None) -> dict[str, Any]:
        key = external_id or f"{title}|{start.isoformat()}"
        if key in self.events:
            self.events[key].update({"title": title, "start": start, "end": end, "description": description, "timezone": timezone})
            return {"updated": True, "id": key}
        self.events[key] = {"title": title, "start": start, "end": end, "description": description, "timezone": timezone}
        return {"updated": False, "id": key}

    def fetch_events(self) -> list[dict[str, Any]]:
        return [dict(e, id=k) for k, e in self.events.items()]


class GoogleCalendarBackend(CalendarBackend):
    name = "google"
    integration_status = "UNVALIDATED"


class OutlookCalendarBackend(CalendarBackend):
    name = "outlook"
    integration_status = "UNVALIDATED"


class CalendarService:
    def __init__(self, db: Session, backend: CalendarBackend | None = None):
        self.db = db
        self.backend = backend or MockCalendarBackend()

    def upsert_interview(self, interview: m.Interview) -> dict[str, Any]:
        if not interview.interview_date:
            return {"status": "skipped", "reason": "no confirmed datetime"}
        start = interview.interview_date
        end = start + timedelta(minutes=60)
        external_id = interview.external_id or f"interview:{interview.id}"
        result = self.backend.upsert_event(
            title=f"Interview: {interview.role} at {interview.company}",
            start=start,
            end=end,
            description=interview.meeting_url,
            external_id=external_id,
            timezone=interview.timezone,
        )
        row = self.db.execute(
            select(m.CalendarEvent).where(m.CalendarEvent.interview_id == interview.id)
        ).scalar_one_or_none()
        if row:
            row.external_id = result.get("id")
            row.start = start
            row.end = end
            row.title = f"Interview: {interview.role} at {interview.company}"
            row.description = interview.meeting_url
        else:
            self.db.add(
                m.CalendarEvent(
                    interview_id=interview.id,
                    provider=self.backend.name,
                    external_id=result.get("id"),
                    title=f"Interview: {interview.role} at {interview.company}",
                    start=start,
                    end=end,
                    timezone=interview.timezone,
                    description=interview.meeting_url,
                )
            )
        self.db.commit()
        return result