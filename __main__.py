from __future__ import annotations

"""CLI entry point: `python -m core <command>`.

Commands
--------
serve   Run the FastAPI app (default http://127.0.0.1:8000).
ats     Run the bundled Mock ATS (default port 9000).
seed    Create the demo candidate (Asha Patel) from bundled fixtures.
run     One full pipeline iteration: discover -> verify -> dedupe -> match -> prepare.
daily   Full closed loop: cycle + outcome analysis + skill gaps + career plan + report.
status  Print source health + candidate counts + daily report summary.
import_resume  Load your resume from the resume/ folder into the pipeline.
"""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from core.core.config import get_settings
from core.core.logging_config import get_logger

log = get_logger(__name__)

RESUME_DIR = Path(__file__).resolve().parent / "resume"
RESUME_TYPES = {".pdf", ".docx", ".txt"}
RESUME_MARKER = Path(get_settings().storage_dir) / "resume_imported.hash"


def _seed_candidate(db) -> list[int]:
    import json

    from core.candidates.service import CandidateService

    svc = CandidateService(db)
    cand = svc.get_or_create("asha.patel@example.com")
    cand.name = "Asha Patel"
    cand.phone = "+91-9876543210"
    cand.location = "Bengaluru, India"
    cand.work_authorization = "India"
    cand.visa_status = "Not required"
    cand.years_of_experience = 10.0
    cand.current_title = "Technical Architect"
    cand.notice_period = "30 days"
    cand.current_ctc = "4000000"
    cand.expected_ctc = "5500000"
    cand.employment_type = "Full-time"
    cand.remote_preference = "Remote"
    cand.relocation_preference = "no"
    db.commit()

    facts = {
        "name": "Asha Patel",
        "email": "asha.patel@example.com",
        "phone": "+91-9876543210",
        "location": "Bengaluru, India",
        "years_of_experience": 10.0,
        "current_title": "Technical Architect",
        "notice_period": "30 days",
        "expected_ctc": "5500000",
        "work_authorization": "India",
        "visa_status": "Not required",
        "skills": [
            "python", "azure", "kubernetes", "microservices", "architecture",
            "rest", "graphql", "sql", "terraform", "docker", "technical leadership",
            "solution design", "fastapi", "postgresql",
        ],
        "technologies": ["python", "azure", "kubernetes", "docker", "terraform", "fastapi"],
        "previous_employers": ["Acme Systems", "Beta Digital"],
        "projects": ["Platform re-architecture", "Cloud migration"],
        "certifications": ["Azure Solutions Architect Expert"],
        "education": [{"degree": "B.Tech"}],
        "summary": "Technical architect with 10 years designing cloud-native platforms.",
        "relocation_preference": "no",
        "remote_preference": "Remote",
    }
    for key, value in facts.items():
        payload = str(value) if not isinstance(value, (list, dict)) else json.dumps(value)
        svc.confirm_fact(cand.id, key, payload)
    for key, value in {
        "target_roles": "Technical Architect,Solution Architect,Cloud Architect,AI Architect",
        "allowed_locations": "Bengaluru,Remote India,Remote",
        "minimum_salary": "3500000",
        "remote_preference": "Remote",
        "excluded_companies": "",
        "preferred_companies": "",
    }.items():
        svc.set_preference(cand.id, key, value)
    db.commit()
    return [cand.id]


_REAL_SOURCE_NAMES = ["greenhouse", "lever", "company_career", "linkedin", "indeed", "jsearch"]


def _restrict_sources(names: list[str] | None) -> None:
    """Disable every source adapter except the requested ones.

    Default: REAL sources only (fake demo feeds are excluded unless explicitly
    requested via names=[...] or ALLOW_FAKE_JOBS=1).
    """
    from core.discovery.base import registry, register_default_adapters

    register_default_adapters()
    allowed = names or list(_REAL_SOURCE_NAMES)
    for adapter in registry.all():
        adapter.enabled = adapter.name in allowed
    log.info("active sources: %s", allowed)


FAKE_SOURCES = ("mock_feed", "mock_ats")


