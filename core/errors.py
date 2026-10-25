from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class RetryableError(Exception):
    """A transient error that can be retried."""


class NonRetryableError(Exception):
    """A permanent error that should bubble up as a handoff."""


class HandoffRequiredError(Exception):
    def __init__(self, reason: str, context: dict[str, Any] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.context = context or {}


class SourceHealthError(Exception):
    pass


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 5
    cooldown_seconds: float = 60.0
    failures: int = 0
    opened_at: float | None = None

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.opened_at = time.time()

    @property
    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        if time.time() - self.opened_at >= self.cooldown_seconds:
            self.opened_at = None
            self.failures = 0
            return False
        return True


@dataclass
class RetryPolicy:
    max_retries: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0
    retry_on: tuple[type[Exception], ...] = (RetryableError, TimeoutError, OSError)


def retry(policy: RetryPolicy | None = None) -> Callable[[Callable[..., T]], Callable[..., T]]:
    p = policy or RetryPolicy()

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        def wrapper(*args: Any, **kwargs: Any) -> T:
            delay = p.base_delay
            last: Exception | None = None
            for attempt in range(p.max_retries + 1):
                try:
                    return fn(*args, **kwargs)
                except p.retry_on as exc:  # type: ignore[misc]
                    last = exc
                    if attempt >= p.max_retries:
                        break
                    time.sleep(min(delay, p.max_delay))
                    delay *= 2
            raise last  # type: ignore[misc]
        return wrapper

    return decorator


@dataclass
class ErrorRecord:
    source: str
    error_type: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)