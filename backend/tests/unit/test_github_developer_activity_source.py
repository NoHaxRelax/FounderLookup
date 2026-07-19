"""Deterministic tests for bounded public GitHub developer-activity collection."""

import asyncio
import json
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import cast

import pytest

from founderlookup.domain.common import KnowledgeState
from founderlookup.domain.discovery import (
    AcquisitionRequest,
    AcquisitionResult,
    AcquisitionStatus,
    BoundedRetrievalRequest,
    CollectionResultStatus,
    DiscoveryRequest,
)
from founderlookup.domain.evidence import DataClassification, SourceCategory
from founderlookup.ingestion.ports import AcquisitionPort, DiscoveryPort
from founderlookup.ingestion.sources.github import (
    GitHubDeveloperActivitySource,
    GitHubPolicy,
)
from founderlookup.ingestion.sources.http import (
    HttpResponse,
    HttpTransportError,
)

FIXED_TIME = datetime(2026, 7, 1, 12, tzinfo=UTC)
API_VERSION = "2026-03-10"


def _now() -> datetime:
    return FIXED_TIME


@dataclass(frozen=True)
class _Call:
    url: str
    headers: Mapping[str, str]
    timeout_seconds: float
    max_bytes: int


class _CapturingTransport:
    def __init__(self, responses: Mapping[str, HttpResponse]) -> None:
        self._responses = dict(responses)
        self._calls: list[_Call] = []

    @property
    def calls(self) -> tuple[_Call, ...]:
        return tuple(self._calls)

    async def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_bytes: int,
    ) -> HttpResponse:
        self._calls.append(
            _Call(
                url=url,
                headers=dict(headers),
                timeout_seconds=timeout_seconds,
                max_bytes=max_bytes,
            )
        )
        try:
            return self._responses[url]
        except KeyError as error:
            raise HttpTransportError("no deterministic response") from error


def _json_response(
    payload: object,
    status: int = 200,
    *,
    headers: Mapping[str, str] | None = None,
) -> HttpResponse:
    return HttpResponse(
        status=status,
        headers=dict(headers or {"content-type": "application/json"}),
        body=json.dumps(payload).encode(),
    )


def _search_urls(query: str, per_page: int) -> tuple[str, str]:
    encoded = urllib.parse.quote(query, safe="")
    return (
        f"https://api.github.com/search/users?q={encoded}&per_page={per_page}&page=1",
        f"https://api.github.com/search/repositories?q={encoded}&per_page={per_page}&page=1",
    )


def _discovery_request(
    query: str = "ai infra founder",
    *,
    category: SourceCategory = SourceCategory.DEVELOPER_ACTIVITY,
    max_results: int = 5,
    allowed_domains: tuple[str, ...] = (),
    excluded_domains: tuple[str, ...] = (),
) -> DiscoveryRequest:
    return DiscoveryRequest(
        request_id="discovery-request-gh-001",
        query_plan_id="query-plan-gh-001",
        requested_at=FIXED_TIME,
        retrieval_requests=(
            BoundedRetrievalRequest(
                retrieval_request_id="retrieval-gh-001",
                query=query,
                source_categories=(category,),
                allowed_domains=allowed_domains,
                excluded_domains=excluded_domains,
                max_results=max_results,
                max_pages=1,
                timeout_seconds=30,
            ),
        ),
    )


def _profile_record() -> dict[str, object]:
    return {
        "login": "octocat",
        "id": 1,
        "node_id": "MDQ6VXNlcjE=",
        "type": "User",
        "name": "The Octocat",
        "company": "GitHub",
        "location": "San Francisco",
        "bio": "Public builder bio",
        "html_url": "https://github.com/octocat",
        "public_repos": 2,
        "followers": 20,
        "created_at": "2008-01-14T04:33:35Z",
        "updated_at": "2026-06-28T10:00:00Z",
        "email": "public-but-unnecessary@example.test",
        "private_gists": 81,
        "total_private_repos": 100,
    }


