"""Mock ATS web application (FastAPI).

Simulates a small applicant tracking system so the Job Agent pipeline can be
exercised end-to-end over real HTTP without touching live job boards:

* ``GET  /api/jobs``            -> JSON job feed (consumed by MockATSAdapter)
* ``GET  /api/jobs/{job_ref}``  -> single job JSON
* ``GET  /jobs/{job_ref}``      -> HTML job detail page (verification target)
* ``GET  /jobs/{job_ref}/apply``-> HTML application form (submission target)
* ``POST /jobs/{job_ref}/apply``-> validates + stores the application, then
                                  renders a confirmation page with an
                                  ``APP-XXXXXX`` id (the evidence the agent
                                  requires before reporting success)
* ``GET  /api/submissions``     -> inspection endpoint for tests/demo
* ``GET  /health``              -> liveness probe

Deliberate safety fixtures (see ``mock_ats.store``):

* a closed posting ("this position is closed") that verification must mark
  REMOVED, and
* a CAPTCHA-walled apply form that submission must hand off instead of
  guessing.
"""

from __future__ import annotations

import html as html_mod
import re
import uuid
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

try:  # normal case: package imported as part of the core package
    from core.mock_ats.store import JOBS, SUBMISSIONS, public_jobs
except ModuleNotFoundError:  # fallback: standalone `mock_ats` on sys.path
    from mock_ats.store import JOBS, SUBMISSIONS, public_jobs

app = FastAPI(title="Mock ATS", version="1.0.0")

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z' .-]{1,79}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _job_or_404(job_ref: str):
    job = JOBS.get(job_ref)
    return job


# ---------------------------------------------------------------------------
# JSON API (consumed by core.discovery.adapters.ats.MockATSAdapter)
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "jobs": len(JOBS), "submissions": len(SUBMISSIONS)}


@app.get("/api/jobs")
def list_jobs(request: Request) -> dict[str, Any]:
    return {"jobs": public_jobs(_base_url(request))}


@app.get("/api/jobs/{job_ref}")
def get_job(job_ref: str, request: Request) -> Any:
    job = _job_or_404(job_ref)
    if not job:
        return JSONResponse(status_code=404, content={"error": "job not found"})
    base = _base_url(request)
    payload = {k: v for k, v in job.items() if k not in ("closed", "captcha")}
    payload["url"] = f"{base}/jobs/{job['id']}"
    payload["application_url"] = f"{base}/jobs/{job['id']}/apply"
    return payload


@app.get("/api/submissions")
def list_submissions() -> dict[str, Any]:
    return {"submissions": list(SUBMISSIONS.values())}


# ---------------------------------------------------------------------------
# HTML pages
# ---------------------------------------------------------------------------

_PAGE_CSS = """
body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 720px; color: #1d1d1f; }
h1 { font-size: 1.5rem; } .muted { color: #666; font-size: .9rem; }
label { display: block; margin-top: 1rem; font-weight: 600; }
input, select, textarea { width: 100%; padding: .45rem; margin-top: .25rem; box-sizing: border-box; }
button { margin-top: 1.25rem; padding: .6rem 1.4rem; }
.errors { color: #b3261e; }
.captcha-box { background: #f2f2f2; border: 1px solid #ccc; padding: 1rem; text-align: center; }
"""


def _page(title: str, body: str) -> str:
    return (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html_mod.escape(title)}</title><style>{_PAGE_CSS}</style></head>"
        f"<body>{body}</body></html>"
    )


def _esc(value: Any) -> str:
    return html_mod.escape(str(value if value is not None else ""))


@app.get("/jobs/{job_ref}", response_class=HTMLResponse)
def job_page(job_ref: str, request: Request):
    job = _job_or_404(job_ref)
    if not job:
        return HTMLResponse(_page("Not found", "<h1>404 — this job does not exist</h1>"), status_code=404)
    if job.get("closed"):
        return HTMLResponse(
            _page(
                job["title"],
                f"<h1>{_esc(job['title'])}</h1>"
                f"<p class='muted'>{_esc(job['company'])} — {_esc(job['location'])}</p>"
                "<p>Update: this position is closed. We are no longer accepting "
                "applications for this requisition.</p>",
            )
        )
    base = _base_url(request)
    return HTMLResponse(
        _page(
            job["title"],
            f"<h1>{_esc(job['title'])}</h1>"
            f"<p class='muted'>{_esc(job['company'])} — {_esc(job['location'])} · "
            f"{_esc(job.get('employment_type') or '')} · {_esc(job.get('salary_text') or '')}</p>"
            f"<p>{_esc(job['description'])}</p>"
            f"<p><a href='{base}/jobs/{job['id']}/apply'>Apply now</a></p>",
        )
    )


