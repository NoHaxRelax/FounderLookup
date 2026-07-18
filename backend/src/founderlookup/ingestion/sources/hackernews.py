"""Hacker News public-social source adapter.

Implements the provider-neutral ``DiscoveryPort`` and ``AcquisitionPort`` against
the free Algolia Hacker News Search API. Algolia HN Search is a free, keyless
index of Hacker News stories and users, recorded as ``PUBLIC_SOCIAL`` Evidence.
Its ``points`` value is retrieval relevance, never founder or trust signal.
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

_API_ROOT = "https://hn.algolia.com/api/v1"
_ITEM_ROOT = "https://news.ycombinator.com/item"
_ADAPTER_ID = "hackernews-social-v0"
_JSON_MEDIA_TYPE = "application/json"
_MAX_DISCOVERY_BYTES = 2_000_000
_MAX_HITS_PER_PAGE = 1000


def _username_from_url(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc.lower() not in {"news.ycombinator.com", "www.news.ycombinator.com"}:
        return None
    if parsed.path.rstrip("/") != "/user":
        return None
    values = urllib.parse.parse_qs(parsed.query).get("id")
    if not values:
        return None
    username = values[0]
    if not username or not all(c.isalnum() or c in "-_" for c in username):
        return None
    return username


class HackerNewsSocialSource:
    """Discover and acquire Hacker News public-social Evidence.

    Conforms to ``DiscoveryPort`` and ``AcquisitionPort``. A ``DiscoveryRequest``
    is served only for retrieval requests whose source categories include
    ``PUBLIC_SOCIAL``; other categories are left to their own adapters.
    """

    source_category = SourceCategory.PUBLIC_SOCIAL
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
            hits_per_page = min(retrieval.max_results, _MAX_HITS_PER_PAGE)
            query = urllib.parse.quote(retrieval.query, safe="")
            url = f"{_API_ROOT}/search?query={query}&tags=story&hitsPerPage={hits_per_page}"
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
                        safe_message="public-social discovery request failed",
                        retryable=True,
                    )
                )
                continue

            failure = discovery_failure_for_status(response.status, operation_id)
            if failure is not None:
                failures.append(failure)
                continue

            hits = decode_json(response.body).get("hits")
            if not isinstance(hits, list):
                continue
            for raw in hits[: retrieval.max_results]:
                if not isinstance(raw, dict):
                    continue
                object_id = raw.get("objectID")
                if not isinstance(object_id, str) or not object_id:
                    continue
                rank += 1
                story_url = raw.get("url")
                original_url = (
                    story_url
                    if isinstance(story_url, str) and story_url
                    else f"{_ITEM_ROOT}?id={object_id}"
                )
                author = raw.get("author")
                headline = raw.get("title")
                if isinstance(author, str) and author:
                    title = author
                elif isinstance(headline, str) and headline:
                    title = headline
                else:
                    title = object_id
                leads.append(
                    DiscoveryLead(
                        lead_id=f"hackernews-story-{slug(object_id)}",
                        retrieval_request_id=retrieval.retrieval_request_id,
                        original_url=original_url,
                        source_category=self.source_category,
                        discovered_at=self._now(),
                        rank=rank,
                        title=KnowledgeValue[str].known(title),
                        provider_summary=KnowledgeValue[str].unknown(
                            "A Hacker News search result is a lead, not primary evidence"
                        ),
                        retrieval_relevance=relevance(raw.get("points")),
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
        username = _username_from_url(request.original_url)
        if username is None:
            return self._acquisition_failure(
                request,
                AcquisitionStatus.FAILED,
                operation_id,
                "unsupported_url",
                "URL is not a Hacker News user profile",
                retryable=False,
            )

        url = f"{_API_ROOT}/users/{username}"
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
                "public-social acquisition request failed",
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
                    "Hacker News user record has no single authoritative event time"
                ),
            )
        if response.status in {403, 429}:
            return self._acquisition_failure(
                request,
                AcquisitionStatus.BLOCKED,
                operation_id,
                "rate_limited",
                "Hacker News rate limit or access restriction",
                retryable=True,
            )
        if response.status == 404:
            return self._acquisition_failure(
                request,
                AcquisitionStatus.FAILED,
                operation_id,
                "not_found",
                "Hacker News user not found",
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


__all__ = ["HackerNewsSocialSource"]
