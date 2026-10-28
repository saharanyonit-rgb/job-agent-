from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.api.deps import CandidateService, get_db
from core.config import get_settings
from core.database import models as m
from core.candidates.resume_parser import ResumeParseError

router = APIRouter(prefix="/api/candidates", tags=["candidates"])

DEFAULT_CANDIDATE_ID = 1


class FactConfirmIn(BaseModel):
    key: str
    value: str


class PreferenceIn(BaseModel):
    key: str
    value: str


class CandidateProfileOut(BaseModel):
    id: int
    facts: dict
    preferences: dict
    resumes: list


@router.post("")
def create_candidate(email: str | None = None, db: Session = Depends(get_db)) -> dict:
    cand = CandidateService(db).get_or_create(email)
    return {"id": cand.id}


@router.post("/{candidate_id}/resume")
async def upload_resume(
    candidate_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    filename = file.filename or "resume"
    suffix = Path(filename).suffix.lower()
    if suffix not in (".pdf", ".docx", ".txt"):
        raise HTTPException(400, "Only PDF, DOCX, TXT resumes supported")
    tmp = Path(get_settings().storage_dir) / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    dest = tmp / f"upload_{int(time.time())}{suffix}"
    dest.write_bytes(await file.read())
    try:
        resume = CandidateService(db).upload_resume(candidate_id, dest, filename)
    except ResumeParseError as exc:
        raise HTTPException(400, str(exc))
    return {"id": resume.id, "filename": resume.filename, "parsed": resume.parsed}


@router.get("/{candidate_id}")
def profile(candidate_id: int, db: Session = Depends(get_db)) -> CandidateProfileOut:
    svc = CandidateService(db)
    facts = svc.get_facts(candidate_id)
    prefs = svc.get_preferences(candidate_id)
    resumes = db.query(m.Resume).filter(m.Resume.candidate_id == candidate_id).all()
    return CandidateProfileOut(
        id=candidate_id,
        facts=facts.to_dict(),
        preferences=prefs,
        resumes=[{"id": r.id, "filename": r.filename, "is_original": r.is_original} for r in resumes],
    )


@router.post("/{candidate_id}/confirm-fact")
def confirm_fact(candidate_id: int, body: FactConfirmIn, db: Session = Depends(get_db)) -> dict:
    CandidateService(db).confirm_fact(candidate_id, body.key, body.value)
    return {"ok": True, "key": body.key, "source": "candidate_confirmed"}


@router.post("/{candidate_id}/preferences")
def set_preference(candidate_id: int, body: PreferenceIn, db: Session = Depends(get_db)) -> dict:
    CandidateService(db).set_preference(candidate_id, body.key, body.value)
    return {"ok": True}