def _purge_fake_jobs(db) -> int:
    """Delete jobs (and dependent rows) that came from fake/demo sources."""
    from core.database import models as m

    removed = 0
    for job in db.query(m.Job).filter(m.Job.source.in_(FAKE_SOURCES)).all():
        app_ids = [a.id for a in db.query(m.Application).filter(m.Application.job_id == job.id).all()]
        for table, column in (
            (m.ApplicationEvent, m.ApplicationEvent.application_id),
            (m.ApplicationStrategy, m.ApplicationStrategy.application_id),
        ):
            if app_ids:
                db.query(table).filter(column.in_(app_ids)).delete(synchronize_session=False)
        db.query(m.Application).filter(m.Application.job_id == job.id).delete(synchronize_session=False)
        db.query(m.JobEvent).filter(m.JobEvent.job_id == job.id).delete(synchronize_session=False)
        db.query(m.Evidence).filter(
            m.Evidence.entity_type == "job", m.Evidence.entity_id == job.id
        ).delete(synchronize_session=False)
        db.delete(job)
        removed += 1
    db.commit()
    return removed


def _offline_verifier() -> None:
    """Verify with a local stub so the demo never needs the network."""
    import core.jobs.service as jobs_service
    from core.jobs.verify import JobVerifier

    class _AlwaysOk:
        def check(self, url: str) -> dict[str, Any]:
            return {"status_code": 200, "ok": True, "final_url": url, "evidence": "cli-offline-verified"}

    real = jobs_service.JobVerifier

    def _factory(*args: Any, **kwargs: Any) -> JobVerifier:
        return real(url_checker=_AlwaysOk())

    jobs_service.JobVerifier = _factory


def _apply_source_policy(args: argparse.Namespace) -> None:
    """Configure which job sources run for this command.

    Default policy: REAL sources only; fake/demo feeds are excluded and any
    fake jobs already stored are purged. ``--use-fake-sources`` (or legacy
    ``--offline``) opts back into the bundled mock feed / Mock ATS demo.
    """
    if getattr(args, "use_fake_sources", False) or getattr(args, "offline", False):
        _restrict_sources(["mock_feed", "mock_ats"])
        _offline_verifier()
        return
    _restrict_sources(None)  # real sources only
    if not getattr(args, "keep_fake_jobs", False):
        from core.database.base import SessionLocal

        db = SessionLocal()
        try:
            removed = _purge_fake_jobs(db)
        finally:
            db.close()
        if removed:
            log.info("purged %d fake/demo job(s) from the database", removed)


def _require_candidate(db) -> int:
    from core.database import models as m

    cand = db.query(m.Candidate).order_by(m.Candidate.id).first()
    if cand is None:
        print("No candidate found. Run `python -m core seed` first.")
        raise SystemExit(2)
    return cand.id


def _latest_resume_file() -> Path | None:
    if not RESUME_DIR.is_dir():
        return None
    files = [
        p for p in RESUME_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in RESUME_TYPES
    ]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def _primary_candidate_id(db, models) -> int:
    cand = db.query(models.Candidate).order_by(models.Candidate.id).first()
    if cand is None:
        cand = models.Candidate()
        db.add(cand)
        db.commit()
        db.refresh(cand)
    return cand.id


