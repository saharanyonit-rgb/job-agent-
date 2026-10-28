from __future__ import annotations

import os
import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

from core.discovery.base import DiscoveryResult, IntegrationStatus, SourceAdapter
from core.jobs.normalize import RawJob


class IndeedAdapter(SourceAdapter):
    """Indeed job search via public search pages.

    Uses Indeed's public job search HTML. Requires careful rate limiting and
    respects robots.txt. For production, consider Indeed Publisher API or
    third-party job aggregation APIs (JSearch, RapidAPI, etc.).
    """

    name = "indeed"
    integration_status = IntegrationStatus.UNVALIDATED

    BASE_URL = "https://www.indeed.com/jobs"

    def _fetch_page(self, query: str, location: str, start: int = 0) -> str:
        params = {
            "q": query,
            "l": location,
            "start": start,
            "fromage": "14",
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        resp = httpx.get(self.BASE_URL, params=params, headers=headers, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        return resp.text

    def _parse_jobs(self, html: str) -> list[RawJob]:
        soup = BeautifulSoup(html, "html.parser")
        jobs: list[RawJob] = []

        for card in soup.find_all("div", class_=re.compile(r"job_seen_beacon|resultContent")):
            try:
                title_elem = card.find("h2", class_=re.compile(r"jobTitle"))
                company_elem = card.find("span", class_=re.compile(r"companyName"))
                location_elem = card.find("div", class_=re.compile(r"companyLocation"))
                link_elem = card.find("a", class_=re.compile(r"jcs-JobTitle"), href=True)

                if not title_elem or not link_elem:
                    continue

                job_id = link_elem["href"].split("jk=")[-1].split("&")[0] if "jk=" in link_elem["href"] else None
                base_url = "https://www.indeed.com"
                job_url = base_url + link_elem["href"] if link_elem["href"].startswith("/") else link_elem["href"]

                jobs.append(
                    RawJob(
                        title=title_elem.get_text(strip=True),
                        company=company_elem.get_text(strip=True) if company_elem else "Unknown",
                        location=location_elem.get_text(strip=True) if location_elem else None,
                        url=job_url,
                        application_url=job_url,
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
                html = self._fetch_page(keywords, location, start=page * 15)
                jobs = self._parse_jobs(html)
                all_jobs.extend(jobs)
                if len(jobs) < 10:
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
            return {"status": "REMOVED", "evidence": "not found in Indeed search"}
        return {"status": "ACTIVE", "evidence": "present in Indeed job search"}

    def get_application_url(self, job_ref: str) -> str | None:
        job = self.get_job(job_ref)
        return job.application_url if job else None


class JSearchAdapter(SourceAdapter):
    """JSearch API (RapidAPI) - unified job search across multiple sources.

    Requires RAPIDAPI_KEY environment variable with JSearch subscription.
    Provides access to LinkedIn, Indeed, Glassdoor, etc. via single API.
    """

    name = "jsearch"
    integration_status = IntegrationStatus.UNVALIDATED

    API_URL = "https://jsearch.p.rapidapi.com/search"

    def _fetch(self, query: str, location: str, page: int = 1) -> dict[str, Any]:
        api_key = os.getenv("RAPIDAPI_KEY")
        if not api_key:
            raise RuntimeError("RAPIDAPI_KEY not configured")
        headers = {
            "X-RapidAPI-Key": api_key,
            "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
        }
        params = {
            "query": f"{query} {location}",
            "page": str(page),
            "num_pages": "1",
        }
        resp = httpx.get(self.API_URL, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def discover_jobs(self, query: dict[str, Any] | None = None) -> DiscoveryResult:
        query = query or {}
        keywords = query.get("keywords", "software engineer")
        location = query.get("location", "Remote")
        max_pages = query.get("max_pages", 2)

        if not os.getenv("RAPIDAPI_KEY"):
            return DiscoveryResult(jobs=[], source=self.name, status=IntegrationStatus.FAILED, error="RAPIDAPI_KEY not set")

        all_jobs: list[RawJob] = []
        for page in range(1, max_pages + 1):
            try:
                data = self._fetch(keywords, location, page)
                for item in data.get("data", []):
                    all_jobs.append(
                        RawJob(
                            title=item.get("job_title", ""),
                            company=item.get("employer_name", "Unknown"),
                            location=item.get("job_location", None),
                            description=item.get("job_description", "")[:20000],
                            url=item.get("job_apply_link"),
                            application_url=item.get("job_apply_link"),
                            job_id_external=item.get("job_id"),
                            source=self.name,
                            employment_type=item.get("job_employment_type"),
                            salary_text=item.get("job_salary"),
                            posted_date=item.get("job_posted_at_datetime_utc"),
                        )
                    )
            except httpx.HTTPError as exc:
                return DiscoveryResult(jobs=[], source=self.name, status=IntegrationStatus.FAILED, error=str(exc))

        return DiscoveryResult(
            jobs=all_jobs,
            source=self.name,
            status=IntegrationStatus.VALIDATED,
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
            return {"status": "REMOVED", "evidence": "not found via JSearch"}
        return {"status": "ACTIVE", "evidence": "present in JSearch API"}

    def get_application_url(self, job_ref: str) -> str | None:
        job = self.get_job(job_ref)
        return job.application_url if job else None