"""Opt-in live smoke tests for the public source-specific adapter palette.

These checks never run in the default suite.  They make the four currently reachable
keyless APIs prove discovery plus acquisition through the provider-neutral contracts.  The
PatentsView check guards against its retired legacy endpoint being misreported as an empty
authoritative result while the USPTO transition requires separately provisioned access.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

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
from founderlookup.ingestion.sources.hackernews import HackerNewsSocialSource
from founderlookup.ingestion.sources.http import UrllibHttpTransport
from founderlookup.ingestion.sources.openalex import OpenAlexResearchSource
from founderlookup.ingestion.sources.patentsview import PatentsViewPatentSource
from founderlookup.ingestion.sources.semanticscholar import SemanticScholarResearchSource

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("FOUNDERLOOKUP_RUN_LIVE_TESTS") != "1",
        reason="set FOUNDERLOOKUP_RUN_LIVE_TESTS=1 to call public source APIs",
    ),
]

AdapterFactory = Callable[[], DiscoveryPort | AcquisitionPort]


def _now() -> datetime:
    return datetime.now(UTC)


def _request(name: str, query: str, category: SourceCategory) -> DiscoveryRequest:
    return DiscoveryRequest(
        request_id=f"live-source:{name}",
        query_plan_id="live-source:plan",
        requested_at=_now(),
        retrieval_requests=(
            BoundedRetrievalRequest(
                retrieval_request_id=f"live-source:retrieval:{name}",
                query=query,
                source_categories=(category,),
                max_results=1,
                max_pages=1,
                timeout_seconds=20,
            ),
        ),
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("name", "factory", "category", "query"),
    (
        (
            "github",
            lambda: GitHubDeveloperActivitySource(UrllibHttpTransport(), now=_now),
            SourceCategory.DEVELOPER_ACTIVITY,
            "torvalds",
        ),
        (
            "hackernews",
            lambda: HackerNewsSocialSource(UrllibHttpTransport(), now=_now),
            SourceCategory.PUBLIC_SOCIAL,
            "postgres",
        ),
        (
            "openalex",
            lambda: OpenAlexResearchSource(UrllibHttpTransport(), now=_now),
            SourceCategory.RESEARCH,
            "Geoffrey Hinton",
        ),
        (
            "semantic-scholar",
            lambda: SemanticScholarResearchSource(UrllibHttpTransport(), now=_now),
            SourceCategory.RESEARCH,
            "Geoffrey Hinton",
        ),
    ),
)
async def test_live_source_specific_discovery_and_acquisition(
    name: str,
    factory: AdapterFactory,
    category: SourceCategory,
    query: str,
) -> None:
    adapter = factory()
    assert isinstance(adapter, DiscoveryPort)
    assert isinstance(adapter, AcquisitionPort)

    request = _request(name, query, category)
    discovery = await adapter.discover(request)
    if (
        discovery.status is CollectionResultStatus.FAILED
        and discovery.failures
        and all(item.retryable for item in discovery.failures)
    ):
        await asyncio.sleep(2)
        discovery = await adapter.discover(request)
    assert discovery.status in {
        CollectionResultStatus.SUCCEEDED,
        CollectionResultStatus.PARTIALLY_SUCCEEDED,
    }
    assert discovery.leads
    lead = discovery.leads[0]
    assert lead.source_category is category

    acquisition = await adapter.acquire(
        AcquisitionRequest(
            acquisition_request_id=f"live-source:acquisition:{name}",
            discovery_lead_id=lead.lead_id,
            original_url=lead.original_url,
            requested_at=_now(),
            classification=DataClassification.PUBLIC,
            allowed_media_types=("application/json",),
            max_bytes=2_000_000,
            timeout_seconds=20,
        )
    )
    assert acquisition.status is AcquisitionStatus.ACQUIRED
    assert acquisition.original_url == lead.original_url
    assert acquisition.media_type == "application/json"
    assert acquisition.content
    assert acquisition.content_sha256


@pytest.mark.anyio
async def test_live_patentsview_never_turns_endpoint_transition_into_source_silence() -> None:
    adapter = PatentsViewPatentSource(UrllibHttpTransport(), now=_now)
    result = await adapter.discover(
        _request("patentsview", "machine learning", SourceCategory.PATENT)
    )

    if result.leads:
        assert result.status in {
            CollectionResultStatus.SUCCEEDED,
            CollectionResultStatus.PARTIALLY_SUCCEEDED,
        }
    else:
        assert result.status is CollectionResultStatus.FAILED
        assert result.failures
        assert result.failures[0].safe_code in {
            "invalid_provider_payload",
            "rate_limited",
            "transport_error",
            "upstream_status",
        }
