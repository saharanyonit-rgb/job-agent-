from __future__ import annotations

import abc
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from core.ai.service import chain

EMAIL_CLASSES = [
    "APPLICATION_RECEIVED",
    "APPLICATION_ACKNOWLEDGED",
    "REJECTION",
    "INTERVIEW_INVITATION",
    "INTERVIEW_RESCHEDULE",
    "INTERVIEW_CANCELLED",
    "ASSESSMENT",
    "OFFER",
    "RECRUITER_RESPONSE",
    "NEXT_ROUND_INVITATION",
    "HIRING_FREEZE_ROLE_CLOSED",
    "SUSPICIOUS_EMAIL",
    "RECRUITER_MESSAGE",
    "FOLLOW_UP",
    "OTHER",
]


@dataclass
class RawEmail:
    external_id: str
    from_address: str | None = None
    from_name: str | None = None
    to_address: str | None = None
    subject: str = ""
    body: str = ""
    received_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ClassifiedEmail:
    email: RawEmail
    classification: str = "OTHER"
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "external_id": self.email.external_id,
            "from_address": self.email.from_address,
            "from_name": self.email.from_name,
            "subject": self.email.subject,
            "classification": self.classification,
            "confidence": self.confidence,
            "received_at": self.email.received_at.isoformat(),
        }


class EmailClassifier:
    def __init__(self, ai=chain):
        self.ai = ai

    def classify(self, email: RawEmail) -> ClassifiedEmail:
        text = f"Subject: {email.subject}\nFrom: {email.from_name or email.from_address}\n{email.body[:4000]}"
        try:
            label, confidence = self.ai.classify(text, EMAIL_CLASSES)
        except Exception:
            label, confidence = "OTHER", 0.2
        if label not in EMAIL_CLASSES:
            label = "OTHER"
        return ClassifiedEmail(email=email, classification=label, confidence=confidence)


class InterviewExtractor:
    """Extracts interview details from an email invitation, only as stated."""

    def __init__(self, ai=chain):
        self.ai = ai

    def extract(self, email: RawEmail) -> dict[str, Any]:
        text = f"Subject: {email.subject}\nFrom: {email.from_name or email.from_address}\n{email.body[:6000]}"
        schema = {
            "company": "company or sender domain/name if company not named",
            "role": "job role title if mentioned",
            "round": "interview round name if mentioned (e.g. Technical, HR, Screening)",
            "date": "ISO date YYYY-MM-DD if explicitly stated",
            "time": "HH:MM in 24h if explicitly stated",
            "timezone": "explicit timezone string if stated",
            "meeting_url": "https URL if explicitly present",
            "duration_minutes": "integer duration if stated",
            "instructions": "short instructions if present",
            "interviewer_name": "interviewer's name/first name at the bottom or in the email if present, otherwise empty",
            "interviewer_role": "interviewer's title/role if present, otherwise empty",
            "interview_type": "type of interview if stated (e.g. technical, behavioral, HR, onsite, zoom)",
            "stage": "interview stage number or label if stated (e.g. round 1, first round)",
        }
        try:
            out = self.ai.extract(text, schema)
        except Exception:
            out = {}
        out = self._regex_backfill(out, email)
        return out

    def _regex_backfill(self, out: dict[str, Any], email: RawEmail) -> dict[str, Any]:
        subject = email.subject or ""
        body = email.body or ""
        # URLs
        if not out.get("meeting_url"):
            m = re.search(r"https?://(?:meet\.google\.com|teams\.microsoft\.com|zoom\.us|webex)[^\s\"'<>]+", body)
            if m:
                out["meeting_url"] = m.group(0).rstrip(").")
        # ISO date
        if not out.get("date"):
            m = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", body)
            if m:
                out["date"] = m.group(1)
        # "on <day> ... at HH:MM"
        if not out.get("time"):
            m = re.search(r"\b(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)?", body)
            if m:
                hh, mm = int(m.group(1)), m.group(2)
                meridiem = (m.group(3) or "").upper()
                if meridiem == "PM" and hh < 12:
                    hh += 12
                elif meridiem == "AM" and hh == 12:
                    hh = 0
                out["time"] = f"{hh:02d}:{mm}"
        # Timezone: "Timezone: Asia/Kolkata"
        if not out.get("timezone"):
            m = re.search(r"\bTime[ -]?Zone\s*:?\s*([A-Za-z]+/[A-Za-z_\-]+)", body, re.I)
            if m:
                out["timezone"] = m.group(1)
        # Round: "Round: Technical Interview"
        if not out.get("round"):
            m = re.search(r"\bRound\s*:?\s*([A-Za-z][\w .&/-]{1,60})", body, re.I)
            if m:
                out["round"] = m.group(1).strip().rstrip(".")
        # Company: "interview at <Company>" in body, else "<role> at <Company>" in subject
        if not out.get("company"):
            m = re.search(r"[Ii]nterview\s+at\s+([A-Z][A-Za-z0-9& .\-]{2,40}?)(?:[\.,]|$)", body)
            if not m:
                m = re.search(r"\bat\s+([A-Z][A-Za-z0-9& .\-]{2,40}?)(?:[\.,]|$)", subject)
            if m:
                out["company"] = m.group(1).strip().rstrip("., ")
        # Role: "Interview Invitation - <role> at <Company>", or "<role> position"
        if not out.get("role"):
            m = re.search(r"-\s*([A-Za-z][\w .\-]{2,60}?)\s+at\s+", subject)
            if not m:
                m = re.search(r"([A-Za-z][\w .\-]{2,60}?)\s+position\b", subject, re.I)
            if m:
                out["role"] = m.group(1).strip().rstrip("., ")
        return out