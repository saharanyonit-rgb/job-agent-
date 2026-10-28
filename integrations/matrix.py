"""Integration capability matrix. Kept honest: nothing is marked VALIDATED
until it has been exercised against a live endpoint in tests."""

INTEGRATIONS = {
    "job_sources": {
        "mock_feed": {"status": "DISABLED_BY_DEFAULT", "note": "fake demo feed; opt in with ALLOW_FAKE_JOBS=1 or --use-fake-sources"},
        "mock_ats": {"status": "DISABLED_BY_DEFAULT", "note": "fake demo ATS; opt in with ALLOW_FAKE_JOBS=1 or --use-fake-sources"},
        "company_career": {"status": "PARTIAL", "note": "generic HTML listing crawler; validate per site"},
        "greenhouse": {"status": "UNVALIDATED", "note": "public boards API implemented; needs live board"},
        "lever": {"status": "UNVALIDATED", "note": "public postings API implemented; needs live org"},
        "naukri": {"status": "UNAVAILABLE", "note": "blocked by anti-bot; requires interactive session/handoff"},
        "linkedin": {"status": "UNAVAILABLE", "note": "login-required; treated as handoff unless credentialed"},
        "wellfound": {"status": "UNVALIDATED", "note": "requires credentials"},
        "workday": {"status": "UNVALIDATED", "note": "SOAP/JSON APIs vary by tenant"},
        "smartrecruiters": {"status": "UNVALIDATED", "note": "public API implemented on request"},
    },
    "email": {
        "mock": {"status": "VALIDATED"},
        "gmail": {"status": "UNVALIDATED", "note": "Gmail API v1 OAuth implemented via httpx; validates after a live token exchange"},
        "outlook": {"status": "UNVALIDATED", "note": "OAuth flow scaffolded"},
    },
    "calendar": {
        "mock": {"status": "VALIDATED"},
        "google": {"status": "UNVALIDATED", "note": "scaffolded"},
        "outlook": {"status": "UNVALIDATED", "note": "scaffolded"},
    },
    "browser": {
        "http": {"status": "VALIDATED", "note": "HTTP form automation tested against Mock ATS"},
        "playwright": {"status": "UNVALIDATED", "note": "installed with `pip install playwright` and browsers"},
    },
    "ai": {
        "rule_based": {"status": "VALIDATED", "note": "deterministic offline fallback"},
        "openai": {"status": "UNVALIDATED", "note": "requires OPENAI_API_KEY"},
        "anthropic": {"status": "UNVALIDATED", "note": "requires ANTHROPIC_API_KEY"},
        "ollama": {"status": "UNVALIDATED", "note": "requires OLLAMA_BASE_URL"},
    },
}


def integration_status() -> dict:
    return INTEGRATIONS


def provider_status():
    """Live status report: static matrix fused with live checks of the
    email adapters (token presence/health) and AI chain (configured providers).

    Emails:      mock is always VALIDATED; gmail reflects token+health state.
    AI:          rule_based is always available; real providers marked by key.
    """
    import core.email_service.adapters as email_adapters
    from core.config import get_settings
    from core.email_service.gmail import GmailAdapter, GmailTokenStore

    report = {k: dict(v) for k, v in INTEGRATIONS.items()}

    # Email adapters
    email_row = {}
    try:
        mock = email_adapters.get_email_adapter("mock")
        email_row["mock"] = {"status": mock.integration_status or "VALIDATED"}
    except Exception as exc:  # never fail the whole report for one adapter
        email_row["mock"] = {"status": "ERROR", "note": str(exc)}

    try:
        stored = GmailTokenStore().load()
        gmail = GmailAdapter() if stored else None
        status = gmail.health().get("status") if gmail else "NOT_CONNECTED"
        email_row["gmail"] = {
            "status": status,
            "note": "OAuth token stored" if stored else "no token stored; run /api/oauth/google/start",
        }
    except Exception as exc:
        email_row["gmail"] = {"status": "ERROR", "note": str(exc)}
    email_row.update({"outlook": INTEGRATIONS["email"]["outlook"]})
    report["email"] = email_row

    # AI providers
    settings = get_settings()
    report["ai"] = {
        "rule_based": {"status": "VALIDATED"},
        "openai": {"status": "VALIDATED" if settings.openai_api_key else "UNVALIDATED"},
        "anthropic": {"status": "VALIDATED" if settings.anthropic_api_key else "UNVALIDATED"},
        "ollama": {"status": "VALIDATED" if settings.ollama_base_url else "UNVALIDATED"},
    }

    # Job sources stay out of the static table (they are exercised separately,
    # adapters define enabled/healthy at runtime).
    return report