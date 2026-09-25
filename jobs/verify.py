from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from job_agent.core.logging_config import get_logger

log = get_logger(__name__)

VERIFICATION_STATUSES = ["ACTIVE", "EXPIRED", "REMOVED", "UNKNOWN", "DUPLICATE", "REJECTED"]


@dataclass
class VerificationResult:
    status: str
    verification_source: str
    evidence: dict[str, Any] = field(default_factory=dict)
    checked_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def ok(self) -> bool:
        return self.status == "ACTIVE"


class UrlChecker(Protocol):
    def check(self, url: str) -> dict[str, Any]: ...


class HttpUrlChecker:
    def check(self, url: str) -> dict[str, Any]:
        try:
            resp = httpx.get(url, timeout=15, follow_redirects=True, headers={"User-Agent": "JobAgent/0.1"})
            return {
                "status_code": resp.status_code,
                "ok": resp.status_code < 400,
                "final_url": str(resp.url),
                "evidence": f"HTTP {resp.status_code}",
            }
        except httpx.HTTPError as exc:
            return {"status_code": None, "ok": False, "error": str(exc), "evidence": f"request failed: {exc}"}


class JobVerifier:
    def __init__(self, url_checker: UrlChecker | None = None):
        self.url_checker = url_checker or HttpUrlChecker()

    def verify(
        self,
        *,
        title: str,
        company: str,
        location: str | None,
        url: str | None,
        application_url: str | None,
        source: str,
    ) -> VerificationResult:
        evidence: dict[str, Any] = {"title": title, "company": company, "source": source}

        if not title or not company:
            return VerificationResult("UNKNOWN", source, {"error": "missing title or company"})

        if url:
            check = self.url_checker.check(url)
            evidence["url_check"] = check
            if not check.get("ok"):
                code = check.get("status_code")
                if code in (404, 410):
                    return VerificationResult("REMOVED", "http", evidence)
                return VerificationResult("UNKNOWN", "http", evidence)

        if application_url:
            check = self.url_checker.check(application_url)
            evidence["application_url_check"] = check
            if not check.get("ok"):
                code = check.get("status_code")
                if code in (404, 410):
                    evidence["application_url_note"] = "application URL not reachable"
                else:
                    return VerificationResult("UNKNOWN", "http", evidence)

        evidence["verified_fields"] = {
            "location": location,
            "role": title,
            "company": company,
        }
        return VerificationResult("ACTIVE", source, evidence)


class CareerPageVerifier:
    """Verifies by confirming the job appears on the company career page listing."""

    def __init__(self, url_checker: UrlChecker | None = None):
        self.url_checker = url_checker or HttpUrlChecker()

    def verify_listing_contains(self, career_url: str, job_id: str | None, title: str) -> VerificationResult:
        check = self.url_checker.check(career_url)
        evidence = {"career_url": career_url, "url_check": check}
        if not check.get("ok"):
            status = "REMOVED" if check.get("status_code") in (404, 410) else "UNKNOWN"
            return VerificationResult(status, "career_page", evidence)
        body = check.get("body", "")
        if job_id and job_id in body:
            evidence["matched_by"] = "job_id"
            return VerificationResult("ACTIVE", "career_page", evidence)
        if title and title.lower() in body.lower():
            evidence["matched_by"] = "title"
            return VerificationResult("ACTIVE", "career_page", evidence)
        evidence["note"] = "job not found on listing page"
        return VerificationResult("UNKNOWN", "career_page", evidence)