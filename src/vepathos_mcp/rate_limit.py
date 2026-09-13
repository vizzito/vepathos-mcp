"""Per-subject sliding-window rate limits at the MCP edge.

Best effort per instance: Vepathos Core remains authoritative (quota, concurrency, idempotency).
This layer only stops a misbehaving agent from hammering Core through one replica.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

from vepathos_mcp.errors.codes import DomainError, ErrorCode

WINDOW_SECONDS = 60.0
MAX_TRACKED_SUBJECTS = 50_000


class RateLimiter:
    def __init__(self, limits: dict[str, int], clock: Callable[[], float] = time.monotonic) -> None:
        self._limits = limits
        self._clock = clock
        self._hits: dict[tuple[str, str], deque[float]] = {}

    def check(self, subject: str, bucket: str) -> None:
        limit = self._limits.get(bucket)
        if limit is None:
            return
        now = self._clock()
        key = (subject, bucket)
        hits = self._hits.get(key)
        if hits is None:
            if len(self._hits) >= MAX_TRACKED_SUBJECTS:
                self._evict(now)
            hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= limit:
            retry_after = max(1, int(WINDOW_SECONDS - (now - hits[0]) + 0.999))
            raise DomainError(
                ErrorCode.RATE_LIMITED,
                "Too many requests to Vepathos from this connection.",
                suggestion=f"Wait about {retry_after} seconds before calling again.",
                retry_after_seconds=retry_after,
            )
        hits.append(now)

    def _evict(self, now: float) -> None:
        stale = [key for key, hits in self._hits.items() if not hits or now - hits[-1] >= WINDOW_SECONDS]
        for key in stale:
            del self._hits[key]