def _import_resume(db, path: Path, candidate_id: int) -> dict[str, Any]:
    import re

    from core.candidates.resume_parser import extract_text
    from core.candidates.service import CandidateService
    from core.database import models as m

    db.query(m.CandidateFact).filter(m.CandidateFact.candidate_id == candidate_id).delete()
    db.query(m.CandidateSkill).filter(m.CandidateSkill.candidate_id == candidate_id).delete()
    db.query(m.Resume).filter(m.Resume.candidate_id == candidate_id).delete()
    db.commit()

    svc = CandidateService(db)
    resume = svc.upload_resume(candidate_id, path, path.name)

    text = extract_text(path).text
    facts = svc.get_facts(candidate_id)

    def _fact(key: str) -> Any:
        return facts.value(key)

    email = _fact("email") or (re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text) or [None])[0]
    phone = _fact("phone") or (re.search(r"\+?\d[\d\s-]{9,14}", text) or [None])[0]
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    name = _fact("name") or first_line
    location = _fact("location")
    if location is None:
        for ln in text.splitlines():
            s = ln.strip()
            if s and "email" not in s.lower() and "linkedin" not in s.lower() and not s.startswith("http") and ("india" in s.lower() or "," in s):
                location = s
                break

    income = re.search(r"(?:minimum|expected)?\s*(?:salary|ctc)\s*:?\s*([\d,]+)", text, re.IGNORECASE)
    expected_ctc = _fact("expected_ctc") or (income.group(1) if income else "1200000")
    notice = re.search(r"notice\s*(?:period)?\s*:?\s*([\w\s]+)", text, re.IGNORECASE)
    notice_period = _fact("notice_period") or (notice.group(1).strip() if notice else "30 days")
    work_auth = _fact("work_authorization") or "India"
    remote = _fact("remote_preference") or ""

    extras = {
        "name": name,
        "email": email,
        "phone": phone,
        "location": location,
        "work_authorization": work_auth,
        "notice_period": notice_period,
        "expected_ctc": expected_ctc,
    }
    for key, value in extras.items():
        if value and not _fact(key):
            svc.confirm_fact(candidate_id, key, str(value))

    facts = svc.get_facts(candidate_id)
    cand = db.get(m.Candidate, candidate_id)
    for key, col in {
        "name": "name", "email": "email", "phone": "phone", "location": "location",
        "notice_period": "notice_period", "expected_ctc": "expected_ctc",
        "current_title": "current_title", "work_authorization": "work_authorization",
    }.items():
        value = facts.value(key)
        if value and getattr(cand, col, None) in (None, ""):
            setattr(cand, col, str(value))
    db.commit()

    for key in [
        "target_roles", "allowed_locations", "minimum_salary",
        "remote_preference", "preferred_companies", "excluded_companies",
    ]:
        svc.set_preference(candidate_id, key, remote if key == "remote_preference" else "")

    RESUME_MARKER.parent.mkdir(parents=True, exist_ok=True)
    RESUME_MARKER.write_text(hashlib.sha256(path.read_bytes()).hexdigest(), encoding="utf-8")
    return {
        "candidate_id": candidate_id,
        "filename": resume.filename,
        "path": resume.path,
        "parsed": resume.parsed,
        "contact": {k: extras[k] for k in ("name", "email", "phone", "location", "notice_period", "expected_ctc", "work_authorization")},
    }


def _sync_resume(db) -> None:
    """Auto-import a resume from the resume/ folder the first time it appears (or changes)."""
    path = _latest_resume_file()
    if path is None:
        return
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    im_ported = RESUME_MARKER.exists() and RESUME_MARKER.read_text(encoding="utf-8").strip() == digest
    from core.database import models as m

    has_resume = db.query(m.Resume).count() > 0
    if im_ported and has_resume:
        return
    candidate_id = _primary_candidate_id(db, m)
    log.info("New resume detected (%s); importing into pipeline.", path.name)
    _import_resume(db, path, candidate_id)


def cmd_import_resume(args: argparse.Namespace) -> int:
    from core.database.base import SessionLocal, init_db

    init_db()
    from core.database import models as m

    path = Path(args.path) if args.path else _latest_resume_file()
    if path is None:
        print("No resume found. Put your resume (.pdf/.docx/.txt) in the 'resume' folder.")
        return 1
    if not path.is_file():
        print(f"Resume file not found: {path}")
        return 1
    db = SessionLocal()
    try:
        candidate_id = _primary_candidate_id(db, m)
        out = _import_resume(db, path, candidate_id)
    finally:
        db.close()
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("core.api.app:app", host=args.host, port=args.port)
    return 0


