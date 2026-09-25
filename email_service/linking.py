from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_agent.database import models as m

LINK_STRONG = "STRONG"
LINK_MEDIUM = "MEDIUM"
LINK_AMBIGUOUS = "AMBIGUOUS"
LINK_NO_EVIDENCE = "NO_EVIDENCE"

TERMINAL_STATUSES = ("REJECTED", "CLOSED", "WITHDRAWN", "DUPLICATE", "EXPIRED")

# How far back (relative to the email) an application may still be considered
# a *live* link candidate. Older open applications are treated as stale.
MAX_LINK_AGE_DAYS = 180

# Diacritic/format noise that should never be treated as a company name.
_COMPANY_STOPWORDS = {
    "recruiting",
    "recruitment",
    "talent",
    "careers",
    "career",
    "hiring",
    "hr",
    "jobs",
    "job",
    "team",
    "people",
    "noreply",
    "no_reply",
    "no-reply",
    "donotreply",
    "mail",
    "mailbox",
    "notifications",
    "info",
    "support",
    "admin",
    "apps",
    "ats",
    "workday",
    "greenhouse",
    "lever",
}


@dataclass
class LinkDecision:
    """Outcome of evidence-based email -> application linking."""

    strength: str
    application: m.Application | None = None
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    candidates: list[m.Application] = field(default_factory=list)
    method: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "strength": self.strength,
            "confidence": self.confidence,
            "reasons": self.reasons,
            "candidate_application_ids": [a.id for a in self.candidates],
            "method": self.method,
            "application_id": self.application.id if self.application else None,
        }


