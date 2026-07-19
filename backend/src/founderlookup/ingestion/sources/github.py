"""Bounded public GitHub developer-activity source adapter.

The adapter uses only GitHub's public REST endpoints.  It never scrapes GitHub
HTML, follows authenticated-user routes, or treats a missing search result as a
negative founder signal.  An optional server-side token may raise rate limits,
but every accepted record is still required to declare itself public and is
reduced to an allowlisted public field set before persistence.

``discover`` searches public users and repositories and returns exact
``github.com`` profile/repository URLs as provider-neutral leads.  ``acquire``
turns one of those URLs into a deterministic JSON snapshot containing exact API
endpoint provenance, bounded public profile/repository/event records, and safe
per-operation telemetry.  A partially available activity snapshot remains
useful evidence while explicitly recording gaps; source silence is never proof
that activity, traction, or prior work does not exist.

This is intended for human-reviewed investor sourcing and evaluation.  It does
not automate outreach, collect private data, or support bulk contact/spam uses.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Final, Literal, cast

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
from founderlookup.domain.evidence import DataClassification, SourceCategory
from founderlookup.ingestion.sources._support import (
    discovery_failure_for_status,
    relevance,
    result_status,
    slug,
)
from founderlookup.ingestion.sources.http import (
    HttpResponse,
    HttpTransport,
    HttpTransportError,
)

_API_ROOT: Final = "https://api.github.com"
_HTML_ROOT: Final = "https://github.com"
_API_VERSION: Final = "2026-03-10"
_ADAPTER_ID: Final = "github-developer-activity-v0"
_SNAPSHOT_SCHEMA_VERSION: Final = "github-developer-activity-snapshot.v0"
_JSON_MEDIA_TYPE: Final = "application/json"
_TERMS_URL: Final = "https://docs.github.com/en/site-policy/github-terms/github-terms-of-service"
_COLLECTION_PURPOSE: Final = "investor_sourcing_and_evaluation"
_MAX_QUERY_CHARS: Final = 256
_MAX_GITHUB_RESULTS: Final = 100
_LOGIN_MAX: Final = 39
_REPOSITORY_MAX: Final = 100
_LOGIN_PATTERN: Final = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_REPOSITORY_PATTERN: Final = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_RESERVED_HTML_ROOTS: Final = frozenset(
    {
        "about",
        "apps",
        "codespaces",
        "collections",
        "contact",
        "enterprise",
        "events",
        "explore",
        "features",
        "issues",
        "login",
        "marketplace",
        "new",
        "notifications",
        "organizations",
        "orgs",
        "pricing",
        "pulls",
        "search",
        "security",
        "settings",
        "site",
        "sponsors",
        "topics",
        "users",
    }
)
_PROFILE_FIELDS: Final = (
    "login",
    "id",
    "node_id",
    "type",
    "name",
    "company",
    "blog",
    "location",
    "bio",
    "public_repos",
    "public_gists",
    "followers",
    "following",
    "created_at",
    "updated_at",
    "html_url",
)
_REPOSITORY_FIELDS: Final = (
    "id",
    "node_id",
    "name",
    "full_name",
    "html_url",
    "description",
    "fork",
    "archived",
    "disabled",
    "private",
    "visibility",
    "language",
    "topics",
    "homepage",
    "size",
    "stargazers_count",
    "watchers_count",
    "forks_count",
    "open_issues_count",
    "created_at",
    "updated_at",
    "pushed_at",
)
_EVENT_FIELDS: Final = ("id", "type", "public", "created_at")
_EVENT_PAYLOAD_FIELDS: Final = (
    "action",
    "ref",
    "ref_type",
    "master_branch",
    "description",
    "pusher_type",
    "push_id",
    "size",
    "distinct_size",
    "head",
    "before",
    "repository_id",
    "number",
)


@dataclass(frozen=True, slots=True)
class GitHubPolicy:
    """Server-controlled ceilings for the public GitHub adapter."""

    max_queries: int = 1
    max_discovery_requests: int = 2
    max_results: int = 10
    max_activity_repositories: int = 10
    max_public_events: int = 20
    max_acquisition_requests: int = 3
    max_response_bytes: int = 1_000_000
    max_snapshot_bytes: int = 1_500_000
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        values = {
            "max_queries": self.max_queries,
            "max_discovery_requests": self.max_discovery_requests,
            "max_results": self.max_results,
            "max_activity_repositories": self.max_activity_repositories,
            "max_public_events": self.max_public_events,
            "max_acquisition_requests": self.max_acquisition_requests,
            "max_response_bytes": self.max_response_bytes,
            "max_snapshot_bytes": self.max_snapshot_bytes,
            "timeout_seconds": self.timeout_seconds,
        }
        if any(value <= 0 for value in values.values()):
            raise ValueError("GitHub policy budgets must be positive")
        if self.max_results > 20:
            raise ValueError("GitHub max_results cannot exceed the MVP ceiling of 20")
        if self.max_activity_repositories > _MAX_GITHUB_RESULTS:
            raise ValueError("GitHub repository capture cannot exceed 100 records")
        if self.max_public_events > _MAX_GITHUB_RESULTS:
            raise ValueError("GitHub event capture cannot exceed 100 records")
        if self.max_snapshot_bytes > 2_000_000:
            raise ValueError("GitHub snapshot bytes cannot exceed 2,000,000")


@dataclass(frozen=True, slots=True)
class _GitHubTarget:
    kind: Literal["user", "repository"]
    owner: str
    repository: str | None
    canonical_url: str


class GitHubDeveloperActivitySource:
    """Discover and acquire bounded, authoritative public GitHub activity."""

    source_category = SourceCategory.DEVELOPER_ACTIVITY
    adapter_id = _ADAPTER_ID

    def __init__(
        self,
        transport: HttpTransport,
        *,
        now: Callable[[], datetime],
        token: str | None = None,
        policy: GitHubPolicy | None = None,
    ) -> None:
        if token is not None and not token.strip():
            raise ValueError("GitHub token must be non-blank when supplied")
        self._transport = transport
        self._now = now
        self._token = token.strip() if token is not None else None
        self._policy = policy or GitHubPolicy()

    def _headers(self) -> dict[str, str]:
        headers = {
            "accept": "application/vnd.github+json",
            "x-github-api-version": _API_VERSION,
        }
        if self._token is not None:
            headers["authorization"] = f"Bearer {self._token}"
        return headers

    async def discover(self, request: DiscoveryRequest) -> DiscoveryResult:
        started = time.monotonic()
        leads: list[DiscoveryLead] = []
        failures: list[CollectionFailure] = []
        seen_urls: set[str] = set()
        request_count = 0
        handled_queries = 0

        for retrieval in request.retrieval_requests:
            if self.source_category not in retrieval.source_categories:
                continue
            operation_prefix = f"{_ADAPTER_ID}:discover:{retrieval.retrieval_request_id}"
            if handled_queries >= self._policy.max_queries:
                failures.append(
                    _failure(
                        operation_prefix,
                        "query_budget_exceeded",
                        "GitHub query budget was reached",
                        retryable=False,
                    )
                )
                continue
            handled_queries += 1

            query = retrieval.query.strip()
            if len(query) > _MAX_QUERY_CHARS:
                failures.append(
                    _failure(
                        operation_prefix,
                        "query_too_long",
                        "GitHub query exceeds the adapter limit",
                        retryable=False,
                    )
                )
                continue
            if retrieval.published_after is not None or retrieval.published_before is not None:
                failures.append(
                    _failure(
                        operation_prefix,
                        "unsupported_time_window",
                        "GitHub identity discovery cannot safely apply an activity time window",
                        retryable=False,
                    )
                )
                continue
            if not _domain_policy_allows_github(
                allowed=retrieval.allowed_domains,
                excluded=retrieval.excluded_domains,
            ):
                failures.append(
                    _failure(
                        operation_prefix,
                        "domain_policy_rejected",
                        "GitHub is outside the approved retrieval domain policy",
                        retryable=False,
                    )
                )
                continue

            result_limit = min(retrieval.max_results, self._policy.max_results)
            encoded_query = urllib.parse.quote(query, safe="")
            search_operations = (
                (
                    "users",
                    f"{_API_ROOT}/search/users?q={encoded_query}&per_page={result_limit}&page=1",
                ),
                (
                    "repositories",
                    f"{_API_ROOT}/search/repositories?q={encoded_query}"
                    f"&per_page={result_limit}&page=1",
                ),
            )
            for search_kind, url in search_operations:
                operation_id = f"{operation_prefix}:{search_kind}"
                if len(leads) >= result_limit:
                    break
                if request_count >= self._policy.max_discovery_requests:
                    failures.append(
                        _failure(
                            operation_id,
                            "request_budget_exceeded",
                            "GitHub discovery request budget was reached",
                            retryable=False,
                        )
                    )
                    continue
                request_count += 1
                response, transport_failure = await self._get(
                    url,
                    operation_id=operation_id,
                    timeout_seconds=min(
                        float(retrieval.timeout_seconds), self._policy.timeout_seconds
                    ),
                    max_bytes=self._policy.max_response_bytes,
                    safe_context="GitHub discovery",
                )
                if transport_failure is not None:
                    failures.append(transport_failure)
                    continue
                assert response is not None
                status_failure = discovery_failure_for_status(response.status, operation_id)
                if status_failure is not None:
                    failures.append(status_failure)
                    continue
                parsed = _json_object(response.body)
                items = parsed.get("items") if parsed is not None else None
                if not isinstance(items, list):
                    failures.append(
                        _failure(
                            operation_id,
                            "invalid_provider_payload",
                            "GitHub returned an invalid discovery payload",
                            retryable=True,
                        )
                    )
                    continue

                for raw_item in items:
                    if len(leads) >= result_limit:
                        break
                    raw = _string_keyed_object(raw_item)
                    if raw is None:
                        continue
                    mapped = (
                        _user_lead(raw, retrieval.retrieval_request_id, self._now())
                        if search_kind == "users"
                        else _repository_lead(raw, retrieval.retrieval_request_id, self._now())
                    )
                    if mapped is None or mapped.original_url in seen_urls:
                        continue
                    seen_urls.add(mapped.original_url)
                    leads.append(mapped.model_copy(update={"rank": len(leads) + 1}))

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
        operation_prefix = f"{_ADAPTER_ID}:acquire:{request.acquisition_request_id}"
        if request.classification is not DataClassification.PUBLIC:
            return self._acquisition_failure(
                request,
                AcquisitionStatus.BLOCKED,
                operation_prefix,
                "classification_not_public",
                "GitHub collection accepts only explicitly public source locators",
                retryable=False,
            )
        if not _allows_json(request.allowed_media_types):
            return self._acquisition_failure(
                request,
                AcquisitionStatus.BLOCKED,
                operation_prefix,
                "media_type_not_allowed",
                "GitHub JSON is outside the acquisition media policy",
                retryable=False,
            )
        try:
            target = _parse_public_target(request.original_url)
        except ValueError:
            return self._acquisition_failure(
                request,
                AcquisitionStatus.BLOCKED,
                operation_prefix,
                "unsupported_url",
                "URL is outside the public GitHub source allowlist",
                retryable=False,
            )

        endpoint_specs = _activity_endpoints(target, self._policy)
        operations: list[dict[str, object]] = []
        captured: dict[str, object] = {}
        source_times: list[datetime] = []
        accepted_primary = False

        for index, (operation_kind, url, result_limit) in enumerate(endpoint_specs):
            operation_id = f"{operation_prefix}:{operation_kind}"
            if index >= self._policy.max_acquisition_requests:
                operations.append(
                    _operation_failure(
                        operation_kind,
                        url,
                        _failure(
                            operation_id,
                            "request_budget_exceeded",
                            "GitHub acquisition request budget was reached",
                            retryable=False,
                        ),
                        AcquisitionStatus.BLOCKED,
                    )
                )
                continue

            response, transport_failure = await self._get(
                url,
                operation_id=operation_id,
                timeout_seconds=min(float(request.timeout_seconds), self._policy.timeout_seconds),
                max_bytes=min(request.max_bytes, self._policy.max_response_bytes),
                safe_context="GitHub acquisition",
            )
            if transport_failure is not None:
                if index == 0:
                    return self._acquisition_failure(
                        request,
                        AcquisitionStatus.FAILED,
                        operation_id,
                        transport_failure.safe_code,
                        transport_failure.safe_message,
                        retryable=transport_failure.retryable,
                    )
                operations.append(
                    _operation_failure(
                        operation_kind,
                        url,
                        transport_failure,
                        AcquisitionStatus.FAILED,
                    )
                )
                continue

            assert response is not None
            status_failure = _acquisition_failure_for_status(response.status, operation_id)
            if status_failure is not None:
                failure_status, failure = status_failure
                if index == 0:
                    return self._acquisition_failure(
                        request,
                        failure_status,
                        operation_id,
                        failure.safe_code,
                        failure.safe_message,
                        retryable=failure.retryable,
                    )
                operations.append(_operation_failure(operation_kind, url, failure, failure_status))
                continue

            if index == 0 and target.kind == "repository":
                primary_repository = _json_object(response.body)
                if (
                    primary_repository is not None
                    and primary_repository.get("private") is not False
                ):
                    return self._acquisition_failure(
                        request,
                        AcquisitionStatus.BLOCKED,
                        operation_id,
                        "non_public_record",
                        "GitHub repository is not an explicitly public record",
                        retryable=False,
                    )

            normalized = _normalize_activity_payload(
                target=target,
                operation_kind=operation_kind,
                body=response.body,
                result_limit=result_limit,
            )
            if normalized is None:
                failure = _failure(
                    operation_id,
                    "invalid_provider_payload",
                    "GitHub returned an invalid or non-public activity payload",
                    retryable=True,
                )
                if index == 0:
                    return self._acquisition_failure(
                        request,
                        AcquisitionStatus.FAILED,
                        operation_id,
                        failure.safe_code,
                        failure.safe_message,
                        retryable=failure.retryable,
                    )
                operations.append(
                    _operation_failure(operation_kind, url, failure, AcquisitionStatus.FAILED)
                )
                continue

            value, result_count, event_times = normalized
            captured[operation_kind] = value
            source_times.extend(event_times)
            operations.append(
                _operation_success(
                    operation_kind,
                    url,
                    response,
                    result_count=result_count,
                )
            )
            if index == 0:
                accepted_primary = True

        if not accepted_primary:
            return self._acquisition_failure(
                request,
                AcquisitionStatus.FAILED,
                operation_prefix,
                "primary_record_unavailable",
                "GitHub primary public record was not acquired",
                retryable=True,
            )

        completed_at = self._now()
        partial = any(operation["status"] != "succeeded" for operation in operations)
        snapshot: dict[str, object] = {
            "schema_version": _SNAPSHOT_SCHEMA_VERSION,
            "adapter_id": _ADAPTER_ID,
            "api_version": _API_VERSION,
            "status": "partially_succeeded" if partial else "succeeded",
            "retrieved_at": completed_at.isoformat(),
            "classification": DataClassification.PUBLIC.value,
            "collection_purpose": _COLLECTION_PURPOSE,
            "source_terms": _TERMS_URL,
            "subject": {
                "kind": target.kind,
                "owner": target.owner,
                "repository": target.repository,
                "original_url": target.canonical_url,
            },
            "operations": operations,
            "records": captured,
            "interpretation": {
                "search_silence": "not_negative_evidence",
                "public_events": "recent_public_activity_only_not_complete_history",
                "metrics": "context_only_not_founder_or_trust_score",
            },
        }
        content = json.dumps(
            snapshot,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        content_limit = min(request.max_bytes, self._policy.max_snapshot_bytes)
        if len(content) > content_limit:
            return self._acquisition_failure(
                request,
                AcquisitionStatus.FAILED,
                operation_prefix,
                "snapshot_too_large",
                "GitHub activity snapshot exceeded the configured byte budget",
                retryable=False,
            )

        source_event_time = (
            KnowledgeValue[datetime].known(max(source_times))
            if source_times
            else KnowledgeValue[datetime].unknown(
                "The captured GitHub records expose no valid source event time"
            )
        )
        return AcquisitionResult(
            result_id=f"{_ADAPTER_ID}:acquisition:{request.acquisition_request_id}",
            acquisition_request_id=request.acquisition_request_id,
            original_url=request.original_url,
            status=AcquisitionStatus.ACQUIRED,
            completed_at=completed_at,
            content=content,
            media_type=_JSON_MEDIA_TYPE,
            content_sha256=sha256(content).hexdigest(),
            source_event_time=source_event_time,
        )

    async def _get(
        self,
        url: str,
        *,
        operation_id: str,
        timeout_seconds: float,
        max_bytes: int,
        safe_context: str,
    ) -> tuple[HttpResponse | None, CollectionFailure | None]:
        try:
            response = await self._transport.get(
                url,
                headers=self._headers(),
                timeout_seconds=timeout_seconds,
                max_bytes=max_bytes,
            )
        except HttpTransportError:
            return None, _failure(
                operation_id,
                "transport_error",
                f"{safe_context} request failed",
                retryable=True,
            )
        if len(response.body) > max_bytes:
            return None, _failure(
                operation_id,
                "response_too_large",
                f"{safe_context} response exceeded the configured byte budget",
                retryable=False,
            )
        return response, None

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
                "No public GitHub content was acquired, so no source event time exists"
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
    safe_code: str,
    safe_message: str,
    *,
    retryable: bool,
) -> CollectionFailure:
    return CollectionFailure(
        operation_id=operation_id,
        safe_code=safe_code,
        safe_message=safe_message,
        retryable=retryable,
    )


def _parse_public_target(url: str) -> _GitHubTarget:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.username is not None or parsed.password is not None:
        raise ValueError("GitHub source URL must use credential-free HTTPS")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("GitHub source URL has an invalid port") from error
    if port not in {None, 443} or parsed.query or parsed.fragment:
        raise ValueError("GitHub source URL contains unsupported components")
    if "%" in parsed.path or "\\" in parsed.path or parsed.path.endswith("/"):
        raise ValueError("GitHub source URL must use a canonical path")
    segments = parsed.path.removeprefix("/").split("/") if parsed.path != "/" else []
    if not segments or any(not segment for segment in segments):
        raise ValueError("GitHub source URL path is invalid")

    host = (parsed.hostname or "").lower()
    if host == "github.com":
        if len(segments) not in {1, 2}:
            raise ValueError("GitHub HTML URL is not a public profile or repository")
        owner = segments[0]
        repository = segments[1] if len(segments) == 2 else None
    elif host == "api.github.com":
        if len(segments) == 2 and segments[0] == "users":
            owner = segments[1]
            repository = None
        elif len(segments) == 3 and segments[0] == "repos":
            owner = segments[1]
            repository = segments[2]
        else:
            raise ValueError("GitHub API URL is not an allowlisted public record")
    else:
        raise ValueError("GitHub source host is not allowlisted")

    if not _valid_login(owner) or owner.lower() in _RESERVED_HTML_ROOTS:
        raise ValueError("GitHub owner is invalid")
    if repository is not None and not _valid_repository(repository):
        raise ValueError("GitHub repository is invalid")
    canonical_url = (
        f"https://{host}/{segments[0]}/{segments[1]}/{segments[2]}"
        if host == "api.github.com" and repository is not None
        else f"https://{host}/{segments[0]}/{segments[1]}"
        if host == "api.github.com"
        else f"{_HTML_ROOT}/{owner}/{repository}"
        if repository is not None
        else f"{_HTML_ROOT}/{owner}"
    )
    explicit_standard_port = port == 443 and url == canonical_url.replace(host, f"{host}:443")
    if url != canonical_url and not explicit_standard_port:
        raise ValueError("GitHub source URL is not canonical")
    return _GitHubTarget(
        kind="repository" if repository is not None else "user",
        owner=owner,
        repository=repository,
        canonical_url=url,
    )


def _valid_login(value: str) -> bool:
    return len(value) <= _LOGIN_MAX and _LOGIN_PATTERN.fullmatch(value) is not None


def _valid_repository(value: str) -> bool:
    return (
        len(value) <= _REPOSITORY_MAX
        and value not in {".", ".."}
        and not value.endswith(".git")
        and _REPOSITORY_PATTERN.fullmatch(value) is not None
    )


def _domain_policy_allows_github(
    *,
    allowed: tuple[str, ...],
    excluded: tuple[str, ...],
) -> bool:
    normalized_allowed = {_normalize_domain(value) for value in allowed}
    normalized_excluded = {_normalize_domain(value) for value in excluded}
    if None in normalized_allowed or None in normalized_excluded:
        return False
    github_domains = {"github.com", "api.github.com"}
    if normalized_excluded & github_domains:
        return False
    return not normalized_allowed or "github.com" in normalized_allowed


def _normalize_domain(value: str) -> str | None:
    candidate = value.strip().lower().rstrip(".")
    if not candidate or "://" in candidate or "/" in candidate or ":" in candidate:
        return None
    return candidate


def _user_lead(
    raw: Mapping[str, object],
    retrieval_request_id: str,
    discovered_at: datetime,
) -> DiscoveryLead | None:
    login = raw.get("login")
    html_url = raw.get("html_url")
    if raw.get("type") != "User" or not isinstance(login, str) or not isinstance(html_url, str):
        return None
    try:
        target = _parse_public_target(html_url)
    except ValueError:
        return None
    if target.kind != "user" or target.owner.lower() != login.lower():
        return None
    return DiscoveryLead(
        lead_id=f"github-user-{slug(login)}",
        retrieval_request_id=retrieval_request_id,
        original_url=target.canonical_url,
        source_category=SourceCategory.DEVELOPER_ACTIVITY,
        discovered_at=discovered_at,
        rank=1,
        title=KnowledgeValue[str].known(login),
        provider_summary=KnowledgeValue[str].unknown(
            "A GitHub search result is a lead, not primary evidence"
        ),
        retrieval_relevance=relevance(raw.get("score")),
    )


def _repository_lead(
    raw: Mapping[str, object],
    retrieval_request_id: str,
    discovered_at: datetime,
) -> DiscoveryLead | None:
    full_name = raw.get("full_name")
    html_url = raw.get("html_url")
    if raw.get("private") is not False:
        return None
    if not isinstance(full_name, str) or not isinstance(html_url, str):
        return None
    try:
        target = _parse_public_target(html_url)
    except ValueError:
        return None
    if target.kind != "repository" or target.repository is None:
        return None
    if f"{target.owner}/{target.repository}".lower() != full_name.lower():
        return None
    return DiscoveryLead(
        lead_id=f"github-repository-{slug(target.owner)}-{slug(target.repository)}",
        retrieval_request_id=retrieval_request_id,
        original_url=target.canonical_url,
        source_category=SourceCategory.DEVELOPER_ACTIVITY,
        discovered_at=discovered_at,
        rank=1,
        title=KnowledgeValue[str].known(full_name),
        provider_summary=KnowledgeValue[str].unknown(
            "A GitHub search result is a lead, not primary evidence"
        ),
        retrieval_relevance=relevance(raw.get("score")),
    )


def _allows_json(allowed_media_types: tuple[str, ...]) -> bool:
    return any(
        value.split(";", 1)[0].strip().lower() == _JSON_MEDIA_TYPE for value in allowed_media_types
    )


def _activity_endpoints(
    target: _GitHubTarget,
    policy: GitHubPolicy,
) -> tuple[tuple[str, str, int], ...]:
    owner = urllib.parse.quote(target.owner, safe="")
    if target.kind == "user":
        return (
            ("profile", f"{_API_ROOT}/users/{owner}", 1),
            (
                "repositories",
                f"{_API_ROOT}/users/{owner}/repos?type=owner&sort=updated&direction=desc"
                f"&per_page={policy.max_activity_repositories}&page=1",
                policy.max_activity_repositories,
            ),
            (
                "public_events",
                f"{_API_ROOT}/users/{owner}/events/public"
                f"?per_page={policy.max_public_events}&page=1",
                policy.max_public_events,
            ),
        )
    assert target.repository is not None
    repository = urllib.parse.quote(target.repository, safe="._-")
    return (
        ("repository", f"{_API_ROOT}/repos/{owner}/{repository}", 1),
        (
            "public_events",
            f"{_API_ROOT}/repos/{owner}/{repository}/events"
            f"?per_page={policy.max_public_events}&page=1",
            policy.max_public_events,
        ),
    )


def _normalize_activity_payload(
    *,
    target: _GitHubTarget,
    operation_kind: str,
    body: bytes,
    result_limit: int,
) -> tuple[object, int, tuple[datetime, ...]] | None:
    if operation_kind == "profile":
        raw = _json_object(body)
        if raw is None or raw.get("type") != "User":
            return None
        login = raw.get("login")
        html_url = raw.get("html_url")
        if not isinstance(login, str) or login.lower() != target.owner.lower():
            return None
        if not isinstance(html_url, str) or not _url_matches_target(html_url, target):
            return None
        profile = _copy_public_fields(raw, _PROFILE_FIELDS)
        return profile, 1, _times_from_records((profile,))

    if operation_kind == "repository":
        raw = _json_object(body)
        if raw is None or raw.get("private") is not False:
            return None
        html_url = raw.get("html_url")
        if not isinstance(html_url, str) or not _url_matches_target(html_url, target):
            return None
        repository = _sanitize_repository(raw)
        return repository, 1, _times_from_records((repository,))

    raw_items = _json_list(body)
    if raw_items is None:
        return None
    if operation_kind == "repositories":
        repositories: list[dict[str, object]] = []
        for raw_item in raw_items:
            raw = _string_keyed_object(raw_item)
            if raw is None or raw.get("private") is not False:
                continue
            html_url = raw.get("html_url")
            owner = _string_keyed_object(raw.get("owner"))
            if not isinstance(html_url, str) or owner is None:
                continue
            owner_login = owner.get("login")
            if not isinstance(owner_login, str) or owner_login.lower() != target.owner.lower():
                continue
            try:
                parsed_target = _parse_public_target(html_url)
            except ValueError:
                continue
            if parsed_target.kind != "repository":
                continue
            repositories.append(_sanitize_repository(raw))
            if len(repositories) >= result_limit:
                break
        return repositories, len(repositories), _times_from_records(tuple(repositories))

    if operation_kind == "public_events":
        events: list[dict[str, object]] = []
        for raw_item in raw_items:
            raw = _string_keyed_object(raw_item)
            if raw is None or raw.get("public") is not True:
                continue
            if not _event_matches_target(raw, target):
                continue
            event = _sanitize_event(raw)
            if event is None:
                continue
            events.append(event)
            if len(events) >= result_limit:
                break
        return events, len(events), _times_from_records(tuple(events))
    return None


def _event_matches_target(raw: Mapping[str, object], target: _GitHubTarget) -> bool:
    if target.kind == "user":
        actor = _string_keyed_object(raw.get("actor"))
        actor_login = actor.get("login") if actor is not None else None
        return isinstance(actor_login, str) and actor_login.lower() == target.owner.lower()
    assert target.repository is not None
    repository = _string_keyed_object(raw.get("repo"))
    repository_name = repository.get("name") if repository is not None else None
    return (
        isinstance(repository_name, str)
        and repository_name.lower() == f"{target.owner}/{target.repository}".lower()
    )


def _url_matches_target(url: str, target: _GitHubTarget) -> bool:
    try:
        parsed = _parse_public_target(url)
    except ValueError:
        return False
    return (
        parsed.kind == target.kind
        and parsed.owner.lower() == target.owner.lower()
        and (parsed.repository or "").lower() == (target.repository or "").lower()
    )


def _sanitize_repository(raw: Mapping[str, object]) -> dict[str, object]:
    result = _copy_public_fields(raw, _REPOSITORY_FIELDS)
    owner = _string_keyed_object(raw.get("owner"))
    if owner is not None:
        result["owner"] = _copy_public_fields(owner, ("login", "id", "node_id", "type", "html_url"))
    license_record = _string_keyed_object(raw.get("license"))
    if license_record is not None:
        result["license"] = _copy_public_fields(license_record, ("key", "name", "spdx_id", "url"))
    return result


def _sanitize_event(raw: Mapping[str, object]) -> dict[str, object] | None:
    event_id = raw.get("id")
    event_type = raw.get("type")
    created_at = raw.get("created_at")
    if not isinstance(event_id, str) or not isinstance(event_type, str):
        return None
    if not isinstance(created_at, str) or _parse_utc(created_at) is None:
        return None
    result = _copy_public_fields(raw, _EVENT_FIELDS)
    actor = _string_keyed_object(raw.get("actor"))
    if actor is not None:
        result["actor"] = _copy_public_fields(actor, ("id", "login", "display_login", "url"))
    repository = _string_keyed_object(raw.get("repo"))
    if repository is not None:
        result["repository"] = _copy_public_fields(repository, ("id", "name", "url"))
    payload = _string_keyed_object(raw.get("payload"))
    if payload is not None:
        result["payload"] = _copy_public_fields(payload, _EVENT_PAYLOAD_FIELDS)
    return result


def _copy_public_fields(
    raw: Mapping[str, object],
    fields: tuple[str, ...],
) -> dict[str, object]:
    return {field: raw[field] for field in fields if field in raw}


def _times_from_records(records: tuple[Mapping[str, object], ...]) -> tuple[datetime, ...]:
    values: list[datetime] = []
    for record in records:
        for field in ("created_at", "updated_at", "pushed_at"):
            parsed = _parse_utc(record.get(field))
            if parsed is not None:
                values.append(parsed)
    return tuple(values)


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _json_object(body: bytes) -> dict[str, object] | None:
    try:
        parsed = cast(object, json.loads(body))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return _string_keyed_object(parsed)


def _json_list(body: bytes) -> list[object] | None:
    try:
        parsed = cast(object, json.loads(body))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return cast(list[object], parsed) if isinstance(parsed, list) else None


def _string_keyed_object(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        return None
    return cast(dict[str, object], value)


def _operation_success(
    operation_kind: str,
    url: str,
    response: HttpResponse,
    *,
    result_count: int,
) -> dict[str, object]:
    return {
        "kind": operation_kind,
        "url": url,
        "status": "succeeded",
        "result_count": result_count,
        "response_bytes": len(response.body),
        "response_sha256": sha256(response.body).hexdigest(),
        "rate_limit": _safe_rate_limit(response.headers),
    }


def _operation_failure(
    operation_kind: str,
    url: str,
    failure: CollectionFailure,
    status: AcquisitionStatus,
) -> dict[str, object]:
    return {
        "kind": operation_kind,
        "url": url,
        "status": status.value,
        "failure": {
            "safe_code": failure.safe_code,
            "safe_message": failure.safe_message,
            "retryable": failure.retryable,
        },
    }


def _safe_rate_limit(headers: Mapping[str, str]) -> dict[str, object]:
    normalized = {key.lower(): value for key, value in headers.items()}
    result: dict[str, object] = {}
    for header, output_key in (
        ("x-ratelimit-limit", "limit"),
        ("x-ratelimit-remaining", "remaining"),
        ("x-ratelimit-used", "used"),
        ("x-ratelimit-reset", "reset_epoch"),
        ("x-poll-interval", "poll_interval_seconds"),
    ):
        raw = normalized.get(header)
        if raw is None:
            continue
        try:
            result[output_key] = int(raw)
        except ValueError:
            continue
    resource = normalized.get("x-ratelimit-resource")
    if resource in {"core", "search"}:
        result["resource"] = resource
    return result


def _acquisition_failure_for_status(
    status: int,
    operation_id: str,
) -> tuple[AcquisitionStatus, CollectionFailure] | None:
    if status == 200:
        return None
    if status in {403, 429}:
        return AcquisitionStatus.BLOCKED, _failure(
            operation_id,
            "rate_limited",
            "GitHub rate limit or access restriction",
            retryable=True,
        )
    if status == 401:
        return AcquisitionStatus.BLOCKED, _failure(
            operation_id,
            "authentication_rejected",
            "GitHub rejected the optional server credential",
            retryable=False,
        )
    if status == 404:
        return AcquisitionStatus.FAILED, _failure(
            operation_id,
            "not_found",
            "GitHub public record was not found",
            retryable=False,
        )
    if status == 410:
        return AcquisitionStatus.FAILED, _failure(
            operation_id,
            "api_version_unavailable",
            "GitHub API version or resource is no longer available",
            retryable=False,
        )
    if status == 451:
        return AcquisitionStatus.BLOCKED, _failure(
            operation_id,
            "legal_restriction",
            "GitHub record is unavailable for legal reasons",
            retryable=False,
        )
    return AcquisitionStatus.FAILED, _failure(
        operation_id,
        "upstream_status",
        "GitHub returned an unexpected upstream status",
        retryable=status >= 500,
    )


__all__ = ["GitHubDeveloperActivitySource", "GitHubPolicy"]
