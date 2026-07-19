"""Deterministic tests for the PatentsView patent source adapter."""

import asyncio
import json
import urllib.parse
from collections.abc import Mapping
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
from founderlookup.ingestion.sources.patentsview import PatentsViewPatentSource

FIXED_TIME = datetime(2026, 7, 1, 12, tzinfo=UTC)
_PATENT_URL = "https://patents.google.com/patent/10000000"


def _now() -> datetime:
    return FIXED_TIME


def _patents_query_url(criteria: dict[str, object]) -> str:
    encoded = json.dumps(criteria, separators=(",", ":"))
    return f"https://api.patentsview.org/patents/query?q={urllib.parse.quote(encoded, safe='')}"


def _json_response(payload: Mapping[str, object], status: int = 200) -> HttpResponse:
    return HttpResponse(
        status=status,
        headers={"content-type": "application/json"},
        body=json.dumps(payload).encode(),
    )


def _discovery_request(
    query: str = "quantum error correction gate",
    *,
    category: SourceCategory = SourceCategory.PATENT,
    max_results: int = 5,
) -> DiscoveryRequest:
    return DiscoveryRequest(
        request_id="discovery-request-pv-001",
        query_plan_id="query-plan-pv-001",
        requested_at=FIXED_TIME,
        retrieval_requests=(
            BoundedRetrievalRequest(
                retrieval_request_id="retrieval-pv-001",
                query=query,
                source_categories=(category,),
                max_results=max_results,
                max_pages=1,
                timeout_seconds=10,
            ),
        ),
    )


def test_adapter_conforms_to_both_ports() -> None:
    adapter = PatentsViewPatentSource(RecordedHttpTransport({}), now=_now)
    assert isinstance(adapter, DiscoveryPort)
    assert isinstance(adapter, AcquisitionPort)


def test_discover_maps_patents_to_leads() -> None:
    request = _discovery_request()
    url = _patents_query_url({"_text_any": {"patent_title": "quantum error correction gate"}})
    transport = RecordedHttpTransport(
        {
            url: _json_response(
                {
                    "patents": [
                        {"patent_id": "10000000", "patent_title": "Robust Quantum Gate"},
                        {"patent_id": "9999999", "patent_title": "Adaptive Cooling System"},
                    ]
                }
            )
        }
    )
    adapter = PatentsViewPatentSource(transport, now=_now)

    result = asyncio.run(adapter.discover(request))

    assert result.status is CollectionResultStatus.SUCCEEDED
    assert transport.calls == (url,)
    assert [lead.title.value for lead in result.leads] == [
        "Robust Quantum Gate",
        "Adaptive Cooling System",
    ]
    assert [lead.rank for lead in result.leads] == [1, 2]

    first = result.leads[0]
    assert first.original_url == _PATENT_URL
    assert first.source_category is SourceCategory.PATENT
    assert first.discovered_at == FIXED_TIME
    assert first.lead_id == "patentsview-patent-10000000"
    assert first.provider_summary.state is KnowledgeState.UNKNOWN
    assert first.retrieval_relevance.state is KnowledgeState.UNKNOWN
    assert result.usage.cost_amount.value == 0.0
    assert result.usage.result_count == 2


def test_discover_no_results_is_empty_success() -> None:
    request = _discovery_request(query="no such patent")
    url = _patents_query_url({"_text_any": {"patent_title": "no such patent"}})
    adapter = PatentsViewPatentSource(
        RecordedHttpTransport({url: _json_response({"patents": []})}), now=_now
    )

    result = asyncio.run(adapter.discover(request))

    assert result.status is CollectionResultStatus.SUCCEEDED
    assert result.leads == ()
    assert result.failures == ()


