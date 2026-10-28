from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import quote_plus

import httpx

from core.discovery.base import DiscoveryResult, IntegrationStatus, SourceAdapter
from core.jobs.normalize import RawJob


class RemoteJobBoardAdapter(SourceAdapter):
    """Aggregator job boards with open public APIs (no key required).

    Adds real listings from sources *other than* company ATS boards so the
    agent is not limited to a single website:

      - We Work Remotely (RSS feed)   https://weworkremotely.com/remote-jobs.rss
      - Remotive (public JSON API)    https://remotive.com/api/remote-jobs?search=...

    Queries are driven by the candidate's target keywords (override via
    env JOB_BOARD_QUERIES="civil engineer,project manager,...").
    """

    name = "job_boards"
    integration_status = IntegrationStatus.UNVALIDATED

    DEFAULT_QUERIES = [
        "civil engineer",
        "civil engineering",
        "project engineer construction",
        "site engineer",
    ]

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=25, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 JobAgent/0.1"}
        )

    @staticmethod
    def _queries(query: dict[str, Any]) -> list[str]:
        import os

        raw = query.get("queries") or os.getenv("JOB_BOARD_QUERIES")
        if isinstance(raw, (list, tuple)):
            return [str(x).strip() for x in raw if str(x).strip()]
        if raw:
            return [s.strip() for s in str(raw).split(",") if s.strip()]
        return list(RemoteJobBoardAdapter.DEFAULT_QUERIES)

    # ---- We Work Remotely (RSS) -------------------------------------------------
    def _fetch_wwr(self, client: httpx.Client) -> list[RawJob]:
        jobs: list[RawJob] = []
        try:
            resp = client.get("https://weworkremotely.com/remote-jobs.rss")
            resp.raise_for_status()
        except httpx.HTTPError:
            return jobs
        import xml.etree.ElementTree as ET

        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError:
            return jobs
        wanted = {q.lower() for q in self._queries({})}
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = (item.findtext("description") or "").strip()
            cat = item.findtext("category") or ""
            tl = title.lower()
            if not any(w in tl or w in (desc or "").lower()[:400] for w in wanted):
                continue
            jobs.append(
                RawJob(
                    title=title,
                    company=cat or "We Work Remotely",
                    location="Remote",
                    description=desc[:5000],
                    url=link,
                    application_url=link,
                    job_id_external=hashlib.sha1(link.encode()).hexdigest()[:16],
                    source=self.name,
                    employment_type="Remote",
                    extra={"board": "weworkremotely"},
                )
            )
        return jobs

    # ---- Remotive (JSON API) ----------------------------------------------------
    def _fetch_remotive(self, client: httpx.Client, queries: list[str]) -> list[RawJob]:
        jobs: list[RawJob] = []
        seen: set[str] = set()
        for q in queries:
            try:
                resp = client.get(f"https://remotive.com/api/remote-jobs?search={quote_plus(q)}")
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError):
                continue
            for item in data.get("jobs", []):
                url = item.get("url", "")
                if not url or url in seen:
                    continue
                seen.add(url)
                jobs.append(
                    RawJob(
                        title=item.get("title", ""),
                        company=item.get("company_name", "Remotive"),
                        location=item.get("candidate_required_location") or "Remote",
                        description=(item.get("description") or "")[:5000],
                        url=url,
                        application_url=item.get("application_url") or url,
                        job_id_external=str(item.get("id") or hashlib.sha1(url.encode()).hexdigest()[:16]),
                        source=self.name,
                        employment_type=item.get("employment_type"),
                        posted_date=None,
                        salary_text=item.get("salary"),
                        extra={"board": "remotive", "tags": item.get("tags")},
                    )
                )
        return jobs

    def discover_jobs(self, query: dict[str, Any] | None = None) -> DiscoveryResult:
        query = query or {}
        queries = self._queries(query)
        client = self._client()
        jobs: list[RawJob] = []
        errors: list[str] = []
        try:
            jobs += self._fetch_wwr(client)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"weworkremotely: {exc}")
        try:
            jobs += self._fetch_remotive(client, queries)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"remotive: {exc}")
        finally:
            client.close()
        status = (
            IntegrationStatus.VALIDATED
            if jobs and not errors
            else IntegrationStatus.PARTIAL
            if jobs
            else IntegrationStatus.FAILED
        )
        return DiscoveryResult(
            jobs=jobs,
            source=self.name,
            status=status,
            error="; ".join(errors) if errors and not jobs else None,
            evidence={"count": len(jobs), "boards": ["weworkremotely", "remotive"]},
        )

    def get_job(self, job_ref: str) -> RawJob | None:
        result = self.discover_jobs()
        for job in result.jobs:
            if job.job_id_external == job_ref:
                return job
        return None

    def verify_job(self, job_ref: str) -> dict[str, Any]:
        job = self.get_job(job_ref)
        if not job or not job.url:
            return {"status": "UNKNOWN", "evidence": "not found in board feeds"}
        try:
            resp = httpx.get(job.url, timeout=15, follow_redirects=True,
                             headers={"User-Agent": "Mozilla/5.0 JobAgent/0.1"})
            if resp.status_code == 200:
                return {"status": "ACTIVE", "evidence": f"HTTP 200 at {job.url}"}
            return {"status": "REMOVED", "evidence": f"HTTP {resp.status_code}"}
        except httpx.HTTPError as exc:
            return {"status": "UNKNOWN", "evidence": str(exc)}

    def get_application_url(self, job_ref: str) -> str | None:
        job = self.get_job(job_ref)
        return job.application_url if job else None
