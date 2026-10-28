from __future__ import annotations

import os
import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

from core.discovery.base import DiscoveryResult, IntegrationStatus, SourceAdapter
from core.jobs.normalize import RawJob


class LinkedInAdapter(SourceAdapter):
    """LinkedIn job search via public search pages (rate-limited, no API key required).

    Uses LinkedIn's public job search HTML pages. Requires careful rate limiting.
    For production, consider LinkedIn Partner API or third-party providers.
    """

    name = "linkedin"
    integration_status = IntegrationStatus.UNVALIDATED

    BASE_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

    def _fetch_page(self, keywords: str, location: str, start: int = 0) -> str:
        params = {
            "keywords": keywords,
            "location": location,
            "start": start,
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        resp = httpx.get(self.BASE_URL, params=params, headers=headers, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        return resp.text

    def _parse_jobs(self, html: str) -> list[RawJob]:
        soup = BeautifulSoup(html, "html.parser")
        jobs: list[RawJob] = []
        for li in soup.find_all("li"):
            try:
                title_elem = li.find("h3", class_=re.compile(r"base-search-card__title"))
                company_elem = li.find("h4", class_=re.compile(r"base-search-card__subtitle"))
                location_elem = li.find("span", class_=re.compile(r"job-search-card__location"))
                link_elem = li.find("a", class_=re.compile(r"base-card__full-link"), href=True)

                if not title_elem or not link_elem:
                    continue

                job_id = link_elem["href"].split("/")[-2] if "/jobs/view/" in link_elem["href"] else None
                jobs.append(
                    RawJob(
                        title=title_elem.get_text(strip=True),
                        company=company_elem.get_text(strip=True) if company_elem else "Unknown",
                        location=location_elem.get_text(strip=True) if location_elem else None,
                        url=link_elem["href"],
                        application_url=link_elem["href"],
                        job_id_external=job_id,
                        source=self.name,
                    )
                )
            except Exception:
                continue
        return jobs

    def discover_jobs(self, query: dict[str, Any] | None = None) -> DiscoveryResult:
        query = query or {}
        keywords = query.get("keywords", "software engineer")
        location = query.get("location", "Remote")
        max_pages = query.get("max_pages", 2)

        all_jobs: list[RawJob] = []
        for page in range(max_pages):
            try:
                html = self._fetch_page(keywords, location, start=page * 25)
                jobs = self._parse_jobs(html)
                all_jobs.extend(jobs)
                if len(jobs) < 25:
                    break
            except httpx.HTTPError as exc:
                return DiscoveryResult(jobs=[], source=self.name, status=IntegrationStatus.FAILED, error=str(exc))

        return DiscoveryResult(
            jobs=all_jobs,
            source=self.name,
            status=IntegrationStatus.PARTIAL,
            evidence={"count": len(all_jobs), "keywords": keywords, "location": location},
        )

    def get_job(self, job_ref: str) -> RawJob | None:
        result = self.discover_jobs()
        for job in result.jobs:
            if job.job_id_external == job_ref:
                return job
        return None

    def verify_job(self, job_ref: str) -> dict[str, Any]:
        job = self.get_job(job_ref)
        if not job:
            return {"status": "REMOVED", "evidence": "not found in LinkedIn search"}
        return {"status": "ACTIVE", "evidence": "present in LinkedIn job search"}

    def get_application_url(self, job_ref: str) -> str | None:
        job = self.get_job(job_ref)
        return job.application_url if job else None


class LinkedInGuestAdapter(SourceAdapter):
    """Alternative: LinkedIn guest API endpoint (used by frontend)."""

    name = "linkedin_guest"
    integration_status = IntegrationStatus.UNVALIDATED

    def discover_jobs(self, query: dict[str, Any] | None = None) -> DiscoveryResult:
        return DiscoveryResult(
            jobs=[], source=self.name, status=IntegrationStatus.UNAVAILABLE,
            error="linkedin_guest adapter not implemented; enable ALLOW_FAKE_JOBS or use 'linkedin'",
        )

    def get_job(self, job_ref: str) -> RawJob | None:
        return None

    def verify_job(self, job_ref: str) -> dict[str, Any]:
        return {"status": "UNKNOWN", "evidence": "guest adapter not implemented"}

    def get_application_url(self, job_ref: str) -> str | None:
        return None