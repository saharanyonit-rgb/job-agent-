from __future__ import annotations

import os
from typing import Any

import httpx

from core.discovery.base import DiscoveryResult, IntegrationStatus, SourceAdapter
from core.jobs.normalize import RawJob

# Well-known public Greenhouse boards fetched by default (real job listings).
# Includes civil/construction-industry boards relevant to the candidate profile.
DEFAULT_GREENHOUSE_BOARDS = [
    "stripe",
    "duolingo",
    "airbnb",
    "gitlab",
    "cloudflare",
    "datadog",
    "mongodb",
    "hashicorp",
    "twilio",
    "roblox",
    # Civil / construction engineering (verified live public boards)
    "olsson",       # Olsson Engineering – 380+ live civil/structural/geotech roles
    "civil",        # Civility (land development)
]

# Boards that returned 404 during last live check (kept out of defaults):
# aecom, jacobs, kiewit, stantec, arup, hntb, parsons, icf, hdr, kimley-horn,
# blackandveatch, tutorperini, burnsmcdonnell, wsp, gannettfleming, kleinfelder


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
        boards = self._resolve_boards(query)
        if not boards:
            return DiscoveryResult(
                jobs=[],
                source=self.name,
                status=self.integration_status,
                error="no board token configured",
            )
        jobs: list[RawJob] = []
        errors: list[str] = []
        for board in boards:
            company = query.get("company") if len(boards) == 1 else None
            try:
                data = self._fetch(board)
            except httpx.HTTPError as exc:
                errors.append(f"{board}: {exc}")
                continue
            for item in data.get("jobs", []):
                location = (item.get("location") or {}).get("name")
                jobs.append(
                    RawJob(
                        title=item.get("title", ""),
                        company=company or board,
                        location=location,
                        description=item.get("content"),
                        url=item.get("absolute_url"),
                        application_url=item.get("absolute_url"),
                        job_id_external=str(item.get("id")),
                        source=self.name,
                        employment_type=(item.get("departments") or [{}])[0].get("name"),
                    )
                )
        if not jobs and errors:
            return DiscoveryResult(jobs=[], source=self.name, status=IntegrationStatus.FAILED, error="; ".join(errors))
        return DiscoveryResult(jobs=jobs, source=self.name, status=IntegrationStatus.PARTIAL, evidence={"count": len(jobs), "boards": len(boards)})

    @staticmethod
    def _resolve_boards(query: dict[str, Any]) -> list[str]:
        """Board tokens from query, GREENHOUSE_BOARD_TOKEN, or a default set of
        well-known public Greenhouse boards."""
        raw = (
            query.get("board")
            or query.get("boards")
            or os.getenv("GREENHOUSE_BOARD_TOKEN")
            or os.getenv("GREENHOUSE_BOARDS")
            or ",".join(DEFAULT_GREENHOUSE_BOARDS)
        )
        if isinstance(raw, (list, tuple)):
            items = [str(x).strip() for x in raw]
        else:
            items = [s.strip() for s in str(raw).replace("\n", ",").split(",")]
        return [i for i in items if i]

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