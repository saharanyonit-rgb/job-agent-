from __future__ import annotations

import base64
import email as email_util
import email.utils
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from core.config import get_settings
from core.email_service.adapters import EmailAdapter
from core.email_service.classifier import RawEmail

GMAIL_SCOPE = (
    "https://www.googleapis.com/auth/gmail.readonly "
    "https://www.googleapis.com/auth/gmail.send"
)
TOKEN_URI = "https://oauth2.googleapis.com/token"
AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"


class GmailTokenStore:
    """Persists OAuth tokens to disk (outside source control) in storage_dir.

    Tokens are never written to logs. The file must be treated as a secret.
    """

    def __init__(self, path: str | None = None):
        if path is None:
            settings = get_settings()
            path = str(Path(settings.storage_dir) / "tokens" / "gmail.json")
        self.path = Path(path)

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None

    def save(self, tokens: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(tokens), encoding="utf-8")

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()


def exchange_code(code: str, *, client_id: str, client_secret: str, redirect_uri: str,
                  http: httpx.Client | None = None) -> dict[str, Any]:
    """Exchange an authorization code for tokens via the Google token endpoint."""
    http = http or httpx.Client(timeout=30)
    resp = http.post(
        TOKEN_URI,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token"),
        "expires_in": data.get("expires_in"),
        "scope": data.get("scope"),
        "token_uri": TOKEN_URI,
        "client_id": client_id,
        "client_secret": client_secret,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }


def build_authorization_url(*, client_id: str, redirect_uri: str) -> str:
    from urllib.parse import quote

    return (
        f"{AUTH_URI}?client_id={quote(client_id, safe='')}"
        f"&redirect_uri={quote(redirect_uri, safe='')}"
        "&response_type=code"
        f"&scope={quote(GMAIL_SCOPE, safe=' ').replace(' ', '%20')}"
        "&access_type=offline"
        "&prompt=consent"
    )


