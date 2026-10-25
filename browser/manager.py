from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from job_agent.browser.detectors import PageDetector
from job_agent.core.config import get_settings
from job_agent.core.logging_config import get_logger

log = get_logger(__name__)


@dataclass
class NavigationResult:
    html: str
    status_code: int
    url: str
    page_detection: dict[str, Any] = field(default_factory=dict)


class FormBackend(Protocol):
    def load_url(self, url: str) -> NavigationResult: ...
    def submit_form(
        self,
        action: str,
        method: str,
        values: dict[str, Any],
        files: dict[str, str],
        referer: str = "",
    ) -> NavigationResult: ...


class HttpFormBackend:
    """Backend that interacts with HTML forms over real HTTP (no browser process).

    Uses httpx (multipart when files present) and BeautifulSoup semantics already
    provided by detectors. This is what automated tests exercise against the
    bundled Mock ATS; the Playwright backend targets real sites.
    """

    def __init__(self, follow_redirects: bool = True, transport: Any = None):
        self.follow_redirects = follow_redirects
        self.detector = PageDetector()
        self._transport = transport
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=30.0,
                follow_redirects=self.follow_redirects,
                headers={"User-Agent": "JobAgent/0.1 (application automation)"},
                transport=self._transport,
            )
        return self._client

    def load_url(self, url: str) -> NavigationResult:
        resp = self.client.get(url)
        result = NavigationResult(html=resp.text, status_code=resp.status_code, url=str(resp.url))
        result.page_detection = self.detector.detect(resp.text)
        return result

    def submit_form(
        self,
        action: str,
        method: str,
        values: dict[str, Any],
        files: dict[str, str],
        referer: str = "",
    ) -> NavigationResult:
        headers = {"Referer": referer} if referer else {}
        # Separate file fields
        file_fields: dict[str, tuple[str, bytes, str]] = {}
        text_values: dict[str, Any] = {}
        for key, value in values.items():
            if key in files and files[key]:
                path = files[key]
                import pathlib

                p = pathlib.Path(path)
                file_fields[key] = (p.name, p.read_bytes(), "application/octet-stream")
            else:
                text_values[key] = value

        kwargs: dict[str, Any] = {"headers": headers}
        if file_fields:
            data = {k: str(v) for k, v in text_values.items()}
            files_payload = [(k, v) for k, v in file_fields.items()]
            resp = self.client.request(method.upper(), action, data=data, files=files_payload, **kwargs)
        else:
            if method.lower() == "get":
                resp = self.client.request("GET", action, params=text_values, **kwargs)
            else:
                resp = self.client.request("POST", action, data=text_values, **kwargs)

        result = NavigationResult(html=resp.text, status_code=resp.status_code, url=str(resp.url))
        result.page_detection = self.detector.detect(resp.text)
        log.info(
            "Submitted form %s -> HTTP %s (url=%s)", action, resp.status_code, result.url
        )
        return result


