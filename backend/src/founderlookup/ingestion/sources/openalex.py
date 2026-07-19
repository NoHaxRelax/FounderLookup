"""OpenAlex scholarly source adapter.

Implements the provider-neutral ``DiscoveryPort`` and ``AcquisitionPort`` against
the free OpenAlex API. OpenAlex is a free, keyless, authoritative index of
scholarly authors and works, recorded as ``RESEARCH`` Evidence. Its
``relevance_score`` is retrieval relevance, never founder or trust signal. An
optional ``mailto`` joins OpenAlex's polite pool; it is not authentication.
"""

from __future__ import annotations

import time
import urllib.parse
from collections.abc import Callable
from datetime import datetime
from hashlib import sha256

from founderlookup.domain.common import KnowledgeValue
from founderlookup.domain.discovery import (
    AcquisitionRequest,
    AcquisitionResult,
    AcquisitionStatus,
    CollectionFailure,
    DiscoveryLead,
    DiscoveryRequest,
    DiscoveryResult,
    ProviderUsage,
)
from founderlookup.domain.evidence import SourceCategory
from founderlookup.ingestion.sources._support import (
    decode_json,
    discovery_failure_for_status,
    relevance,
    result_status,
    slug,
)
from founderlookup.ingestion.sources.http import HttpTransport, HttpTransportError

_API_ROOT = "https://api.openalex.org"
_ADAPTER_ID = "openalex-research-v0"
_JSON_MEDIA_TYPE = "application/json"
_MAX_DISCOVERY_BYTES = 2_000_000
_MAX_PER_PAGE = 200


def _short_id(entity_url: str) -> str:
    return entity_url.rstrip("/").rsplit("/", 1)[-1] or entity_url


