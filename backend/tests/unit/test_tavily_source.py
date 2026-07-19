"""Tavily adapter tests use MockTransport and never load credentials or use network."""

import asyncio
import json
from datetime import UTC, datetime
from hashlib import sha256

import httpx
from pydantic import SecretStr

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
from founderlookup.ingestion.tavily import TavilyPolicy, TavilySource

NOW = datetime(2026, 7, 19, 10, tzinfo=UTC)
KEY = "fictional-tavily-key-never-use-live"


def _request(
    *,
    query: str = "technical founder Berlin AI infrastructure",
    allowed_domains: tuple[str, ...] = ("example.com",),
    excluded_domains: tuple[str, ...] = (),
    max_results: int = 10,
) -> DiscoveryRequest:
    return DiscoveryRequest(
        request_id="tavily-discovery-request-01",
        query_plan_id="query-plan-01",
        requested_at=NOW,
        retrieval_requests=(
            BoundedRetrievalRequest(
                retrieval_request_id="retrieval-01",
                query=query,
                source_categories=(SourceCategory.COMPANY_UPDATE,),
                allowed_domains=allowed_domains,
                excluded_domains=excluded_domains,
                max_results=max_results,
                max_pages=min(max_results, 5),
                timeout_seconds=30,
            ),
        ),
    )


def _acquisition(url: str = "https://example.com/founder") -> AcquisitionRequest:
    return AcquisitionRequest(
        acquisition_request_id="tavily-acquisition-request-01",
        discovery_lead_id="tavily-lead-01",
        original_url=url,
        requested_at=NOW,
        classification=DataClassification.PUBLIC,
        allowed_media_types=("text/markdown",),
        max_bytes=10_000,
        timeout_seconds=30,
    )


def _source(
    handler: httpx.MockTransport,
    *,
    policy: TavilyPolicy | None = None,
) -> tuple[TavilySource, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=handler)
    return (
        TavilySource(
            api_key=SecretStr(KEY),
            policy=policy
            or TavilyPolicy(
                allowed_domains=("example.com",),
                excluded_domains=("blocked.example.com",),
            ),
            now=lambda: NOW,
            client=client,
        ),
        client,
    )


def test_tavily_adapter_conforms_to_provider_neutral_ports() -> None:
    source, client = _source(httpx.MockTransport(lambda _request: httpx.Response(500)))
    try:
        assert isinstance(source, DiscoveryPort)
        assert isinstance(source, AcquisitionPort)
        assert KEY not in repr(source)
    finally:
        asyncio.run(client.aclose())


def test_search_maps_only_public_allowed_urls_and_keeps_snippet_as_metadata() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        assert request.url == "https://api.tavily.com/search"
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert payload["search_depth"] == "basic"
        assert payload["max_results"] == 10
        assert payload["include_raw_content"] is False
        assert payload["include_domains"] == ["example.com"]
        assert payload["exclude_domains"] == ["blocked.example.com"]
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Public company update",
                        "url": "https://example.com/founder",
                        "content": "Provider snippet is not source evidence.",
                        "score": 0.91,
                    },
                    {
                        "title": "Escaped provider result",
                        "url": "https://outside.example.org/founder",
                        "content": "Must be rejected locally.",
                        "score": 0.8,
                    },
                    {
                        "title": "Private network",
                        "url": "http://127.0.0.1/admin",
                        "content": "Must never be acquired.",
                        "score": 0.7,
                    },
                ]
            },
        )

    source, client = _source(httpx.MockTransport(handler))
    try:
        result = asyncio.run(source.discover(_request()))
    finally:
        asyncio.run(client.aclose())

    assert len(requests) == 1
    assert result.status is CollectionResultStatus.PARTIALLY_SUCCEEDED
    assert len(result.leads) == 1
    lead = result.leads[0]
    assert lead.original_url == "https://example.com/founder"
    assert lead.provider_summary.value == "Provider snippet is not source evidence."
    assert lead.retrieval_relevance.value == 0.91
    assert "evidence" not in type(lead).model_fields
    assert {failure.safe_code for failure in result.failures} == {
        "domain_policy_rejected",
        "unsafe_original_url",
    }


