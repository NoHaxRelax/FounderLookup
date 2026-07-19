"""Bounded in-memory public-route throttling."""

from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from collections.abc import Callable
from threading import Lock

from founderlookup.api.errors import APIProblem


class FixedWindowRateLimiter:
    """Process-local limiter appropriate for the explicitly single-process MVP."""

    def __init__(
        self,
        *,
        window_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window_seconds = window_seconds
        self._clock = clock
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, *, bucket: str, key: str, limit: int) -> None:
        now = self._clock()
        oldest_allowed = now - self._window_seconds
        with self._lock:
            events = self._events[(bucket, key)]
            while events and events[0] <= oldest_allowed:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, math.ceil(events[0] + self._window_seconds - now))
                raise APIProblem(
                    status=429,
                    code="rate_limit_exceeded",
                    title="Too many requests",
                    headers={"Retry-After": str(retry_after)},
                )
            events.append(now)
