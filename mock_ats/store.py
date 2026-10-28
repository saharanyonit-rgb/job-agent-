"""In-memory data store for the Mock ATS.

The job feed mirrors the deterministic demo feed in
``core.discovery.adapters.mock_source`` so both discovery sources resolve
to the *same* jobs (identical company / title / location / external id), which
lets the dedupe stage merge them into one canonical job while the mock_ats
adapter supplies verifiable, submittable URLs.

Two deliberate behaviours exercise the pipeline's safety paths:

* ``MOCK-OOO-909`` (Out Of Office Architects) renders a "this position is
  closed" page, so HTTP verification must classify it as REMOVED.
* ``MOCK-CAP-808`` (Traptop Systems) renders a CAPTCHA wall on its apply form,
  so submission must trigger a HANDOFF instead of guessing.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

JOBS: dict[str, dict[str, Any]] = {
    "MOCK-TA-101": {
        "id": "MOCK-TA-101",
        "title": "Technical Architect",
        "company": "Skidata Solutions",
        "location": "Bengaluru, India",
        "employment_type": "Full-time",
        "salary_text": "4500000 - 6000000 INR",
        "description": (
            "5+ years experience. Design microservices on Azure with .NET, Kubernetes. "
            "Lead technical leadership across squads. Full-time. Salary 45-60 LPA."
        ),
        "closed": False,
        "captcha": False,
    },
    "MOCK-SA-202": {
        "id": "MOCK-SA-202",
        "title": "Solution Architect",
        "company": "EY Global",
        "location": "Remote India",
        "employment_type": "Full-time",
        "salary_text": "5000000 - 7000000 INR",
        "description": (
            "8+ years consulting experience. Solution design, Azure, AWS, microservices, REST. "
            "Hybrid engagement model. Full-time. Salary 50-70 LPA."
        ),
        "closed": False,
        "captcha": False,
    },
    "MOCK-AA-404": {
        "id": "MOCK-AA-404",
        "title": "AI Architect",
        "company": "Metafilter Capital",
        "location": "Gurgaon, India",
        "employment_type": "Full-time",
        "salary_text": "7000000 - 9000000 INR",
        "description": (
            "10+ years with 4+ in GenAI/LLM systems. RAG, MLOps, Python, architecture, "
            "technical leadership. Full-time. Salary 70-90 LPA. Hybrid."
        ),
        "closed": False,
        "captcha": False,
    },
    # --- low-match fixture (should score below the policy minimum) ----------
    "MOCK-DA-303": {
        "id": "MOCK-DA-303",
        "title": "Senior Data Analyst",
        "company": "Gall Analytics",
        "location": "Mumbai, India",
        "employment_type": "Full-time",
        "salary_text": "1200000 - 1800000 INR",
        "description": "3+ years. SQL, Tableau, Power BI, ETL. Salary 12-18 LPA.",
        "closed": False,
        "captcha": False,
    },
    "MOCK-JSE-505": {
        "id": "MOCK-JSE-505",
        "title": "Java Software Engineer",
        "company": "Acme Corp",
        "location": "Pune, India",
        "employment_type": "Full-time",
        "salary_text": "800000 INR",
        "description": "2 years experience. Java, Spring Boot, SQL. On-site.",
        "closed": False,
        "captcha": False,
    },
    # --- safety-path fixtures ------------------------------------------------
    "MOCK-CAP-808": {
        "id": "MOCK-CAP-808",
        "title": "Platform Architect",
        "company": "Traptop Systems",
        "location": "Hyderabad, India",
        "employment_type": "Full-time",
        "salary_text": "6000000 - 8000000 INR",
        "description": (
            "8+ years platform engineering. Kubernetes, Terraform, microservices, "
            "architecture, technical leadership. Salary 60-80 LPA."
        ),
        "closed": False,
        "captcha": True,
    },
    "MOCK-OOO-909": {
        "id": "MOCK-OOO-909",
        "title": "Cloud Architect",
        "company": "Out Of Office Architects",
        "location": "Remote India",
        "employment_type": "Full-time",
        "salary_text": "5000000 - 6500000 INR",
        "description": (
            "Azure, AWS, Kubernetes, microservices architecture and technical "
            "leadership. Salary 50-65 LPA."
        ),
        "closed": True,
        "captcha": False,
    },
}

# application_id -> submission record (filled by POST /jobs/{id}/apply)
SUBMISSIONS: dict[str, dict[str, Any]] = {}


def public_jobs(base_url: str) -> list[dict[str, Any]]:
    """JSON-safe job list served by GET /api/jobs."""
    out = []
    for job in JOBS.values():
        item = {k: v for k, v in job.items() if k not in ("closed", "captcha")}
        item["url"] = f"{base_url}/jobs/{job['id']}"
        item["application_url"] = f"{base_url}/jobs/{job['id']}/apply"
        out.append(item)
    return out