def test_discover_rejects_a_successful_non_patent_payload() -> None:
    """A retired endpoint redirect must not masquerade as an authoritative no-result."""

    request = _discovery_request(query="redirected endpoint")
    url = _patents_query_url({"_text_any": {"patent_title": "redirected endpoint"}})
    adapter = PatentsViewPatentSource(
        RecordedHttpTransport(
            {
                url: HttpResponse(
                    status=200,
                    headers={"content-type": "text/html"},
                    body=b"<html><title>API transition</title></html>",
                )
            }
        ),
        now=_now,
    )

    result = asyncio.run(adapter.discover(request))

    assert result.status is CollectionResultStatus.FAILED
    assert result.leads == ()
    assert result.failures[0].safe_code == "invalid_provider_payload"


def test_discover_rate_limited_is_failure() -> None:
    request = _discovery_request(query="rate limited")
    url = _patents_query_url({"_text_any": {"patent_title": "rate limited"}})
    adapter = PatentsViewPatentSource(
        RecordedHttpTransport({url: _json_response({}, status=429)}), now=_now
    )

    result = asyncio.run(adapter.discover(request))

    assert result.status is CollectionResultStatus.FAILED
    assert result.leads == ()
    assert result.failures[0].safe_code == "rate_limited"


def test_discover_skips_other_categories() -> None:
    request = _discovery_request(category=SourceCategory.DEVELOPER_ACTIVITY)
    transport = RecordedHttpTransport({})
    adapter = PatentsViewPatentSource(transport, now=_now)

    result = asyncio.run(adapter.discover(request))

    assert transport.calls == ()
    assert result.leads == ()
    assert result.usage.request_count == 0


def _acquisition_request(url: str = _PATENT_URL) -> AcquisitionRequest:
    return AcquisitionRequest(
        acquisition_request_id="acquisition-pv-001",
        discovery_lead_id="patentsview-patent-10000000",
        original_url=url,
        requested_at=FIXED_TIME,
        classification=DataClassification.PUBLIC,
        allowed_media_types=("application/json",),
        max_bytes=200_000,
        timeout_seconds=10,
    )


def test_acquire_captures_patent_record_with_hash() -> None:
    record = {"patents": [{"patent_id": "10000000", "patent_title": "Robust Quantum Gate"}]}
    response = _json_response(record)
    url = _patents_query_url({"patent_id": "10000000"})
    adapter = PatentsViewPatentSource(
        RecordedHttpTransport({url: response}),
        now=_now,
    )

    result = asyncio.run(adapter.acquire(_acquisition_request()))

    assert result.status is AcquisitionStatus.ACQUIRED
    assert result.media_type == "application/json"
    assert result.content_sha256 == sha256(response.body).hexdigest()


def test_acquire_rejects_a_successful_non_patent_payload() -> None:
    url = _patents_query_url({"patent_id": "10000000"})
    adapter = PatentsViewPatentSource(
        RecordedHttpTransport(
            {
                url: HttpResponse(
                    status=200,
                    headers={"content-type": "text/html"},
                    body=b"<html><title>API transition</title></html>",
                )
            }
        ),
        now=_now,
    )

    result = asyncio.run(adapter.acquire(_acquisition_request()))

    assert result.status is AcquisitionStatus.FAILED
    assert result.failure is not None
    assert result.failure.safe_code == "invalid_provider_payload"


def test_acquire_not_found_is_failure() -> None:
    url = _patents_query_url({"patent_id": "404"})
    adapter = PatentsViewPatentSource(
        RecordedHttpTransport({url: _json_response({}, status=404)}),
        now=_now,
    )

    result = asyncio.run(
        adapter.acquire(_acquisition_request("https://patents.google.com/patent/404"))
    )

    assert result.status is AcquisitionStatus.FAILED
    assert result.failure is not None
    assert result.failure.safe_code == "not_found"


def test_acquire_rejects_non_patents_url() -> None:
    adapter = PatentsViewPatentSource(RecordedHttpTransport({}), now=_now)

    result = asyncio.run(adapter.acquire(_acquisition_request("https://github.com/octocat")))

    assert result.status is AcquisitionStatus.FAILED
    assert result.failure is not None
    assert result.failure.safe_code == "unsupported_url"