def cmd_ats(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("core.mock_ats.app:app", host=args.host, port=args.port)
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    from core.database.base import SessionLocal, init_db

    init_db(reset=args.reset)
    if args.reset:
        RESUME_MARKER.unlink(missing_ok=True)
    db = SessionLocal()
    try:
        ids = _seed_candidate(db)
    finally:
        db.close()
    print(json.dumps({"seeded_candidates": ids}, indent=2))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from core.database.base import SessionLocal, init_db
    from core.orchestrator.orchestrator import JobAgentOrchestrator

    init_db()
    _apply_source_policy(args)
    db = SessionLocal()
    try:
        _sync_resume(db)
        candidate_id = _require_candidate(db)
        orch = JobAgentOrchestrator(db)
        summary = orch.cycle(candidate_id, dry_run=not args.submit)
    finally:
        db.close()
    print(json.dumps(summary, indent=2, default=str))
    return 0


def cmd_daily(args: argparse.Namespace) -> int:
    from core.database.base import SessionLocal, init_db
    from core.orchestrator.orchestrator import JobAgentOrchestrator

    init_db()
    _apply_source_policy(args)
    db = SessionLocal()
    try:
        _sync_resume(db)
        candidate_id = _require_candidate(db)
        orch = JobAgentOrchestrator(db)
        summary = orch.run_daily_career_loop(candidate_id, dry_run=not args.submit)
    finally:
        db.close()
    print(json.dumps(summary, indent=2, default=str))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from core.database.base import SessionLocal, init_db
    from core.database import models as m

    init_db()
    db = SessionLocal()
    try:
        sources = db.query(m.JobSource).all()
        out = {
            "sources": [
                {
                    "name": s.name,
                    "healthy": s.healthy,
                    "integration_status": s.integration_status,
                    "failures": s.failures,
                    "last_checked": s.last_checked.isoformat() if s.last_checked else None,
                    "error": s.error,
                }
                for s in sources
            ],
            "counts": {
                "candidates": db.query(m.Candidate).count(),
                "jobs": db.query(m.Job).count(),
                "applications": db.query(m.Application).count(),
                "evidence": db.query(m.Evidence).count(),
                "daily_reports": db.query(m.DailyReport).count(),
            },
        }
    finally:
        db.close()
    print(json.dumps(out, indent=2, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="core",
        description="Evidence-based closed-loop career agent.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the FastAPI app")
    serve.add_argument("--host", default=None, help="default from settings.env / 127.0.0.1")
    serve.add_argument("--port", type=int, default=None)
    serve.set_defaults(func=cmd_serve)

    ats = sub.add_parser("ats", help="Run the bundled Mock ATS")
    ats.add_argument("--host", default="127.0.0.1")
    ats.add_argument("--port", type=int, default=9000)
    ats.set_defaults(func=cmd_ats)

    seed = sub.add_parser("seed", help="Create the demo candidate")
    seed.add_argument("--reset", action="store_true", help="drop and recreate all tables first")
    seed.set_defaults(func=cmd_seed)

    run = sub.add_parser("run", help="One pipeline iteration (real job sources by default)")
    run.add_argument("--submit", action="store_true", help="actually submit (no --submit = dry run)")
    run.add_argument(
        "--offline",
        action="store_true",
        help="legacy alias for --use-fake-sources (bundled mock feed + Mock ATS demo)",
    )
    run.add_argument(
        "--use-fake-sources",
        action="store_true",
        help="use the bundled fake/demo sources (mock_feed + Mock ATS) instead of real job sites",
    )
    run.add_argument("--keep-fake-jobs", action="store_true", help="don't purge previously stored fake/demo jobs")
    run.set_defaults(func=cmd_run)

    daily = sub.add_parser("daily", help="Full closed loop + daily report (real job sources by default)")
    daily.add_argument("--submit", action="store_true", help="actually submit (no --submit = dry run)")
    daily.add_argument(
        "--offline",
        action="store_true",
        help="legacy alias for --use-fake-sources (bundled mock feed + Mock ATS demo)",
    )
    daily.add_argument(
        "--use-fake-sources",
        action="store_true",
        help="use the bundled fake/demo sources (mock_feed + Mock ATS) instead of real job sites",
    )
    daily.add_argument("--keep-fake-jobs", action="store_true", help="don't purge previously stored fake/demo jobs")
    daily.set_defaults(func=cmd_daily)

    status = sub.add_parser("status", help="Print source health and counts")
    status.set_defaults(func=cmd_status)

    imp = sub.add_parser(
        "import_resume",
        help="Load your resume (pdf/docx/txt) into the pipeline and rebuild the candidate profile",
    )
    imp.add_argument("path", nargs="?", default=None, help="resume file path (default: newest file in resume/)")
    imp.set_defaults(func=cmd_import_resume)

    return parser


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(args, "host") and args.host is None:
        args.host = settings.api_host
    if hasattr(args, "port") and args.port is None:
        args.port = settings.api_port
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())