def _repository_record(
    *,
    private: bool = False,
    name: str = "Hello-World",
) -> dict[str, object]:
    return {
        "id": 1296269,
        "node_id": "repo-node",
        "name": name,
        "full_name": f"octocat/{name}",
        "html_url": f"https://github.com/octocat/{name}",
        "description": "Public developer work",
        "private": private,
        "visibility": "private" if private else "public",
        "fork": False,
        "archived": False,
        "disabled": False,
        "language": "Python",
        "topics": ["ai", "infrastructure"],
        "stargazers_count": 12,
        "forks_count": 3,
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2026-06-29T10:00:00Z",
        "pushed_at": "2026-06-30T09:00:00Z",
        "owner": {
            "login": "octocat",
            "id": 1,
            "type": "User",
            "html_url": "https://github.com/octocat",
            "email": "must-not-survive@example.test",
        },
        "permissions": {"admin": True},
    }


def _public_event(*, repository_name: str = "octocat/Hello-World") -> dict[str, object]:
    return {
        "id": "event-1",
        "type": "PushEvent",
        "public": True,
        "created_at": "2026-06-30T11:00:00Z",
        "actor": {"id": 1, "login": "octocat", "display_login": "octocat"},
        "repo": {"id": 1296269, "name": repository_name, "url": "safe-api-url"},
        "payload": {
            "push_id": 44,
            "head": "abc123",
            "before": "def456",
            "commits": [
                {
                    "sha": "abc123",
                    "author": {"email": "must-not-survive@example.test"},
                    "message": "not needed for the activity snapshot",
                }
            ],
        },
    }


def _profile_urls() -> tuple[str, str, str]:
    return (
        "https://api.github.com/users/octocat",
        "https://api.github.com/users/octocat/repos?type=owner&sort=updated"
        "&direction=desc&per_page=10&page=1",
        "https://api.github.com/users/octocat/events/public?per_page=20&page=1",
    )


def _repository_urls() -> tuple[str, str]:
    return (
        "https://api.github.com/repos/octocat/Hello-World",
        "https://api.github.com/repos/octocat/Hello-World/events?per_page=20&page=1",
    )


def _acquisition_request(
    url: str = "https://github.com/octocat",
    *,
    classification: DataClassification = DataClassification.PUBLIC,
    allowed_media_types: tuple[str, ...] = ("application/json",),
    max_bytes: int = 100_000,
) -> AcquisitionRequest:
    return AcquisitionRequest(
        acquisition_request_id="acquisition-gh-001",
        discovery_lead_id="github-user-octocat",
        original_url=url,
        requested_at=FIXED_TIME,
        classification=classification,
        allowed_media_types=allowed_media_types,
        max_bytes=max_bytes,
        timeout_seconds=30,
    )


def _snapshot(result: AcquisitionResult) -> dict[str, object]:
    assert result.content is not None
    parsed = cast(object, json.loads(result.content))
    assert isinstance(parsed, dict)
    return cast(dict[str, object], parsed)


