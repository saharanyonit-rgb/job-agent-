from __future__ import annotations

from typing import Any

import httpx

from core.discovery.base import DiscoveryResult, IntegrationStatus, SourceAdapter
from core.jobs.normalize import RawJob


class MockATSAdapter(SourceAdapter):
    """Adapter for the bundled Mock ATS used in end-to-end tests.

    Talks to the mock ATS over HTTP (default http://127.0.0.1:9000) using its
    JSON jobs endpoint. Status becomes VALIDATED only after a successful fetch
    (i.e. real testing against a running mock ATS).
    """

    name = "mock_ats"
    integration_status = IntegrationStatus.UNVALIDATED
    base_url: str = "http://127.0.0.1:9000"

    def __init__(self, base_url: str | None = None, http: httpx.Client | None = None):
        if base_url:
            self.base_url = base_url
        self._http = http

    def _client(self) -> httpx.Client:
        return self._http or httpx.Client(timeout=15, follow_redirects=True)

    def discover_jobs(self, query: dict[str, Any] | None = None) -> DiscoveryResult:
        query = query or {}
        base = query.get("base_url") or self.base_url
        try:
            resp = self._client().get(f"{base}/api/jobs")
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            return DiscoveryResult(
                jobs=[], source=self.name, status=self.integration_status, error=str(exc)
            )
        jobs = [
            RawJob(
                title=item["title"],
                company=item.get("company", "MockCorp"),
                location=item.get("location"),
                description=item.get("description", ""),
                url=f"{base}/jobs/{item['id']}",
                application_url=f"{base}/jobs/{item['id']}/apply",
                job_id_external=str(item["id"]),
                source=self.name,
                employment_type=item.get("employment_type"),
                salary_text=item.get("salary_text"),
            )
            for item in data.get("jobs", data if isinstance(data, list) else [])
        ]
        status = IntegrationStatus.VALIDATED if jobs else self.integration_status
        return DiscoveryResult(jobs=jobs, source=self.name, status=status, evidence={"count": len(jobs)})

    def get_job(self, job_ref: str) -> RawJob | None:
        base = self.base_url
        try:
            resp = self._client().get(f"{base}/api/jobs/{job_ref}")
            resp.raise_for_status()
            item = resp.json()
        except Exception:
            return None
        return RawJob(
            title=item["title"],
            company=item.get("company", "MockCorp"),
            location=item.get("location"),
            description=item.get("description", ""),
            url=f"{base}/jobs/{item['id']}",
            application_url=f"{base}/jobs/{item['id']}/apply",
            job_id_external=str(item["id"]),
            source=self.name,
            employment_type=item.get("employment_type"),
            salary_text=item.get("salary_text"),
        )

    def verify_job(self, job_ref: str) -> dict[str, Any]:
        job = self.get_job(job_ref)
        if not job:
            return {"status": "REMOVED", "evidence": "job not found on mock ATS"}
        return {"status": "ACTIVE", "evidence": f"job {job_ref} served by mock ATS API"}

    def get_application_url(self, job_ref: str) -> str | None:
        job = self.get_job(job_ref)
        return job.application_url if job else None