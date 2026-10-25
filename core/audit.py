from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from job_agent.database import models as m


@dataclass
class AuditEntry:
    agent: str
    action: str
    job_id: int | None = None
    company: str | None = None
    detail: dict[str, Any] | None = None


class AuditLogger:
    def __init__(self, db: Session):
        self.db = db

    def log(self, agent: str, action: str, **detail: Any) -> None:
        entry = m.AuditLog(
            timestamp=time.time(),
            agent=agent,
            action=action,
            detail=detail,
        )
        self.db.add(entry)
        self.db.commit()