def _as_object(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _as_list(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast(list[object], value)


def test_policy_validation_and_adapter_port_conformance() -> None:
    adapter = GitHubDeveloperActivitySource(_CapturingTransport({}), now=_now)
    assert isinstance(adapter, DiscoveryPort)
    assert isinstance(adapter, AcquisitionPort)

    with pytest.raises(ValueError, match="positive"):
        GitHubPolicy(max_queries=0)
    with pytest.raises(ValueError, match="20"):
        GitHubPolicy(max_results=21)
    with pytest.raises(ValueError, match="non-blank"):
        GitHubDeveloperActivitySource(_CapturingTransport({}), now=_now, token="  ")


def test_discover_maps_public_users_and_repositories_to_exact_leads() -> None:
    users_url, repositories_url = _search_urls("ai infra founder", 5)
    transport = _CapturingTransport(
        {
            users_url: _json_response(
                {
                    "items": [
                        {
                            "login": "octocat",
                            "type": "User",
                            "html_url": "https://github.com/octocat",
                            "score": 1.0,
                        },
                        {
                            "login": "github",
                            "type": "Organization",
                            "html_url": "https://github.com/github",
                            "score": 0.9,
                        },
                    ]
                }
            ),
            repositories_url: _json_response(
                {
                    "items": [
                        {**_repository_record(), "score": 0.7},
                        {**_repository_record(private=True, name="private-work"), "score": 2.0},
                    ]
                }
            ),
        }
    )
    adapter = GitHubDeveloperActivitySource(transport, now=_now)

    result = asyncio.run(adapter.discover(_discovery_request()))

    assert result.status is CollectionResultStatus.SUCCEEDED
    assert result.failures == ()
    assert [lead.original_url for lead in result.leads] == [
        "https://github.com/octocat",
        "https://github.com/octocat/Hello-World",
    ]
    assert [lead.rank for lead in result.leads] == [1, 2]
    assert all(lead.source_category is SourceCategory.DEVELOPER_ACTIVITY for lead in result.leads)
    assert result.leads[0].provider_summary.state is KnowledgeState.UNKNOWN
    assert result.leads[0].retrieval_relevance.value == 1.0
    assert result.usage.request_count == 2
    assert result.usage.result_count == 2
    assert result.usage.cost_amount.value == 0.0
    assert [call.url for call in transport.calls] == [users_url, repositories_url]
    assert all(call.headers["x-github-api-version"] == API_VERSION for call in transport.calls)
    assert all("authorization" not in call.headers for call in transport.calls)
    assert all(call.timeout_seconds == 10.0 for call in transport.calls)


def test_discovery_no_results_is_success_and_never_negative_evidence() -> None:
    users_url, repositories_url = _search_urls("nobody matches this", 5)
    adapter = GitHubDeveloperActivitySource(
        _CapturingTransport(
            {
                users_url: _json_response({"items": []}),
                repositories_url: _json_response({"items": []}),
            }
        ),
        now=_now,
    )

    result = asyncio.run(adapter.discover(_discovery_request("nobody matches this")))

    assert result.status is CollectionResultStatus.SUCCEEDED
    assert result.leads == ()
    assert result.failures == ()
    assert result.usage.result_count == 0


def test_discovery_partial_failure_preserves_authoritative_lead() -> None:
    users_url, repositories_url = _search_urls("builder", 5)
    transport = _CapturingTransport(
        {
            users_url: _json_response(
                {
                    "items": [
                        {
                            "login": "octocat",
                            "type": "User",
                            "html_url": "https://github.com/octocat",
                        }
                    ]
                }
            ),
            repositories_url: _json_response({}, status=429),
        }
    )

    result = asyncio.run(
        GitHubDeveloperActivitySource(transport, now=_now).discover(_discovery_request("builder"))
    )

    assert result.status is CollectionResultStatus.PARTIALLY_SUCCEEDED
    assert len(result.leads) == 1
    assert result.failures[0].safe_code == "rate_limited"
    assert result.failures[0].retryable is True


@pytest.mark.parametrize(
    ("discovery_input", "expected_code"),
    [
        (_discovery_request("x" * 257), "query_too_long"),
        (
            _discovery_request("builder", excluded_domains=("github.com",)),
            "domain_policy_rejected",
        ),
        (
            _discovery_request("builder", allowed_domains=("example.com",)),
            "domain_policy_rejected",
        ),
    ],
)
def test_discovery_rejects_unbounded_or_disallowed_requests_without_http(
    discovery_input: DiscoveryRequest,
    expected_code: str,
) -> None:
    transport = _CapturingTransport({})

    result = asyncio.run(
        GitHubDeveloperActivitySource(transport, now=_now).discover(discovery_input)
    )

    assert result.status is CollectionResultStatus.FAILED
    assert result.failures[0].safe_code == expected_code
    assert transport.calls == ()


def test_profile_acquisition_captures_sanitized_activity_with_exact_provenance() -> None:
    profile_url, repositories_url, events_url = _profile_urls()
    rate_headers = {
        "x-ratelimit-limit": "5000",
        "x-ratelimit-remaining": "4997",
        "x-ratelimit-used": "3",
        "x-ratelimit-reset": "1782907200",
        "x-ratelimit-resource": "core",
    }
    transport = _CapturingTransport(
        {
            profile_url: _json_response(_profile_record(), headers=rate_headers),
            repositories_url: _json_response([_repository_record()]),
            events_url: _json_response([_public_event()]),
        }
    )
    adapter = GitHubDeveloperActivitySource(
        transport,
        now=_now,
        token="server-token-sentinel",
    )

    result = asyncio.run(adapter.acquire(_acquisition_request()))

    assert result.status is AcquisitionStatus.ACQUIRED
    assert result.media_type == "application/json"
    assert result.content is not None
    assert result.content_sha256 == sha256(result.content).hexdigest()
    assert result.source_event_time.value == datetime(2026, 6, 30, 11, tzinfo=UTC)
    snapshot = _snapshot(result)
    assert snapshot["schema_version"] == "github-developer-activity-snapshot.v0"
    assert snapshot["api_version"] == API_VERSION
    assert snapshot["status"] == "succeeded"
    assert snapshot["classification"] == "public"
    assert snapshot["collection_purpose"] == "investor_sourcing_and_evaluation"

    subject = _as_object(snapshot["subject"])
    assert subject["original_url"] == "https://github.com/octocat"
    operations = _as_list(snapshot["operations"])
    assert [_as_object(item)["url"] for item in operations] == list(_profile_urls())
    first_rate_limit = _as_object(_as_object(operations[0])["rate_limit"])
    assert first_rate_limit == {
        "limit": 5000,
        "remaining": 4997,
        "used": 3,
        "reset_epoch": 1782907200,
        "resource": "core",
    }

    records = _as_object(snapshot["records"])
    profile = _as_object(records["profile"])
    assert profile["login"] == "octocat"
    assert "email" not in profile
    assert "private_gists" not in profile
    assert "total_private_repos" not in profile
    repositories = _as_list(records["repositories"])
    repository = _as_object(repositories[0])
    assert repository["private"] is False
    assert "permissions" not in repository
    owner = _as_object(repository["owner"])
    assert "email" not in owner
    events = _as_list(records["public_events"])
    event_payload = _as_object(_as_object(events[0])["payload"])
    assert event_payload["head"] == "abc123"
    assert "commits" not in event_payload

    assert all(
        call.headers["authorization"] == "Bearer server-token-sentinel" for call in transport.calls
    )
    assert all(call.headers["x-github-api-version"] == API_VERSION for call in transport.calls)
    assert "server-token-sentinel" not in result.model_dump_json()


def test_secondary_failure_produces_partial_snapshot_without_negative_inference() -> None:
    profile_url, repositories_url, events_url = _profile_urls()
    transport = _CapturingTransport(
        {
            profile_url: _json_response(_profile_record()),
            repositories_url: _json_response({}, status=503),
            events_url: _json_response([]),
        }
    )

    result = asyncio.run(
        GitHubDeveloperActivitySource(transport, now=_now).acquire(_acquisition_request())
    )

    assert result.status is AcquisitionStatus.ACQUIRED
    snapshot = _snapshot(result)
    assert snapshot["status"] == "partially_succeeded"
    operations = [_as_object(item) for item in _as_list(snapshot["operations"])]
    repository_operation = next(
        operation for operation in operations if operation["kind"] == "repositories"
    )
    assert repository_operation["status"] == "failed"
    failure = _as_object(repository_operation["failure"])
    assert failure["safe_code"] == "upstream_status"
    interpretation = _as_object(snapshot["interpretation"])
    assert interpretation["search_silence"] == "not_negative_evidence"
    records = _as_object(snapshot["records"])
    assert "repositories" not in records
    assert records["public_events"] == []


def test_repository_acquisition_requires_public_record_and_filters_events() -> None:
    repository_url, events_url = _repository_urls()
    private_event = {**_public_event(), "id": "event-private", "public": False}
    transport = _CapturingTransport(
        {
            repository_url: _json_response(_repository_record()),
            events_url: _json_response([_public_event(), private_event]),
        }
    )
    request = _acquisition_request("https://github.com/octocat/Hello-World")

    result = asyncio.run(GitHubDeveloperActivitySource(transport, now=_now).acquire(request))

    assert result.status is AcquisitionStatus.ACQUIRED
    records = _as_object(_snapshot(result)["records"])
    assert _as_object(records["repository"])["full_name"] == "octocat/Hello-World"
    assert len(_as_list(records["public_events"])) == 1

    private_transport = _CapturingTransport(
        {repository_url: _json_response(_repository_record(private=True))}
    )
    blocked = asyncio.run(
        GitHubDeveloperActivitySource(private_transport, now=_now).acquire(request)
    )
    assert blocked.status is AcquisitionStatus.BLOCKED
    assert blocked.failure is not None
    assert blocked.failure.safe_code == "non_public_record"
    assert blocked.content is None


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/octocat",
        "https://www.github.com/octocat",
        "https://user:pass@github.com/octocat",
        "https://github.com/octocat?tab=repositories",
        "https://github.com/octocat#activity",
        "https://github.com/search",
        "https://github.com/octocat/Hello-World/actions",
        "https://api.github.com/user",
        "https://api.github.com/users/octocat/events/public",
        "https://example.test/octocat",
    ],
)
def test_acquisition_strictly_rejects_noncanonical_or_nonpublic_endpoints(
    url: str,
) -> None:
    transport = _CapturingTransport({})
    result = asyncio.run(
        GitHubDeveloperActivitySource(transport, now=_now).acquire(_acquisition_request(url))
    )
    assert result.status is AcquisitionStatus.BLOCKED
    assert result.failure is not None
    assert result.failure.safe_code == "unsupported_url"
    assert transport.calls == ()


