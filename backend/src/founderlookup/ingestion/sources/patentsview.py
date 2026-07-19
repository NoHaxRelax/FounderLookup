"""PatentsView patent source adapter.

Implements the provider-neutral ``DiscoveryPort`` and ``AcquisitionPort`` against
the free PatentsView legacy API. PatentsView is a free, keyless index of granted
US patents, recorded as ``PATENT`` Evidence. A discovery result is a lead into a
patent record on Google Patents, never a founder or trust signal on its own.
"""

from __future__ import annotations

import json
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

_API_ROOT = "https://api.patentsview.org"
_ADAPTER_ID = "patentsview-patent-v0"
_JSON_MEDIA_TYPE = "application/json"
_MAX_DISCOVERY_BYTES = 2_000_000
_PATENT_HOST = "patents.google.com"


def _patent_url(patent_id: str) -> str:
    return f"https://{_PATENT_HOST}/patent/{patent_id}"


def _query_url(criteria: dict[str, object]) -> str:
    encoded = json.dumps(criteria, separators=(",", ":"))
    return f"{_API_ROOT}/patents/query?q={urllib.parse.quote(encoded, safe='')}"


def _patent_id_from_url(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc.lower() not in {_PATENT_HOST, f"www.{_PATENT_HOST}"}:
        return None
    parts = [segment for segment in parsed.path.split("/") if segment]
    if not parts:
        return None
    return parts[-1]


class PatentsViewPatentSource:
    """Discover and acquire PatentsView granted-patent Evidence.

    Conforms to ``DiscoveryPort`` and ``AcquisitionPort``. A ``DiscoveryRequest``
    is served only for retrieval requests whose source categories include
    ``PATENT``; other categories are left to their own adapters.
    """

    source_category = SourceCategory.PATENT
    adapter_id = _ADAPTER_ID

    def __init__(
        self,
        transport: HttpTransport,
        *,
        now: Callable[[], datetime],
    ) -> None:
        self._transport = transport
        self._now = now

    def _headers(self) -> dict[str, str]:
        return {"accept": "application/json"}

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
            url = _query_url({"_text_any": {"patent_title": retrieval.query}})
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
                        safe_message="patent-source discovery request failed",
                        retryable=True,
                    )
                )
                continue

            failure = discovery_failure_for_status(response.status, operation_id)
            if failure is not None:
                failures.append(failure)
                continue

            patents = decode_json(response.body).get("patents")
            if not isinstance(patents, list):
                failures.append(
                    CollectionFailure(
                        operation_id=operation_id,
                        safe_code="invalid_provider_payload",
                        safe_message="patent source returned an invalid discovery payload",
                        retryable=True,
                    )
                )
                continue
            for raw in patents[: retrieval.max_results]:
                if not isinstance(raw, dict):
                    continue
                patent_id = raw.get("patent_id")
                if not isinstance(patent_id, str) or not patent_id:
                    continue
                rank += 1
                patent_title = raw.get("patent_title")
                title = (
                    patent_title
                    if isinstance(patent_title, str) and patent_title
                    else _patent_url(patent_id)
                )
                leads.append(
                    DiscoveryLead(
                        lead_id=f"patentsview-patent-{slug(patent_id)}",
                        retrieval_request_id=retrieval.retrieval_request_id,
                        original_url=_patent_url(patent_id),
                        source_category=self.source_category,
                        discovered_at=self._now(),
                        rank=rank,
                        title=KnowledgeValue[str].known(title),
                        provider_summary=KnowledgeValue[str].unknown(
                            "A PatentsView search result is a lead, not primary evidence"
                        ),
                        retrieval_relevance=relevance(None),
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
        patent_id = _patent_id_from_url(request.original_url)
        if patent_id is None:
            return self._acquisition_failure(
                request,
                AcquisitionStatus.FAILED,
                operation_id,
                "unsupported_url",
                "URL is not a Google Patents patent",
                retryable=False,
            )

        url = _query_url({"patent_id": patent_id})
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
                "patent-source acquisition request failed",
                retryable=True,
            )

        if response.status == 200:
            patents = decode_json(response.body).get("patents")
            if not isinstance(patents, list) or not patents:
                return self._acquisition_failure(
                    request,
                    AcquisitionStatus.FAILED,
                    operation_id,
                    "invalid_provider_payload",
                    "patent source returned an invalid acquisition payload",
                    retryable=True,
                )
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
                    "PatentsView patent record has no single authoritative event time"
                ),
            )
        if response.status in {403, 429}:
            return self._acquisition_failure(
                request,
                AcquisitionStatus.BLOCKED,
                operation_id,
                "rate_limited",
                "PatentsView rate limit or access restriction",
                retryable=True,
            )
        if response.status == 404:
            return self._acquisition_failure(
                request,
                AcquisitionStatus.FAILED,
                operation_id,
                "not_found",
                "PatentsView patent not found",
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


__all__ = ["PatentsViewPatentSource"]
