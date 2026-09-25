from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from job_agent.api.deps import JobAgentOrchestrator, get_db
from job_agent.database import models as m

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class DiscoverIn(BaseModel):
    queries: dict = {}


@router.post("/discover")
def discover(body: DiscoverIn | None = None, db: Session = Depends(get_db)) -> dict:
    orch = JobAgentOrchestrator(db)
    return orch.discover()


@router.post("/verify")
def verify(db: Session = Depends(get_db)) -> dict:
    return JobAgentOrchestrator(db).verify()


@router.post("/dedupe")
def dedupe(db: Session = Depends(get_db)) -> dict:
    return JobAgentOrchestrator(db).dedupe()


@router.get("")
def list_jobs(status: str | None = None, db: Session = Depends(get_db)) -> list[dict]:
    q = db.query(m.Job)
    if status:
        q = q.filter(m.Job.verification_status == status)
    jobs = q.order_by(m.Job.id.desc()).limit(200).all()
    return [_job_dict(j) for j in jobs]


@router.get("/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)) -> dict:
    job = db.get(m.Job, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return _job_dict(job)


def _job_dict(j: m.Job) -> dict:
    return {
        "id": j.id,
        "title": j.title,
        "company": j.company,
        "location": j.location,
        "remote_status": j.remote_status,
        "employment_type": j.employment_type,
        "experience_min": j.experience_min,
        "experience_max": j.experience_max,
        "skills": j.skills or [],
        "salary_min": j.salary_min,
        "salary_max": j.salary_max,
        "salary_currency": j.salary_currency,
        "source": j.source,
        "url": j.url,
        "application_url": j.application_url,
        "verification_status": j.verification_status,
        "status": j.status,
        "fingerprint": j.fingerprint,
    }