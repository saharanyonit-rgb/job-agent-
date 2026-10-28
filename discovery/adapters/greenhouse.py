from __future__ import annotations

import os
from typing import Any

import httpx

from core.discovery.base import DiscoveryResult, IntegrationStatus, SourceAdapter
from core.jobs.normalize import RawJob


class GreenhouseAdapter(SourceAdapter):
    """Greenhouse public job board API (boards-api.greenhouse.io)."""

    name = "greenhouse"
    integration_status = IntegrationStatus.UNVALIDATED

    def _fetch(self, board_token: str) -> dict[str, Any]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true"
        resp = httpx.get(url, timeout=20, headers={"User-Agent": "JobAgent/0.1"})
        resp.raise_for_status()
        return resp.json()

    def discover_jobs(self, query: dict[str, Any] | None = None) -> DiscoveryResult:
        query = query or {}
        board = query.get("board") or os.getenv("GREENHOUSE_BOARD_TOKEN", "")
        if not board:
            return DiscoveryResult(
                jobs=[],
                source=self.name,
                status=self.integration_status,
                error="no board token configured",
            )
        try:
            data = self._fetch(board)
        except httpx.HTTPError as exc:
            return DiscoveryResult(jobs=[], source=self.name, status=IntegrationStatus.FAILED, error=str(exc))
        jobs: list[RawJob] = []
        for item in data.get("jobs", []):
            location = (item.get("location") or {}).get("name")
            jobs.append(
                RawJob(
                    title=item.get("title", ""),
                    company=query.get("company") or board,
                    location=location,
                    description=item.get("content"),
                    url=item.get("absolute_url"),
                    application_url=item.get("absolute_url"),
                    job_id_external=str(item.get("id")),
                    source=self.name,
                    employment_type=(item.get("departments") or [{}])[0].get("name"),
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
            return {"status": "REMOVED", "evidence": "not found in board"}
        return {"status": "ACTIVE", "evidence": "present in Greenhouse board API"}

    def get_application_url(self, job_ref: str) -> str | None:
        job = self.get_job(job_ref)
        return job.application_url if job else None