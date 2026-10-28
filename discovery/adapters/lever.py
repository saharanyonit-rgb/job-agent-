from __future__ import annotations

from typing import Any

import httpx

from core.discovery.base import DiscoveryResult, IntegrationStatus, SourceAdapter
from core.jobs.normalize import RawJob


class LeverAdapter(SourceAdapter):
    """Lever public postings API (api.lever.co/v0/postings)."""

    name = "lever"
    integration_status = IntegrationStatus.UNVALIDATED

    def _fetch(self, org: str) -> list[dict[str, Any]]:
        url = f"https://api.lever.co/v0/postings/{org}?mode=json"
        resp = httpx.get(url, timeout=20, headers={"User-Agent": "JobAgent/0.1"})
        resp.raise_for_status()
        return resp.json()

    def discover_jobs(self, query: dict[str, Any] | None = None) -> DiscoveryResult:
        query = query or {}
        org = query.get("org") or query.get("company") or ""
        if not org:
            return DiscoveryResult(jobs=[], source=self.name, status=self.integration_status, error="no org specified")
        try:
            data = self._fetch(org)
        except httpx.HTTPError as exc:
            return DiscoveryResult(jobs=[], source=self.name, status=IntegrationStatus.FAILED, error=str(exc))
        jobs: list[RawJob] = []
        for item in data:
            cats = item.get("categories") or {}
            location = (item.get("lists") or {}).get("Location") if isinstance(item.get("lists"), dict) else None
            loc = location if isinstance(location, str) else cats.get("location")
            apply = item.get("applyUrl") or item.get("hostedUrl")
            jobs.append(
                RawJob(
                    title=item.get("text", ""),
                    company=org,
                    location=loc,
                    description=(item.get("descriptionPlain") or "")[:20000],
                    url=item.get("hostedUrl"),
                    application_url=apply,
                    job_id_external=item.get("id"),
                    source=self.name,
                    employment_type=cats.get("commitment"),
                )
            )
        return DiscoveryResult(jobs=jobs, source=self.name, status=IntegrationStatus.PARTIAL, evidence={"count": len(jobs)})

    def get_job(self, job_ref: str) -> RawJob | None:
        result = self.discover_jobs()
        for job in result.jobs:
            if job.job_id_external == job_ref:
                return job
        return None

    def verify_job(self, job_ref: str) -> dict[str, Any]:
        job = self.get_job(job_ref)
        if not job:
            return {"status": "REMOVED", "evidence": "not found on Lever"}
        return {"status": "ACTIVE", "evidence": "present in Lever postings API"}

    def get_application_url(self, job_ref: str) -> str | None:
        job = self.get_job(job_ref)
        return job.application_url if job else None