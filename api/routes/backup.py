from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from job_agent.api.deps import get_db
from job_agent.database import models as m

router = APIRouter(prefix="/api/backup", tags=["backup"])


@router.get("")
def backup(db: Session = Depends(get_db)) -> Response:
    """Export all tables (minus no secrets) to a zip of JSON files."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        tables = [
            m.Candidate, m.CandidateFact, m.CandidatePreference, m.Resume, m.Company,
            m.JobSource, m.Job, m.JobEvent, m.Application, m.ApplicationEvent,
            m.Recruiter, m.EmailMessage, m.Interview, m.CalendarEvent, m.Followup,
            m.Notification, m.AgentRun, m.AgentError, m.AuditLog, m.AgentSetting,
        ]
        for model in tables:
            rows = db.query(model).all()
            data = []
            for row in rows:
                item = {c: getattr(row, c) for c in model.__table__.columns.keys()}
                for k, v in item.items():
                    if hasattr(v, "isoformat"):
                        item[k] = v.isoformat()
                data.append(item)
            zf.writestr(model.__tablename__ + ".json", json.dumps(data, default=str))
        zf.writestr("metadata.json", json.dumps({"backup_at_utc": datetime.utcnow().isoformat()}))

    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=job_agent_backup.zip"},
    )