"""Bounded Tavily discovery and original-content acquisition adapter.

Tavily search snippets and relevance scores remain ``DiscoveryLead`` metadata.  Only
content returned by the separate Extract operation can cross the acquisition boundary
and become a Source Artifact.  The adapter accepts only public HTTP(S) targets and never
includes provider response bodies or credentials in failures.
"""

from __future__ import annotations

import ipaddress
import json
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Final
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import SecretStr

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
from founderlookup.domain.evidence import DataClassification
from founderlookup.ingestion.sources._support import relevance, result_status

_ADAPTER_ID: Final = "tavily-web-v0"
_API_ROOT: Final = "https://api.tavily.com"
_MAX_QUERY_CHARS: Final = 400
_ALLOWED_PORTS: Final = frozenset({80, 443})
_PROHIBITED_HOSTS: Final = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
        "metadata.aws.internal",
    }
)
_PROHIBITED_SUFFIXES: Final = (".localhost", ".local", ".internal", ".home.arpa")


@dataclass(frozen=True, slots=True)
class TavilyPolicy:
    """Server-controlled request and source-policy ceilings."""

    max_queries: int = 1
    max_results: int = 10
    max_pages: int = 5
    max_content_bytes: int = 500_000
    max_response_bytes: int = 2_000_000
    timeout_seconds: float = 20.0
    allowed_domains: tuple[str, ...] = ()
    excluded_domains: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        positive_values = {
            "max_queries": self.max_queries,
            "max_results": self.max_results,
            "max_pages": self.max_pages,
            "max_content_bytes": self.max_content_bytes,
            "max_response_bytes": self.max_response_bytes,
            "timeout_seconds": self.timeout_seconds,
        }
        if any(value <= 0 for value in positive_values.values()):
            raise ValueError("Tavily policy budgets must be positive")
        if self.max_results > 20:
            raise ValueError("Tavily max_results cannot exceed 20")
        if self.max_pages > self.max_results:
            raise ValueError("Tavily max_pages cannot exceed max_results")
        allowed = tuple(_normalize_domain(item) for item in self.allowed_domains)
        excluded = tuple(_normalize_domain(item) for item in self.excluded_domains)
        if set(allowed) & set(excluded):
            raise ValueError("a Tavily domain cannot be both allowed and excluded")
        object.__setattr__(self, "allowed_domains", allowed)
        object.__setattr__(self, "excluded_domains", excluded)


