from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_agent.database import models as m
from job_agent.email_service.adapters import get_email_adapter
from job_agent.email_service.classifier import EmailClassifier, InterviewExtractor, RawEmail


class EmailService:
    def __init__(self, db: Session, adapter_name: str = "mock"):
        self.db = db
        self.adapter = get_email_adapter(adapter_name)
        self.classifier = EmailClassifier()
        self.extractor = InterviewExtractor()

    def process_inbox(self) -> list[dict[str, Any]]:
        raw_messages = self.adapter.fetch_unread()
        processed: list[dict[str, Any]] = []
        for raw in raw_messages:
            row = self.db.execute(
                select(m.EmailMessage).where(
                    m.EmailMessage.external_id == raw.external_id
                )
            ).scalar_one_or_none()
            existed = row is not None
            if not row:
                classified = self.classifier.classify(raw)
                extracted = (
                    self.extractor.extract(raw)
                    if classified.classification.startswith("INTERVIEW") or classified.classification in ("OFFER", "ASSESSMENT")
                    else None
                )
                row = m.EmailMessage(
                    source=self.adapter.name,
                    external_id=raw.external_id,
                    from_address=raw.from_address,
                    from_name=raw.from_name,
                    to_address=raw.to_address,
                    subject=raw.subject,
                    body=raw.body,
                    classification=classified.classification,
                    classification_confidence=classified.confidence,
                    extracted=extracted,
                )
                self.db.add(row)
                self.db.commit()
                self.db.refresh(row)
            self.adapter.mark_processed(raw.external_id)
            processed.append(
                {
                    "id": row.id,
                    "classification": row.classification,
                    "confidence": row.classification_confidence,
                    "new": not existed,
                    "subject": row.subject,
                }
            )
        return processed

    def latest_classification(self) -> str:
        row = self.db.execute(
            select(m.EmailMessage).order_by(m.EmailMessage.received_at.desc()).limit(1)
        ).scalar_one_or_none()
        return row.classification if row else None