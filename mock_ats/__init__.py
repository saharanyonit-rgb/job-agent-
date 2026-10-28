"""Bundled Mock ATS: a tiny FastAPI app that simulates an applicant tracking
system so the full discover -> verify -> match -> submit loop can be exercised
end-to-end without touching live job boards.

Run it with:  python -m core ats   (default http://127.0.0.1:9000)
"""

try:  # normal case: package imported as part of the core package
    from core.mock_ats.store import JOBS, SUBMISSIONS
except ModuleNotFoundError:  # fallback: standalone `mock_ats` on sys.path
    from mock_ats.store import JOBS, SUBMISSIONS

__all__ = ["JOBS", "SUBMISSIONS"]
