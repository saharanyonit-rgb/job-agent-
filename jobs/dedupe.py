from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from core.ai.service import chain
from core.database import models as m


def _norm(s: str | None) -> str:
    return (s or "").lower().strip()


def job_key(job: m.Job) -> tuple[str, str, str, str]:
    return (_norm(job.company), _norm(job.normalized_title or job.title), _norm(job.location), _norm(job.job_id_external))


def url_key(job: m.Job) -> str | None:
    if not job.url:
        return None
    return job.url.rstrip("/").lower()


def _job_embedding_text(job: m.Job) -> str:
    """Create text representation for embedding."""
    parts = [
        job.company or "",
        job.normalized_title or job.title or "",
        job.location or "",
        " ".join(job.skills or []) if job.skills else "",
    ]
    return " | ".join(p for p in parts if p)


def _semantic_similarity(text1: str, text2: str) -> float:
    """Compute semantic similarity using LLM. Returns 0.0-1.0."""
    try:
        prompt = (
            "Rate the semantic similarity between these two job postings on a scale of 0.0 to 1.0.\n"
            "Consider: same role, same company, same location, same requirements.\n"
            "STRICT: Different job titles (e.g., Software Engineer vs Data Scientist) should score LOW.\n"
            "Return only a JSON object: {\"score\": 0.85}\n\n"
            f"JOB 1:\n{text1}\n\nJOB 2:\n{text2}"
        )
        result = chain.structured_output(prompt)
        return max(0.0, min(1.0, float(result.get("score", 0.0))))
    except Exception:
        return 0.0


def _companies_match(company1: str | None, company2: str | None) -> bool:
    """Check if two company names likely represent the same company."""
    if not company1 or not company2:
        return False
    c1 = company1.lower().strip()
    c2 = company2.lower().strip()
    if c1 == c2:
        return True
    # Handle common variations
    c1_clean = c1.replace(".", "").replace(",", "").replace("inc", "").replace("llc", "").replace("corp", "").strip()
    c2_clean = c2.replace(".", "").replace(",", "").replace("inc", "").replace("llc", "").replace("corp", "").strip()
    return c1_clean == c2_clean


def find_duplicate_groups(jobs: Iterable[m.Job]) -> list[list[m.Job]]:
    """Group jobs that likely represent the same canonical job across sources."""
    jobs = list(jobs)
    groups: dict[str, list[m.Job]] = defaultdict(list)

    for job in jobs:
        if job.fingerprint:
            groups[f"fp:{job.fingerprint}"].append(job)

    # Cross-source merge: same company + title + location, or same URL
    cross: dict[str, list[m.Job]] = defaultdict(list)
    for job in jobs:
        cross[("ct:" + "|".join(job_key(job)[:3]))].append(job)
        uk = url_key(job)
        if uk:
            cross[f"url:{uk}"].append(job)

    # Semantic similarity grouping for near-duplicates (SAME COMPANY ONLY)
    semantic_groups: list[list[m.Job]] = []
    processed: set[int] = set()
    for i, job1 in enumerate(jobs):
        if job1.id in processed:
            continue
        group = [job1]
        text1 = _job_embedding_text(job1)
        for job2 in jobs[i + 1:]:
            if job2.id in processed:
                continue
            # Only run semantic similarity for same-company jobs
            # Cross-company semantic matching is too risky for false positives
            if not _companies_match(job1.company, job2.company):
                continue
            text2 = _job_embedding_text(job2)
            score = _semantic_similarity(text1, text2)
            if score >= 0.85:  # High threshold for semantic duplicates
                group.append(job2)
                processed.add(job2.id)
        if len(group) > 1:
            semantic_groups.append(group)
        processed.add(job1.id)

    merged: dict[int, m.Job] = {}
    final: list[list[m.Job]] = []
    seen_ids: set[int] = set()

    candidates_groups = list(groups.values()) + list(cross.values()) + semantic_groups
    for group in candidates_groups:
        unique: list[m.Job] = []
        ids = set()
        for job in group:
            if job.id not in ids:
                ids.add(job.id)
                unique.append(job)
        if len(unique) > 1:
            # skip if all already covered by another bigger group
            if ids.issubset(seen_ids):
                continue
            seen_ids |= ids
            final.append(unique)
    return final


def merge_duplicates(jobs: list[m.Job]) -> tuple[m.Job | None, list[m.Job]]:
    """Pick the richest record as canonical; mark others merged_into."""
    if not jobs:
        return None, []

    def richness(job: m.Job) -> int:
        return sum(
            [
                1 if job.description else 0,
                1 if job.application_url else 0,
                1 if job.job_id_external else 0,
                1 if job.salary_min else 0,
                len(job.skills or []),
            ]
        )

    canonical = max(jobs, key=richness)
    others = [j for j in jobs if j.id != canonical.id]
    for other in others:
        other.merged_into_id = canonical.id
        if other.verification_status not in ("ACTIVE",):
            other.verification_status = "DUPLICATE"
        other.status = "DUPLICATE"
    return canonical, others