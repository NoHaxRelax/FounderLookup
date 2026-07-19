"""Small P0 authentication and bounded in-memory public-route throttling."""

from __future__ import annotations

import hashlib
import hmac
import math
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock

from founderlookup.api.errors import APIProblem


@dataclass(frozen=True, slots=True)
class InvestorPrincipal:
    principal_id: str = "investor"


class InvestorAuthenticator:
    """Retain only the configured token digest and compare candidate digests safely."""

    def __init__(self, bearer_token: str) -> None:
        self._expected_digest = hashlib.sha256(bearer_token.encode("utf-8")).digest()

    def authenticate(self, authorization: str | None) -> InvestorPrincipal:
        scheme, separator, credentials = (authorization or "").partition(" ")
        well_formed = bool(separator) and scheme.casefold() == "bearer" and bool(credentials)
        presented = credentials if well_formed else ""
        presented_digest = hashlib.sha256(presented.encode("utf-8")).digest()
        if not hmac.compare_digest(self._expected_digest, presented_digest):
            raise APIProblem(
                status=401,
                code="investor_authentication_required",
                title="Access denied",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return InvestorPrincipal()


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
