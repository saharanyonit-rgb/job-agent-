from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from core.email_service.classifier import RawEmail


class EmailAdapter(abc.ABC):
    name: str = "base"
    integration_status: str = "UNVALIDATED"

    @abc.abstractmethod
    def fetch_unread(self) -> list[RawEmail]:
        ...

    @abc.abstractmethod
    def send(self, to: str, subject: str, body: str) -> dict[str, Any]:
        ...

    @abc.abstractmethod
    def mark_processed(self, email_id: str) -> None:
        ...


@dataclass
class Mailbox:
    messages: list[RawEmail] = field(default_factory=list)

    def deliver(self, email: RawEmail) -> None:
        self.messages.insert(0, email)

    def clear(self) -> None:
        self.messages.clear()


_IN_MEMORY: Mailbox | None = None


def get_mock_mailbox() -> Mailbox:
    global _IN_MEMORY
    if _IN_MEMORY is None:
        _IN_MEMORY = Mailbox()
    return _IN_MEMORY


class MockEmailAdapter(EmailAdapter):
    """In-memory adapter for tests. Real adapters follow this interface."""

    name = "mock"
    integration_status = "VALIDATED"

    def __init__(self, mailbox: Mailbox | None = None):
        self.mailbox = mailbox or get_mock_mailbox()
        self.processed: set[str] = set()

    def fetch_unread(self) -> list[RawEmail]:
        return [m for m in self.mailbox.messages if m.external_id not in self.processed]

    def send(self, to: str, subject: str, body: str) -> dict[str, Any]:
        return {"status": "ok", "to": to, "subject": subject}

    def mark_processed(self, email_id: str) -> None:
        self.processed.add(email_id)


class GmailAdapter(EmailAdapter):
    """Google Mail OAuth adapter (Gmail API v1), implemented in core.email_service.gmail.

    Real implementation performs token storage, refresh, fetch/send/mark-read via
    the Gmail API using httpx (no google SDK needed). This placeholder is kept so
    imports referencing `core.email_service.adapters.GmailAdapter` keep working.
    """
    name = "gmail"
    integration_status = "UNVALIDATED"

    def __init__(self, access_token: str | None = None):
        self.access_token = access_token

    def fetch_unread(self) -> list[Any]:
        raise NotImplementedError("use core.email_service.gmail.GmailAdapter for the real adapter")

    def send(self, to: str, subject: str, body: str) -> dict[str, Any]:
        raise NotImplementedError("use core.email_service.gmail.GmailAdapter for the real adapter")

    def mark_processed(self, email_id: str) -> None:
        raise NotImplementedError("use core.email_service.gmail.GmailAdapter for the real adapter")


class OutlookAdapter(EmailAdapter):
    """Microsoft Graph Mail adapter (OAuth)."""
    name = "outlook"
    integration_status = "UNVALIDATED"

    def __init__(self, access_token: str | None = None):
        self.access_token = access_token

    def _headers(self) -> dict[str, str]:
        if not self.access_token:
            raise RuntimeError("Outlook adapter requires an OAuth access token")
        return {"Authorization": f"Bearer {self.access_token}"}


adapters: dict[str, EmailAdapter] = {}


def get_email_adapter(name: str | None = None) -> EmailAdapter:
    name = name or "mock"
    if name not in adapters:
        if name == "gmail":
            from core.email_service.gmail import GmailAdapter

            adapters[name] = GmailAdapter()
        elif name == "outlook":
            adapters[name] = OutlookAdapter()
        else:
            adapters[name] = MockEmailAdapter()
    return adapters[name]