class TavilySource:
    """Direct async HTTP implementation of ``DiscoveryPort`` and ``AcquisitionPort``."""

    adapter_id = _ADAPTER_ID

    def __init__(
        self,
        *,
        api_key: SecretStr,
        policy: TavilyPolicy,
        now: Callable[[], datetime],
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.get_secret_value().strip():
            raise ValueError("Tavily API key must be non-blank")
        self._api_key = api_key
        self._policy = policy
        self._now = now
        self._client = client

    @asynccontextmanager
    async def _client_scope(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._client is not None:
            yield self._client
            return
        async with httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
        ) as client:
            yield client

    def _headers(self) -> Mapping[str, str]:
        return {
            "authorization": f"Bearer {self._api_key.get_secret_value()}",
            "accept": "application/json",
            "content-type": "application/json",
        }

    async def _post_json(
        self,
        path: str,
        payload: Mapping[str, object],
        *,
        timeout_seconds: float,
    ) -> tuple[int, bytes]:
        try:
            async with (
                self._client_scope() as client,
                client.stream(
                    "POST",
                    f"{_API_ROOT}{path}",
                    headers=self._headers(),
                    json=dict(payload),
                    timeout=timeout_seconds,
                ) as response,
            ):
                declared = response.headers.get("content-length")
                if declared is not None:
                    try:
                        if int(declared) > self._policy.max_response_bytes:
                            return 413, b""
                    except ValueError:
                        return 502, b""
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self._policy.max_response_bytes:
                        return 413, b""
                    chunks.append(chunk)
                return response.status_code, b"".join(chunks)
        except (httpx.HTTPError, TimeoutError):
            return 599, b""

    async def discover(self, request: DiscoveryRequest) -> DiscoveryResult:
        started = time.monotonic()
        leads: list[DiscoveryLead] = []
        failures: list[CollectionFailure] = []
        request_count = 0
        seen_urls: set[str] = set()
        rank = 0

        for index, retrieval in enumerate(request.retrieval_requests):
            operation_id = f"{_ADAPTER_ID}:discover:{retrieval.retrieval_request_id}"
            if index >= self._policy.max_queries:
                failures.append(
                    _failure(
                        operation_id,
                        "query_budget_exceeded",
                        "Tavily query budget was reached",
                        retryable=False,
                    )
                )
                continue
            query = retrieval.query.strip()
            if len(query) > _MAX_QUERY_CHARS:
                failures.append(
                    _failure(
                        operation_id,
                        "query_too_long",
                        "Tavily query exceeds the 400-character provider limit",
                        retryable=False,
                    )
                )
                continue
            try:
                allowed, excluded = _effective_domains(
                    policy_allowed=self._policy.allowed_domains,
                    policy_excluded=self._policy.excluded_domains,
                    request_allowed=retrieval.allowed_domains,
                    request_excluded=retrieval.excluded_domains,
                )
            except ValueError:
                failures.append(
                    _failure(
                        operation_id,
                        "domain_policy_rejected",
                        "The retrieval domain policy is not permitted",
                        retryable=False,
                    )
                )
                continue

            max_results = min(retrieval.max_results, self._policy.max_results)
            payload: dict[str, object] = {
                "query": query,
                "search_depth": "basic",
                "max_results": max_results,
                "include_answer": False,
                "include_raw_content": False,
                "include_images": False,
            }
            if allowed:
                payload["include_domains"] = list(allowed)
            if excluded:
                payload["exclude_domains"] = list(excluded)
            if retrieval.published_after is not None:
                payload["start_date"] = retrieval.published_after.date().isoformat()
            if retrieval.published_before is not None:
                payload["end_date"] = retrieval.published_before.date().isoformat()

            request_count += 1
            status, body = await self._post_json(
                "/search",
                payload,
                timeout_seconds=min(float(retrieval.timeout_seconds), self._policy.timeout_seconds),
            )
            status_failure = _discovery_status_failure(status, operation_id)
            if status_failure is not None:
                failures.append(status_failure)
                continue
            parsed = _json_object(body)
            results = parsed.get("results")
            if not isinstance(results, list):
                failures.append(
                    _failure(
                        operation_id,
                        "invalid_provider_payload",
                        "Tavily returned an invalid discovery payload",
                        retryable=True,
                    )
                )
                continue

            source_category = retrieval.source_categories[0]
            for result_index, raw in enumerate(results[:max_results], start=1):
                if not isinstance(raw, dict):
                    continue
                original_url = raw.get("url")
                if not isinstance(original_url, str):
                    continue
                try:
                    normalized_url, host = _validate_public_url(original_url)
                except ValueError:
                    failures.append(
                        _failure(
                            f"{operation_id}:result:{result_index}",
                            "unsafe_original_url",
                            "A Tavily result URL was rejected by public-source policy",
                            retryable=False,
                        )
                    )
                    continue
                if not _domain_is_allowed(host, allowed=allowed, excluded=excluded):
                    failures.append(
                        _failure(
                            f"{operation_id}:result:{result_index}",
                            "domain_policy_rejected",
                            "A Tavily result fell outside the approved domain policy",
                            retryable=False,
                        )
                    )
                    continue
                if normalized_url in seen_urls:
                    continue
                seen_urls.add(normalized_url)
                rank += 1
                title = raw.get("title")
                snippet = raw.get("content")
                digest = sha256(
                    f"{retrieval.retrieval_request_id}\0{normalized_url}".encode()
                ).hexdigest()[:24]
                leads.append(
                    DiscoveryLead(
                        lead_id=f"tavily-lead-{digest}",
                        retrieval_request_id=retrieval.retrieval_request_id,
                        original_url=normalized_url,
                        source_category=source_category,
                        discovered_at=self._now(),
                        rank=rank,
                        title=(
                            KnowledgeValue[str].known(title.strip())
                            if isinstance(title, str) and title.strip()
                            else KnowledgeValue[str].unknown("Tavily did not return a source title")
                        ),
                        provider_summary=(
                            KnowledgeValue[str].known(snippet.strip())
                            if isinstance(snippet, str) and snippet.strip()
                            else KnowledgeValue[str].unknown(
                                "Tavily did not return a discovery snippet"
                            )
                        ),
                        retrieval_relevance=relevance(raw.get("score")),
                    )
                )

        return DiscoveryResult(
            result_id=f"{_ADAPTER_ID}:discovery:{request.request_id}",
            request_id=request.request_id,
            status=result_status(bool(leads), bool(failures)),
            completed_at=self._now(),
            leads=tuple(leads),
            failures=tuple(failures),
            usage=ProviderUsage(
                adapter_id=_ADAPTER_ID,
                operation_id=f"{_ADAPTER_ID}:discover:{request.request_id}",
                request_count=request_count,
                result_count=len(leads),
                elapsed_milliseconds=max(0, int((time.monotonic() - started) * 1000)),
                cost_amount=KnowledgeValue[float].unknown(
                    "Tavily response did not provide request-level billed credits"
                ),
                cost_currency=KnowledgeValue[str].known("Tavily credits"),
            ),
        )

    async def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        operation_id = f"{_ADAPTER_ID}:acquire:{request.acquisition_request_id}"
        if request.classification is not DataClassification.PUBLIC:
            return self._acquisition_failure(
                request,
                AcquisitionStatus.BLOCKED,
                operation_id,
                "classification_blocked",
                "Tavily Extract accepts public content only",
                retryable=False,
            )
        try:
            original_url, host = _validate_public_url(request.original_url)
        except ValueError:
            return self._acquisition_failure(
                request,
                AcquisitionStatus.BLOCKED,
                operation_id,
                "unsafe_original_url",
                "The original URL was rejected by public-source policy",
                retryable=False,
            )
        if not _domain_is_allowed(
            host,
            allowed=self._policy.allowed_domains,
            excluded=self._policy.excluded_domains,
        ):
            return self._acquisition_failure(
                request,
                AcquisitionStatus.BLOCKED,
                operation_id,
                "domain_policy_rejected",
                "The original URL is outside the approved domain policy",
                retryable=False,
            )

        supported_media = next(
            (
                item
                for item in request.allowed_media_types
                if item.split(";", 1)[0].strip().lower() in {"text/markdown", "text/plain"}
            ),
            None,
        )
        if supported_media is None:
            return self._acquisition_failure(
                request,
                AcquisitionStatus.BLOCKED,
                operation_id,
                "media_type_blocked",
                "The acquisition media-type policy does not allow extracted text",
                retryable=False,
            )

        status, body = await self._post_json(
            "/extract",
            {
                "urls": [original_url],
                "extract_depth": "basic",
                "format": "markdown",
                "include_images": False,
            },
            timeout_seconds=min(float(request.timeout_seconds), self._policy.timeout_seconds),
        )
        status_failure = _acquisition_status_failure(status)
        if status_failure is not None:
            status_value, safe_code, safe_message, retryable = status_failure
            return self._acquisition_failure(
                request,
                status_value,
                operation_id,
                safe_code,
                safe_message,
                retryable=retryable,
            )

        parsed = _json_object(body)
        results = parsed.get("results")
        if not isinstance(results, list):
            return self._acquisition_failure(
                request,
                AcquisitionStatus.FAILED,
                operation_id,
                "invalid_provider_payload",
                "Tavily returned an invalid extraction payload",
                retryable=True,
            )
        selected: dict[str, object] | None = None
        for raw in results:
            if not isinstance(raw, dict):
                continue
            returned_url = raw.get("url")
            if isinstance(returned_url, str) and _urls_equivalent(returned_url, original_url):
                selected = raw
                break
        if selected is None:
            return self._acquisition_failure(
                request,
                AcquisitionStatus.FAILED,
                operation_id,
                "source_not_extracted",
                "Tavily did not extract the requested original source",
                retryable=True,
            )
        raw_content = selected.get("raw_content")
        if not isinstance(raw_content, str) or not raw_content.strip():
            return self._acquisition_failure(
                request,
                AcquisitionStatus.FAILED,
                operation_id,
                "empty_source_content",
                "Tavily returned no usable original-source content",
                retryable=True,
            )
        content = raw_content.encode("utf-8")
        maximum = min(request.max_bytes, self._policy.max_content_bytes)
        if len(content) > maximum:
            return self._acquisition_failure(
                request,
                AcquisitionStatus.BLOCKED,
                operation_id,
                "content_budget_exceeded",
                "Extracted source content exceeded the byte budget",
                retryable=False,
            )
        return AcquisitionResult(
            result_id=f"{_ADAPTER_ID}:acquisition:{request.acquisition_request_id}",
            acquisition_request_id=request.acquisition_request_id,
            original_url=original_url,
            status=AcquisitionStatus.ACQUIRED,
            completed_at=self._now(),
            content=content,
            media_type="text/markdown; charset=utf-8",
            content_sha256=sha256(content).hexdigest(),
            source_event_time=KnowledgeValue[datetime].unknown(
                "Tavily Extract did not establish an authoritative source event time"
            ),
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
                "No original source content was acquired"
            ),
            failure=_failure(
                operation_id,
                safe_code,
                safe_message,
                retryable=retryable,
            ),
        )


