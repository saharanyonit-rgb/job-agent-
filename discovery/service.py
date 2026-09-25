from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from job_agent.database import models as m
from job_agent.discovery.base import DiscoveryResult, SourceAdapter, registry, register_default_adapters
from job_agent.jobs.service import JobService
from job_agent.core.errors import CircuitBreaker
from job_agent.core.logging_config import get_logger

log = get_logger(__name__)

_breakers: dict[str, CircuitBreaker] = {}


def _breaker(name: str) -> CircuitBreaker:
    if name not in _breakers:
        _breakers[name] = CircuitBreaker(name=name)
    return _breakers[name]


class DiscoveryService:
    def __init__(self, db: Session, job_service: JobService | None = None):
        self.db = db
        self.jobs = job_service or JobService(db)
        register_default_adapters()

    def run_discovery(self, queries: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        queries = queries or {}
        totals = {"discovered": 0, "created": 0, "updated": 0, "sources_ok": 0, "sources_failed": 0, "errors": []}
        for adapter in registry.all():
            if not adapter.enabled:
                continue
            breaker = _breaker(adapter.name)
            if breaker.is_open:
                totals["errors"].append({"source": adapter.name, "error": "circuit open"})
                continue
            query = queries.get(adapter.name, {})
            try:
                result = adapter.discover_jobs(query)
            except Exception as exc:
                breaker.record_failure()
                self._mark_source(adapter.name, healthy=False, error=str(exc))
                totals["sources_failed"] += 1
                totals["errors"].append({"source": adapter.name, "error": str(exc)})
                log.error("Source %s failed: %s", adapter.name, exc)
                continue

            if result.error:
                breaker.record_failure()
                self._mark_source(adapter.name, healthy=False, error=result.error)
                totals["sources_failed"] += 1
                totals["errors"].append({"source": adapter.name, "error": result.error})
                continue

            breaker.record_success()
            self._mark_source(adapter.name, healthy=True, status=result.status.value, count=len(result.jobs))
            totals["sources_ok"] += 1

            for raw in result.jobs:
                try:
                    job, created = self.jobs.upsert_from_raw(raw)
                    totals["discovered"] += 1
                    totals["created" if created else "updated"] += 1
                except Exception as exc:
                    totals["errors"].append({"source": adapter.name, "job": raw.title, "error": str(exc)})
                    log.error("Failed to upsert job %r: %s", raw.title, exc)
        return totals

    def _mark_source(self, name: str, healthy: bool, error: str | None = None, status: str | None = None, count: int | None = None) -> None:
        row = self.db.query(m.JobSource).filter(m.JobSource.name == name).first()
        if not row:
            row = m.JobSource(name=name)
            self.db.add(row)
        row.healthy = healthy
        row.error = error
        row.last_checked = datetime.utcnow()
        if status:
            row.integration_status = status
        if not healthy:
            row.failures = (row.failures or 0) + 1
        else:
            row.failures = 0
        self.db.commit()

    def source_health(self) -> list[dict[str, Any]]:
        rows = self.db.query(m.JobSource).all()
        return [
            {
                "name": r.name,
                "healthy": r.healthy,
                "integration_status": r.integration_status,
                "failures": r.failures,
                "last_checked": r.last_checked.isoformat() if r.last_checked else None,
                "error": r.error,
            }
            for r in rows
        ]