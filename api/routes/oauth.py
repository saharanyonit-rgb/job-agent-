from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Query

from job_agent.core.config import get_settings

router = APIRouter(prefix="/api/oauth", tags=["oauth"])

# OAuth is active once Google credentials are configured. The callback exchanges
# the authorization code server-side and stores tokens (offline access) in the
# GmailTokenStore under storage_dir so the email/calendar adapters can use them.
# Tokens are never logged or committed.


@router.get("/google/start")
def google_start() -> dict:
    settings = get_settings()
    if not settings.google_client_id:
        raise HTTPException(400, "GOOGLE_CLIENT_ID not configured")
    from job_agent.email_service.gmail import build_authorization_url

    url = build_authorization_url(
        client_id=settings.google_client_id,
        redirect_uri=settings.google_redirect_uri,
    )
    return {"authorization_url": url, "scopes": ["gmail.readonly", "gmail.send", "calendar"]}


@router.get("/google/callback")
def google_callback(code: str = Query("")) -> dict:
    settings = get_settings()
    if not settings.google_client_secret:
        raise HTTPException(400, "GOOGLE_CLIENT_SECRET not configured")
    if not code:
        raise HTTPException(400, "missing authorization code")

    from job_agent.email_service.gmail import exchange_code, register_gmail_adapter

    try:
        credentials = exchange_code(
            code,
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            redirect_uri=settings.google_redirect_uri,
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(400, f"token exchange failed: {exc.response.status_code}")
    except httpx.HTTPError as exc:
        raise HTTPException(400, f"token exchange failed: {exc}")

    adapter = register_gmail_adapter(credentials)
    status = adapter.health()
    return {"status": "connected", "integration_status": status.get("status", "CONFIGURED"), "email": status.get("email")}


@router.get("/google/status")
def google_status() -> dict:
    from job_agent.email_service.gmail import GmailTokenStore

    store = GmailTokenStore()
    creds = store.load()
    if not creds:
        return {"status": "NOT_CONNECTED"}
    from job_agent.email_service.gmail import GmailAdapter

    adapter = GmailAdapter(creds)
    return {"status": adapter.health()}


@router.get("/microsoft/start")
def microsoft_start() -> dict:
    settings = get_settings()
    if not settings.microsoft_client_id:
        raise HTTPException(400, "MICROSOFT_CLIENT_ID not configured")
    url = (
        "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        f"?client_id={settings.microsoft_client_id}"
        f"&redirect_uri={settings.microsoft_redirect_uri}"
        "&response_type=code&scope=Mail.Read%20Mail.Send%20Calendars.ReadWrite&prompt=consent"
    )
    return {"authorization_url": url}


@router.get("/microsoft/callback")
def microsoft_callback(code: str = Query("")) -> dict:
    return {"status": "code_received", "exchange_pending": True, "hint": "Microsoft Graph OAuth is not yet wired server-side"}