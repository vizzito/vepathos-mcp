"""A small circuit breaker so an unavailable Core fails fast instead of piling up waiting calls."""

from __future__ import annotations

import time
from collections.abc import Callable


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        reset_after_seconds: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = failure_threshold
        self._reset_after = reset_after_seconds
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if self._clock() - self._opened_at >= self._reset_after:
            # Half-open: let the next call through; its outcome closes or re-opens the circuit.
            return False
        return True

    @property
    def retry_after_seconds(self) -> int:
        if self._opened_at is None:
            return 0
        remaining = self._reset_after - (self._clock() - self._opened_at)
        return max(1, int(remaining + 0.999))

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = self._clock()