def test_acquisition_enforces_classification_media_and_body_budgets() -> None:
    transport = _CapturingTransport({})
    adapter = GitHubDeveloperActivitySource(transport, now=_now)

    private = asyncio.run(
        adapter.acquire(_acquisition_request(classification=DataClassification.FOUNDER_PRIVATE))
    )
    wrong_media = asyncio.run(
        adapter.acquire(_acquisition_request(allowed_media_types=("text/html",)))
    )
    assert private.status is AcquisitionStatus.BLOCKED
    assert wrong_media.status is AcquisitionStatus.BLOCKED
    assert private.failure is not None
    assert private.failure.safe_code == "classification_not_public"
    assert wrong_media.failure is not None
    assert wrong_media.failure.safe_code == "media_type_not_allowed"
    assert transport.calls == ()

    profile_url, _, _ = _profile_urls()
    oversized_transport = _CapturingTransport(
        {profile_url: HttpResponse(status=200, headers={}, body=b"x" * 101)}
    )
    oversized = asyncio.run(
        GitHubDeveloperActivitySource(
            oversized_transport,
            now=_now,
            policy=GitHubPolicy(max_response_bytes=100),
        ).acquire(_acquisition_request())
    )
    assert oversized.status is AcquisitionStatus.FAILED
    assert oversized.failure is not None
    assert oversized.failure.safe_code == "response_too_large"


