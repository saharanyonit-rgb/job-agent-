from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from job_agent.database import models as m


def _norm(s: str | None) -> str:
    return (s or "").lower().strip()


def job_key(job: m.Job) -> tuple[str, str, str, str]:
    return (_norm(job.company), _norm(job.normalized_title or job.title), _norm(job.location), _norm(job.job_id_external))


def url_key(job: m.Job) -> str | None:
    if not job.url:
        return None
    return job.url.rstrip("/").lower()


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

    merged: dict[int, m.Job] = {}
    final: list[list[m.Job]] = []
    seen_ids: set[int] = set()

    candidates_groups = list(groups.values()) + list(cross.values())
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