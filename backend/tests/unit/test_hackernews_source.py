"""Deterministic tests for the Hacker News public-social source adapter."""

import asyncio
import json
import urllib.parse
from datetime import UTC, datetime
from hashlib import sha256

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
from founderlookup.ingestion.sources.hackernews import HackerNewsSocialSource
from founderlookup.ingestion.sources.http import HttpResponse, RecordedHttpTransport

FIXED_TIME = datetime(2026, 7, 1, 12, tzinfo=UTC)
_USER_URL = "https://news.ycombinator.com/user?id=pg"


def _now() -> datetime:
    return FIXED_TIME


def _search_url(query: str, hits_per_page: int) -> str:
    return (
        "https://hn.algolia.com/api/v1/search"
        f"?query={urllib.parse.quote(query, safe='')}&tags=story&hitsPerPage={hits_per_page}"
    )


def _json_response(payload: dict[str, object], status: int = 200) -> HttpResponse:
    return HttpResponse(
        status=status,
        headers={"content-type": "application/json"},
        body=json.dumps(payload).encode(),
    )


def _discovery_request(
    query: str = "yc founder launch",
    *,
    category: SourceCategory = SourceCategory.PUBLIC_SOCIAL,
    max_results: int = 5,
) -> DiscoveryRequest:
    return DiscoveryRequest(
        request_id="discovery-request-hn-001",
        query_plan_id="query-plan-hn-001",
        requested_at=FIXED_TIME,
        retrieval_requests=(
            BoundedRetrievalRequest(
                retrieval_request_id="retrieval-hn-001",
                query=query,
                source_categories=(category,),
                max_results=max_results,
                max_pages=1,
                timeout_seconds=10,
            ),
        ),
    )


def test_adapter_conforms_to_both_ports() -> None:
    adapter = HackerNewsSocialSource(RecordedHttpTransport({}), now=_now)
    assert isinstance(adapter, DiscoveryPort)
    assert isinstance(adapter, AcquisitionPort)


def test_discover_maps_stories_to_leads() -> None:
    request = _discovery_request()
    url = _search_url("yc founder launch", 5)
    transport = RecordedHttpTransport(
        {
            url: _json_response(
                {
                    "hits": [
                        {
                            "objectID": "8863",
                            "author": "pg",
                            "title": "My YC app: Dropbox",
                            "url": "https://www.getdropbox.com/u/2/screencast.html",
                            "points": 104,
                        },
                        {
                            "objectID": "121003",
                            "author": "tel",
                            "title": "Ask HN: The Arc Effect",
                            "points": 25,
                        },
                    ]
                }
            )
        }
    )
    adapter = HackerNewsSocialSource(transport, now=_now)

    result = asyncio.run(adapter.discover(request))

    assert result.status is CollectionResultStatus.SUCCEEDED
    assert transport.calls == (url,)
    assert [lead.title.value for lead in result.leads] == ["pg", "tel"]
    assert [lead.rank for lead in result.leads] == [1, 2]

    first = result.leads[0]
    assert first.original_url == "https://news.ycombinator.com/user?id=pg"
    assert first.source_category is SourceCategory.PUBLIC_SOCIAL
    assert first.discovered_at == FIXED_TIME
    assert first.lead_id == "hackernews-story-8863"
    assert first.provider_summary.state is KnowledgeState.UNKNOWN
    assert first.retrieval_relevance.value == 104.0

    # Story search is only a discovery signal; acquisition targets the explicit
    # public author profile and never treats the linked story as founder identity.
    assert result.leads[1].original_url == "https://news.ycombinator.com/user?id=tel"

    assert result.usage.cost_amount.value == 0.0
    assert result.usage.result_count == 2


def test_discover_no_results_is_empty_success() -> None:
    request = _discovery_request(query="no such story")
    url = _search_url("no such story", 5)
    adapter = HackerNewsSocialSource(
        RecordedHttpTransport({url: _json_response({"hits": []})}), now=_now
    )

    result = asyncio.run(adapter.discover(request))

    assert result.status is CollectionResultStatus.SUCCEEDED
    assert result.leads == ()
    assert result.failures == ()


def test_discover_rate_limited_is_failure() -> None:
    request = _discovery_request(query="rate limited")
    url = _search_url("rate limited", 5)
    adapter = HackerNewsSocialSource(
        RecordedHttpTransport({url: _json_response({}, status=429)}), now=_now
    )

    result = asyncio.run(adapter.discover(request))

    assert result.status is CollectionResultStatus.FAILED
    assert result.leads == ()
    assert result.failures[0].safe_code == "rate_limited"


def test_discover_skips_other_categories() -> None:
    request = _discovery_request(category=SourceCategory.RESEARCH)
    transport = RecordedHttpTransport({})
    adapter = HackerNewsSocialSource(transport, now=_now)

    result = asyncio.run(adapter.discover(request))

    assert transport.calls == ()
    assert result.leads == ()
    assert result.usage.request_count == 0


def _acquisition_request(url: str = _USER_URL) -> AcquisitionRequest:
    return AcquisitionRequest(
        acquisition_request_id="acquisition-hn-001",
        discovery_lead_id="hackernews-story-8863",
        original_url=url,
        requested_at=FIXED_TIME,
        classification=DataClassification.PUBLIC,
        allowed_media_types=("application/json",),
        max_bytes=200_000,
        timeout_seconds=10,
    )


def test_acquire_captures_user_record_with_hash() -> None:
    record = {"username": "pg", "karma": 155000, "about": "Founder of YC"}
    response = _json_response(record)
    adapter = HackerNewsSocialSource(
        RecordedHttpTransport({"https://hn.algolia.com/api/v1/users/pg": response}),
        now=_now,
    )

    result = asyncio.run(adapter.acquire(_acquisition_request()))

    assert result.status is AcquisitionStatus.ACQUIRED
    assert result.media_type == "application/json"
    assert result.content == response.body
    assert result.content_sha256 == sha256(response.body).hexdigest()


def test_acquire_not_found_is_failure() -> None:
    adapter = HackerNewsSocialSource(
        RecordedHttpTransport(
            {"https://hn.algolia.com/api/v1/users/ghost": _json_response({}, status=404)}
        ),
        now=_now,
    )

    result = asyncio.run(
        adapter.acquire(_acquisition_request("https://news.ycombinator.com/user?id=ghost"))
    )

    assert result.status is AcquisitionStatus.FAILED
    assert result.failure is not None
    assert result.failure.safe_code == "not_found"


def test_acquire_rejects_non_hackernews_url() -> None:
    adapter = HackerNewsSocialSource(RecordedHttpTransport({}), now=_now)

    result = asyncio.run(
        adapter.acquire(_acquisition_request("https://github.com/octocat"))
    )

    assert result.status is AcquisitionStatus.FAILED
    assert result.failure is not None
    assert result.failure.safe_code == "unsupported_url"
