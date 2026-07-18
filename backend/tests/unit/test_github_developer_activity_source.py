"""Deterministic tests for the GitHub developer-activity source adapter."""

import asyncio
import json
import urllib.parse
from datetime import UTC, datetime
from hashlib import sha256

import pytest

from founderlookup.domain.common import KnowledgeState
from founderlookup.domain.discovery import (
    AcquisitionRequest,
    AcquisitionStatus,
    BoundedRetrievalRequest,
    CollectionResultStatus,
    DiscoveryRequest,
)
from founderlookup.domain.evidence import DataClassification, SourceCategory
from founderlookup.ingestion.ports import AcquisitionPort, DiscoveryPort
from founderlookup.ingestion.sources.github import GitHubDeveloperActivitySource
from founderlookup.ingestion.sources.http import HttpResponse, RecordedHttpTransport

FIXED_TIME = datetime(2026, 7, 1, 12, tzinfo=UTC)


def _now() -> datetime:
    return FIXED_TIME


def _search_url(query: str, per_page: int) -> str:
    return (
        "https://api.github.com/search/users"
        f"?q={urllib.parse.quote(query, safe='')}&per_page={per_page}"
    )


def _json_response(payload: dict[str, object], status: int = 200) -> HttpResponse:
    return HttpResponse(
        status=status,
        headers={"content-type": "application/json"},
        body=json.dumps(payload).encode(),
    )


def _discovery_request(
    query: str = "ai infra founder",
    *,
    category: SourceCategory = SourceCategory.DEVELOPER_ACTIVITY,
    max_results: int = 5,
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
                max_results=max_results,
                max_pages=1,
                timeout_seconds=10,
            ),
        ),
    )


def test_adapter_conforms_to_both_ports() -> None:
    adapter = GitHubDeveloperActivitySource(RecordedHttpTransport({}), now=_now)
    assert isinstance(adapter, DiscoveryPort)
    assert isinstance(adapter, AcquisitionPort)


def test_discover_maps_search_results_to_leads() -> None:
    request = _discovery_request()
    url = _search_url("ai infra founder", 5)
    transport = RecordedHttpTransport(
        {
            url: _json_response(
                {
                    "items": [
                        {
                            "login": "octocat",
                            "html_url": "https://github.com/octocat",
                            "score": 1.0,
                        },
                        {
                            "login": "defunkt",
                            "html_url": "https://github.com/defunkt",
                            "score": 0.7,
                        },
                    ]
                }
            )
        }
    )
    adapter = GitHubDeveloperActivitySource(transport, now=_now)

    result = asyncio.run(adapter.discover(request))

    assert result.status is CollectionResultStatus.SUCCEEDED
    assert result.failures == ()
    assert transport.calls == (url,)
    assert [lead.title.value for lead in result.leads] == ["octocat", "defunkt"]
    assert [lead.rank for lead in result.leads] == [1, 2]

    first = result.leads[0]
    assert first.original_url == "https://github.com/octocat"
    assert first.source_category is SourceCategory.DEVELOPER_ACTIVITY
    assert first.discovered_at == FIXED_TIME
    # A provider result is a lead, never primary evidence by itself.
    assert first.provider_summary.state is KnowledgeState.UNKNOWN
    # A search score is retrieval relevance, not founder or trust signal.
    assert first.retrieval_relevance.value == 1.0

    # Free source records zero, known cost; usage is operational metadata only.
    assert result.usage.cost_amount.value == 0.0
    assert result.usage.result_count == 2
    assert result.usage.elapsed_milliseconds >= 0


def test_discover_no_results_is_empty_success_not_failure() -> None:
    request = _discovery_request(query="nobody matches this")
    url = _search_url("nobody matches this", 5)
    adapter = GitHubDeveloperActivitySource(
        RecordedHttpTransport({url: _json_response({"items": []})}), now=_now
    )

    result = asyncio.run(adapter.discover(request))

    assert result.status is CollectionResultStatus.SUCCEEDED
    assert result.leads == ()
    assert result.failures == ()


def test_discover_rate_limited_is_a_failure_with_no_leads() -> None:
    request = _discovery_request(query="rate limited")
    url = _search_url("rate limited", 5)
    adapter = GitHubDeveloperActivitySource(
        RecordedHttpTransport({url: _json_response({}, status=403)}), now=_now
    )

    result = asyncio.run(adapter.discover(request))

    assert result.status is CollectionResultStatus.FAILED
    assert result.leads == ()
    assert result.failures[0].safe_code == "rate_limited"
    assert result.failures[0].retryable is True


def test_discover_skips_retrieval_of_other_categories() -> None:
    request = _discovery_request(category=SourceCategory.RESEARCH)
    transport = RecordedHttpTransport({})
    adapter = GitHubDeveloperActivitySource(transport, now=_now)

    result = asyncio.run(adapter.discover(request))

    # Not this adapter's category: no request is made and no lead is fabricated.
    assert transport.calls == ()
    assert result.leads == ()
    assert result.usage.request_count == 0
    assert result.status is CollectionResultStatus.SUCCEEDED


def _acquisition_request(url: str = "https://github.com/octocat") -> AcquisitionRequest:
    return AcquisitionRequest(
        acquisition_request_id="acquisition-gh-001",
        discovery_lead_id="github-user-octocat",
        original_url=url,
        requested_at=FIXED_TIME,
        classification=DataClassification.PUBLIC,
        allowed_media_types=("application/json",),
        max_bytes=100_000,
        timeout_seconds=10,
    )


def test_acquire_captures_original_record_with_hash() -> None:
    record = {"login": "octocat", "id": 1, "name": "The Octocat"}
    response = _json_response(record)
    adapter = GitHubDeveloperActivitySource(
        RecordedHttpTransport({"https://api.github.com/users/octocat": response}),
        now=_now,
    )

    result = asyncio.run(adapter.acquire(_acquisition_request()))

    assert result.status is AcquisitionStatus.ACQUIRED
    assert result.media_type == "application/json"
    assert result.content == response.body
    assert result.content_sha256 == sha256(response.body).hexdigest()
    assert result.completed_at == FIXED_TIME


def test_acquire_not_found_is_a_failure_without_content() -> None:
    adapter = GitHubDeveloperActivitySource(
        RecordedHttpTransport(
            {"https://api.github.com/users/ghost": _json_response({}, status=404)}
        ),
        now=_now,
    )

    result = asyncio.run(adapter.acquire(_acquisition_request("https://github.com/ghost")))

    assert result.status is AcquisitionStatus.FAILED
    assert result.content is None
    assert result.failure is not None
    assert result.failure.safe_code == "not_found"


def test_acquire_rejects_non_github_url() -> None:
    adapter = GitHubDeveloperActivitySource(RecordedHttpTransport({}), now=_now)

    result = asyncio.run(
        adapter.acquire(_acquisition_request("https://example.invalid/octocat"))
    )

    assert result.status is AcquisitionStatus.FAILED
    assert result.failure is not None
    assert result.failure.safe_code == "unsupported_url"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
