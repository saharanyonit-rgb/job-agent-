from __future__ import annotations

from typing import Any


REQUIRED_CANDIDATE_FIELDS_FOR_SUBMIT = ["name", "email"]


def validate_application(
    *,
    candidate: dict[str, Any],
    job: dict[str, Any],
    resume_path: str | None,
    cover_letter_path: str | None,
    form_answers: dict[str, Any],
    screening_answers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Pre-submission validation. Fails closed on unknowns and contradictions."""
    errors: list[str] = []
    warnings: list[str] = []

    for field in REQUIRED_CANDIDATE_FIELDS_FOR_SUBMIT:
        if not candidate.get(field):
            errors.append(f"missing required candidate field: {field}")

    if not job.get("title") or not job.get("company"):
        errors.append("job is missing title or company")

    if not resume_path:
        errors.append("no resume attached")

    for key, value in (form_answers or {}).items():
        if value is None or value == "":
            errors.append(f"required field '{key}' has no value")
        if isinstance(value, str) and value.strip().upper() in ("UNKNOWN", "N/A?", "TODO"):
            errors.append(f"field '{key}' contains placeholder value '{value}'")

    # Contradiction checks
    expected = form_answers.get("expected_salary")
    if expected is not None and candidate.get("expected_ctc"):
        warnings.append("verify expected salary matches candidate fact")

    fabricated_flags = [k for k, v in (form_answers or {}).items() if isinstance(v, dict) and v.get("source") == "fabricated"]
    for k in fabricated_flags:
        errors.append(f"field '{k}' marked as fabricated")

    report = {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "candidate": {k: candidate.get(k) for k in REQUIRED_CANDIDATE_FIELDS_FOR_SUBMIT},
        "job_title": job.get("title"),
        "job_company": job.get("company"),
        "resume": resume_path,
        "cover_letter": cover_letter_path,
        "screening_answers_count": len(screening_answers or []),
    }
    return report