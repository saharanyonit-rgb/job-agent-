from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import get_settings
from core.logging_config import setup_logging
from core.database.base import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Autonomous Job Agent", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    from core.api.routes import (
        applications,
        audit,
        backup,
        candidates,
        career,
        commands,
        dashboard,
        emails,
        exports,
        followups,
        interviews,
        jobs,
        notifications,
        oauth,
        settings as settings_routes,
        sources,
    )

    for router in [
        audit.router,
        applications.router,
        backup.router,
        candidates.router,
        career.router,
        commands.router,
        dashboard.router,
        emails.router,
        exports.router,
        followups.router,
        interviews.router,
        jobs.router,
        notifications.router,
        oauth.router,
        settings_routes.router,
        sources.router,
    ]:
        app.include_router(router)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()