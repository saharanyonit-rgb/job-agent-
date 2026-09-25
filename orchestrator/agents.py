from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from job_agent.core.observability import ObservabilityService
from job_agent.email_service.classifier import EmailClassifier
from job_agent.interviews.service import InterviewService
from job_agent.interviews.preparation import InterviewPreparationAgent
from job_agent.candidates.service import CandidateService
from job_agent.database import models as m
from job_agent.calendar.service import CalendarService
from job_agent.notifications.engine import NotificationEngine
from job_agent.core.logging_config import get_logger

log = get_logger(__name__)


class DiscreteAgents:
    """Slim facades over reusable services so each agent keeps one responsibility."""

    def __init__(self, db: Session):
        self.db = db
        self.interviews = InterviewService(db)
        self.notifications = NotificationEngine(db)
        self.calendar = CalendarService(db)
        self.candidates = CandidateService(db)
        self.email_classifier = EmailClassifier()
        self.prep = InterviewPreparationAgent()

    def monitor_email(self, adapter: str = "mock") -> list[dict[str, Any]]:
        from job_agent.email_service.service import EmailService

        service = EmailService(self.db, adapter_name=adapter)
        processed = service.process_inbox()
        interviews_created: list[dict[str, Any]] = []
        for row in self.db.query(m.EmailMessage).filter(m.EmailMessage.processed.is_(False)).all():
            if row.classification.startswith("INTERVIEW") and row.extracted is not None:
                interview = self.interviews.create_from_email(row, row.extracted)
                if interview:
                    self.calendar.upsert_interview(interview)
                    self.notifications.notify(
                        "Interview scheduled",
                        f"{interview.role} at {interview.company} on {interview.interview_date or 'date TBD'}",
                        priority="CRITICAL",
                        dedup_key=f"interview-created:{row.external_id or row.id}",
                    )
                    interviews_created.append(interview.id)
                row.processed = True
            else:
                row.processed = True
        self.db.commit()
        return {"emails_processed": len(processed), "interviews_created": interviews_created}

    def verify_submissions(self) -> list[dict[str, Any]]:
        """Re-checks API/status endpoints where available. Honest: no evidence, no claim."""
        from sqlalchemy import select

        submissions = (
            self.db.execute(
                select(m.Application).where(m.Application.status.in_(["SUBMITTING"]))
            )
            .scalars()
            .all()
        )
        return [{"application_id": a.id, "status": a.status} for a in submissions]

    def prepare_interview(self, interview_id: int, candidate_id: int) -> dict[str, Any]:
        interview = self.db.get(m.Interview, interview_id)
        if not interview:
            raise ValueError("interview not found")
        facts = self.candidates.get_facts(candidate_id)
        job = None
        if interview.application_id:
            app = self.db.get(m.Application, interview.application_id)
            if app:
                job = self.db.get(m.Job, app.job_id)
        package = self.prep.prepare(interview, facts, job)
        interview.preparation = package
        self.db.commit()
        return package

    def when_is_my_interview(self) -> dict[str, Any]:
        """'When is my interview?' — only confirmed database facts."""
        interview = self.interviews.next_confirmed()
        if not interview:
            return {"message": "No confirmed interview is currently scheduled.", "interviews": []}
        return {
            "message": (
                f"Next confirmed interview: {interview.role} at {interview.company}\n"
                f"Round: {interview.round or 'TBD'}\n"
                f"Date/time: {interview.interview_date.strftime('%Y-%m-%d %H:%M') if interview.interview_date else 'TBD'} "
                f"({interview.timezone or 'UTC'})\n"
                f"Meeting: {interview.meeting_url or 'not shared'}"
            ),
            "interviews": [
                {
                    "company": interview.company,
                    "role": interview.role,
                    "round": interview.round,
                    "date": interview.interview_date.strftime("%Y-%m-%d") if interview.interview_date else None,
                    "time": interview.interview_date.strftime("%H:%M") if interview.interview_date else None,
                    "timezone": interview.timezone,
                    "meeting_url": interview.meeting_url,
                    "source": interview.source,
                }
            ],
        }