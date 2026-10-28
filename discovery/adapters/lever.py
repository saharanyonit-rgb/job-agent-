from __future__ import annotations

import os
from typing import Any

import httpx

from core.discovery.base import DiscoveryResult, IntegrationStatus, SourceAdapter
from core.jobs.normalize import RawJob

# Well-known public Lever orgs fetched by default (real job listings).
DEFAULT_LEVER_ORGS = [
    "netflix",
    "plaid",
    "visionml",
    "archdriver",
    "mistral",
]


class LeverAdapter(SourceAdapter):
    """Lever public postings API (api.lever.co/v0/postings)."""

    name = "lever"
    integration_status = IntegrationStatus.UNVALIDATED

    def _fetch(self, org: str) -> list[dict[str, Any]]:
        url = f"https://api.lever.co/v0/postings/{org}?mode=json"
        resp = httpx.get(url, timeout=20, headers={"User-Agent": "JobAgent/0.1"})
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _resolve_orgs(query: dict[str, Any]) -> list[str]:
        raw = (
            query.get("org")
            or query.get("orgs")
            or os.getenv("LEVER_ORG")
            or os.getenv("LEVER_ORGS")
            or ",".join(DEFAULT_LEVER_ORGS)
        )
        if isinstance(raw, (list, tuple)):
            items = [str(x).strip() for x in raw]
        else:
            items = [s.strip() for s in str(raw).replace("\n", ",").split(",")]
        return [i for i in items if i]

    def discover_jobs(self, query: dict[str, Any] | None = None) -> DiscoveryResult:
        query = query or {}
        orgs = self._resolve_orgs(query)
        if not orgs:
            return DiscoveryResult(jobs=[], source=self.name, status=self.integration_status, error="no org specified")
        jobs: list[RawJob] = []
        errors: list[str] = []
        for org in orgs:
            try:
                data = self._fetch(org)
            except httpx.HTTPError as exc:
                errors.append(f"{org}: {exc}")
                continue
            if not isinstance(data, list):
                continue
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
        if not jobs and errors:
            return DiscoveryResult(jobs=[], source=self.name, status=IntegrationStatus.FAILED, error="; ".join(errors))
        return DiscoveryResult(jobs=jobs, source=self.name, status=IntegrationStatus.PARTIAL, evidence={"count": len(jobs), "orgs": len(orgs)})

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