def playwright_available() -> bool:
    """True when the playwright package AND a usable Chromium binary are present."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:
        return False


def resolve_backend_kind(preferred: str = "auto") -> str:
    """Resolve 'auto'/'playwright'/'http' to a concrete backend kind.

    Falls back to 'http' when Playwright is requested but unavailable, so the
    pipeline degrades gracefully instead of crashing mid-run.
    """
    settings = get_settings()
    kind = preferred if preferred != "auto" else settings.browser_backend
    if kind == "http":
        return "http"
    if playwright_available():
        return "playwright"
    log.warning("Playwright backend requested but unavailable; falling back to HTTP form backend")
    return "http"


# Realistic desktop UA + viewport used by all Playwright sessions to reduce
# trivial bot-detection (default Playwright UA contains 'HeadlessChrome').
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
_VIEWPORT = {"width": 1366, "height": 900}


class PlaywrightFormBackend:
    """Real-browser backend using Playwright. Lazily imports so tests without
    Playwright installed still pass."""

    def __init__(self, headless: bool | None = None):
        self.headless = get_settings().browser_headless if headless is None else headless
        self.detector = PageDetector()
        self._page = None

    def load_url(self, url: str) -> NavigationResult:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(user_agent=_BROWSER_UA, viewport=_VIEWPORT)
            page = context.new_page()
            resp = page.goto(url, wait_until="networkidle", timeout=30000)
            html = page.content()
            status = resp.status if resp else 200
            result = NavigationResult(html=html, status_code=status, url=page.url)
            result.page_detection = self.detector.detect(html)
            context.close()
            browser.close()
        return result

    def submit_form(
        self,
        action: str,
        method: str,
        values: dict[str, Any],
        files: dict[str, str],
        referer: str = "",
    ) -> NavigationResult:
        # Interactive fill-and-submit inside a real Playwright session. The
        # submitter navigates to the form, fills visible fields by name, and
        # follows the multi-page flow until success/block detection.
        return InteractiveSubmitter(headless=self.headless).submit(action, values, files)


class InteractiveSubmitter:
    """Fills forms inside a real Playwright page, using labels/ARIA/nearby text."""

    def __init__(self, headless: bool | None = None):
        self.headless = get_settings().browser_headless if headless is None else headless
        self.detector = PageDetector()

    def submit(
        self,
        start_url: str,
        values: dict[str, Any],
        files: dict[str, str],
        max_pages: int = 5,
    ) -> NavigationResult:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(user_agent=_BROWSER_UA, viewport=_VIEWPORT)
            # Mask the webdriver flag so basic bot checks don't reject us.
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page = context.new_page()
            try:
                page.goto(start_url, wait_until="networkidle", timeout=45000)

                for _ in range(max_pages):
                    detection = self.detector.detect(page.content())
                    if detection.get("captcha") or detection.get("mfa"):
                        return NavigationResult(
                            html=page.content(),
                            status_code=403,
                            url=page.url,
                            page_detection=detection,
                        )
                    if detection.get("success"):
                        return NavigationResult(
                            html=page.content(),
                            status_code=200,
                            url=page.url,
                            page_detection=detection,
                        )
                    form = page.locator("form").first
                    if not form.count():
                        break
                    self._fill_visible(page, values, files)
                    submit_btn = page.locator("button[type=submit], input[type=submit]").first
                    if not submit_btn.count():
                        # No explicit submit control; try implicit form submit.
                        form.press("Enter")
                    else:
                        submit_btn.click()
                    try:
                        page.wait_for_load_state("networkidle", timeout=15000)
                    except Exception:
                        pass
                html = page.content()
                detection = self.detector.detect(html)
                result = NavigationResult(html=html, status_code=200, url=page.url, page_detection=detection)
            finally:
                context.close()
                browser.close()
            return result

    def _fill_visible(self, page, values: dict[str, Any], files: dict[str, str]) -> None:
        for name, value in values.items():
            if name in files and files[name]:
                self._set_file(page, name, files[name])
                continue
            self._set_field(page, name, value)

    def _set_field(self, page, name: str, value: Any) -> None:
        locator = page.locator(f'[name="{name}"]').first
        if not locator.count():
            return
        tag = locator.evaluate("el => el.tagName")
        ftype = locator.get_attribute("type") or "text"
        value_s = "" if value is None else str(value)
        if ftype == "checkbox":
            if value_s and value_s.lower() in ("yes", "true", "accepted", "1"):
                locator.check()
        elif ftype == "radio":
            locator.check(force=True)
        elif tag == "SELECT":
            locator.select_option(value=value_s, label=value_s)
        elif tag == "TEXTAREA" or ftype in ("text", "email", "tel", "url", "number", "password"):
            locator.fill(value_s)

    def _set_file(self, page, name: str, path: str) -> None:
        locator = page.locator(f'input[name="{name}"][type="file"]').first
        if locator.count():
            locator.set_input_files(path)