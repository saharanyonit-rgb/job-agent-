from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.database import models as m
from core.core.audit import AuditLogger


class RuntimeStatus:
    """Agent status observable via API/dashboard."""

    def __init__(self):
        self.current_task: str = "idle"
        self.last_run_at: datetime | None = None
        self.next_scheduled_tasks: dict[str, datetime] = {}
        self.last_cycle: dict[str, Any] = {}

    def reset(self) -> None:
        self.current_task = "idle"

    def task_start(self, task: str) -> None:
        self.current_task = task

    def cycle_complete(self, task: str, result: dict[str, Any]) -> None:
        self.last_run_at = datetime.utcnow()
        self.last_cycle[task] = {"finished": self.last_run_at.isoformat(), "result": result}
        self.current_task = "idle"


runtime_status = RuntimeStatus()


class ObservabilityService:
    def __init__(self, db: Session):
        self.db = db

    def summary(self) -> dict[str, Any]:
        from core.database.base import engine

        app_counts = self.db.execute(select(m.Application.status, func.count()).group_by(m.Application.status)).all()
        job_counts = self.db.execute(select(m.Job.verification_status, func.count()).group_by(m.Job.verification_status)).all()
        interviews = self.db.query(m.Interview).count()
        emails = self.db.query(m.EmailMessage).count()
        notifications = self.db.query(m.Notification).count()
        errors = self.db.query(m.AgentError).order_by(m.AgentError.created_at.desc()).limit(10).all()
        runs = self.db.query(m.AgentRun).order_by(m.AgentRun.started_at.desc()).limit(5).all()

        return {
            "status": runtime_status.current_task,
            "last_run_at": runtime_status.last_run_at.isoformat() if runtime_status.last_run_at else None,
            "last_cycle": runtime_status.last_cycle,
            "applications": {s: c for s, c in app_counts},
            "jobs": {s: c for s, c in job_counts},
            "counts": {
                "interviews": interviews,
                "emails": emails,
                "notifications": notifications,
                "errors_total": self.db.query(m.AgentError).count(),
            },
            "recent_errors": [
                {"id": e.id, "source": e.source, "error_type": e.error_type, "message": e.message, "created_at": e.created_at.isoformat()}
                for e in errors
            ],
            "recent_runs": [
                {"id": r.id, "task": r.task, "status": r.status, "result": r.result or {}, "started_at": r.started_at.isoformat()}
                for r in runs
            ],
        }

    def audit_trail(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.query(m.AuditLog).order_by(m.AuditLog.timestamp.desc()).limit(limit).all()
        return [
            {"time": r.timestamp, "agent": r.agent, "action": r.action, "job_id": r.job_id, "company": r.company, "detail": r.detail}
            for r in rows
        ]

    def record_error(self, source: str, error_type: str, message: str, context: dict[str, Any] | None = None) -> None:
        self.db.add(m.AgentError(source=source, error_type=error_type, message=message[:4000], context=context))
        self.db.commit()

    def start_run(self, task: str) -> m.AgentRun:
        row = m.AgentRun(task=task, status="RUNNING")
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def finish_run(self, run: m.AgentRun, result: dict[str, Any], status: str = "OK") -> None:
        run.result = result
        run.status = status
        run.finished_at = datetime.utcnow()
        self.db.commit()