def _author_id_from_url(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc.lower() not in {"openalex.org", "www.openalex.org", "api.openalex.org"}:
        return None
    parts = [segment for segment in parsed.path.split("/") if segment]
    if not parts:
        return None
    candidate = parts[-1]
    if not candidate.startswith("A") or not candidate[1:].isdigit():
        return None
    return candidate


class OpenAlexResearchSource:
    """Discover and acquire OpenAlex scholarly-author Evidence.

    Conforms to ``DiscoveryPort`` and ``AcquisitionPort``. A ``DiscoveryRequest``
    is served only for retrieval requests whose source categories include
    ``RESEARCH``; other categories are left to their own adapters.
    """

    source_category = SourceCategory.RESEARCH
    adapter_id = _ADAPTER_ID

    def __init__(
        self,
        transport: HttpTransport,
        *,
        now: Callable[[], datetime],
        mailto: str | None = None,
    ) -> None:
        self._transport = transport
        self._now = now
        self._mailto = mailto

    def _headers(self) -> dict[str, str]:
        return {"accept": "application/json"}

    def _mailto_suffix(self, separator: str) -> str:
        if not self._mailto:
            return ""
        return f"{separator}mailto={urllib.parse.quote(self._mailto, safe='')}"

    async def discover(self, request: DiscoveryRequest) -> DiscoveryResult:
        started = time.monotonic()
        leads: list[DiscoveryLead] = []
        failures: list[CollectionFailure] = []
        request_count = 0
        rank = 0

        for retrieval in request.retrieval_requests:
            if self.source_category not in retrieval.source_categories:
                continue
            request_count += 1
            per_page = min(retrieval.max_results, _MAX_PER_PAGE)
            query = urllib.parse.quote(retrieval.query, safe="")
            url = (
                f"{_API_ROOT}/authors?search={query}&per-page={per_page}{self._mailto_suffix('&')}"
            )
            operation_id = f"{_ADAPTER_ID}:discover:{retrieval.retrieval_request_id}"
            try:
                response = await self._transport.get(
                    url,
                    headers=self._headers(),
                    timeout_seconds=float(retrieval.timeout_seconds),
                    max_bytes=_MAX_DISCOVERY_BYTES,
                )
            except HttpTransportError:
                failures.append(
                    CollectionFailure(
                        operation_id=operation_id,
                        safe_code="transport_error",
                        safe_message="scholarly-source discovery request failed",
                        retryable=True,
                    )
                )
                continue

            failure = discovery_failure_for_status(response.status, operation_id)
            if failure is not None:
                failures.append(failure)
                continue

            results = decode_json(response.body).get("results")
            if not isinstance(results, list):
                continue
            for raw in results[: retrieval.max_results]:
                if not isinstance(raw, dict):
                    continue
                entity_id = raw.get("id")
                if not isinstance(entity_id, str) or not entity_id:
                    continue
                rank += 1
                display = raw.get("display_name")
                title = display if isinstance(display, str) and display else entity_id
                leads.append(
                    DiscoveryLead(
                        lead_id=f"openalex-author-{slug(_short_id(entity_id))}",
                        retrieval_request_id=retrieval.retrieval_request_id,
                        original_url=entity_id,
                        source_category=self.source_category,
                        discovered_at=self._now(),
                        rank=rank,
                        title=KnowledgeValue[str].known(title),
                        provider_summary=KnowledgeValue[str].unknown(
                            "An OpenAlex search result is a lead, not primary evidence"
                        ),
                        retrieval_relevance=relevance(raw.get("relevance_score")),
                    )
                )

        usage = ProviderUsage(
            adapter_id=_ADAPTER_ID,
            operation_id=f"{_ADAPTER_ID}:discover:{request.request_id}",
            request_count=request_count,
            result_count=len(leads),
            elapsed_milliseconds=max(0, int((time.monotonic() - started) * 1000)),
            cost_amount=KnowledgeValue[float].known(0.0),
            cost_currency=KnowledgeValue[str].known("USD"),
        )
        return DiscoveryResult(
            result_id=f"{_ADAPTER_ID}:discovery:{request.request_id}",
            request_id=request.request_id,
            status=result_status(bool(leads), bool(failures)),
            completed_at=self._now(),
            leads=tuple(leads),
            failures=tuple(failures),
            usage=usage,
        )

    async def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        operation_id = f"{_ADAPTER_ID}:acquire:{request.acquisition_request_id}"
        author_id = _author_id_from_url(request.original_url)
        if author_id is None:
            return self._acquisition_failure(
                request,
                AcquisitionStatus.FAILED,
                operation_id,
                "unsupported_url",
                "URL is not an OpenAlex author",
                retryable=False,
            )

        url = f"{_API_ROOT}/authors/{author_id}{self._mailto_suffix('?')}"
        try:
            response = await self._transport.get(
                url,
                headers=self._headers(),
                timeout_seconds=float(request.timeout_seconds),
                max_bytes=request.max_bytes,
            )
        except HttpTransportError:
            return self._acquisition_failure(
                request,
                AcquisitionStatus.FAILED,
                operation_id,
                "transport_error",
                "scholarly-source acquisition request failed",
                retryable=True,
            )

        if response.status == 200:
            return AcquisitionResult(
                result_id=f"{_ADAPTER_ID}:acquisition:{request.acquisition_request_id}",
                acquisition_request_id=request.acquisition_request_id,
                original_url=request.original_url,
                status=AcquisitionStatus.ACQUIRED,
                completed_at=self._now(),
                content=response.body,
                media_type=_JSON_MEDIA_TYPE,
                content_sha256=sha256(response.body).hexdigest(),
                source_event_time=KnowledgeValue[datetime].unknown(
                    "OpenAlex author record has no single authoritative event time"
                ),
            )
        if response.status in {403, 429}:
            return self._acquisition_failure(
                request,
                AcquisitionStatus.BLOCKED,
                operation_id,
                "rate_limited",
                "OpenAlex rate limit or access restriction",
                retryable=True,
            )
        if response.status == 404:
            return self._acquisition_failure(
                request,
                AcquisitionStatus.FAILED,
                operation_id,
                "not_found",
                "OpenAlex author not found",
                retryable=False,
            )
        return self._acquisition_failure(
            request,
            AcquisitionStatus.FAILED,
            operation_id,
            "upstream_status",
            "unexpected upstream status",
            retryable=response.status >= 500,
        )

    def _acquisition_failure(
        self,
        request: AcquisitionRequest,
        status: AcquisitionStatus,
        operation_id: str,
        safe_code: str,
        safe_message: str,
        *,
        retryable: bool,
    ) -> AcquisitionResult:
        return AcquisitionResult(
            result_id=f"{_ADAPTER_ID}:acquisition:{request.acquisition_request_id}",
            acquisition_request_id=request.acquisition_request_id,
            original_url=request.original_url,
            status=status,
            completed_at=self._now(),
            source_event_time=KnowledgeValue[datetime].unknown(
                "No content was acquired, so no source event time exists"
            ),
            failure=CollectionFailure(
                operation_id=operation_id,
                safe_code=safe_code,
                safe_message=safe_message,
                retryable=retryable,
            ),
        )


__all__ = ["OpenAlexResearchSource"]
