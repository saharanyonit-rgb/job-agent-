from __future__ import annotations

from datetime import datetime
from typing import Any

from core.discovery.base import DiscoveryResult, IntegrationStatus, SourceAdapter
from core.jobs.normalize import RawJob

# Deterministic demo/mock feed so the pipeline always has jobs to process.
_MOCK_JOBS: list[dict[str, Any]] = [
    {
        "title": "Technical Architect",
        "company": "Skidata Solutions",
        "location": "Bengaluru, India",
        "description": (
            "5+ years experience. Design microservices on Azure with .NET, Kubernetes. "
            "Lead technical leadership across squads. Full-time. Salary 45-60 LPA."
        ),
        "job_id_external": "SKD-TA-101",
        "url": "https://careers.skidata.example/jobs/SKD-TA-101",
        "application_url": "https://careers.skidata.example/apply/SKD-TA-101",
        "employment_type": "Full-time",
        "salary_text": "4500000 - 6000000 INR",
    },
    {
        "title": "Solution Architect",
        "company": "EY Global",
        "location": "Remote India",
        "description": (
            "8+ years consulting experience. Solution design, Azure, AWS, microservices, REST. "
            "Hybrid engagement model. Full-time. Salary 50-70 LPA."
        ),
        "job_id_external": "EY-SA-202",
        "url": "https://careers.ey.example/jobs/EY-SA-202",
        "application_url": "https://careers.ey.example/apply/EY-SA-202",
        "employment_type": "Full-time",
        "salary_text": "5000000 - 7000000 INR",
    },
    {
        "title": "Senior Data Analyst",
        "company": "Gall Analytics",
        "location": "Mumbai, India",
        "description": "3+ years. SQL, Tableau, Power BI, ETL. Salary 12-18 LPA.",
        "job_id_external": "GAL-DA-303",
        "url": "https://careers.gall.example/jobs/GAL-DA-303",
        "application_url": "https://careers.gall.example/apply/GAL-DA-303",
        "employment_type": "Full-time",
        "salary_text": "1200000 - 1800000 INR",
    },
    {
        "title": "AI Architect",
        "company": "Metafilter Capital",
        "location": "Gurgaon, India",
        "description": (
            "10+ years with 4+ in GenAI/LLM systems. RAG, MLOps, Python, architecture, "
            "technical leadership. Full-time. Salary 70-90 LPA. Hybrid."
        ),
        "job_id_external": "MFC-AA-404",
        "url": "https://jobs.metafilter.example/MFC-AA-404",
        "application_url": "https://jobs.metafilter.example/apply/MFC-AA-404",
        "employment_type": "Full-time",
        "salary_text": "7000000 - 9000000 INR",
    },
    {
        "title": "Java Software Engineer",
        "company": "Acme Corp",
        "location": "Pune, India",
        "description": "2 years experience. Java, Spring Boot, SQL. On-site.",
        "job_id_external": "ACME-JSE-505",
        "url": "https://acme.example/careers/ACME-JSE-505",
        "application_url": "https://acme.example/careers/ACME-JSE-505/apply",
        "employment_type": "Full-time",
        "salary_text": "800000 INR",
    },
]


class MockSourceAdapter(SourceAdapter):
    name = "mock_feed"
    integration_status = IntegrationStatus.VALIDATED
    enabled = True

    def discover_jobs(self, query: dict[str, Any] | None = None) -> DiscoveryResult:
        query = query or {}
        company_filter = (query.get("company") or "").lower()
        jobs: list[RawJob] = []
        for item in _MOCK_JOBS:
            if company_filter and company_filter not in item["company"].lower():
                continue
            jobs.append(
                RawJob(
                    title=item["title"],
                    company=item["company"],
                    location=item["location"],
                    description=item["description"],
                    url=item["url"],
                    application_url=item["application_url"],
                    job_id_external=item["job_id_external"],
                    source=self.name,
                    employment_type=item.get("employment_type"),
                    salary_text=item.get("salary_text"),
                    posted_date=datetime.utcnow(),
                )
            )
        return DiscoveryResult(jobs=jobs, source=self.name, status=self.integration_status)

    def get_job(self, job_ref: str) -> RawJob | None:
        result = self.discover_jobs()
        for job in result.jobs:
            if job.job_id_external == job_ref:
                return job
        return None

    def verify_job(self, job_ref: str) -> dict[str, Any]:
        job = self.get_job(job_ref)
        if not job:
            return {"status": "REMOVED", "evidence": "job_ref not in feed"}
        return {"status": "ACTIVE", "evidence": "job present in mock feed"}

    def get_application_url(self, job_ref: str) -> str | None:
        job = self.get_job(job_ref)
        return job.application_url if job else None


def all_mock_jobs() -> list[dict[str, Any]]:
    return list(_MOCK_JOBS)