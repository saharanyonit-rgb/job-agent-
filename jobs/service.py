from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.database import models as m
from core.jobs.dedupe import find_duplicate_groups, merge_duplicates
from core.jobs.normalize import NormalizedJob, normalize_job, RawJob
from core.jobs.verify import JobVerifier, VerificationResult
from core.logging_config import get_logger

log = get_logger(__name__)


class JobService:
    def __init__(self, db: Session, verifier: JobVerifier | None = None):
        self.db = db
        self.verifier = verifier or JobVerifier()

    def upsert_from_raw(self, raw: RawJob) -> tuple[m.Job, bool]:
        """Insert or update a job from raw input. Returns (job, created)."""
        normalized = normalize_job(raw)
        existing = self.db.execute(
            select(m.Job).where(
                m.Job.fingerprint == normalized.fingerprint,
                m.Job.merged_into_id.is_(None),
            )
        ).scalar_one_or_none()
        if existing is None and normalized.job_id_external and normalized.source:
            existing = self.db.execute(
                select(m.Job).where(
                    m.Job.job_id_external == normalized.job_id_external,
                    m.Job.source == normalized.source,
                )
            ).scalar_one_or_none()

        if existing:
            if existing.content_hash and normalized.content_hash != existing.content_hash:
                self.db.add(
                    m.JobEvent(
                        job_id=existing.id,
                        event_type="JOB_UPDATED",
                        data={
                            "old_hash": existing.content_hash,
                            "new_hash": normalized.content_hash,
                            "title": normalized.title,
                        },
                    )
                )
                existing.verification_status = "UNKNOWN"
            existing.title = normalized.title
            existing.normalized_title = normalized.normalized_title
            existing.company = normalized.company
            existing.location = normalized.location
            existing.country = normalized.country
            existing.remote_status = normalized.remote_status
            existing.employment_type = normalized.employment_type
            existing.experience_min = normalized.experience_min
            existing.experience_max = normalized.experience_max
            existing.skills = normalized.skills
            existing.salary_min = normalized.salary_min
            existing.salary_max = normalized.salary_max
            existing.salary_currency = normalized.salary_currency
            existing.description = normalized.description
            existing.job_id_external = normalized.job_id_external
            existing.url = normalized.url
            existing.application_url = normalized.application_url
            existing.content_hash = normalized.content_hash
            existing.fingerprint = normalized.fingerprint
            self.db.commit()
            self.db.refresh(existing)
            return existing, False

        job = m.Job(
            title=normalized.title,
            normalized_title=normalized.normalized_title,
            company=normalized.company,
            location=normalized.location,
            country=normalized.country,
            remote_status=normalized.remote_status,
            employment_type=normalized.employment_type,
            experience_min=normalized.experience_min,
            experience_max=normalized.experience_max,
            skills=normalized.skills,
            salary_min=normalized.salary_min,
            salary_max=normalized.salary_max,
            salary_currency=normalized.salary_currency,
            description=normalized.description,
            job_id_external=normalized.job_id_external,
            source=normalized.source,
            url=normalized.url,
            application_url=normalized.application_url,
            posted_date=normalized.posted_date,
            fingerprint=normalized.fingerprint,
            content_hash=normalized.content_hash,
            status="DISCOVERED",
            last_checked=datetime.utcnow(),
        )
        self.db.add(job)
        self.db.flush()
        self.db.add(m.JobEvent(job_id=job.id, event_type="JOB_DISCOVERED", data={"source": normalized.source}))
        self.db.commit()
        self.db.refresh(job)
        return job, True

    def verify_job(self, job: m.Job) -> VerificationResult:
        result = self.verifier.verify(
            title=job.title,
            company=job.company,
            location=job.location,
            url=job.url,
            application_url=job.application_url,
            source=job.source,
        )
        job.verification_status = result.status
        job.verified_at = result.checked_at
        job.verification_source = result.verification_source
        job.verification_evidence = str(result.evidence)[:4000]
        job.last_checked = datetime.utcnow()
        if result.status == "ACTIVE" and job.status == "DISCOVERED":
            job.status = "VERIFIED"
        elif result.status in ("REMOVED", "EXPIRED"):
            job.status = "CLOSED"
        self.db.add(
            m.JobEvent(
                job_id=job.id,
                event_type=f"JOB_{result.status}",
                data={"evidence": result.evidence},
            )
        )
        self.db.commit()
        return result

    def dedupe(self) -> list[m.Job]:
        jobs = self.db.execute(
            select(m.Job).where(m.Job.merged_into_id.is_(None))
        ).scalars().all()
        canonical_jobs: list[m.Job] = []
        for group in find_duplicate_groups(jobs):
            canonical, _others = merge_duplicates(group)
            if canonical:
                canonical_jobs.append(canonical)
        self.db.commit()
        return canonical_jobs

    def eligible_jobs(self) -> list[m.Job]:
        return (
            self.db.execute(
                select(m.Job).where(
                    m.Job.verification_status == "ACTIVE",
                    m.Job.merged_into_id.is_(None),
                    m.Job.status.notin_(["CLOSED", "DUPLICATE", "REJECTED"]),
                )
            )
            .scalars()
            .all()
        )

    def jobs_to_verify(self) -> list[m.Job]:
        """Jobs that have not yet been verified (or need re-verification)."""
        return (
            self.db.execute(
                select(m.Job).where(
                    m.Job.verification_status != "ACTIVE",
                    m.Job.merged_into_id.is_(None),
                    m.Job.status.notin_(["CLOSED", "DUPLICATE", "REJECTED"]),
                )
            )
            .scalars()
            .all()
        )