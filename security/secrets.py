from __future__ import annotations

import os

from job_agent.core.config import get_settings


class SecretManager:
    """Loads secrets from environment/.env. Never stores in source control."""

    SENSITIVE = {
        "openai_api_key",
        "anthropic_api_key",
        "google_client_secret",
        "microsoft_client_secret",
        "telegram_bot_token",
    }

    def __init__(self, env: dict[str, str] | None = None):
        self.env = env or dict(os.environ)
        self.settings = get_settings()

    def get(self, name: str) -> str:
        return self.env.get(name, "")

    def safe_env_example(self) -> str:
        """List of unset sensitive vars, for the user to fill manually."""
        missing = [n for n in self.SENSITIVE if not getattr(self.settings, n, "")]
        return missing


def redact(text: str) -> str:
    """Removes likely secrets from log text."""
    import re

    text = re.sub(r"Bearer [A-Za-z0-9\-._]+", "Bearer [REDACTED]", text)
    text = re.sub(r"api[_-]?key['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9\-_]+", "api_key=[REDACTED]", text)
    return text