from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup

from core.discovery.base import DiscoveryResult, IntegrationStatus, SourceAdapter
from core.jobs.normalize import RawJob, extract_years, parse_salary


@dataclass
class CareerPageRecord:
    company: str
    career_url: str
    last_hash: str | None = None
    last_checked: float = 0.0
    seen_jobs: dict[str, str] = field(default_factory=dict)  # external_id -> content_hash


_CAREER_PAGES: list[CareerPageRecord] = []


def add_career_page(company: str, career_url: str) -> CareerPageRecord:
    for rec in _CAREER_PAGES:
        if rec.career_url == career_url:
            return rec
    rec = CareerPageRecord(company=company, career_url=career_url)
    _CAREER_PAGES.append(rec)
    return rec


def list_career_pages() -> list[CareerPageRecord]:
    return list(_CAREER_PAGES)


class CompanyCareerAdapter(SourceAdapter):
    """Monitors company career pages for job listings and material changes."""

    name = "company_career"
    integration_status = IntegrationStatus.PARTIAL

    def __init__(self, http: httpx.Client | None = None):
        self._http = http
        self._cache: dict[str, tuple[float, str]] = {}
        self.cache_ttl = 1800.0

    def _client(self) -> httpx.Client:
        return self._http or httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": "JobAgent/0.1"})

    def _fetch(self, url: str) -> str:
        now = time.time()
        if url in self._cache and now - self._cache[url][0] < self.cache_ttl:
            return self._cache[url][1]
        resp = self._client().get(url)
        resp.raise_for_status()
        self._cache[url] = (now, resp.text)
        return resp.text

    def discover_jobs(self, query: dict[str, Any] | None = None) -> DiscoveryResult:
        query = query or {}
        url = query.get("url") or (query.get("company") and self._url_for_company(str(query.get("company"))))
        jobs: list[RawJob] = []
        events: list[dict[str, Any]] = []
        try:
            for rec in _CAREER_PAGES:
                if query.get("url") and rec.career_url != query["url"]:
                    continue
                if query.get("company") and query["company"].lower() not in rec.company.lower():
                    continue
                html = self._fetch(rec.career_url)
                found = self._parse_listing(rec, html)
                jobs.extend(found)
        except httpx.HTTPError as exc:
            return DiscoveryResult(jobs=[], source=self.name, status=IntegrationStatus.FAILED, error=str(exc))
        except Exception as exc:
            return DiscoveryResult(jobs=[], source=self.name, status=IntegrationStatus.FAILED, error=str(exc))

        return DiscoveryResult(
            jobs=jobs,
            source=self.name,
            status=self.integration_status,
            evidence={"events": events, "count": len(jobs)},
        )

    def _url_for_company(self, company: str) -> str | None:
        for rec in _CAREER_PAGES:
            if company.lower() in rec.company.lower():
                return rec.career_url
        return None

    def _parse_listing(self, rec: CareerPageRecord, html: str) -> list[RawJob]:
        soup = BeautifulSoup(html, "html.parser")
        jobs: list[RawJob] = []
        # Generic: anchors whose href suggests a job posting
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = " ".join(a.get_text(" ", strip=True).split())
            if not text or len(text) > 300:
                continue
            if not any(token in href.lower() for token in ["/job", "/jobs", "/career", "/position", "/opening", "/vacanc"]):
                if not a.get("class") or not any("job" in str(c).lower() for c in a.get("class", [])):
                    continue
            job_id = href.rstrip("/").split("/")[-1][:100]
            description = ""
            parent = a.find_parent(["li", "div", "tr", "article"])
            if parent:
                description = parent.get_text(" ", strip=True)[:4000]
            full_url = href if href.startswith("http") else _absolutize(rec.career_url, href)
            exp_min, exp_max = extract_years(description)
            jobs.append(
                RawJob(
                    title=text,
                    company=rec.company,
                    location=_extract_location(description),
                    description=description,
                    url=full_url,
                    application_url=full_url,
                    job_id_external=job_id,
                    source=self.name,
                    salary_text=None,
                )
            )
        # Change detection against previously seen jobs
        for job in jobs:
            h = hashlib.sha256((job.description or job.title).encode()).hexdigest()
            prev = rec.seen_jobs.get(job.job_id_external or job.url or job.title)
            if prev and prev != h:
                # material change -> JOB_UPDATED will be raised by JobService on upsert
                pass
            rec.seen_jobs[job.job_id_external or job.url or job.title] = h
        rec.last_checked = time.time()
        rec.last_hash = hashlib.sha256(html.encode()).hexdigest()
        # de-dupe
        seen: set[str] = set()
        uniq: list[RawJob] = []
        for j in jobs:
            key = j.job_id_external or j.url or j.title
            if key not in seen:
                seen.add(key)
                uniq.append(j)
        return uniq

    def get_job(self, job_ref: str) -> RawJob | None:
        result = self.discover_jobs()
        for job in result.jobs:
            if job.job_id_external == job_ref:
                return job
        return None

    def verify_job(self, job_ref: str) -> dict[str, Any]:
        job = self.get_job(job_ref)
        if not job:
            return {"status": "REMOVED", "evidence": "job no longer listed on career page"}
        return {"status": "ACTIVE", "evidence": "job present on career page listing", "url": job.url}

    def get_application_url(self, job_ref: str) -> str | None:
        job = self.get_job(job_ref)
        return job.application_url if job else None


def _absolutize(base: str, href: str) -> str:
    from urllib.parse import urljoin

    return urljoin(base, href)


def _extract_location(text: str) -> str | None:
    import re

    m = re.search(
        r"(Bengaluru|Bangalore|Mumbai|Pune|Hyderabad|Chennai|Gurgaon|Noida|Delhi|Ahmedabad|Remote|India|United States|New York|London)",
        text,
        re.I,
    )
    return m.group(1) if m else None