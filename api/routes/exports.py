from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from core.database import models as m
from core.api.deps import get_db

router = APIRouter(prefix="/api/export", tags=["export"])

TABLES = {
    "applications": m.Application,
    "jobs": m.Job,
    "interviews": m.Interview,
    "recruiters": m.Recruiter,
    "notifications": m.Notification,
    "audit_logs": m.AuditLog,
}


@router.get("/{kind}.json")
def export_json(kind: str, db: Session = Depends(get_db)) -> Response:
    return _export(kind, "json", db)


@router.get("/{kind}.csv")
def export_csv(kind: str, db: Session = Depends(get_db)) -> Response:
    return _export(kind, "csv", db)


def _export(kind: str, fmt: str, db: Session) -> Response:
    if kind not in TABLES:
        return Response("unknown table", status_code=404)
    model = TABLES[kind]
    rows = db.query(model).all()
    data = []
    for row in rows:
        item = {}
        for col in model.__table__.columns.keys():
            value = getattr(row, col)
            if hasattr(value, "isoformat"):
                value = value.isoformat()
            item[col] = value
        data.append(item)

    if fmt == "json":
        return Response(json.dumps(data, default=str), media_type="application/json")

    buf = io.StringIO()
    if data:
        writer = csv.DictWriter(buf, fieldnames=list(data[0].keys()))
        writer.writeheader()
        for item in data:
            writer.writerow({k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in item.items()})
    return Response(buf.getvalue(), media_type="text/csv")