def test_snapshot_budget_and_upstream_failures_are_safe() -> None:
    profile_url, _, _ = _profile_urls()
    snapshot_limited = asyncio.run(
        GitHubDeveloperActivitySource(
            _CapturingTransport({profile_url: _json_response(_profile_record())}),
            now=_now,
            policy=GitHubPolicy(max_acquisition_requests=1, max_snapshot_bytes=100),
        ).acquire(_acquisition_request())
    )
    assert snapshot_limited.status is AcquisitionStatus.FAILED
    assert snapshot_limited.failure is not None
    assert snapshot_limited.failure.safe_code == "snapshot_too_large"

    unauthorized = asyncio.run(
        GitHubDeveloperActivitySource(
            _CapturingTransport({profile_url: _json_response({}, status=401)}),
            now=_now,
            token="secret-sentinel",
        ).acquire(_acquisition_request())
    )
    assert unauthorized.status is AcquisitionStatus.BLOCKED
    assert unauthorized.failure is not None
    assert unauthorized.failure.safe_code == "authentication_rejected"
    assert "secret-sentinel" not in unauthorized.model_dump_json()


def test_adapter_ignores_other_source_categories() -> None:
    transport = _CapturingTransport({})
    result = asyncio.run(
        GitHubDeveloperActivitySource(transport, now=_now).discover(
            _discovery_request(category=SourceCategory.RESEARCH)
        )
    )
    assert result.status is CollectionResultStatus.SUCCEEDED
    assert result.leads == ()
    assert result.usage.request_count == 0
    assert transport.calls == ()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
