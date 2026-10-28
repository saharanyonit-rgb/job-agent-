from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from core.database import models as m
from core.discovery.base import DiscoveryResult, SourceAdapter, registry, register_default_adapters
from core.jobs.service import JobService
from core.errors import CircuitBreaker
from core.logging_config import get_logger

log = get_logger(__name__)

_breakers: dict[str, CircuitBreaker] = {}


def _breaker(name: str) -> CircuitBreaker:
    if name not in _breakers:
        _breakers[name] = CircuitBreaker(name=name)
    return _breakers[name]


async def _run_adapter_async(adapter: SourceAdapter, query: dict[str, Any]) -> DiscoveryResult:
    """Run a single adapter's discover_jobs in a thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, adapter.discover_jobs, query)


class DiscoveryService:
    def __init__(self, db: Session, job_service: JobService | None = None):
        self.db = db
        self.jobs = job_service or JobService(db)
        register_default_adapters()

    def run_discovery(self, queries: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        """Synchronous version for backward compatibility."""
        return asyncio.run(self.run_discovery_async(queries))

    async def run_discovery_async(self, queries: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        """Async version: runs all adapters in parallel."""
        queries = queries or {}
        totals = {"discovered": 0, "created": 0, "updated": 0, "sources_ok": 0, "sources_failed": 0, "errors": []}

        enabled_adapters = [a for a in registry.all() if a.enabled]
        if not enabled_adapters:
            return totals

        # Prepare tasks
        tasks = []
        for adapter in enabled_adapters:
            breaker = _breaker(adapter.name)
            if breaker.is_open:
                totals["errors"].append({"source": adapter.name, "error": "circuit open"})
                continue
            query = queries.get(adapter.name, {})
            tasks.append((adapter, _run_adapter_async(adapter, query)))

        # Run all in parallel
        results = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)

        for (adapter, _), result in zip(tasks, results):
            if isinstance(result, Exception):
                breaker = _breaker(adapter.name)
                breaker.record_failure()
                self._mark_source(adapter.name, healthy=False, error=str(result))
                totals["sources_failed"] += 1
                totals["errors"].append({"source": adapter.name, "error": str(result)})
                log.error("Source %s failed: %s", adapter.name, result)
                continue

            if result.error:
                breaker = _breaker(adapter.name)
                breaker.record_failure()
                self._mark_source(adapter.name, healthy=False, error=result.error)
                totals["sources_failed"] += 1
                totals["errors"].append({"source": adapter.name, "error": result.error})
                continue

            breaker = _breaker(adapter.name)
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