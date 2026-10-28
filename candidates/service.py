from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.candidates.models import (
    CandidateFactCollection,
    ProvenanceFact,
)
from core.candidates.resume_parser import (
    ResumeParseError,
    ResumeParsed,
    ResumeText,
    ResumeParser,
    extract_text,
)
from core.candidates.skills import CandidateSkillService
from core.database import models as m
from core.config import get_settings

FACT_KEYS = [
    "name", "email", "phone", "location", "work_authorization", "visa_status",
    "years_of_experience", "current_title", "notice_period", "current_ctc",
    "expected_ctc", "employment_type", "remote_preference", "hybrid_preference",
    "relocation_preference", "summary", "skills", "technologies", "certifications",
    "education", "projects", "previous_employers", "achievements", "languages",
    "preferred_industries", "excluded_roles", "portfolio_links", "preferred_roles",
    "availability",
]


class CandidateService:
    def __init__(self, db: Session):
        self.db = db

    def get_or_create(self, email: str | None = None) -> m.Candidate:
        if email:
            existing = self.db.execute(select(m.Candidate).where(m.Candidate.email == email)).scalar_one_or_none()
            if existing:
                return existing
        candidate = m.Candidate()
        if email:
            candidate.email = email
        self.db.add(candidate)
        self.db.commit()
        self.db.refresh(candidate)
        return candidate

    def get_facts(self, candidate_id: int) -> CandidateFactCollection:
        rows = self.db.execute(
            select(m.CandidateFact).where(m.CandidateFact.candidate_id == candidate_id)
        ).scalars().all()
        coll = CandidateFactCollection()
        for row in rows:
            coll.facts[row.key] = ProvenanceFact(
                key=row.key,
                value=_parse_value(row.value),
                source=row.source,
                verified=row.verified,
                evidence=row.evidence,
            )
        return coll

    def confirm_fact(self, candidate_id: int, key: str, value: str) -> None:
        import json as _json

        if isinstance(value, (list, dict)):
            value = _json.dumps(value)
        else:
            value = str(value)
        existing = self.db.execute(
            m.CandidateFact.__table__.select().where(
                m.CandidateFact.candidate_id == candidate_id, m.CandidateFact.key == key
            )
        ).first()
        if existing:
            row = self.db.get(m.CandidateFact, existing.id)
            row.value = value
            row.source = "candidate_confirmed"
            row.verified = True
        else:
            row = m.CandidateFact(
                candidate_id=candidate_id,
                key=key,
                value=value,
                source="candidate_confirmed",
                verified=True,
            )
            self.db.add(row)
        self.db.flush()
        if key == "skills":
            CandidateSkillService(self.db).upsert_by_fact(candidate_id, row)
        self.db.commit()

    def import_facts(self, candidate_id: int, facts: CandidateFactCollection) -> None:
        for key, fact in facts.facts.items():
            if not self.db.execute(
                m.CandidateFact.__table__.select().where(
                    m.CandidateFact.candidate_id == candidate_id, m.CandidateFact.key == key
                )
            ).first():
                row = m.CandidateFact(
                    candidate_id=candidate_id,
                    key=key,
                    value=_serialize_value(fact.value),
                    source=fact.source,
                    verified=fact.verified,
                    evidence=fact.evidence,
                )
                self.db.add(row)
                if key == "skills":
                    self.db.flush()
                    CandidateSkillService(self.db).upsert_by_fact(candidate_id, row)
        self.db.commit()

    def set_preference(self, candidate_id: int, key: str, value: str) -> None:
        existing = self.db.execute(
            m.CandidatePreference.__table__.select().where(
                m.CandidatePreference.candidate_id == candidate_id,
                m.CandidatePreference.key == key,
            )
        ).first()
        if existing:
            row = self.db.get(m.CandidatePreference, existing.id)
            row.value = value
        else:
            self.db.add(m.CandidatePreference(candidate_id=candidate_id, key=key, value=value))
        self.db.commit()

    def get_preferences(self, candidate_id: int) -> dict[str, str]:
        rows = self.db.execute(
            select(m.CandidatePreference).where(m.CandidatePreference.candidate_id == candidate_id)
        ).scalars().all()
        return {r.key: r.value for r in rows}

    # --- resumes ---

    def upload_resume(self, candidate_id: int, original_path: str | Path, original_name: str) -> m.Resume:
        original_path = Path(original_path)
        ext = original_path.suffix.lower()
        if ext not in (".pdf", ".docx", ".txt"):
            raise ResumeParseError(f"Unsupported file type: {ext}")

        text = extract_text(original_path)
        storage = Path(get_settings().storage_dir) / "resumes" / str(candidate_id)
        storage.mkdir(parents=True, exist_ok=True)
        dest_name = "resume_original" + ext
        dest = storage / dest_name
        shutil.copyfile(original_path, dest)

        parsed = ResumeParser().parse(text)
        resume = m.Resume(
            candidate_id=candidate_id,
            filename=dest_name,
            original_name=original_name,
            path=str(dest),
            file_type=ext.lstrip("."),
            is_original=True,
            content_hash=text.content_hash,
            parsed=parsed.to_dict(),
        )
        self.db.add(resume)
        self.db.commit()
        self.db.refresh(resume)

        self._apply_parsed_facts(candidate_id, parsed, resume.filename)
        return resume

    def create_version(self, candidate_id: int, original: m.Resume, payload: dict[str, Any], name: str) -> m.Resume:
        storage = Path(get_settings().storage_dir) / "resumes" / str(candidate_id)
        storage.mkdir(parents=True, exist_ok=True)
        write_needed = payload.get("write_file", False)
        text = payload.get("text", "")
        if write_needed and text:
            ext = Path(original.filename).suffix
            dest = storage / f"{name}{ext}"
            dest.write_text(text, encoding="utf-8")
            path = str(dest)
        else:
            path = original.path
        version = m.Resume(
            candidate_id=candidate_id,
            filename=f"{name}{Path(original.filename).suffix}",
            original_name=f"{name}{Path(original.filename).suffix}",
            path=path,
            file_type=original.file_type,
            is_original=False,
            content_hash=hashlib.sha256(path.encode()).hexdigest(),
            parsed=payload.get("parsed", original.parsed),
        )
        self.db.add(version)
        self.db.commit()
        self.db.refresh(version)
        return version

    def latest_resume(self, candidate_id: int) -> m.Resume | None:
        return self.db.execute(
            select(m.Resume)
            .where(m.Resume.candidate_id == candidate_id)
            .order_by(m.Resume.is_original.desc(), m.Resume.id.desc())
            .limit(1)
        ).scalar_one_or_none()

    # --- internal ---

    def _apply_parsed_facts(self, candidate_id: int, parsed: ResumeParsed, evidence: str) -> None:
        coll = CandidateFactCollection()
        if parsed.years_of_experience is not None:
            coll.set("years_of_experience", parsed.years_of_experience, "resume", verified=False, evidence=evidence)
        if parsed.current_title:
            coll.set("current_title", parsed.current_title, "resume", verified=False, evidence=evidence)
        coll.set("skills", parsed.skills or [], "resume", verified=False, evidence=evidence)
        coll.set("technologies", parsed.technologies or [], "resume", verified=False, evidence=evidence)
        coll.set("certifications", parsed.certifications or [], "resume", verified=False, evidence=evidence)
        coll.set("education", parsed.education or [], "resume", verified=False, evidence=evidence)
        coll.set("projects", parsed.projects or [], "resume", verified=False, evidence=evidence)
        coll.set("previous_employers", parsed.previous_employers or [], "resume", verified=False, evidence=evidence)
        coll.set("achievements", parsed.achievements or [], "resume", verified=False, evidence=evidence)
        if parsed.summary:
            coll.set("summary", parsed.summary, "resume", verified=False, evidence=evidence)
        self.import_facts(candidate_id, coll)


def _serialize_value(value: Any) -> str | None:
    import json

    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def _parse_value(value: str | None) -> Any:
    import json

    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value