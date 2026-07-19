"""Executable-runtime coverage for durable intake and fail-closed OCR composition."""

import asyncio
import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from itertools import count
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from founderlookup.api.settings import APISettings
from founderlookup.application.screening_bridge import DeterministicScreeningBridge
from founderlookup.application.service import FakeVCBrainService
from founderlookup.application.sourcing import MultiAdapterSourcingCoordinator
from founderlookup.demo.bootstrap import seed_local_demo
from founderlookup.domain.common import KnowledgeValue
from founderlookup.domain.lifecycles import OutboundCandidateStatus
from founderlookup.infrastructure.intake_repository import SQLiteIntakeRepository
from founderlookup.infrastructure.persistence import RecordCategory
from founderlookup.ingestion.extraction import (
    ExtractedPdfPage,
    PdfExtractionBlockedError,
    PdfExtractionRequest,
    PdfExtractionResult,
    PdfExtractionUsage,
    PdfPageConfidence,
)
from founderlookup.ingestion.intake import ExtractionAttemptStatus
from founderlookup.ingestion.sources.http import HttpResponse
from founderlookup.runtime import create_runtime_app

NOW = datetime(2026, 7, 19, 8, tzinfo=UTC)
PDF = b"%PDF-1.7\nfictional runtime deck"


class RuntimeFakeExtractor:
    def __init__(self) -> None:
        self.requests: list[PdfExtractionRequest] = []

    async def extract(self, request: PdfExtractionRequest) -> PdfExtractionResult:
        self.requests.append(request)
        return self.result(request)

    @staticmethod
    def result(request: PdfExtractionRequest) -> PdfExtractionResult:
        return PdfExtractionResult(
            extraction_id="pdf-extraction:runtime-fake",
            source_artifact_id=request.source_artifact_id,
            input_sha256=request.input_sha256,
            extractor_version="runtime-fake.v0",
            model_version=KnowledgeValue[str].known("runtime-fake-model.v0"),
            extracted_at=NOW,
            pages=(
                ExtractedPdfPage(
                    page_index=0,
                    locator="page:0",
                    markdown="# Fictional runtime deck",
                    confidence=PdfPageConfidence(
                        average=KnowledgeValue[float].unknown("fake omitted confidence"),
                        minimum=KnowledgeValue[float].unknown("fake omitted confidence"),
                    ),
                ),
            ),
            usage=PdfExtractionUsage(
                pages_processed=KnowledgeValue[int].known(1),
                document_size_bytes=KnowledgeValue[int].known(len(request.content)),
            ),
        )


class BlockingRuntimeFakeExtractor(RuntimeFakeExtractor):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def extract(self, request: PdfExtractionRequest) -> PdfExtractionResult:
        self.requests.append(request)
        self.started.set()
        await self.release.wait()
        return self.result(request)


class RecoveringRuntimeFakeExtractor(RuntimeFakeExtractor):
    async def extract(self, request: PdfExtractionRequest) -> PdfExtractionResult:
        if not self.requests:
            self.requests.append(request)
            raise PdfExtractionBlockedError
        return await super().extract(request)


def _settings(
    data_dir: Path,
    *,
    demo_seed_enabled: bool = False,
    tavily_enabled: bool = False,
    github_enabled: bool = False,
    hackernews_enabled: bool = False,
    openalex_enabled: bool = False,
    semantic_scholar_enabled: bool = False,
    patentsview_enabled: bool = False,
) -> APISettings:
    return APISettings(
        _env_file=None,  # type: ignore[call-arg]  # pydantic-settings runtime override
        data_dir=data_dir,
        investor_api_key=SecretStr("runtime-investor-token"),
        founder_status_pepper=SecretStr("runtime-founder-status-pepper"),
        cors_origins="https://vc.example",
        maximum_deck_bytes=1_000_000,
        mistral_api_key=None,
        demo_seed_enabled=demo_seed_enabled,
        tavily_api_key=(SecretStr("fictional-runtime-tavily-key") if tavily_enabled else None),
        tavily_enabled=tavily_enabled,
        tavily_allowed_domains="example.com",
        github_enabled=github_enabled,
        hackernews_enabled=hackernews_enabled,
        openalex_enabled=openalex_enabled,
        semantic_scholar_enabled=semantic_scholar_enabled,
        patentsview_enabled=patentsview_enabled,
    )