class EvidenceLinker:
    """Evidence-based email -> application linking.

    Replaces the old heuristic "one open application for the matching company"
    with scored, auditable evidence. The resolver never *guesses*: if signals
    are missing or conflict it returns AMBIGUOUS / NO_EVIDENCE so the caller
    can hand off to a human instead of mutating application state.
    """

    def __init__(self, db: Session, *, max_age_days: int = MAX_LINK_AGE_DAYS):
        self.db = db
        self.max_age_days = max_age_days

    # ------------------------------------------------------------------ API

    def resolve(self, row: m.EmailMessage, now: datetime | None = None) -> LinkDecision:
        """Return a LinkDecision for a classified email.

        Order of evidence (strongest first):
          1. Explicit application / ATS reference id in the email text.
          2. Company hint + job role both present and matching one application.
          3. Company hint matching exactly one live application.
          4. Ambiguity (several plausible matches) -> AMBIGUOUS.
          5. No usable signals -> NO_EVIDENCE.
        """
        now = now or datetime.utcnow()
        if row.linked_application_id:
            app = self.db.get(m.Application, row.linked_application_id)
            if app:
                return LinkDecision(
                    LINK_STRONG,
                    application=app,
                    confidence=1.0,
                    reasons=["previously linked application id"],
                    method="existing_link",
                )
            row.linked_application_id = None
            self.db.commit()

        candidates = self._live_candidates(row, now)
        if not candidates:
            return LinkDecision(
                LINK_NO_EVIDENCE,
                confidence=0.0,
                reasons=["no live applications"],
                method="no_candidates",
            )

        # 1) Explicit reference ids (application id / ATS job id) in the email.
        explicit = self._match_explicit_ids(row, candidates)
        if explicit is not None:
            return explicit

        # 2) Company hint.
        company_apps = self._match_company(row, candidates)
        if not company_apps:
            return LinkDecision(
                LINK_NO_EVIDENCE,
                confidence=0.0,
                reasons=["no company signal matched"],
                method="no_company",
            )

        # 3) Role signal within company matches.
        role_apps = self._match_role(row, company_apps)
        if len(role_apps) == 1:
            app = role_apps[0]
            job = self._job_for(app)
            return LinkDecision(
                LINK_STRONG,
                application=app,
                confidence=0.95,
                reasons=[f"company and role match for '{job.company if job else app.job_id}'"],
                candidates=[app],
                method="company_and_role",
            )
        if len(role_apps) > 1:
            return self._ambiguous(role_apps, reason="multiple role matches")

        if len(company_apps) == 1:
            app = company_apps[0]
            job = self._job_for(app)
            return LinkDecision(
                LINK_MEDIUM,
                application=app,
                confidence=0.8,
                reasons=[f"company match '{job.company if job else app.job_id}'"],
                candidates=[app],
                method="company_only",
            )

        return self._ambiguous(company_apps, reason="multiple company matches")

    # ----------------------------------------------------------- evidence

    def _live_candidates(self, row: m.EmailMessage, now: datetime) -> list[m.Application]:
        oldest = (row.received_at or now) - timedelta(days=self.max_age_days)
        rows = (
            self.db.execute(
                select(m.Application)
                .join(m.Job)
                .where(
                    m.Application.status.notin_(TERMINAL_STATUSES),
                    m.Application.submitted_at.isnot(None),
                    m.Application.submitted_at >= oldest,
                )
                .order_by(m.Application.submitted_at.desc())
            )
        ).scalars().all()
        return list(rows)

    def _match_explicit_ids(self, row: m.EmailMessage, candidates: list[m.Application]) -> LinkDecision | None:
        """Match an application/ATS id mentioned in the email to the application."""
        text = self._searchable_text(row)
        if not text:
            return None
        matches: list[m.Application] = []
        reasons: list[str] = []
        for app in candidates:
            ids = [str(app.id)]
            if app.application_id_external:
                ids.append(str(app.application_id_external))
            job = self._job_for(app)
            if job and job.job_id_external:
                ids.append(str(job.job_id_external))
            for ref in ids:
                if ref and re.search(rf"\b{re.escape(ref)}\b", text):
                    matches.append(app)
                    reasons.append(f"explicit reference '{ref}'")
                    break
        if not matches:
            return None
        unique = {a.id: a for a in matches}
        if len(unique) == 1:
            app = next(iter(unique.values()))
            return LinkDecision(
                LINK_STRONG,
                application=app,
                confidence=0.97,
                reasons=reasons,
                candidates=[app],
                method="explicit_reference",
            )
        return self._ambiguous(list(unique.values()), reason="explicit reference matched multiple applications")

    def _match_company(self, row: m.EmailMessage, candidates: list[m.Application]) -> list[m.Application]:
        hints = self._company_hints(row)
        if not hints:
            return []
        return [a for a in candidates if self._company_matches(a, hints)]

    def _match_role(self, row: m.EmailMessage, candidates: list[m.Application]) -> list[m.Application]:
        text = self._searchable_text(row)
        if not text:
            return []
        needle = self._normalize(text)
        out = []
        for app in candidates:
            job = self._job_for(app)
            if not job:
                continue
            title_variants = []
            if job.title:
                title_variants.append(self._normalize(job.title))
            if job.normalized_title and self._normalize(job.normalized_title) not in title_variants:
                title_variants.append(self._normalize(job.normalized_title))
            if any(tv and tv in needle for tv in title_variants):
                out.append(app)
        return out

    def _ambiguous(self, apps: list[m.Application], *, reason: str) -> LinkDecision:
        return LinkDecision(
            LINK_AMBIGUOUS,
            confidence=0.0,
            reasons=[reason],
            candidates=[a for a in apps][:10],
            method="ambiguous",
        )

    # ---------------------------------------------------------- helpers

    @staticmethod
    def _searchable_text(row: m.EmailMessage) -> str:
        parts = [row.subject or "", row.body or ""]
        # Forwarded/replied email chains may still be valid; drop the transport
        # prefix noise but keep the content.
        return " ".join(parts)

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    def _company_hints(self, row: m.EmailMessage) -> list[str]:
        hints: list[str] = []

        # From address: registrable-ish domain, dropping the TLD and mailbox noise.
        if row.from_address:
            domain = (row.from_address or "").split("@")[-1].strip().lower()
            labels = [l for l in domain.split(".") if l]
            if len(labels) >= 2:
                labels = labels[:-1]  # drop TLD
            labels = [l for l in labels if l not in _COMPANY_STOPWORDS]
            if labels:
                hints.append(labels[-1])
                if len(labels) >= 2:
                    hints.append(".".join(labels))

        # From name: strip recruiting/talent/careers phrasing.
        if row.from_name:
            name = self._normalize(row.from_name)
            words = [w for w in name.split() if w not in _COMPANY_STOPWORDS]
            if words:
                hints.append(" ".join(words))
                if len(words) >= 2:
                    hints.append(words[0])

        # Subject can name the company, e.g. "Interview at Skidata".
        subject_norm = self._normalize(row.subject or "")
        m = re.search(r"\b(?:at|from|by)\s+([a-z][a-z0-9 .&-]{2,40})(?:\b)", subject_norm)
        if m:
            hints.append(m.group(1).strip())

        seen: set[str] = set()
        unique = []
        for h in hints:
            h = h.strip()
            if h and h not in seen and len(h) >= 2:
                seen.add(h)
                unique.append(h)
        return unique

    def _company_matches(self, app: m.Application, hints: list[str]) -> bool:
        job = self._job_for(app)
        if not job:
            return False
        company = self._normalize(job.company)
        if not company:
            return False
        for hint in hints:
            hint_norm = self._normalize(hint)
            if not hint_norm:
                continue
            if company == hint_norm or company in hint_norm or hint_norm in company:
                return True
        return False

    def _job_for(self, app: m.Application) -> m.Job | None:
        if getattr(app, "job", None) is not None:
            return app.job
        return self.db.get(m.Job, app.job_id) if app.job_id else None