class GmailAdapter(EmailAdapter):
    """Real Gmail API v1 adapter (OAuth via httpx; no google SDK required).

    - fetch_unread(): lists is:unread messages and returns parsed RawEmail.
    - send(): encodes an RFC 5322 message as base64url and posts it.
    - mark_processed(): removes the UNREAD label so the message stays out of
      fetch_unread without deleting it.
    """

    name = "gmail"
    integration_status = "UNVALIDATED"

    def __init__(self, credentials: dict[str, Any] | None = None, *, http: httpx.Client | None = None):
        if credentials is None:
            credentials = GmailTokenStore().load() or {}
        self.credentials = credentials
        self.http = http or httpx.Client(timeout=30)

    # -- health/status ------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Liveness probe: call users/me/profile. Never logs tokens."""
        if not self.credentials.get("access_token"):
            return {"status": "UNCONFIGURED", "note": "no OAuth access token"}
        try:
            resp = self.http.get(
                f"{GMAIL_API}/profile",
                headers=self._headers(),
            )
            if resp.status_code != 200:
                return {"status": "ERROR", "detail": resp.status_code}
            return {"status": "VALIDATED", "email": resp.json().get("emailAddress")}
        except Exception as exc:  # noqa: BLE001 - surfaced for the status endpoint
            return {"status": "ERROR", "detail": str(exc)}

    # -- auth helpers ---------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        token = self.credentials.get("access_token")
        if not token:
            raise RuntimeError("Gmail adapter requires an OAuth access token; run the OAuth flow first")
        return {"Authorization": f"Bearer {token}"}

    def _refresh_token(self) -> bool:
        refresh = self.credentials.get("refresh_token")
        if not refresh:
            return False
        try:
            resp = self.http.post(
                TOKEN_URI,
                data={
                    "grant_type": "refresh_token",
                    "client_id": self.credentials.get("client_id", ""),
                    "client_secret": self.credentials.get("client_secret", ""),
                    "refresh_token": refresh,
                },
            )
            if resp.status_code != 200:
                return False
            data = resp.json()
            self.credentials["access_token"] = data.get("access_token")
            if data.get("expires_in"):
                self.credentials["expires_in"] = data.get("expires_in")
            GmailTokenStore().save(self.credentials)
            return True
        except httpx.HTTPError:
            return False

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Issue an authed request, refreshing the token once on a 401."""
        resp = self.http.request(
            method,
            f"{GMAIL_API}{path}",
            headers=self._headers(),
            **kwargs,
        )
        if resp.status_code == 401 and self._refresh_token():
            resp = self.http.request(
                method,
                f"{GMAIL_API}{path}",
                headers=self._headers(),
                **kwargs,
            )
        return resp

    # -- EmailAdapter interface -------------------------------------------------

    def fetch_unread(self) -> list[RawEmail]:
        resp = self._request("GET", "/messages", params={"q": "is:unread", "maxResults": 50})
        resp.raise_for_status()
        payload = resp.json()
        out: list[RawEmail] = []
        for item in payload.get("messages", []):
            try:
                out.append(self._get_message(item["id"]))
            except httpx.HTTPStatusError:
                continue
        return out

    def _get_message(self, msg_id: str) -> RawEmail:
        resp = self._request("GET", f"/messages/{msg_id}", params={"format": "full"})
        resp.raise_for_status()
        data = resp.json()
        headers = {h["name"].lower(): h["value"] for h in data.get("payload", {}).get("headers", [])}
        subject = headers.get("subject")
        from_raw = headers.get("from", "")
        to_raw = headers.get("to", "")
        date_raw = headers.get("date")
        received_at = datetime.utcnow()
        if date_raw:
            parsed = email.utils.parsedate_to_datetime(date_raw)
            if parsed:
                received_at = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        body = self._extract_text(data.get("payload", {}))
        return RawEmail(
            external_id=f"gmail:{msg_id}",
            from_address=self._extract_address(from_raw),
            from_name=self._extract_name(from_raw),
            to_address=self._extract_address(to_raw),
            subject=subject or "",
            body=body,
            received_at=received_at,
        )

    def _extract_text(self, payload: dict[str, Any]) -> str:
        body = self._maybe_decode(payload.get("body", {}).get("data"))
        if body:
            return body
        parts: list[str] = []
        for part in payload.get("parts", []):
            if part.get("mimeType") == "text/plain":
                text = self._maybe_decode(part.get("body", {}).get("data"))
                if text:
                    parts.append(text)
        return "\n".join(parts)

    @staticmethod
    def _maybe_decode(data: str | None) -> str:
        if not data:
            return ""
        try:
            return base64.urlsafe_b64decode(data.encode("ascii")).decode("utf-8", errors="replace")
        except (ValueError, UnicodeEncodeError):
            return ""

    @staticmethod
    def _extract_address(raw: str) -> str | None:
        m = re.search(r"[^\s<>]+@[^\s<>]+", raw or "")
        return m.group(0) if m else None

    @staticmethod
    def _extract_name(raw: str) -> str | None:
        m = re.match(r"^\s*([^<@]+)@", raw or "") or re.match(r"^\s*(.+?)\s*<[^>]+>", raw or "")
        if not m:
            return None
        name = m.group(1).strip().strip('"')
        return name or None

    def send(self, to: str, subject: str, body: str) -> dict[str, Any]:
        msg = email_util.message.EmailMessage()
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        resp = self._request("POST", "/messages/send", json={"raw": raw})
        resp.raise_for_status()
        data = resp.json()
        return {"status": "ok", "id": data.get("id"), "to": to, "subject": subject}

    def mark_processed(self, email_id: str) -> None:
        if email_id.startswith("gmail:"):
            email_id = email_id.split(":", 1)[1]
        resp = self._request(
            "POST",
            f"/messages/{email_id}/modify",
            json={"removeLabelIds": ["UNREAD"]},
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Gmail mark_processed failed: {resp.status_code}")


def register_gmail_adapter(credentials: dict[str, Any], *, http: httpx.Client | None = None) -> GmailAdapter:
    """Build a GmailAdapter from stored/exchanged credentials and register it."""
    from core.email_service.adapters import adapters

    adapter = GmailAdapter(credentials, http=http)
    adapter.integration_status = "VALIDATED" if adapter.health().get("status") == "VALIDATED" else "CONFIGURED"
    adapters["gmail"] = adapter
    GmailTokenStore().save(credentials)
    return adapter