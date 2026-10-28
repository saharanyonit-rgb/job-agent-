from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, inspect as sa_inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from core.core.config import get_settings


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_dir(url: str) -> None:
    if url.startswith("sqlite"):
        path = url.split(":///")[-1]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)


def create_db_engine(url: str | None = None):
    url = url or get_settings().database_url
    _ensure_sqlite_dir(url)
    kwargs: dict = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


engine = create_db_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def rebind(url: str) -> None:
    """Re-point the global engine/session at a different database (used by tests)."""
    global engine, SessionLocal
    _ensure_sqlite_dir(url)
    engine.dispose()
    kwargs: dict = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    SessionLocal.configure(bind=engine, autoflush=False, expire_on_commit=False)


def _add_missing_columns(engine) -> None:
    """Lightweight schema migration: add model columns missing from an existing DB.

    Handles SQLite and Postgres. Only adds nullable columns (or columns with a
    server default) so pre-existing rows stay valid. Runs after create_all so
    new tables are created up front and only older tables get ALTER'd.
    """
    import core.database.models as md

    insp = sa_inspect(engine)
    for table in md.Base.metadata.sorted_tables:
        try:
            existing = {c["name"] for c in insp.get_columns(table.name)}
        except Exception:
            continue
        for column in table.columns:
            if column.name in existing:
                continue
            if column.nullable is False and column.server_default is None:
                continue
            col_type = column.type.compile(engine.dialect)
            nullable = "NULL" if column.nullable else "NOT NULL"
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type} {nullable}'
            with engine.begin() as conn:
                conn.execute(text(ddl))


def init_db(reset: bool = False) -> None:
    import core.database.models  # noqa: F401

    if reset:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()