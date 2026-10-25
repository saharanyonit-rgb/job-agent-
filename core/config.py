from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "sqlite:///./storage/job_agent.db"

    # AI providers
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"
    ollama_base_url: str = ""
    ollama_model: str = "llama3.1"
    llm_providers: str = "openai,anthropic,ollama,rule_based"

    # OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/oauth/google/callback"
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_redirect_uri: str = "http://localhost:8000/api/oauth/microsoft/callback"

    # Notifications
    telegram_bot_token: str = ""

    # Policy defaults
    app_mode: str = "PREPARE_AND_APPROVE"
    minimum_match: int = 80
    max_daily_applications: int = 20

    # Phase 3 / Phase 16 quality gates
    max_applications_per_company: int = 3
    max_applications_per_role: int = 5
    company_cooldown_days: int = 30
    min_evidence_confidence: float = 0.8
    max_low_confidence_applications: int = 5
    low_confidence_window_hours: int = 24
    low_confidence_window_align_utc_day: bool = True
    max_hard_failures: int = 0

    # Phase 7/11 outcome analysis
    outcome_min_sample: int = 10

    # Scheduler intervals (minutes)
    discover_interval_minutes: int = 120
    verify_interval_minutes: int = 240
    followup_interval_minutes: int = 1440

    # Runtime
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    log_level: str = "INFO"
    agent_enabled: bool = True
    browser_headless: bool = True
    storage_dir: str = "./storage"

    # Browser backend selection for application submission.
    #   auto       -> Playwright when installed, otherwise HTTP fallback
    #   playwright -> require Playwright (real-browser form filling)
    #   http       -> plain HTTP form backend only
    browser_backend: str = "auto"


@lru_cache
def get_settings() -> Settings:
    return Settings()