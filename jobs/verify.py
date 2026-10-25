from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from job_agent.core.logging_config import get_logger

log = get_logger(__name__)

VERIFICATION_STATUSES = ["ACTIVE", "EXPIRED", "REMOVED", "UNKNOWN", "DUPLICATE", "REJECTED"]

# Patterns indicating a job is no longer available
CLOSED_PATTERNS = [
    r"position\s+(?:has\s+been\s+)?filled",
    r"job\s+(?:has\s+been\s+)?filled",
    r"no\s+longer\s+available",
    r"no\s+longer\s+accepting",
    r"position\s+closed",
    r"job\s+closed",
    r"requisition\s+closed",
    r"this\s+position\s+is\s+(?:closed|filled)",
    r"this\s+job\s+is\s+(?:closed|filled)",
    r"role\s+(?:has\s+been\s+)?filled",
    r"role\s+closed",
    r"vacancy\s+filled",
    r"vacancy\s+closed",
    r"application\s+closed",
    r"hiring\s+complete",
    r"position\s+expired",
    r"job\s+expired",
]

# Patterns indicating anti-bot / CAPTCHA / interstitial pages
ANTIBOT_PATTERNS = [
    r"captcha",
    r"checking\s+your\s+browser",
    r"verify\s+you\s+are\s+human",
    r"please\s+wait",
    r"cloudflare",
    r"ray\s+id",
    r"ddos\s+protection",
    r"access\s+denied",
    r"rate\s+limit",
    r"too\s+many\s+requests",
    r"please\s+complete\s+the\s+security\s+check",
    r"recaptcha",
    r"hcaptcha",
    r"turnstile",
]


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
    """HTTP-based URL checker that returns full response for analysis."""

    def __init__(self, follow_redirects: bool = True, timeout: float = 15.0):
        self.follow_redirects = follow_redirects
        self.timeout = timeout
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout,
                follow_redirects=self.follow_redirects,
                headers={"User-Agent": "JobAgent/0.1 (job verification)"},
            )
        return self._client

    def check(self, url: str) -> dict[str, Any]:
        """Check a URL and return detailed response for analysis."""
        try:
            resp = self.client.get(url)
            return {
                "status_code": resp.status_code,
                "ok": resp.status_code < 400,
                "final_url": str(resp.url),
                "initial_url": url,
                "evidence": f"HTTP {resp.status_code}",
                "body": resp.text,
                "headers": dict(resp.headers),
                "redirect_chain": [str(r.url) for r in resp.history] if resp.history else [],
            }
        except httpx.InvalidURL as exc:
            return {"status_code": None, "ok": False, "error": f"invalid URL: {exc}", "evidence": f"invalid URL: {exc}", "body": ""}
        except httpx.TimeoutException as exc:
            return {"status_code": None, "ok": False, "error": f"timeout: {exc}", "evidence": f"timeout: {exc}", "body": ""}
        except httpx.HTTPError as exc:
            return {"status_code": None, "ok": False, "error": str(exc), "evidence": f"request failed: {exc}", "body": ""}
        except Exception as exc:
            # Catch-all for any unexpected exceptions
            return {"status_code": None, "ok": False, "error": f"unexpected error: {exc}", "evidence": f"unexpected error: {exc}", "body": ""}

    def _is_suspicious_redirect(self, initial_url: str, final_url: str, redirect_chain: list[str]) -> bool:
        """Check if redirect chain is suspicious (e.g., job page -> homepage)."""
        if not redirect_chain:
            return False
        
        # Parse domains
        try:
            initial_domain = urlparse(initial_url).netloc.lower()
            final_domain = urlparse(final_url).netloc.lower()
        except Exception:
            return False
        
        # Cross-domain redirect is suspicious
        if initial_domain != final_domain:
            return True
        
        # Check if final URL is a homepage (root path)
        final_path = urlparse(final_url).path
        if final_path in ("/", "", "/index.html", "/home"):
            # Job page redirecting to homepage is suspicious
            initial_path = urlparse(initial_url).path
            if initial_path and initial_path not in ("/", "", "/index.html", "/home"):
                return True
        
        return False