def test_search_enforces_query_and_provider_result_bounds_before_network() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"results": []})

    source, client = _source(
        httpx.MockTransport(handler),
        policy=TavilyPolicy(max_results=3, max_pages=3),
    )
    try:
        too_long = asyncio.run(
            source.discover(_request(query="q" * 401, allowed_domains=(), max_results=10))
        )
        bounded = asyncio.run(
            source.discover(_request(query="bounded", allowed_domains=(), max_results=10))
        )
    finally:
        asyncio.run(client.aclose())

    assert too_long.status is CollectionResultStatus.FAILED
    assert too_long.failures[0].safe_code == "query_too_long"
    assert bounded.status is CollectionResultStatus.SUCCEEDED
    assert calls == 1


def test_provider_failure_redacts_key_and_body() -> None:
    provider_body = f"internal failure mentioning {KEY} and private diagnostics"
    source, client = _source(
        httpx.MockTransport(lambda _request: httpx.Response(500, text=provider_body))
    )
    try:
        result = asyncio.run(source.discover(_request()))
    finally:
        asyncio.run(client.aclose())

    serialized = result.model_dump_json()
    assert result.status is CollectionResultStatus.FAILED
    assert KEY not in serialized
    assert provider_body not in serialized
    assert result.failures[0].safe_code == "provider_unavailable"


def test_extract_captures_original_url_content_and_hash() -> None:
    extracted = "# Original public page\n\nSource-backed content."

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.tavily.com/extract"
        payload = json.loads(request.content)
        assert payload == {
            "urls": ["https://example.com/founder"],
            "extract_depth": "basic",
            "format": "markdown",
            "include_images": False,
        }
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://example.com/founder",
                        "raw_content": extracted,
                    }
                ],
                "failed_results": [],
            },
        )

    source, client = _source(httpx.MockTransport(handler))
    try:
        result = asyncio.run(source.acquire(_acquisition()))
    finally:
        asyncio.run(client.aclose())

    content = extracted.encode()
    assert result.status is AcquisitionStatus.ACQUIRED
    assert result.original_url == "https://example.com/founder"
    assert result.content == content
    assert result.content_sha256 == sha256(content).hexdigest()
    assert result.media_type == "text/markdown; charset=utf-8"
    assert result.source_event_time.state is KnowledgeState.UNKNOWN


def test_extract_rejects_private_auth_walled_and_non_public_targets_without_network() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    source, client = _source(httpx.MockTransport(handler))
    try:
        private = asyncio.run(source.acquire(_acquisition("http://169.254.169.254/latest")))
        credentialed = asyncio.run(
            source.acquire(_acquisition("https://user:pass@example.com/private"))
        )
        non_public_request = _acquisition().model_copy(
            update={"classification": DataClassification.FOUNDER_PRIVATE}
        )
        non_public = asyncio.run(source.acquire(non_public_request))
    finally:
        asyncio.run(client.aclose())

    assert calls == 0
    assert private.status is credentialed.status is non_public.status is AcquisitionStatus.BLOCKED
    assert private.failure is not None
    assert private.failure.safe_code == "unsafe_original_url"
    assert non_public.failure is not None
    assert non_public.failure.safe_code == "classification_blocked"


def test_extract_blocks_content_above_the_source_byte_budget() -> None:
    source, client = _source(
        httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://example.com/founder",
                            "raw_content": "x" * 101,
                        }
                    ]
                },
            )
        ),
        policy=TavilyPolicy(
            max_content_bytes=100,
            allowed_domains=("example.com",),
        ),
    )
    try:
        result = asyncio.run(source.acquire(_acquisition()))
    finally:
        asyncio.run(client.aclose())

    assert result.status is AcquisitionStatus.BLOCKED
    assert result.content is None
    assert result.failure is not None
    assert result.failure.safe_code == "content_budget_exceeded"