@app.get("/jobs/{job_ref}/apply", response_class=HTMLResponse)
def apply_form(job_ref: str, request: Request):
    job = _job_or_404(job_ref)
    if not job:
        return HTMLResponse(_page("Not found", "<h1>404 — this job does not exist</h1>"), status_code=404)
    if job.get("closed"):
        return HTMLResponse(
            _page(
                "Applications closed",
                f"<h1>{_esc(job['title'])}</h1><p>This position is closed; applications are no longer accepted.</p>",
            )
        )
    base = _base_url(request)
    if job.get("captcha"):
        return HTMLResponse(
            _page(
                f"Apply — {job['title']}",
                f"<h1>Apply: {_esc(job['title'])}</h1>"
                "<div class='captcha-box' data-testid='captcha'>"
                "<p>Please verify you are a human to continue the security check.</p>"
                "<p class='muted'>reCAPTCHA placeholder — automated agents must stop here.</p>"
                "</div>",
            )
        )
    body = f"""
<h1>Apply: {_esc(job['title'])}</h1>
<p class='muted'>{_esc(job['company'])} — {_esc(job['location'])}</p>
<form method="post" action="{base}/jobs/{_esc(job['id'])}/apply" enctype="multipart/form-data">
  <input type="hidden" name="job_id" value="{_esc(job['id'])}">
  <label for="full_name">Full Name</label>
  <input id="full_name" name="full_name" type="text" required>
  <label for="email">Email</label>
  <input id="email" name="email" type="email" required>
  <label for="phone">Phone</label>
  <input id="phone" name="phone" type="tel" required>
  <label for="years_experience">Years of Experience</label>
  <input id="years_experience" name="years_experience" type="text" required>
  <label for="notice_period">Notice Period</label>
  <input id="notice_period" name="notice_period" type="text" required>
  <label for="expected_salary">Expected Salary</label>
  <input id="expected_salary" name="expected_salary" type="text" required>
  <label for="work_authorization">Work Authorization</label>
  <select id="work_authorization" name="work_authorization" required>
    <option value="">Select…</option>
    <option value="India">India</option>
    <option value="US Citizen">US Citizen</option>
    <option value="EU">EU</option>
    <option value="Other">Other</option>
  </select>
  <label for="relocation">Willing to Relocate</label>
  <select id="relocation" name="relocation" required>
    <option value="">Select…</option>
    <option value="yes">Yes</option>
    <option value="no">No</option>
  </select>
  <label for="resume">Resume (PDF/DOCX)</label>
  <input id="resume" name="resume" type="file" required>
  <label for="why_interested">Why are you interested in this role?</label>
  <textarea id="why_interested" name="why_interested" rows="4"></textarea>
  <button type="submit">Submit application</button>
</form>
"""
    return HTMLResponse(_page(f"Apply — {job['title']}", body))


@app.post("/jobs/{job_ref}/apply", response_class=HTMLResponse)
async def submit_application(
    job_ref: str,
    request: Request,
    full_name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    years_experience: str = Form(""),
    notice_period: str = Form(""),
    expected_salary: str = Form(""),
    work_authorization: str = Form(""),
    relocation: str = Form(""),
    why_interested: str = Form(""),
):
    job = _job_or_404(job_ref)
    if not job:
        return HTMLResponse(_page("Not found", "<h1>404 — this job does not exist</h1>"), status_code=404)
    if job.get("closed"):
        return HTMLResponse(
            _page("Applications closed", f"<h1>{_esc(job['title'])}</h1><p>This position is closed.</p>")
        )
    if job.get("captcha"):
        return HTMLResponse(
            _page(
                "Security check",
                "<div class='captcha-box' data-testid='captcha'><p>Please verify you are a human "
                "to continue the security check.</p></div>",
            )
        )

    # Resume file (multipart) — presence is required.
    resume_name = ""
    try:
        files = await request.form()
        upload = files.get("resume")
        if upload is not None and hasattr(upload, "filename"):
            resume_name = upload.filename or ""
    except Exception:  # pragma: no cover - malformed multipart
        resume_name = ""

    errors: list[str] = []
    if not _NAME_RE.match(full_name.strip()):
        errors.append("Full Name is required and must contain letters only.")
    if not _EMAIL_RE.match(email.strip()):
        errors.append("A valid Email is required.")
    if not re.search(r"\d{3}", phone or ""):
        errors.append("Phone number is required.")
    if not (years_experience or "").strip():
        errors.append("Years of Experience is required.")
    if not (notice_period or "").strip():
        errors.append("Notice Period is required.")
    if not (expected_salary or "").strip():
        errors.append("Expected Salary is required.")
    if not (work_authorization or "").strip():
        errors.append("Work Authorization is required.")
    if not (relocation or "").strip():
        errors.append("Please answer the relocation question.")
    if not resume_name:
        errors.append("A resume file must be attached.")

    if errors:
        error_items = "".join(f"<li>{_esc(e)}</li>" for e in errors)
        base = _base_url(request)
        body = (
            f"<h1>Validation error</h1>"
            f"<ul class='errors' data-testid='errors'>{error_items}</ul>"
            f"<p><a href='{base}/jobs/{_esc(job['id'])}/apply'>Return to the application form</a></p>"
        )
        return HTMLResponse(_page("Application has errors", body), status_code=422)

    application_id = f"APP-{uuid.uuid4().hex[:6].upper()}"
    SUBMISSIONS[application_id] = {
        "application_id": application_id,
        "job_id": job["id"],
        "job_title": job["title"],
        "company": job["company"],
        "applicant": {
            "full_name": full_name.strip(),
            "email": email.strip(),
            "phone": phone.strip(),
            "years_experience": years_experience.strip(),
            "notice_period": notice_period.strip(),
            "expected_salary": expected_salary.strip(),
            "work_authorization": work_authorization.strip(),
            "relocation": relocation.strip(),
            "why_interested": (why_interested or "").strip(),
            "resume_filename": resume_name,
        },
    }

    body = (
        f"<h1>Thank you for applying</h1>"
        f"<p>Your application was received and will be reviewed by our team.</p>"
        f"<p>Application ID: <span data-testid='application_id'>{application_id}</span></p>"
        f"<p class='muted'>{_esc(job['title'])} at {_esc(job['company'])}</p>"
    )
    return HTMLResponse(_page("Application received", body))
