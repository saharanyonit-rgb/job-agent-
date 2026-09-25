from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_agent.database import models as m


@dataclass
class InterviewDraft:
    company: str
    role: str
    round: str | None = None
    interview_date: datetime | None = None
    timezone: str | None = None
    meeting_url: str | None = None
    source: str = "unknown"
    application_id: int | None = None
    external_id: str | None = None


class InterviewService:
    def __init__(self, db: Session):
        self.db = db

    def create_from_email(self, email_row: m.EmailMessage, extracted: dict[str, Any]) -> m.Interview | None:
        """Create/update an Interview only from explicitly extracted facts."""
        company = extracted.get("company") or (email_row.from_name or (email_row.from_address or "").split("@")[-1])
        role = extracted.get("role") or ""
        if not role:
            return None
        date_str = extracted.get("date")
        time_str = extracted.get("time")
        interview_date = None
        if date_str:
            try:
                interview_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
                if time_str:
                    hh, mm = time_str.split(":")
                    interview_date = interview_date.replace(hour=int(hh), minute=int(mm))
            except (ValueError, TypeError):
                interview_date = None

        meeting_url = extracted.get("meeting_url")
        external_id = email_row.external_id

        existing = None
        if external_id:
            existing = self.db.execute(
                select(m.Interview).where(m.Interview.external_id == external_id)
            ).scalar_one_or_none()

        if existing:
            # Update in place; do not duplicate
            if interview_date:
                existing.interview_date = interview_date
            if meeting_url:
                existing.meeting_url = meeting_url
            existing.round = extracted.get("round") or existing.round
            existing.timezone = extracted.get("timezone") or existing.timezone
            self._apply_intelligence(existing, extracted)
            self.db.commit()
            self.db.refresh(existing)
            return existing

        interview = m.Interview(
            application_id=None,
            company=company,
            role=role,
            round=extracted.get("round"),
            interview_date=interview_date,
            timezone=extracted.get("timezone"),
            meeting_url=meeting_url,
            source="email",
            external_id=external_id,
        )
        self._apply_intelligence(interview, extracted)
        self.db.add(interview)
        self.db.commit()
        self.db.refresh(interview)
        return interview

    def create(self, draft: InterviewDraft) -> m.Interview:
        if draft.external_id:
            existing = self.db.execute(
                select(m.Interview).where(m.Interview.external_id == draft.external_id)
            ).scalar_one_or_none()
            if existing:
                existing.company = draft.company
                existing.role = draft.role
                existing.round = draft.round
                existing.interview_date = draft.interview_date
                existing.timezone = draft.timezone
                existing.meeting_url = draft.meeting_url
                existing.source = draft.source
                self.db.commit()
                self.db.refresh(existing)
                return existing

        interview = m.Interview(
            application_id=draft.application_id,
            company=draft.company,
            role=draft.role,
            round=draft.round,
            interview_date=draft.interview_date,
            timezone=draft.timezone,
            meeting_url=draft.meeting_url,
            source=draft.source,
            external_id=draft.external_id,
        )
        self.db.add(interview)
        self.db.commit()
        self.db.refresh(interview)
        return interview

    def next_confirmed(self, candidate_id: int | None = None, as_of: datetime | None = None) -> m.Interview | None:
        """Returns the next confirmed upcoming interview. Never fabricates."""
        as_of = as_of or datetime.utcnow()
        rows = (
            self.db.execute(
                select(m.Interview)
                .where(m.Interview.status == "SCHEDULED")
                .order_by(m.Interview.interview_date.asc())
            )
            .scalars()
            .all()
        )
        upcoming = [r for r in rows if r.interview_date and r.interview_date >= as_of]
        if upcoming:
            return upcoming[0]
        return None

    def list_interviews(self) -> list[m.Interview]:
        return list(self.db.execute(select(m.Interview).order_by(m.Interview.interview_date.desc())).scalars().all())

    def record_outcome(self, interview: m.Interview, outcome: str, next_step: str | None = None, interviewer: str | None = None) -> m.Interview:
        """Phase 6: capture interview outcome + next step + interviewer (verified or NULL)."""
        if interviewer:
            interview.interviewer_name = interviewer
        interview.outcome = outcome
        if next_step:
            interview.next_step = next_step
        if outcome in ("PASSED", "SELECTED", "NEXT_ROUND"):
            interview.status = "INTERVIEW_COMPLETED"
        elif outcome in ("REJECTED", "FAILED"):
            interview.status = "COMPLETED"
        self.db.commit()
        self.db.refresh(interview)
        return interview

    def _apply_intelligence(self, interview: m.Interview, extracted: dict[str, Any]) -> None:
        """Capture only what was explicitly stated about the interviewer/stage/type."""
        if extracted.get("interviewer_name"):
            interview.interviewer_name = str(extracted["interviewer_name"])[:255]
        if extracted.get("interviewer_role"):
            interview.interviewer_role = str(extracted["interviewer_role"])[:255]
        if extracted.get("interview_type"):
            interview.interview_type = str(extracted["interview_type"])[:50]
        if extracted.get("stage"):
            interview.stage = str(extracted["stage"])[:50]