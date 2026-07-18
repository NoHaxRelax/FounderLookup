"""Deterministic tests for the Semantic Scholar research source adapter."""

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
from founderlookup.ingestion.sources.http import HttpResponse, RecordedHttpTransport
from founderlookup.ingestion.sources.semanticscholar import SemanticScholarResearchSource

FIXED_TIME = datetime(2026, 7, 1, 12, tzinfo=UTC)
_AUTHOR_URL = "https://www.semanticscholar.org/author/1741101"


def _now() -> datetime:
    return FIXED_TIME


def _search_url(query: str, limit: int) -> str:
    return (
        "https://api.semanticscholar.org/graph/v1/author/search"
        f"?query={urllib.parse.quote(query, safe='')}&limit={limit}"
        "&fields=name,paperCount,citationCount"
    )


def _author_url(author_id: str) -> str:
    return (
        f"https://api.semanticscholar.org/graph/v1/author/{author_id}"
        "?fields=name,paperCount,citationCount,hIndex"
    )


def _json_response(payload: dict[str, object], status: int = 200) -> HttpResponse:
    return HttpResponse(
        status=status,
        headers={"content-type": "application/json"},
        body=json.dumps(payload).encode(),
    )


def _discovery_request(
    query: str = "quantum error correction founder",
    *,
    category: SourceCategory = SourceCategory.RESEARCH,
    max_results: int = 5,
) -> DiscoveryRequest:
    return DiscoveryRequest(
        request_id="discovery-request-s2-001",
        query_plan_id="query-plan-s2-001",
        requested_at=FIXED_TIME,
        retrieval_requests=(
            BoundedRetrievalRequest(
                retrieval_request_id="retrieval-s2-001",
                query=query,
                source_categories=(category,),
                max_results=max_results,
                max_pages=1,
                timeout_seconds=10,
            ),
        ),
    )


def test_adapter_conforms_to_both_ports() -> None:
    adapter = SemanticScholarResearchSource(RecordedHttpTransport({}), now=_now)
    assert isinstance(adapter, DiscoveryPort)
    assert isinstance(adapter, AcquisitionPort)


def test_discover_maps_authors_to_leads() -> None:
    request = _discovery_request()
    url = _search_url("quantum error correction founder", 5)
    transport = RecordedHttpTransport(
        {
            url: _json_response(
                {
                    "data": [
                        {
                            "authorId": "1741101",
                            "name": "Ada Researcher",
                            "paperCount": 42,
                            "citationCount": 999,
                        },
                        {
                            "authorId": "2004",
                            "name": "Bo Scientist",
                            "paperCount": 3,
                            "citationCount": 12,
                        },
                    ]
                }
            )
        }
    )
    adapter = SemanticScholarResearchSource(transport, now=_now)

    result = asyncio.run(adapter.discover(request))

    assert result.status is CollectionResultStatus.SUCCEEDED
    assert transport.calls == (url,)
    assert [lead.title.value for lead in result.leads] == ["Ada Researcher", "Bo Scientist"]
    assert [lead.rank for lead in result.leads] == [1, 2]

    first = result.leads[0]
    assert first.original_url == _AUTHOR_URL
    assert first.source_category is SourceCategory.RESEARCH
    assert first.discovered_at == FIXED_TIME
    assert first.lead_id == "semanticscholar-author-1741101"
    assert first.provider_summary.state is KnowledgeState.UNKNOWN
    assert first.retrieval_relevance.state is KnowledgeState.UNKNOWN
    assert result.usage.cost_amount.value == 0.0
    assert result.usage.result_count == 2


def test_discover_no_results_is_empty_success() -> None:
    request = _discovery_request(query="no such scholar")
    url = _search_url("no such scholar", 5)
    adapter = SemanticScholarResearchSource(
        RecordedHttpTransport({url: _json_response({"data": []})}), now=_now
    )

    result = asyncio.run(adapter.discover(request))

    assert result.status is CollectionResultStatus.SUCCEEDED
    assert result.leads == ()
    assert result.failures == ()


def test_discover_rate_limited_is_failure() -> None:
    request = _discovery_request(query="rate limited")
    url = _search_url("rate limited", 5)
    adapter = SemanticScholarResearchSource(
        RecordedHttpTransport({url: _json_response({}, status=429)}), now=_now
    )

    result = asyncio.run(adapter.discover(request))

    assert result.status is CollectionResultStatus.FAILED
    assert result.leads == ()
    assert result.failures[0].safe_code == "rate_limited"


def test_discover_skips_other_categories() -> None:
    request = _discovery_request(category=SourceCategory.DEVELOPER_ACTIVITY)
    transport = RecordedHttpTransport({})
    adapter = SemanticScholarResearchSource(transport, now=_now)

    result = asyncio.run(adapter.discover(request))

    assert transport.calls == ()
    assert result.leads == ()
    assert result.usage.request_count == 0


def _acquisition_request(url: str = _AUTHOR_URL) -> AcquisitionRequest:
    return AcquisitionRequest(
        acquisition_request_id="acquisition-s2-001",
        discovery_lead_id="semanticscholar-author-1741101",
        original_url=url,
        requested_at=FIXED_TIME,
        classification=DataClassification.PUBLIC,
        allowed_media_types=("application/json",),
        max_bytes=200_000,
        timeout_seconds=10,
    )


def test_acquire_captures_author_record_with_hash() -> None:
    record = {"authorId": "1741101", "name": "Ada Researcher", "paperCount": 42, "hIndex": 20}
    response = _json_response(record)
    adapter = SemanticScholarResearchSource(
        RecordedHttpTransport({_author_url("1741101"): response}),
        now=_now,
    )

    result = asyncio.run(adapter.acquire(_acquisition_request()))

    assert result.status is AcquisitionStatus.ACQUIRED
    assert result.media_type == "application/json"
    assert result.content_sha256 == sha256(response.body).hexdigest()


def test_acquire_not_found_is_failure() -> None:
    adapter = SemanticScholarResearchSource(
        RecordedHttpTransport({_author_url("404"): _json_response({}, status=404)}),
        now=_now,
    )

    result = asyncio.run(
        adapter.acquire(_acquisition_request("https://www.semanticscholar.org/author/404"))
    )

    assert result.status is AcquisitionStatus.FAILED
    assert result.failure is not None
    assert result.failure.safe_code == "not_found"


def test_acquire_rejects_non_semanticscholar_url() -> None:
    adapter = SemanticScholarResearchSource(RecordedHttpTransport({}), now=_now)

    result = asyncio.run(
        adapter.acquire(_acquisition_request("https://github.com/octocat"))
    )

    assert result.status is AcquisitionStatus.FAILED
    assert result.failure is not None
    assert result.failure.safe_code == "unsupported_url"
