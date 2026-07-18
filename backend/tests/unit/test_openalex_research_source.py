"""Deterministic tests for the OpenAlex research source adapter."""

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
from founderlookup.ingestion.sources.openalex import OpenAlexResearchSource

FIXED_TIME = datetime(2026, 7, 1, 12, tzinfo=UTC)
_AUTHOR_URL = "https://openalex.org/A5023888391"


def _now() -> datetime:
    return FIXED_TIME


def _authors_url(query: str, per_page: int) -> str:
    return (
        "https://api.openalex.org/authors"
        f"?search={urllib.parse.quote(query, safe='')}&per-page={per_page}"
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
        request_id="discovery-request-oa-001",
        query_plan_id="query-plan-oa-001",
        requested_at=FIXED_TIME,
        retrieval_requests=(
            BoundedRetrievalRequest(
                retrieval_request_id="retrieval-oa-001",
                query=query,
                source_categories=(category,),
                max_results=max_results,
                max_pages=1,
                timeout_seconds=10,
            ),
        ),
    )


def test_adapter_conforms_to_both_ports() -> None:
    adapter = OpenAlexResearchSource(RecordedHttpTransport({}), now=_now)
    assert isinstance(adapter, DiscoveryPort)
    assert isinstance(adapter, AcquisitionPort)


def test_discover_maps_authors_to_leads() -> None:
    request = _discovery_request()
    url = _authors_url("quantum error correction founder", 5)
    transport = RecordedHttpTransport(
        {
            url: _json_response(
                {
                    "results": [
                        {
                            "id": _AUTHOR_URL,
                            "display_name": "Ada Researcher",
                            "relevance_score": 42.5,
                        },
                        {
                            "id": "https://openalex.org/A999",
                            "display_name": "Bo Scientist",
                            "relevance_score": 10.0,
                        },
                    ]
                }
            )
        }
    )
    adapter = OpenAlexResearchSource(transport, now=_now)

    result = asyncio.run(adapter.discover(request))

    assert result.status is CollectionResultStatus.SUCCEEDED
    assert transport.calls == (url,)
    assert [lead.title.value for lead in result.leads] == ["Ada Researcher", "Bo Scientist"]
    assert [lead.rank for lead in result.leads] == [1, 2]

    first = result.leads[0]
    assert first.original_url == _AUTHOR_URL
    assert first.source_category is SourceCategory.RESEARCH
    assert first.discovered_at == FIXED_TIME
    assert first.lead_id == "openalex-author-A5023888391"
    assert first.provider_summary.state is KnowledgeState.UNKNOWN
    assert first.retrieval_relevance.value == 42.5
    assert result.usage.cost_amount.value == 0.0
    assert result.usage.result_count == 2


def test_discover_no_results_is_empty_success() -> None:
    request = _discovery_request(query="no such scholar")
    url = _authors_url("no such scholar", 5)
    adapter = OpenAlexResearchSource(
        RecordedHttpTransport({url: _json_response({"results": []})}), now=_now
    )

    result = asyncio.run(adapter.discover(request))

    assert result.status is CollectionResultStatus.SUCCEEDED
    assert result.leads == ()
    assert result.failures == ()


def test_discover_rate_limited_is_failure() -> None:
    request = _discovery_request(query="rate limited")
    url = _authors_url("rate limited", 5)
    adapter = OpenAlexResearchSource(
        RecordedHttpTransport({url: _json_response({}, status=429)}), now=_now
    )

    result = asyncio.run(adapter.discover(request))

    assert result.status is CollectionResultStatus.FAILED
    assert result.leads == ()
    assert result.failures[0].safe_code == "rate_limited"


def test_discover_skips_other_categories() -> None:
    request = _discovery_request(category=SourceCategory.DEVELOPER_ACTIVITY)
    transport = RecordedHttpTransport({})
    adapter = OpenAlexResearchSource(transport, now=_now)

    result = asyncio.run(adapter.discover(request))

    assert transport.calls == ()
    assert result.leads == ()
    assert result.usage.request_count == 0


def _acquisition_request(url: str = _AUTHOR_URL) -> AcquisitionRequest:
    return AcquisitionRequest(
        acquisition_request_id="acquisition-oa-001",
        discovery_lead_id="openalex-author-A5023888391",
        original_url=url,
        requested_at=FIXED_TIME,
        classification=DataClassification.PUBLIC,
        allowed_media_types=("application/json",),
        max_bytes=200_000,
        timeout_seconds=10,
    )


def test_acquire_captures_author_record_with_hash() -> None:
    record = {"id": _AUTHOR_URL, "display_name": "Ada Researcher", "works_count": 12}
    response = _json_response(record)
    adapter = OpenAlexResearchSource(
        RecordedHttpTransport({"https://api.openalex.org/authors/A5023888391": response}),
        now=_now,
    )

    result = asyncio.run(adapter.acquire(_acquisition_request()))

    assert result.status is AcquisitionStatus.ACQUIRED
    assert result.media_type == "application/json"
    assert result.content_sha256 == sha256(response.body).hexdigest()


def test_acquire_not_found_is_failure() -> None:
    adapter = OpenAlexResearchSource(
        RecordedHttpTransport(
            {"https://api.openalex.org/authors/A404": _json_response({}, status=404)}
        ),
        now=_now,
    )

    result = asyncio.run(
        adapter.acquire(_acquisition_request("https://openalex.org/A404"))
    )

    assert result.status is AcquisitionStatus.FAILED
    assert result.failure is not None
    assert result.failure.safe_code == "not_found"


def test_acquire_rejects_non_openalex_url() -> None:
    adapter = OpenAlexResearchSource(RecordedHttpTransport({}), now=_now)

    result = asyncio.run(
        adapter.acquire(_acquisition_request("https://github.com/octocat"))
    )

    assert result.status is AcquisitionStatus.FAILED
    assert result.failure is not None
    assert result.failure.safe_code == "unsupported_url"
