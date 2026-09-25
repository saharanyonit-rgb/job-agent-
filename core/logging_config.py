from __future__ import annotations

import logging
import sys
from typing import Any

from job_agent.core.config import get_settings


def setup_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers[:] = [handler]
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)


def log_kv(logger: logging.Logger, level: int, msg: str, **kv: Any) -> None:
    suffix = " ".join(f"{k}={v!r}" for k, v in kv.items())
    logger.log(level, f"{msg} [{suffix}]" if suffix else msg)