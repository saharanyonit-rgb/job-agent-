from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_agent.applications.pipeline import (
    ACKNOWLEDGED_LABELS,
    FREEZE_LABELS,
    OFFER_LABELS,
    REJECTION_LABELS,
    RESPONSE_LABELS,
    ApplicationPipeline,
)
from job_agent.database import models as m
from job_agent.core.config import get_settings
from job_agent.email_service.linking import EvidenceLinker, LINK_NO_EVIDENCE, LINK_AMBIGUOUS, LinkDecision


class EmailOutcomeService:
    """Phase 5: turn classified emails into application outcomes.

    Rules:
      - Emails are linked to applications with scored, auditable evidence
        (explicit reference id > company+role > company) via EvidenceLinker.
      - AMBIGUOUS or NO_EVIDENCE links never mutate application state; they
        produce a HANDOFF_REQUIRED notification so a human decides.
      - Low-confidence or suspicious emails never mutate application state.
      - Every state change is recorded as an ApplicationEvent with
        source="email", evidence=<email external_id>, confidence=<score>.
    """

    def __init__(self, db: Session):
        self.db = db
        self.pipeline = ApplicationPipeline(db)
        self.linker = EvidenceLinker(db)

    def process_latest(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = (
            self.db.execute(
                select(m.EmailMessage)
                .order_by(m.EmailMessage.received_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        return [self.process_row(row) for row in rows]

    def process_row(self, row: m.EmailMessage) -> dict[str, Any]:
        conf = float(row.classification_confidence or 0.0)
        min_conf = float(get_settings().min_evidence_confidence)

        link = self._link_application(row)
        if link.strength == LINK_AMBIGUOUS:
            self._persist_link(row, link)
            self._notify_handoff(
                row,
                f"Ambiguous email-to-application link ({len(link.candidates)} candidates). "
                "Review and confirm which application this email refers to.",
            )
            return {"email_id": row.id, "action": "LINK_AMBIGUOUS", "classification": row.classification, "link": link.to_dict()}

        if link.strength == LINK_NO_EVIDENCE or link.application is None:
            self._persist_link(row, link)
            return {"email_id": row.id, "action": "UNLINKED", "classification": row.classification, "link": link.to_dict()}

        application = link.application
        self._persist_link(row, link)

        if row.classification == "SUSPICIOUS_EMAIL":
            self._notify_handoff(row, "Suspicious email detected; do not follow embedded links or share credentials.")
            self.pipeline.record_event(
                application,
                event_type="SUSPICIOUS_EMAIL",
                source="email",
                evidence=row.external_id,
                confidence=conf,
                detail={"subject": row.subject},
            )
            return {"email_id": row.id, "action": "NOTIFIED_HUMAN", "classification": row.classification}

        if conf < min_conf:
            self._notify_handoff(
                row,
                f"Low-confidence email classification '{row.classification}' ({conf:.2f}). Review before acting.",
            )
            self.pipeline.record_event(
                application,
                event_type="LOW_CONFIDENCE",
                source="email",
                evidence=row.external_id,
                confidence=conf,
                detail={"classification": row.classification, "subject": row.subject},
            )
            return {"email_id": row.id, "action": "LOW_CONFIDENCE", "classification": row.classification}

        action = self._apply_outcome(application, row, conf)
        return {"email_id": row.id, "action": action, "classification": row.classification, "confidence": conf, "link": link.to_dict()}

    def _apply_outcome(self, app: m.Application, row: m.EmailMessage, conf: float) -> str:
        label = row.classification
        evidence = row.external_id
        detail = {"subject": row.subject, "from": row.from_name or row.from_address}

        if label in ACKNOWLEDGED_LABELS:
            if app.status == "SUBMITTED":
                self.pipeline.transition(app, "ACKNOWLEDGED", source="email", evidence=evidence, confidence=conf, detail=detail)
            return "ACKNOWLEDGED"

        if label in REJECTION_LABELS:
            if app.status not in ("REJECTED", "CLOSED"):
                app.rejection_reason = (row.subject or "")[:500]
                self.pipeline.transition(app, "REJECTED", source="email", evidence=evidence, confidence=conf, detail=detail)
            return "REJECTED"

        if label in OFFER_LABELS:
            if app.status == "OFFER":
                return "ALREADY_OFFER"
            if app.status in ("SUBMITTED", "ACKNOWLEDGED", "RECEIVED", "ASSESSMENT", "RECRUITER_RESPONSE", "INTERVIEW_SCHEDULED", "INTERVIEW_COMPLETED"):
                self.pipeline.transition(app, "OFFER", source="email", evidence=evidence, confidence=conf, detail=detail)
            return "OFFER"

        if label in RESPONSE_LABELS:
            if app.status in ("SUBMITTED", "ACKNOWLEDGED", "RECEIVED"):
                self.pipeline.transition(app, "RECRUITER_RESPONSE", source="email", evidence=evidence, confidence=conf, detail=detail)
            else:
                self.pipeline.record_event(app, "RECRUITER_RESPONSE", source="email", evidence=evidence, confidence=conf, detail=detail)
            return "RECRUITER_RESPONSE"

        if label == "NEXT_ROUND_INVITATION":
            self.pipeline.record_event(app, "NEXT_ROUND_INVITATION", source="email", evidence=evidence, confidence=conf, detail=detail)
            return "NEXT_ROUND"

        if label in FREEZE_LABELS:
            if app.status in ("SUBMITTED", "ACKNOWLEDGED", "RECEIVED", "RECRUITER_RESPONSE"):
                self.pipeline.transition(app, "EXPIRED", source="email", evidence=evidence, confidence=conf, detail=detail)
            else:
                self.pipeline.record_event(app, "HIRING_FREEZE_ROLE_CLOSED", source="email", evidence=evidence, confidence=conf, detail=detail)
            job = self.db.get(m.Job, app.job_id) if app.job_id else None
            if job:
                job.status = "CLOSED"
                self.db.commit()
            return "ROLE_CLOSED"

        if label == "ASSESSMENT":
            if app.status in ("SUBMITTED", "ACKNOWLEDGED", "RECEIVED", "RECRUITER_RESPONSE"):
                self.pipeline.transition(app, "ASSESSMENT", source="email", evidence=evidence, confidence=conf, detail=detail)
            return "ASSESSMENT"

        # Everything else (INTERVIEW_*, FOLLOW_UP, OTHER, RECRUITER_MESSAGE) is handled
        # by the interview/monitor agents; record the event without changing state.
        self.pipeline.record_event(app, label, source="email", evidence=evidence, confidence=conf, detail=detail)
        return label

    def _link_application(self, row: m.EmailMessage) -> LinkDecision:
        return self.linker.resolve(row)

    def _persist_link(self, row: m.EmailMessage, link: LinkDecision) -> None:
        if link.application is not None and link.strength != LINK_NO_EVIDENCE:
            row.linked_application_id = link.application.id
        row.link_strength = link.strength
        row.link_confidence = link.confidence
        row.link_evidence = link.to_dict()
        self.db.commit()

    def _notify_handoff(self, row: m.EmailMessage, body: str) -> None:
        existing = (
            self.db.query(m.Notification)
            .filter(m.Notification.dedup_key == self._handoff_dedup_key(row))
            .first()
        )
        if existing:
            return
        self.db.add(
            m.Notification(
                priority="HIGH",
                channel="inbox",
                title=f"Review required: {row.subject or 'email'}",
                body=body,
                dedup_key=self._handoff_dedup_key(row),
            )
        )
        self.db.commit()

    @staticmethod
    def _handoff_dedup_key(row: m.EmailMessage) -> str:
        return f"email-handoff:{row.external_id or row.id}"