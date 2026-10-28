from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from core.applications.policy import PolicyService
from core.config import get_settings
from core.logging_config import get_logger
from core.database import models as m

log = get_logger(__name__)


class SchedulerService:
    def __init__(self, db: Session):
        self.db = db
        self._last_discover: datetime | None = None
        self._last_verify: datetime | None = None
        self._last_followup: datetime | None = None
        self._last_full_loop: datetime | None = None

    def run_due(self, orchestrator: Any, candidate_id: int) -> dict[str, Any]:
        settings = get_settings()
        now = datetime.utcnow()
        runs: dict[str, Any] = {}

        def due(last: datetime | None, minutes: int) -> bool:
            if last is None:
                return True
            return now - last >= timedelta(minutes=minutes)

        if due(self._last_discover, settings.discover_interval_minutes):
            runs["discover"] = orchestrator.discover()
            self._last_discover = now

        if due(self._last_verify, settings.verify_interval_minutes):
            runs["verify"] = orchestrator.verify()
            self._last_verify = now

        # Follow-ups processed daily
        from core.followups.engine import FollowupEngine

        if due(self._last_followup, settings.followup_interval_minutes):
            engine = FollowupEngine(self.db)
            plans = engine.plan_pending()
            engine.record_plans(plans)
            sent = engine.process_followups()
            runs["followups"] = {"planned": len(plans), "sent": sent}
            self._last_followup = now

        # Email + interviews always processed each cycle (event-driven)
        runs["cycle"] = orchestrator.cycle(candidate_id)

        # Once per day run the full closed loop: outcomes, gaps, plan, report.
        if due(self._last_full_loop, settings.followup_interval_minutes):
            runs["daily_loop"] = orchestrator.run_daily_career_loop(candidate_id)
            self._last_full_loop = now

        return runs


def schedule(
    orchestrator: Any,
    candidate_id: int,
    use_apscheduler: bool = False,
) -> Any:
    """Install background jobs. Defaults to a simple in-process loop; APScheduler
    available for production."""
    import threading
    import time

    from core.config import get_settings

    scheduler = SchedulerService(orchestrator.db)

    def _loop() -> None:
        while True:
            try:
                scheduler.run_due(orchestrator, candidate_id)
            except Exception as exc:
                log.error("Scheduled cycle failed: %s", exc)
            time.sleep(60)

    if use_apscheduler:
        from apscheduler.schedulers.background import BackgroundScheduler

        aps = BackgroundScheduler()
        aps.add_job(_loop, "interval", minutes=1, max_instances=1)
        aps.start()
        return aps
    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    return thread