def _failure(
    operation_id: str,
    code: str,
    message: str,
    *,
    retryable: bool,
) -> CollectionFailure:
    return CollectionFailure(
        operation_id=operation_id,
        safe_code=code,
        safe_message=message,
        retryable=retryable,
    )


def _json_object(body: bytes) -> dict[str, object]:
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _discovery_status_failure(status: int, operation_id: str) -> CollectionFailure | None:
    if status == 200:
        return None
    if status in {401, 403}:
        return _failure(
            operation_id,
            "provider_access_denied",
            "Tavily discovery access was denied",
            retryable=False,
        )
    if status == 429:
        return _failure(
            operation_id,
            "provider_rate_limited",
            "Tavily discovery was rate limited",
            retryable=True,
        )
    if status == 413:
        return _failure(
            operation_id,
            "provider_response_too_large",
            "Tavily discovery response exceeded the byte budget",
            retryable=False,
        )
    return _failure(
        operation_id,
        "provider_unavailable",
        "Tavily discovery request failed",
        retryable=status == 599 or status >= 500,
    )


def _acquisition_status_failure(
    status: int,
) -> tuple[AcquisitionStatus, str, str, bool] | None:
    if status == 200:
        return None
    if status in {401, 403}:
        return (
            AcquisitionStatus.BLOCKED,
            "provider_access_denied",
            "Tavily extraction access was denied",
            False,
        )
    if status == 429:
        return (
            AcquisitionStatus.BLOCKED,
            "provider_rate_limited",
            "Tavily extraction was rate limited",
            True,
        )
    if status == 413:
        return (
            AcquisitionStatus.BLOCKED,
            "provider_response_too_large",
            "Tavily extraction response exceeded the byte budget",
            False,
        )
    return (
        AcquisitionStatus.FAILED,
        "provider_unavailable",
        "Tavily extraction request failed",
        status == 599 or status >= 500,
    )


