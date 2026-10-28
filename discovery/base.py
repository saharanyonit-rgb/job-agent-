from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.jobs.normalize import RawJob


class IntegrationStatus(str, Enum):
    VALIDATED = "VALIDATED"
    PARTIAL = "PARTIAL"
    UNVALIDATED = "UNVALIDATED"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


@dataclass
class DiscoveryResult:
    jobs: list[RawJob] = field(default_factory=list)
    source: str = ""
    status: IntegrationStatus = IntegrationStatus.UNVALIDATED
    error: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


class SourceAdapter(abc.ABC):
    """Contract every job source must implement."""

    name: str = "base"
    integration_status: IntegrationStatus = IntegrationStatus.UNVALIDATED
    enabled: bool = True

    @abc.abstractmethod
    def discover_jobs(self, query: dict[str, Any] | None = None) -> DiscoveryResult:
        ...

    @abc.abstractmethod
    def get_job(self, job_ref: str) -> RawJob | None:
        ...

    @abc.abstractmethod
    def verify_job(self, job_ref: str) -> dict[str, Any]:
        ...

    @abc.abstractmethod
    def get_application_url(self, job_ref: str) -> str | None:
        ...


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, SourceAdapter] = {}

    def register(self, adapter: SourceAdapter) -> None:
        self._adapters[adapter.name] = adapter

    def get(self, name: str) -> SourceAdapter | None:
        return self._adapters.get(name)

    def all(self) -> list[SourceAdapter]:
        return list(self._adapters.values())

    def statuses(self) -> list[dict[str, Any]]:
        return [
            {
                "name": a.name,
                "integration_status": a.integration_status.value,
                "enabled": a.enabled,
            }
            for a in self._adapters.values()
        ]


registry = AdapterRegistry()


def _real_sources_only() -> bool:
    """True when fake/demo job sources must be excluded (default)."""
    import os

    return os.getenv("ALLOW_FAKE_JOBS", "").strip().lower() not in ("1", "true", "yes")


def register_default_adapters() -> AdapterRegistry:
    from core.discovery.adapters.company_career import CompanyCareerAdapter
    from core.discovery.adapters.greenhouse import GreenhouseAdapter
    from core.discovery.adapters.lever import LeverAdapter
    from core.discovery.adapters.linkedin import LinkedInAdapter, LinkedInGuestAdapter
    from core.discovery.adapters.indeed import IndeedAdapter, JSearchAdapter

    if not registry.get(GreenhouseAdapter.name):
        registry.register(GreenhouseAdapter())
    if not registry.get(LeverAdapter.name):
        registry.register(LeverAdapter())
    if not registry.get(CompanyCareerAdapter.name):
        registry.register(CompanyCareerAdapter())
    if not registry.get(LinkedInAdapter.name):
        registry.register(LinkedInAdapter())
    if not registry.get(LinkedInGuestAdapter.name):
        registry.register(LinkedInGuestAdapter())
    if not registry.get(IndeedAdapter.name):
        registry.register(IndeedAdapter())
    if not registry.get(JSearchAdapter.name):
        registry.register(JSearchAdapter())

    # Fake/demo sources are opt-in only (ALLOW_FAKE_JOBS=1); by default the
    # agent discovers REAL jobs and never injects fabricated listings.
    if not _real_sources_only():
        from core.discovery.adapters.mock_source import MockSourceAdapter
        from core.discovery.adapters.ats import MockATSAdapter

        if not registry.get(MockSourceAdapter.name):
            registry.register(MockSourceAdapter())
        if not registry.get(MockATSAdapter.name):
            registry.register(MockATSAdapter())
    else:
        for fake_name in ("mock_feed", "mock_ats"):
            adapter = registry.get(fake_name)
            if adapter is not None:
                adapter.enabled = False
    return registry