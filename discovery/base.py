from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from job_agent.jobs.normalize import RawJob


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


def register_default_adapters() -> AdapterRegistry:
    from job_agent.discovery.adapters.company_career import CompanyCareerAdapter
    from job_agent.discovery.adapters.greenhouse import GreenhouseAdapter
    from job_agent.discovery.adapters.lever import LeverAdapter
    from job_agent.discovery.adapters.mock_source import MockSourceAdapter
    from job_agent.discovery.adapters.ats import MockATSAdapter

    if not registry.get(MockSourceAdapter.name):
        registry.register(MockSourceAdapter())
    if not registry.get(GreenhouseAdapter.name):
        registry.register(GreenhouseAdapter())
    if not registry.get(LeverAdapter.name):
        registry.register(LeverAdapter())
    if not registry.get(CompanyCareerAdapter.name):
        registry.register(CompanyCareerAdapter())
    if not registry.get(MockATSAdapter.name):
        registry.register(MockATSAdapter())
    return registry