class JobVerifier:
    def __init__(self, url_checker: UrlChecker | None = None):
        self.url_checker = url_checker or HttpUrlChecker()
        self.career_verifier = CareerPageVerifier(url_checker)

    def _guess_career_url(self, company: str) -> str | None:
        """Guess common career page URLs for a company."""
        base = company.lower().replace(" ", "").replace(",", "").replace(".", "")
        common_patterns = [
            f"https://{base}.com/careers",
            f"https://{base}.com/jobs",
            f"https://careers.{base}.com",
            f"https://jobs.{base}.com",
            f"https://{base}.io/careers",
            f"https://{base}.ai/careers",
        ]
        return common_patterns[0]

    def _is_closed_page(self, body: str) -> bool:
        """Check if page content indicates the job is closed/filled."""
        if not body:
            return False
        body_lower = body.lower()
        return any(re.search(pattern, body_lower) for pattern in CLOSED_PATTERNS)

    def _is_antibot_page(self, body: str) -> bool:
        """Check if page is an anti-bot/CAPTCHA/interstitial page."""
        if not body:
            return False
        body_lower = body.lower()
        return any(re.search(pattern, body_lower) for pattern in ANTIBOT_PATTERNS)

    def _is_https_downgrade(self, initial_url: str, final_url: str) -> bool:
        """Check if HTTPS was downgraded to HTTP."""
        return initial_url.lower().startswith("https://") and final_url.lower().startswith("http://")

    def _is_server_error(self, status_code: int | None) -> bool:
        """Check if status code indicates a server error (5xx)."""
        return status_code is not None and 500 <= status_code < 600

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

        # Primary: direct URL check
        url_ok = False
        url_check = None
        if url:
            url_check = self.url_checker.check(url)
            evidence["url_check"] = url_check

            # Check for explicit removal
            code = url_check.get("status_code")
            if code in (404, 410):
                return VerificationResult("REMOVED", "http", evidence)

            # Check for server errors - don't treat as OK, don't fall back
            if self._is_server_error(code):
                evidence["url_check_note"] = f"server error {code}; not falling back to career page"
                # Don't set url_ok = True, but also don't fall back to career page
                # Return UNKNOWN since we can't verify
                return VerificationResult("UNKNOWN", "http", evidence)

            # Check for timeout/network errors
            if code is None:
                evidence["url_check_note"] = "network error or timeout; not falling back to career page"
                return VerificationResult("UNKNOWN", "http", evidence)

            # Check for HTTPS -> HTTP downgrade
            initial = url_check.get("initial_url", url)
            final = url_check.get("final_url", url)
            if self._is_https_downgrade(initial, final):
                evidence["url_check_note"] = "HTTPS downgraded to HTTP"
                return VerificationResult("UNKNOWN", "http", evidence)

            # Check for anti-bot page
            body = url_check.get("body", "")
            if self._is_antibot_page(body):
                evidence["url_check_note"] = "anti-bot/CAPTCHA page detected"
                return VerificationResult("UNKNOWN", "http", evidence)

            # Check for closed/filled job
            if self._is_closed_page(body):
                evidence["url_check_note"] = "page indicates position closed/filled"
                return VerificationResult("REMOVED", "http", evidence)

            # Check for suspicious redirect (e.g., job page -> homepage)
            if hasattr(self.url_checker, '_is_suspicious_redirect'):
                redirect_chain = url_check.get("redirect_chain", [])
                if self.url_checker._is_suspicious_redirect(url, url_check.get("final_url", url), redirect_chain):
                    evidence["url_check_note"] = "suspicious redirect detected (e.g., job page to homepage)"
                    return VerificationResult("UNKNOWN", "http", evidence)

            # Only now mark as OK
            url_ok = True

        # Secondary: application URL check
        app_url_ok = False
        app_check = None
        if application_url:
            app_check = self.url_checker.check(application_url)
            evidence["application_url_check"] = app_check

            code = app_check.get("status_code")
            if code in (404, 410) and not url_ok:
                evidence["application_url_note"] = "application URL not reachable"
            elif self._is_server_error(code):
                evidence["application_url_note"] = f"server error {code}"
            elif code is None:
                evidence["application_url_note"] = "network error or timeout"
            elif self._is_https_downgrade(application_url, app_check.get("final_url", "")):
                evidence["application_url_note"] = "HTTPS downgraded to HTTP"
            elif self._is_antibot_page(app_check.get("body", "")):
                evidence["application_url_note"] = "anti-bot/CAPTCHA page detected"
            elif self._is_closed_page(app_check.get("body", "")):
                evidence["application_url_note"] = "page indicates position closed/filled"
                if not url_ok:
                    return VerificationResult("REMOVED", "http", evidence)
            elif hasattr(self.url_checker, '_is_suspicious_redirect'):
                redirect_chain = app_check.get("redirect_chain", [])
                if self.url_checker._is_suspicious_redirect(application_url, app_check.get("final_url", application_url), redirect_chain):
                    evidence["application_url_note"] = "suspicious redirect detected"
            else:
                app_url_ok = True

        # Tertiary: career page verification (fallback) - ONLY if we couldn't verify directly
        # and the failure wasn't due to server errors/anti-bot
        if not url_ok and not app_url_ok:
            career_url = self._guess_career_url(company)
            if career_url:
                career_result = self.career_verifier.verify_listing_contains(career_url, None, title)
                evidence["career_page_check"] = career_result.evidence
                if career_result.status == "ACTIVE":
                    return VerificationResult("ACTIVE", "career_page", evidence)

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
        
        # Check for anti-bot on career page
        body = check.get("body", "")
        if self._is_antibot_page(body):
            evidence["note"] = "anti-bot page on career site"
            return VerificationResult("UNKNOWN", "career_page", evidence)
        
        if job_id and job_id in body:
            evidence["matched_by"] = "job_id"
            return VerificationResult("ACTIVE", "career_page", evidence)
        
        # More specific title matching: require significant words from title
        if title and self._title_matches_listing(title, body):
            evidence["matched_by"] = "title"
            return VerificationResult("ACTIVE", "career_page", evidence)
        
        evidence["note"] = "job not found on listing page"
        return VerificationResult("UNKNOWN", "career_page", evidence)

    def _is_antibot_page(self, body: str) -> bool:
        if not body:
            return False
        body_lower = body.lower()
        return any(re.search(pattern, body_lower) for pattern in ANTIBOT_PATTERNS)

    def _title_matches_listing(self, title: str, body: str) -> bool:
        """More specific title matching - require key words, not just substring."""
        if not title or not body:
            return False
        title_lower = title.lower()
        body_lower = body.lower()
        
        # Extract significant words (skip common words)
        stop_words = {"senior", "junior", "lead", "principal", "staff", "the", "a", "an", "and", "or", "of", "in", "at", "for", "with", "to", "from"}
        title_words = [w for w in re.findall(r"\b\w+\b", title_lower) if w not in stop_words and len(w) > 2]
        
        if not title_words:
            return False
        
        # Require at least 2 significant words to match, or 1 if it's a very specific term
        matches = sum(1 for w in title_words if w in body_lower)
        if len(title_words) == 1:
            return matches >= 1 and len(title_words[0]) > 5  # e.g., "Kubernetes" but not "Engineer"
        return matches >= 2