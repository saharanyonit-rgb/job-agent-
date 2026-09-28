from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from job_agent.browser.detectors import FormDetector, FormSpec
from job_agent.browser.manager import FormBackend, HttpFormBackend, NavigationResult
from job_agent.browser.mapper import FormMapper, MappingResult
from job_agent.core.logging_config import get_logger

log = get_logger(__name__)


@dataclass
class SubmissionOutcome:
    success: bool = False
    application_id: str | None = None
    handoff_required: bool = False
    handoff_reason: str | None = None
    handoff_detail: Any = None
    page_detection: dict[str, Any] = field(default_factory=dict)
    evidencing: dict[str, Any] = field(default_factory=dict)

    @property
    def has_evidence(self) -> bool:
        return bool(self.application_id) or bool(self.page_detection.get("success"))


class ApplicationSubmitter:
    """Automates a single application form through the configured backend.

    Steps:
      1. Load form URL
      2. Detect form(s)
      3. Map fields to verified candidate facts
      4. Ambiguous/vital fields -> HANDOFF_REQUIRED (no guessing)
      5. Require confirmation evidence before reporting success
    """

    def __init__(
        self,
        backend: FormBackend | None = None,
        max_pages: int = 5,
        backend_kind: str = "playwright",
    ):
        self.backend = backend or (HttpFormBackend() if backend_kind == "http" else None)
        self.max_pages = max_pages
        self.backend_kind = backend_kind
        self.detector = FormDetector()
        self.mapper = FormMapper()

    def apply(
        self,
        application_url: str,
        facts: dict[str, Any],
        files: dict[str, str],
    ) -> SubmissionOutcome:
        # Use HTTP backend if explicitly provided or backend_kind is http
        use_http = isinstance(self.backend, HttpFormBackend) or self.backend_kind == "http"

        if not use_http:
            from job_agent.browser.manager import InteractiveSubmitter

            result = InteractiveSubmitter().submit(application_url, facts, files, max_pages=self.max_pages)
            detection = result.page_detection
            if detection.get("captcha"):
                return SubmissionOutcome(handoff_required=True, handoff_reason="CAPTCHA", page_detection=detection, evidencing={"url": result.url})
            if detection.get("mfa"):
                return SubmissionOutcome(handoff_required=True, handoff_reason="MFA", page_detection=detection, evidencing={"url": result.url})
            return SubmissionOutcome(
                success=detection.get("success", False),
                application_id=detection.get("application_id"),
                page_detection=detection,
                evidencing={"url": result.url, "backend": "playwright"},
            )

        history: list[dict[str, Any]] = []
        nav = self.backend.load_url(application_url)
        current_referer = application_url

        for _ in range(self.max_pages):
            history.append({"url": nav.url, "status": nav.status_code, "detection": nav.page_detection})
            detection = nav.page_detection

            if detection.get("captcha"):
                return SubmissionOutcome(handoff_required=True, handoff_reason="CAPTCHA", page_detection=detection, evidencing={"url": nav.url, "history": history})
            if detection.get("mfa"):
                return SubmissionOutcome(handoff_required=True, handoff_reason="MFA", page_detection=detection, evidencing={"url": nav.url, "history": history})
            if detection.get("success"):
                return SubmissionOutcome(
                    success=True,
                    application_id=detection.get("application_id"),
                    page_detection=detection,
                    evidencing={"final_url": nav.url, "history": history},
                )
            if detection.get("errors"):
                return SubmissionOutcome(
                    success=False,
                    page_detection=detection,
                    evidencing={"errors": detection["errors"], "url": nav.url, "history": history},
                )
            if not detection.get("has_form"):
                return SubmissionOutcome(
                    handoff_required=True,
                    handoff_reason="unsupported_page_no_form_found",
                    page_detection=detection,
                    evidencing={"url": nav.url, "history": history},
                )

            forms = self.detector.detect(nav.html, base_url=nav.url)
            if not forms:
                return SubmissionOutcome(
                    handoff_required=True,
                    handoff_reason="unsupported_website_no_form_detected",
                    page_detection=detection,
                    evidencing={"url": nav.url},
                )

            form = forms[0]
            mapped = self.mapper.map(form, facts, files)
            ambiguous = [m for m in mapped if m.ambiguous]
            if ambiguous:
                return SubmissionOutcome(
                    handoff_required=True,
                    handoff_reason="field_mapping_ambiguous",
                    handoff_detail=ambiguous,
                    page_detection=detection,
                    evidencing={
                        "ambiguous_fields": [m.to_dict() for m in ambiguous],
                        "url": nav.url,
                        "history": history,
                    },
                )

            values = {m.field.name: m.value for m in mapped}
            remaining_files = {k: v for k, v in files.items() if k not in values or values[k] is None}
            nav = self.backend.submit_form(form.action, form.method, values, remaining_files, referer=current_referer)
            current_referer = nav.url
            # Continue loop using the response HTML (handles multi-page forms
            # without re-GETting a POST URL).

        final = nav.page_detection
        return SubmissionOutcome(
            success=final.get("success", False),
            application_id=final.get("application_id"),
            page_detection=final,
            evidencing={"history": history},
        )