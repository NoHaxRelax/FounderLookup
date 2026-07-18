"""GitHub developer-activity source adapter.

Implements the provider-neutral ``DiscoveryPort`` and ``AcquisitionPort`` against
the public GitHub REST API. GitHub is a free, authoritative source-specific API
and anchors the free-first sourcing decision; its results are recorded as
``DEVELOPER_ACTIVITY`` Evidence rather than as generic web snippets. A GitHub
search score is retrieval relevance, never founder or trust signal.
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
    CollectionResultStatus,
    DiscoveryLead,
    DiscoveryRequest,
    DiscoveryResult,
    ProviderUsage,
)
from founderlookup.domain.evidence import SourceCategory
from founderlookup.ingestion.sources.http import (
    HttpResponse,
    HttpTransport,
    HttpTransportError,
)

_API_ROOT = "https://api.github.com"
_HTML_ROOT = "https://github.com"
_ADAPTER_ID = "github-developer-activity-v0"
_JSON_MEDIA_TYPE = "application/json"
_MAX_DISCOVERY_BYTES = 1_000_000
_LOGIN_MAX = 39


def _slug(value: str) -> str:
    cleaned = "".join(c if (c.isalnum() or c in "._-") else "-" for c in value).strip("-")
    return cleaned or "x"


def _login_from_url(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        return None
    parts = [segment for segment in parsed.path.split("/") if segment]
    if not parts:
        return None
    login = parts[0]
    if len(login) > _LOGIN_MAX or not all(c.isalnum() or c == "-" for c in login):
        return None
    return login


def _relevance(score: object) -> KnowledgeValue[float]:
    if isinstance(score, (int, float)):
        return KnowledgeValue[float].known(float(score))
    return KnowledgeValue[float].unknown("GitHub did not return a relevance score")


def _result_status(has_leads: bool, has_failures: bool) -> CollectionResultStatus:
    if has_leads and has_failures:
        return CollectionResultStatus.PARTIALLY_SUCCEEDED
    if not has_leads and has_failures:
        return CollectionResultStatus.FAILED
    return CollectionResultStatus.SUCCEEDED


class GitHubDeveloperActivitySource:
    """Discover and acquire GitHub developer-activity Evidence.

    Conforms to ``DiscoveryPort`` and ``AcquisitionPort``. A ``DiscoveryRequest``
    is served only for retrieval requests whose source categories include
    ``DEVELOPER_ACTIVITY``; other categories are left to their own adapters.
    """

    source_category = SourceCategory.DEVELOPER_ACTIVITY
    adapter_id = _ADAPTER_ID

    def __init__(
        self,
        transport: HttpTransport,
        *,
        now: Callable[[], datetime],
        token: str | None = None,
    ) -> None:
        self._transport = transport
        self._now = now
        self._token = token

    def _headers(self) -> dict[str, str]:
        headers = {
            "accept": "application/vnd.github+json",
            "x-github-api-version": "2022-11-28",
        }
        if self._token:
            headers["authorization"] = f"Bearer {self._token}"
        return headers

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
            per_page = min(retrieval.max_results, 100)
            query = urllib.parse.quote(retrieval.query, safe="")
            url = f"{_API_ROOT}/search/users?q={query}&per_page={per_page}"
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
                        safe_message="developer-source discovery request failed",
                        retryable=True,
                    )
                )
                continue

            failure = _failure_for_status(response.status, operation_id)
            if failure is not None:
                failures.append(failure)
                continue

            items = _decode(response.body).get("items")
            if not isinstance(items, list):
                continue
            for raw in items[: retrieval.max_results]:
                if not isinstance(raw, dict):
                    continue
                login = raw.get("login")
                if not isinstance(login, str) or not login:
                    continue
                rank += 1
                html_url = raw.get("html_url")
                original_url = (
                    html_url
                    if isinstance(html_url, str) and html_url
                    else f"{_HTML_ROOT}/{login}"
                )
                leads.append(
                    DiscoveryLead(
                        lead_id=f"github-user-{_slug(login)}",
                        retrieval_request_id=retrieval.retrieval_request_id,
                        original_url=original_url,
                        source_category=self.source_category,
                        discovered_at=self._now(),
                        rank=rank,
                        title=KnowledgeValue[str].known(login),
                        provider_summary=KnowledgeValue[str].unknown(
                            "A GitHub search result is a lead, not primary evidence"
                        ),
                        retrieval_relevance=_relevance(raw.get("score")),
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
            status=_result_status(bool(leads), bool(failures)),
            completed_at=self._now(),
            leads=tuple(leads),
            failures=tuple(failures),
            usage=usage,
        )

    async def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        operation_id = f"{_ADAPTER_ID}:acquire:{request.acquisition_request_id}"
        login = _login_from_url(request.original_url)
        if login is None:
            return self._acquisition_failure(
                request,
                AcquisitionStatus.FAILED,
                operation_id,
                "unsupported_url",
                "URL is not a GitHub developer profile",
                retryable=False,
            )

        url = f"{_API_ROOT}/users/{login}"
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
                "developer-source acquisition request failed",
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
                    "GitHub user record has no single authoritative event time"
                ),
            )
        if response.status == 403:
            return self._acquisition_failure(
                request,
                AcquisitionStatus.BLOCKED,
                operation_id,
                "rate_limited",
                "GitHub rate limit or access restriction",
                retryable=True,
            )
        if response.status == 404:
            return self._acquisition_failure(
                request,
                AcquisitionStatus.FAILED,
                operation_id,
                "not_found",
                "GitHub developer profile not found",
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


def _decode(body: bytes) -> dict[str, object]:
    try:
        parsed = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _failure_for_status(status: int, operation_id: str) -> CollectionFailure | None:
    if status == 200:
        return None
    if status == 403:
        return CollectionFailure(
            operation_id=operation_id,
            safe_code="rate_limited",
            safe_message="GitHub rate limit or access restriction",
            retryable=True,
        )
    return CollectionFailure(
        operation_id=operation_id,
        safe_code="upstream_status",
        safe_message="unexpected upstream status",
        retryable=status >= 500,
    )


__all__ = ["GitHubDeveloperActivitySource", "HttpResponse"]