class AlwaysUnavailablePublicTransport:
    """Safe deterministic stand-in proving composition never leaks transport detail."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_bytes: int,
    ) -> HttpResponse:
        del headers, timeout_seconds, max_bytes
        self.calls.append(url)
        return HttpResponse(status=503, headers={}, body=b"private upstream detail")


async def _submit(client: httpx.AsyncClient, *, key: str) -> httpx.Response:
    return await client.post(
        "/api/v1/applications",
        data={"company_name": "Celadon Systems"},
        files={"deck": ("deck.pdf", PDF, "application/pdf")},
        headers={"Idempotency-Key": key},
    )


def test_demo_bootstrap_is_idempotent_per_service_instance() -> None:
    identifiers = count(1)
    bridge = DeterministicScreeningBridge()
    service = FakeVCBrainService(
        clock=lambda: NOW,
        id_factory=lambda: f"demo-id-{next(identifiers):04d}",
        capability_pepper=b"fictional-demo-pepper",
        screening_bridge=bridge,
    )

    first = seed_local_demo(service, screening_bridge=bridge)
    second = seed_local_demo(service, screening_bridge=bridge)

    assert second == first
    assert len(service.thesis_history()) == 1
    assert len(service.list_candidates().items) == 1


def test_runtime_demo_seed_is_disabled_by_default(tmp_path: Path) -> None:
    application = create_runtime_app(settings=_settings(tmp_path), clock=lambda: NOW)
    service: FakeVCBrainService = application.state.vc_brain_service

    assert application.state.demo_bootstrap is None
    assert application.state.runtime_environment == "development"
    assert logging.getLogger("founderlookup").level == logging.INFO
    assert service.thesis_history() == ()
    assert service.list_candidates().items == ()


def _thesis_payload() -> dict[str, object]:
    no_preference: dict[str, object] = {
        "mode": "no_preference",
        "operator": None,
        "values": [],
        "unknown_policy": "preserve_as_unknown",
    }
    return {
        "sector": no_preference,
        "stage": no_preference,
        "geography": no_preference,
        "check_size": no_preference,
        "ownership_target": no_preference,
        "risk_appetite": no_preference,
    }


def _sourcing_payload() -> dict[str, object]:
    return {
        "query": "technical founders building enterprise AI infrastructure",
        "source_categories": ["company_update"],
        "allowed_domains": ["example.com"],
        "max_results": 2,
        "max_pages": 2,
        "max_bytes": 50_000,
        "timeout_seconds": 10,
    }


_PRD_QUERY = (
    "technical founder, Berlin, AI infra, enterprise traction, no prior VC backing, "
    "top-tier accelerator"
)


@pytest.mark.anyio
async def test_runtime_plans_the_compound_prd_query_in_one_protected_interaction(
    tmp_path: Path,
) -> None:
    application = create_runtime_app(
        settings=_settings(tmp_path),
        extractor=RuntimeFakeExtractor(),
        clock=lambda: NOW,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="https://test.example",
    ) as client:
        anonymous = await client.post(
            "/api/v1/query-plans",
            json={"raw_query": _PRD_QUERY},
        )
        planned = await client.post(
            "/api/v1/query-plans",
            json={
                "raw_query": _PRD_QUERY,
                "max_results": 50,
                "retrieval_max_results": 20,
                "retrieval_max_pages": 3,
                "retrieval_timeout_seconds": 30,
            },
            headers={"Authorization": "Bearer runtime-investor-token"},
        )
        inert_sql = await client.post(
            "/api/v1/query-plans",
            json={"raw_query": "technical founder in Berlin; DROP TABLE founders; --"},
            headers={"Authorization": "Bearer runtime-investor-token"},
        )
        unbounded = await client.post(
            "/api/v1/query-plans",
            json={"raw_query": "Berlin", "retrieval_max_pages": 4},
            headers={"Authorization": "Bearer runtime-investor-token"},
        )

    assert anonymous.status_code == 401
    assert planned.status_code == 200
    plan = planned.json()
    assert plan["planning_mode"] == "deterministic"
    assert plan["state"] == "validated"
    assert plan["max_results"] == 50
    assert [criterion["field"] for criterion in plan["criteria"]] == [
        "technical_founder",
        "geography",
        "sector",
        "enterprise_traction",
        "prior_vc_backing",
        "accelerator",
    ]
    prior_vc = next(item for item in plan["criteria"] if item["field"] == "prior_vc_backing")
    assert prior_vc["operator"] == "equals"
    assert prior_vc["operands"] == [False]
    assert [(item["text"], item["reason"]) for item in plan["unresolved_phrases"]] == [
        ("top-tier", "subjective term needs confirmation")
    ]
    assert len(plan["retrieval_requests"]) == 1
    retrieval = plan["retrieval_requests"][0]
    assert retrieval["max_results"] == 20
    assert retrieval["max_pages"] == 3
    assert retrieval["timeout_seconds"] == 30
    assert "top-tier" not in retrieval["query"]
    assert "prior venture capital backing" in retrieval["query"]
    assert inert_sql.status_code == 200
    inert_retrieval = inert_sql.json()["retrieval_requests"][0]["query"].casefold()
    assert "drop" not in inert_retrieval
    assert "table" not in inert_retrieval
    assert any(
        item["text"] == "DROP TABLE founders" for item in inert_sql.json()["unresolved_phrases"]
    )
    assert unbounded.status_code == 422
    assert application.state.deterministic_query_planner is not None


@pytest.mark.anyio
async def test_runtime_tavily_sourcing_persists_original_content_and_partial_failure(
    tmp_path: Path,
) -> None:
    search_snippet = "Tavily search snippet must remain retrieval metadata only."
    original_content = "# Public original source\n\nA source page captured by Extract."
    provider_key = "fictional-runtime-tavily-key"

    def tavily_handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.headers["authorization"] == f"Bearer {provider_key}"
        if request.url.path == "/search":
            assert payload["include_raw_content"] is False
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "First public update",
                            "url": "https://example.com/startup-one",
                            "content": search_snippet,
                            "score": 0.93,
                        },
                        {
                            "title": "Second public update",
                            "url": "https://example.com/startup-two",
                            "content": "A second provider snippet.",
                            "score": 0.82,
                        },
                    ]
                },
            )
        if request.url.path == "/extract":
            original_url = payload["urls"][0]
            if original_url == "https://example.com/startup-two":
                return httpx.Response(
                    503,
                    text=f"private provider detail containing {provider_key}",
                )
            return httpx.Response(
                200,
                json={"results": [{"url": original_url, "raw_content": original_content}]},
            )
        raise AssertionError("unexpected Tavily endpoint")

    public_transport = AlwaysUnavailablePublicTransport()
    async with httpx.AsyncClient(transport=httpx.MockTransport(tavily_handler)) as tavily:
        application = create_runtime_app(
            settings=_settings(tmp_path, tavily_enabled=True, github_enabled=True),
            extractor=RuntimeFakeExtractor(),
            tavily_client=tavily,
            public_source_transport=public_transport,
            clock=lambda: NOW,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url="https://test.example",
            headers={"Authorization": "Bearer runtime-investor-token"},
        ) as client:
            thesis = await client.post("/api/v1/theses", json=_thesis_payload())
            sourcing_payload = _sourcing_payload()
            sourcing_payload["source_categories"] = ["developer_activity"]
            # Runtime policy already limits the generic provider to example.com; leaving the
            # command-neutral allowlist empty lets the authoritative GitHub adapter participate.
            sourcing_payload["allowed_domains"] = []
            accepted = await client.post(
                "/api/v1/sourcing-runs",
                json=sourcing_payload,
            )
            run = await client.get(accepted.headers["Location"])
            candidates_response = await client.get("/api/v1/outbound-candidates")

            candidates = candidates_response.json()["items"]
            artifact_id = candidates[0]["source_artifact_ids"][0]
            artifact_response = await client.get(f"/api/v1/artifacts/{artifact_id}")
            repeated = await client.post(
                "/api/v1/sourcing-runs",
                json=sourcing_payload,
            )
            repeated_run = await client.get(repeated.headers["Location"])
            repeated_candidates = await client.get("/api/v1/outbound-candidates")

    assert thesis.status_code == 201
    assert accepted.status_code == 202
    assert accepted.json()["run"]["status"] == "queued"
    assert run.status_code == 200
    assert run.json()["status"] == "partially_succeeded"
    failure_codes = {item["safe_code"] for item in run.json()["failures"]}
    assert {"upstream_status", "provider_unavailable"} <= failure_codes
    assert provider_key not in run.text
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["company_name"] == "Unresolved public lead from example.com"
    assert candidate["founder_id"]["state"] == "unknown"
    assert candidate["founder_id"]["reason"] == "founder_identity_unresolved"
    assert candidate["status"] == "preliminary_assessment"
    assert candidate["preliminary_assessment"] is not None
    assert candidate["preliminary_assessment"]["coverage"]["evidence_count"] == 0
    assert candidate["outreach_draft"] is None
    assert artifact_response.status_code == 200
    assert artifact_response.text == original_content
    assert search_snippet not in artifact_response.text
    assert repeated.status_code == 202
    assert repeated_run.json()["status"] == "partially_succeeded"
    assert len(repeated_candidates.json()["items"]) == 1
    assert repeated_candidates.json()["items"][0]["source_artifact_ids"] == [artifact_id]
    assert application.state.enabled_sourcing_adapters == (
        "tavily-web-v0",
        "github-developer-activity-v0",
    )
    assert len(public_transport.calls) == 4

    memory = application.state.sqlite_memory
    artifacts = memory.list_records(RecordCategory.SOURCE_ARTIFACT)
    assert len(artifacts) == 1
    assert artifacts[0].payload["origin_locator"] == "https://example.com/startup-one"
    assert memory.list_records(RecordCategory.EVIDENCE) == ()
    telemetry = memory.list_records(
        RecordCategory.COLLECTION_TELEMETRY,
        subject_id=run.json()["run_id"],
    )
    assert len(telemetry) == 5
    telemetry_payloads = [dict(item.payload) for item in telemetry]
    assert search_snippet in json.dumps(telemetry_payloads)
    loop_audit = next(
        item for item in telemetry_payloads if item.get("record_type") == "outbound_search_loop"
    )
    assert loop_audit["stop_reason"] == "partial_failure"
    assert loop_audit["outreach_action"] == "none"


@pytest.mark.anyio
async def test_runtime_disabled_tavily_fails_closed_without_a_network_call(
    tmp_path: Path,
) -> None:
    transport = AlwaysUnavailablePublicTransport()
    application = create_runtime_app(
        settings=_settings(tmp_path),
        extractor=RuntimeFakeExtractor(),
        public_source_transport=transport,
        clock=lambda: NOW,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="https://test.example",
        headers={"Authorization": "Bearer runtime-investor-token"},
    ) as client:
        response = await client.post("/api/v1/sourcing-runs", json=_sourcing_payload())

    assert response.status_code == 503
    assert response.json()["code"] == "live_sourcing_unavailable"
    assert application.state.enabled_sourcing_adapters == ()
    assert transport.calls == []


def test_runtime_composes_every_enabled_authoritative_adapter_without_network(
    tmp_path: Path,
) -> None:
    transport = AlwaysUnavailablePublicTransport()
    application = create_runtime_app(
        settings=_settings(
            tmp_path,
            github_enabled=True,
            hackernews_enabled=True,
            openalex_enabled=True,
            semantic_scholar_enabled=True,
            patentsview_enabled=True,
        ),
        extractor=RuntimeFakeExtractor(),
        public_source_transport=transport,
        clock=lambda: NOW,
    )

    assert isinstance(
        application.state.sourcing_coordinator,
        MultiAdapterSourcingCoordinator,
    )
    assert application.state.enabled_sourcing_adapters == (
        "github-developer-activity-v0",
        "hackernews-social-v0",
        "openalex-research-v0",
        "semanticscholar-research-v0",
        "patentsview-patent-v0",
    )
    assert transport.calls == []
    assert not hasattr(application.state, "tavily_source")


@pytest.mark.anyio
async def test_runtime_demo_seed_populates_http_workspace_without_external_actions(
    tmp_path: Path,
) -> None:
    extractor = RuntimeFakeExtractor()
    application = create_runtime_app(
        settings=_settings(tmp_path, demo_seed_enabled=True),
        extractor=extractor,
        clock=lambda: NOW,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="https://test.example",
        headers={"Authorization": "Bearer runtime-investor-token"},
    ) as client:
        thesis_response, candidates_response, opportunities_response = await asyncio.gather(
            client.get("/api/v1/theses/active"),
            client.get("/api/v1/outbound-candidates?limit=50"),
            client.get("/api/v1/opportunities?limit=50"),
        )
        opportunities = opportunities_response.json()["items"]
        opportunity_detail_response = await client.get(
            f"/api/v1/opportunities/{opportunities[0]['opportunity_id']}"
        )

    assert thesis_response.status_code == 200
    assert thesis_response.json()["created_by"] == "system:fictional-demo-bootstrap"
    assert candidates_response.status_code == 200
    candidates = candidates_response.json()["items"]
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["company_name"] == "Jade Meridian Systems — Fictional Demo"
    assert candidate["status"] == OutboundCandidateStatus.READY_FOR_ACTIVATION.value
    assert candidate["source_artifact_ids"] == [
        "demo:source-artifact:fictional-hackathon-001",
        "demo:source-artifact:fictional-research-001",
    ]
    assert candidate["preliminary_assessment"] is not None
    founder_score = candidate["preliminary_assessment"]["founder_score"]
    assert founder_score["state"] == "known"
    assert founder_score["value"]["score_policy_version"] == "founder-score-rubric.v0"
    assert candidate["preliminary_assessment"]["axes"]["founder"]["rating"] == "strong"
    assert candidate["outreach_draft"] is None
    assert opportunities_response.status_code == 200
    assert len(opportunities) == 1
    assert opportunities[0]["origin"] == "inbound"
    assert opportunities[0]["screening_status"] == "blocked"
    assert opportunities[0]["recommendation"] == "needs_information"
    assert opportunity_detail_response.status_code == 200
    opportunity_detail = opportunity_detail_response.json()
    assert opportunity_detail["latest_recommendation"]["action"] == "needs_information"
    assert opportunity_detail["human_decisions"] == []
    assert len(opportunity_detail["latest_memo"]["sections"]) == 5
    assert extractor.requests == []
    assert tuple((tmp_path / "artifacts").iterdir()) == ()


@pytest.mark.anyio
async def test_runtime_persists_private_intake_and_runs_injected_extraction(
    tmp_path: Path,
) -> None:
    extractor = RuntimeFakeExtractor()
    application = create_runtime_app(
        settings=_settings(tmp_path),
        extractor=extractor,
        clock=lambda: NOW,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="https://test.example",
    ) as client:
        response = await _submit(client, key="runtime-attempt-01")
        artifact_response = await client.get(
            f"/api/v1/artifacts/{response.json()['source_artifact_id']}",
            headers={"Authorization": "Bearer runtime-investor-token"},
        )
        run_response = await client.get(
            response.headers["Location"],
            headers={"Authorization": "Bearer runtime-investor-token"},
        )

    assert response.status_code == 202
    assert artifact_response.status_code == 200
    assert artifact_response.content == PDF
    assert run_response.status_code == 200
    assert run_response.json()["status"] == "succeeded"
    assert run_response.json()["stages"][0]["status"] == "succeeded"
    projection_records = application.state.sqlite_memory.list_records(
        RecordCategory.DECK_EVIDENCE_PROJECTION
    )
    metadata_projection_records = application.state.sqlite_memory.list_records(
        RecordCategory.APPLICATION_METADATA_PROJECTION
    )
    assert len(projection_records) == 1
    assert len(metadata_projection_records) == 1
    assert run_response.json()["accepted_output_ids"] == [
        response.json()["source_artifact_id"],
        "pdf-extraction:runtime-fake",
        metadata_projection_records[0].record_id,
        projection_records[0].record_id,
    ]
    assert len(extractor.requests) == 1

    repository = SQLiteIntakeRepository(tmp_path / "intake.sqlite3")
    stored = repository.get_application(response.json()["application_id"])
    assert stored is not None
    assert stored.artifact_stored is True
    assert stored.extraction.value is not None
    assert stored.extraction.value.pages[0].locator == "page:0"
    assert tuple(attempt.status for attempt in stored.extraction_attempts) == (
        ExtractionAttemptStatus.SUCCEEDED,
    )


@pytest.mark.anyio
async def test_runtime_accepts_intake_but_records_block_when_ocr_is_unconfigured(
    tmp_path: Path,
) -> None:
    application = create_runtime_app(settings=_settings(tmp_path), clock=lambda: NOW)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="https://test.example",
    ) as client:
        response = await _submit(client, key="runtime-attempt-02")
        run_response = await client.get(
            response.headers["Location"],
            headers={"Authorization": "Bearer runtime-investor-token"},
        )

    assert response.status_code == 202
    repository = SQLiteIntakeRepository(tmp_path / "intake.sqlite3")
    stored = repository.get_application(response.json()["application_id"])
    assert stored is not None
    assert stored.extraction.value is None
    assert tuple(attempt.status for attempt in stored.extraction_attempts) == (
        ExtractionAttemptStatus.BLOCKED,
    )
    assert stored.extraction_attempts[0].safe_code == "mistral_ocr_configuration_invalid"
    assert run_response.status_code == 200
    assert run_response.json()["status"] == "partially_succeeded"
    assert run_response.json()["stages"][0]["status"] == "failed"
    assert run_response.json()["failures"][0]["safe_code"] == ("mistral_ocr_configuration_invalid")
    assert run_response.json()["accepted_output_ids"] == [response.json()["source_artifact_id"]]


@pytest.mark.anyio
async def test_runtime_coalesces_concurrent_idempotent_extraction_attempts(
    tmp_path: Path,
) -> None:
    extractor = BlockingRuntimeFakeExtractor()
    application = create_runtime_app(
        settings=_settings(tmp_path),
        extractor=extractor,
        clock=lambda: NOW,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="https://test.example",
    ) as client:
        first = asyncio.create_task(_submit(client, key="runtime-concurrent-attempt"))
        await asyncio.wait_for(extractor.started.wait(), timeout=1)
        second = asyncio.create_task(_submit(client, key="runtime-concurrent-attempt"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        extractor.release.set()
        responses = await asyncio.gather(first, second)

    assert [response.status_code for response in responses] == [202, 202]
    assert responses[0].json()["application_id"] == responses[1].json()["application_id"]
    assert len(extractor.requests) == 1


@pytest.mark.anyio
async def test_later_replay_recovers_after_a_coalesced_blocked_attempt(
    tmp_path: Path,
) -> None:
    extractor = RecoveringRuntimeFakeExtractor()
    application = create_runtime_app(
        settings=_settings(tmp_path),
        extractor=extractor,
        clock=lambda: NOW,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="https://test.example",
    ) as client:
        blocked = await _submit(client, key="runtime-recovery-attempt")
        recovered = await _submit(client, key="runtime-recovery-attempt")
        run_response = await client.get(
            recovered.headers["Location"],
            headers={"Authorization": "Bearer runtime-investor-token"},
        )

    assert blocked.status_code == 202
    assert recovered.status_code == 202
    assert recovered.json()["replayed"] is True
    assert recovered.json()["application_id"] == blocked.json()["application_id"]
    assert len(extractor.requests) == 2
    assert run_response.json()["status"] == "succeeded"

    repository = SQLiteIntakeRepository(tmp_path / "intake.sqlite3")
    stored = repository.get_application(recovered.json()["application_id"])
    assert stored is not None
    assert stored.extraction.value is not None
    assert tuple(attempt.status for attempt in stored.extraction_attempts) == (
        ExtractionAttemptStatus.BLOCKED,
        ExtractionAttemptStatus.SUCCEEDED,
    )