def _normalize_domain(value: str) -> str:
    candidate = value.strip().casefold().rstrip(".")
    if (
        not candidate
        or "://" in candidate
        or "/" in candidate
        or "@" in candidate
        or ":" in candidate
    ):
        raise ValueError("domain policy entries must be bare hostnames")
    try:
        encoded = candidate.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ValueError("domain policy entry is invalid") from error
    if encoded == "localhost" or encoded.endswith(_PROHIBITED_SUFFIXES):
        raise ValueError("local domains are not permitted")
    try:
        address = ipaddress.ip_address(encoded)
    except ValueError:
        return encoded
    if not address.is_global:
        raise ValueError("private network domains are not permitted")
    return encoded


def _domain_matches(host: str, policy_domain: str) -> bool:
    return host == policy_domain or host.endswith(f".{policy_domain}")


def _domain_is_allowed(
    host: str,
    *,
    allowed: tuple[str, ...],
    excluded: tuple[str, ...],
) -> bool:
    if any(_domain_matches(host, item) for item in excluded):
        return False
    return not allowed or any(_domain_matches(host, item) for item in allowed)


def _compatible_domain(left: str, right: str) -> str | None:
    if _domain_matches(left, right):
        return left
    if _domain_matches(right, left):
        return right
    return None


def _effective_domains(
    *,
    policy_allowed: tuple[str, ...],
    policy_excluded: tuple[str, ...],
    request_allowed: tuple[str, ...],
    request_excluded: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    requested_allowed = tuple(_normalize_domain(item) for item in request_allowed)
    requested_excluded = tuple(_normalize_domain(item) for item in request_excluded)
    if policy_allowed and requested_allowed:
        intersection = {
            compatible
            for left in policy_allowed
            for right in requested_allowed
            if (compatible := _compatible_domain(left, right)) is not None
        }
        if not intersection:
            raise ValueError("request allowlist does not intersect policy allowlist")
        allowed = tuple(sorted(intersection))
    else:
        allowed = policy_allowed or requested_allowed
    excluded = tuple(sorted(set(policy_excluded) | set(requested_excluded)))
    return allowed, excluded


def _validate_public_url(value: str) -> tuple[str, str]:
    if len(value) > 2_048:
        raise ValueError("URL exceeds public-source policy")
    parsed = urlsplit(value)
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise ValueError("only HTTP(S) sources are permitted")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("credential-bearing URLs are not permitted")
    host = parsed.hostname
    if host is None:
        raise ValueError("source URL requires a hostname")
    host = host.casefold().rstrip(".")
    if host in _PROHIBITED_HOSTS or host.endswith(_PROHIBITED_SUFFIXES):
        raise ValueError("local sources are not permitted")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("source URL has an invalid port") from error
    if port is not None and port not in _ALLOWED_PORTS:
        raise ValueError("nonstandard source ports are not permitted")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as error:
            raise ValueError("source hostname is invalid") from error
    else:
        if not address.is_global:
            raise ValueError("private-network sources are not permitted")
    normalized = urlunsplit(
        (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
            parsed.path or "/",
            parsed.query,
            "",
        )
    )
    return normalized, host


def _urls_equivalent(left: str, right: str) -> bool:
    try:
        normalized_left, _ = _validate_public_url(left)
        normalized_right, _ = _validate_public_url(right)
    except ValueError:
        return False
    return normalized_left.rstrip("/") == normalized_right.rstrip("/")


__all__ = ["TavilyPolicy", "TavilySource"]
