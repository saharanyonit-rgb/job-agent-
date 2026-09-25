from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

REMOTE_TOKENS = {
    "remote": "REMOTE",
    "work from home": "REMOTE",
    "wfh": "REMOTE",
    "hybrid": "HYBRID",
    "on-site": "ONSITE",
    "onsite": "ONSITE",
    "office": "ONSITE",
}

EMPLOYMENT_TOKENS = {
    "full-time": "FULL_TIME",
    "full time": "FULL_TIME",
    "fulltime": "FULL_TIME",
    "part-time": "PART_TIME",
    "part time": "PART_TIME",
    "contract": "CONTRACT",
    "internship": "INTERNSHIP",
    "intern": "INTERNSHIP",
    "freelance": "CONTRACT",
}


def normalize_title(title: str) -> str:
    lowered = title.lower().strip()
    lowered = re.sub(r"\s+", " ", lowered)
    lowered = re.sub(r"\([^)]*\)", "", lowered)
    lowered = re.sub(r"[^\w\s-]", "", lowered)
    return lowered.strip()


def classify_remote(location: str | None, description: str | None = "") -> str | None:
    text = f"{location or ''} {description or ''}".lower()
    for token, label in REMOTE_TOKENS.items():
        if token in text:
            return label
    return None


def classify_employment(text: str | None) -> str | None:
    if not text:
        return None
    lowered = text.lower()
    for token, label in EMPLOYMENT_TOKENS.items():
        if token in lowered:
            return label
    return None


def extract_years(description: str | None) -> tuple[int | None, int | None]:
    if not description:
        return None, None
    patterns = [
        r"(\d{1,2})\s*[-–to]+\s*(\d{1,2})\s*\+?\s*(?:years?|yrs)",
        r"(\d{1,2})\s*\+?\s*(?:years?|yrs)",
        r"(?:experience)\s*(?:of\s*)?(\d{1,2})\s*[-–to]+\s*(\d{1,2})",
    ]
    for pat in patterns:
        m = re.search(pat, description, re.I)
        if m:
            groups = m.groups()
            if len(groups) == 2 and all(g.isdigit() for g in groups if g):
                return int(groups[0]), int(groups[1])
            return int(groups[0]), None
    return None, None


def parse_salary(text: str | None) -> tuple[float | None, float | None, str | None]:
    if not text:
        return None, None, None
    currency = None
    for cur, patterns in {
        "INR": ["₹", "inr", "lpa", "lakh", "lacs"],
        "USD": ["$", "usd"],
        "EUR": ["€", "eur"],
    }.items():
        if any(p in text.lower() for p in patterns):
            currency = cur
            break
    numbers = [float(n) for n in re.findall(r"[\d,]+(?:\.\d+)?", text.replace(",", "")) if float(n) > 0]
    if not numbers:
        return None, None, currency
    if "lpa" in text.lower() or "lakh" in text.lower() or "lacs" in text.lower():
        numbers = [n * 100000 for n in numbers]
    if len(numbers) >= 2:
        numbers.sort()
        return numbers[0], numbers[-1], currency
    return numbers[0], numbers[0], currency


def canonicalize_location(location: str | None) -> str | None:
    if not location:
        return None
    loc = location.strip().strip(",")
    return re.sub(r"\s+", " ", loc)


def country_from_location(location: str | None) -> str | None:
    if not location:
        return None
    low = location.lower()
    if "india" in low or any(c in low for c in ["bengaluru", "bangalore", "mumbai", "pune", "hyderabad", "chennai", "gurgaon", "noida", "delhi"]):
        return "India"
    if any(c in low for c in ["united states", "usa", "new york", "san francisco", "austin", "seattle", "remote - us", "us remote"]):
        return "United States"
    return None


@dataclass
class RawJob:
    title: str
    company: str
    location: str | None = None
    description: str | None = None
    url: str | None = None
    application_url: str | None = None
    job_id_external: str | None = None
    source: str = "unknown"
    posted_date: datetime | None = None
    employment_type: str | None = None
    salary_text: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedJob:
    title: str
    company: str
    location: str | None
    country: str | None
    remote_status: str | None
    employment_type: str | None
    experience_min: int | None
    experience_max: int | None
    skills: list[str]
    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None
    description: str | None
    job_id_external: str | None
    source: str
    url: str | None
    application_url: str | None
    posted_date: datetime | None
    normalized_title: str
    fingerprint: str
    content_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fingerprint_job(job: NormalizedJob) -> str:
    base = "|".join(
        [
            job.company.lower().strip(),
            job.normalized_title,
            (job.location or "").lower().strip(),
            (job.job_id_external or "").strip(),
            (job.url or "").strip(),
        ]
    )
    return hashlib.sha256(base.encode()).hexdigest()


def derive_skills(description: str | None, title: str | None = None) -> list[str]:
    tokens = [
        "python", "java", "javascript", "typescript", "c#", ".net", "go", "rust",
        "sql", "azure", "aws", "gcp", "kubernetes", "docker", "terraform",
        "microservices", "react", "angular", "fastapi", "django", "spring boot",
        "llm", "rag", "genai", "pytorch", "mlops", "kafka", "redis", "mongodb",
        "graphql", "rest", "soap", "oauth", "ci/cd", "gitlab", "jenkins",
        "architecture", "solution design", "technical leadership", "scrum",
    ]
    if title:
        title_low = title.lower()
        if "architect" in title_low:
            tokens = ["Architecture", "Solution Design", "Technical Leadership", *tokens]
    text = f"{title or ''} {description or ''}".lower()
    return sorted({t for t in tokens if t in text})


def compute_content_hash(job: NormalizedJob) -> str:
    payload = "|".join(
        [
            job.normalized_title,
            (job.location or ""),
            (job.description or "")[:4000],
            " ".join(job.skills),
            str(job.experience_min or ""),
            str(job.experience_max or ""),
            str(job.salary_min or ""),
            job.employment_type or "",
            job.remote_status or "",
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def normalize_job(raw: RawJob) -> NormalizedJob:
    title = (raw.title or "").strip()
    company = (raw.company or "").strip()
    if not title or not company:
        raise ValueError("Job requires title and company")

    description = raw.description or ""
    location = canonicalize_location(raw.location)
    remote = classify_remote(location, description)
    employment = raw.employment_type or classify_employment(description)
    exp_min, exp_max = extract_years(description)
    sal_min, sal_max, currency = parse_salary(raw.salary_text or description)
    skills = derive_skills(description, title)

    normalized = NormalizedJob(
        title=title,
        company=company,
        location=location,
        country=country_from_location(location),
        remote_status=remote,
        employment_type=employment,
        experience_min=exp_min,
        experience_max=exp_max,
        skills=skills,
        salary_min=sal_min,
        salary_max=sal_max,
        salary_currency=currency,
        description=description,
        job_id_external=raw.job_id_external,
        source=raw.source,
        url=raw.url,
        application_url=raw.application_url,
        posted_date=raw.posted_date,
        normalized_title=normalize_title(title),
        fingerprint="",
        content_hash="",
    )
    normalized.fingerprint = fingerprint_job(normalized)
    normalized.content_hash = compute_content_hash(normalized)
    return normalized