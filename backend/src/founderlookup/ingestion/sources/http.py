"""Injectable async HTTP transport for source-specific ingestion adapters.

Kept dependency-light on purpose: the real transport uses the standard library
so no HTTP-client dependency is added before the human framework gate. Adapters
depend on the ``HttpTransport`` protocol, so ``RecordedHttpTransport`` drives
contract and unit tests deterministically without network access.
"""

from __future__ import annotations

import asyncio
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

_USER_AGENT = "founderlookup-osint/0.0"


@dataclass(frozen=True)
class HttpResponse:
    """A completed HTTP response, including non-2xx status with a body."""

    status: int
    headers: Mapping[str, str]
    body: bytes


class HttpTransportError(RuntimeError):
    """A transport-level failure carrying no vendor detail upward."""


@runtime_checkable
class HttpTransport(Protocol):
    """Bounded GET transport an adapter can call without knowing the client."""

    async def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_bytes: int,
    ) -> HttpResponse: ...


class UrllibHttpTransport:
    """Standard-library GET transport; adds no third-party dependency."""

    async def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_bytes: int,
    ) -> HttpResponse:
        return await asyncio.to_thread(
            self._get_sync, url, dict(headers), timeout_seconds, max_bytes
        )

    @staticmethod
    def _get_sync(
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
        max_bytes: int,
    ) -> HttpResponse:
        merged = {"user-agent": _USER_AGENT, **headers}
        request = urllib.request.Request(url, headers=merged, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise HttpTransportError("response exceeded the byte budget")
                return HttpResponse(
                    status=int(response.status),
                    headers={k.lower(): v for k, v in response.headers.items()},
                    body=body,
                )
        except urllib.error.HTTPError as error:
            raw = error.read(max_bytes) if error.fp is not None else b""
            headers_out = (
                {k.lower(): v for k, v in error.headers.items()}
                if error.headers is not None
                else {}
            )
            return HttpResponse(status=int(error.code), headers=headers_out, body=raw)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise HttpTransportError("http transport request failed") from error


class RecordedHttpTransport:
    """Deterministic transport that replays canned responses keyed by URL."""

    def __init__(self, responses: Mapping[str, HttpResponse]) -> None:
        self._responses = dict(responses)
        self._calls: list[str] = []

    @property
    def calls(self) -> tuple[str, ...]:
        """URLs requested, in call order."""
        return tuple(self._calls)

    async def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_bytes: int,
    ) -> HttpResponse:
        self._calls.append(url)
        try:
            return self._responses[url]
        except KeyError as error:
            raise HttpTransportError(f"no recorded response for {url}") from error
