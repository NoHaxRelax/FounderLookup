"""Shared provider-neutral contract tests for deterministic ingestion adapters."""

import asyncio
from datetime import UTC, datetime
from hashlib import sha256

import pytest

from founderlookup.domain.common import KnowledgeValue
from founderlookup.domain.discovery import (
    AcquisitionRequest,
    AcquisitionResult,
    AcquisitionStatus,
    BoundedRetrievalRequest,
    CollectionResultStatus,
    DiscoveryLead,
    DiscoveryRequest,
    DiscoveryResult,
    ProviderUsage,
)
from founderlookup.domain.evidence import DataClassification, SourceCategory
from founderlookup.ingestion.fakes import (
    FakeAcquisitionAdapter,
    FakeDiscoveryAdapter,
    MissingFakeResponseError,
)
from founderlookup.ingestion.ports import AcquisitionPort, DiscoveryPort

FIXED_TIME = datetime(2026, 7, 1, 12, tzinfo=UTC)
FIXED_CONTENT = b"Fictional original-source content."


def _discovery_request(request_id: str = "discovery-request-001") -> DiscoveryRequest:
    return DiscoveryRequest(
        request_id=request_id,
        query_plan_id="query-plan-001",
        requested_at=FIXED_TIME,
        retrieval_requests=(
            BoundedRetrievalRequest(
                retrieval_request_id="retrieval-request-001",
                query="fictional AI infrastructure product launch",
                source_categories=(SourceCategory.PRODUCT_LAUNCH,),
                allowed_domains=("launch.example.invalid",),
                max_results=5,
                max_pages=5,
                timeout_seconds=10,
            ),
        ),
    )


def _discovery_result() -> DiscoveryResult:
    return DiscoveryResult(
        result_id="discovery-result-001",
        request_id="discovery-request-001",
        status=CollectionResultStatus.SUCCEEDED,
        completed_at=FIXED_TIME,
        leads=(
            DiscoveryLead(
                lead_id="lead-001",
                retrieval_request_id="retrieval-request-001",
                original_url="https://launch.example.invalid/fictional-company",
                source_category=SourceCategory.PRODUCT_LAUNCH,
                discovered_at=FIXED_TIME,
                rank=1,
                title=KnowledgeValue[str].known("Fictional Company launch"),
                provider_summary=KnowledgeValue[str].unknown(
                    "Provider summaries are not primary evidence"
                ),
                retrieval_relevance=KnowledgeValue[float].known(0.9),
            ),
        ),
        usage=ProviderUsage(
            adapter_id="deterministic-fake-discovery-v0",
            operation_id="discovery-operation-001",
            request_count=1,
            result_count=1,
            elapsed_milliseconds=4,
            cost_amount=KnowledgeValue[float].known(0.0),
            cost_currency=KnowledgeValue[str].known("USD"),
        ),
    )


def _acquisition_request(
    request_id: str = "acquisition-request-001",
) -> AcquisitionRequest:
    return AcquisitionRequest(
        acquisition_request_id=request_id,
        discovery_lead_id="lead-001",
        original_url="https://launch.example.invalid/fictional-company",
        requested_at=FIXED_TIME,
        classification=DataClassification.PUBLIC,
        allowed_media_types=("text/html",),
        max_bytes=100_000,
        timeout_seconds=10,
    )


def _acquisition_result() -> AcquisitionResult:
    return AcquisitionResult(
        result_id="acquisition-result-001",
        acquisition_request_id="acquisition-request-001",
        original_url="https://launch.example.invalid/fictional-company",
        status=AcquisitionStatus.ACQUIRED,
        completed_at=FIXED_TIME,
        content=FIXED_CONTENT,
        media_type="text/html",
        content_sha256=sha256(FIXED_CONTENT).hexdigest(),
        source_event_time=KnowledgeValue[datetime].unknown(
            "The fictional source has no publication timestamp"
        ),
    )


def test_fake_discovery_replays_a_provider_neutral_result() -> None:
    request = _discovery_request()
    expected = _discovery_result()
    adapter = FakeDiscoveryAdapter({request.request_id: expected})

    assert isinstance(adapter, DiscoveryPort)
    first = asyncio.run(adapter.discover(request))
    second = asyncio.run(adapter.discover(request))

    assert first == expected
    assert second == expected
    assert adapter.requests == (request, request)
    serialized = first.model_dump(mode="json")
    assert serialized["leads"][0]["original_url"] == (
        "https://launch.example.invalid/fictional-company"
    )
    assert set(serialized["usage"]) == {
        "adapter_id",
        "operation_id",
        "request_count",
        "result_count",
        "elapsed_milliseconds",
        "cost_amount",
        "cost_currency",
    }


def test_fake_acquisition_replays_original_content() -> None:
    request = _acquisition_request()
    expected = _acquisition_result()
    adapter = FakeAcquisitionAdapter({request.acquisition_request_id: expected})

    assert isinstance(adapter, AcquisitionPort)
    first = asyncio.run(adapter.acquire(request))
    second = asyncio.run(adapter.acquire(request))

    assert first == expected
    assert second == expected
    assert first.original_url == request.original_url
    assert first.content_sha256 == sha256(FIXED_CONTENT).hexdigest()
    assert adapter.requests == (request, request)


def test_fakes_fail_explicitly_for_unseeded_requests() -> None:
    discovery = FakeDiscoveryAdapter({})
    acquisition = FakeAcquisitionAdapter({})

    with pytest.raises(MissingFakeResponseError, match="missing-discovery"):
        asyncio.run(discovery.discover(_discovery_request("missing-discovery")))

    with pytest.raises(MissingFakeResponseError, match="missing-acquisition"):
        asyncio.run(acquisition.acquire(_acquisition_request("missing-acquisition")))
