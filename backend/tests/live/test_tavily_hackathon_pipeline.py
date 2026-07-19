"""Opt-in live Tavily Search/Extract acceptance for a public showcase and deck.

This module never auto-runs. To exercise it deliberately, provide all of:

* ``FOUNDERLOOKUP_RUN_LIVE_TESTS=1``
* ``TAVILY_API_KEY``, ``OPENAI_API_KEY``, and ``MISTRAL_API_KEY``
* ``FOUNDERLOOKUP_LIVE_HACKATHON_URL`` pointing to an approved public showcase
  that explicitly publishes an event, project, participant display name, demo, and
  exactly one directly accessible public pitch-deck link. A repository is asserted only
  when the selected source actually publishes one.

The source URL remains operator-selected because terms/robots approval is source-specific.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from pydantic import SecretStr

from founderlookup.application.models import (
    ThesisCriterion,
    ThesisCriterionMode,
    ThesisDraft,
)
from founderlookup.application.screening_bridge import DeterministicScreeningBridge
from founderlookup.application.service import FakeVCBrainService
from founderlookup.application.sourcing import (
    BoundedPublicPdfAcquisition,
    BoundedSourcingCommand,
    MultiAdapterSourcingCoordinator,
    PublicDeckOcrRecord,
    PublicPdfAcquisitionPolicy,
    PublicSourceCollectionPolicy,
    SourceAdapterBinding,
)
from founderlookup.domain.common import KnowledgeState, KnowledgeValue
from founderlookup.domain.evidence import SourceArtifactKind, SourceCategory
from founderlookup.domain.lifecycles import PipelineRunStatus
from founderlookup.domain.query import UnknownValuePolicy
from founderlookup.infrastructure.artifacts import PrivateArtifactStore
from founderlookup.infrastructure.persistence import RecordCategory, SQLiteMemory
from founderlookup.ingestion.hackathons import (
    HackathonShowcaseProjection,
    IdentityReviewState,
    PublicHackathonDeckRelationship,
)
from founderlookup.ingestion.mistral_ocr import MistralOcrExtractor, MistralOcrSettings
from founderlookup.ingestion.openai_structured import (
    OpenAIStructuredPageExtractor,
    OpenAIStructuredPolicy,
)
from founderlookup.ingestion.tavily import TavilyPolicy, TavilySource

pytestmark = pytest.mark.skipif(
    os.getenv("FOUNDERLOOKUP_RUN_LIVE_TESTS") != "1",
    reason="live provider tests require FOUNDERLOOKUP_RUN_LIVE_TESTS=1",
)

APPROVED_DECK_EDIT = (
    "https://docs.google.com/presentation/d/"
    "1tsx9EmV3Mx0U4Hcv9Hhew0FdMP4ybgdOQPDxFbFZ_DA/edit?usp=sharing"
)
APPROVED_DECK_EXPORT = (
    "https://docs.google.com/presentation/d/1tsx9EmV3Mx0U4Hcv9Hhew0FdMP4ybgdOQPDxFbFZ_DA/export/pdf"
)


def _no_preference() -> ThesisCriterion:
    return ThesisCriterion(
        mode=ThesisCriterionMode.NO_PREFERENCE,
        unknown_policy=UnknownValuePolicy.MANUAL_REVIEW,
    )


def _thesis() -> ThesisDraft:
    criterion = _no_preference()
    return ThesisDraft(
        sector=criterion,
        stage=criterion,
        geography=criterion,
        check_size=criterion,
        ownership_target=criterion,
        risk_appetite=criterion,
    )


def _required_environment() -> tuple[str, str, str, str, str]:
    tavily_key = os.getenv("TAVILY_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    mistral_key = os.getenv("MISTRAL_API_KEY", "").strip()
    showcase_url = os.getenv("FOUNDERLOOKUP_LIVE_HACKATHON_URL", "").strip()
    if not tavily_key:
        pytest.skip("TAVILY_API_KEY is required for the opt-in live test")
    if not openai_key:
        pytest.skip("OPENAI_API_KEY is required for the opt-in live test")
    if not mistral_key:
        pytest.skip("MISTRAL_API_KEY is required for the opt-in live test")
    if not showcase_url:
        pytest.skip("FOUNDERLOOKUP_LIVE_HACKATHON_URL is required so source terms are explicit")
    host = (urlsplit(showcase_url).hostname or "").casefold()
    if not host:
        pytest.fail("FOUNDERLOOKUP_LIVE_HACKATHON_URL must be an absolute public URL")
    return tavily_key, openai_key, mistral_key, showcase_url, host


@pytest.mark.anyio
async def test_live_tavily_showcase_to_separate_deck_and_candidate(tmp_path: Path) -> None:
    tavily_key, openai_key, mistral_key, showcase_url, host = _required_environment()

    def now() -> datetime:
        return datetime.now(UTC)

    approved_domains = (host, "docs.google.com", "googleusercontent.com")
    tavily = TavilySource(
        api_key=SecretStr(tavily_key),
        policy=TavilyPolicy(
            max_queries=1,
            max_results=2,
            max_pages=2,
            max_content_bytes=500_000,
            max_response_bytes=2_000_000,
            timeout_seconds=20,
            allowed_domains=approved_domains,
        ),
        now=now,
    )
    bridge = DeterministicScreeningBridge()
    service_ids = count(1)
    coordinator_ids = count(1)
    service = FakeVCBrainService(
        clock=now,
        id_factory=lambda: f"live-service:{next(service_ids):04d}",
        capability_pepper=b"live-tavily-test-pepper" * 2,
        screening_bridge=bridge,
    )
    service.create_thesis(_thesis(), actor_id="live-test-investor")
    memory = SQLiteMemory(tmp_path / "live-memory.sqlite3")
    artifact_store = PrivateArtifactStore(
        (tmp_path / "live-artifacts").absolute(),
        authorize_read=lambda _principal, _artifact: True,
    )
    structured_extractor = OpenAIStructuredPageExtractor(
        api_key=SecretStr(openai_key),
        policy=OpenAIStructuredPolicy(
            model=os.getenv("FOUNDERLOOKUP_OPENAI_MODEL", "gpt-5.6-luna"),
            max_output_tokens=2_000,
            timeout_seconds=60,
        ),
        now=now,
    )
    query = os.getenv(
        "FOUNDERLOOKUP_LIVE_HACKATHON_QUERY",
        f"site:{host}/software/speechium-by-wako-ai "
        f'Speechium WakoAI CalHacks pitch deck "{showcase_url}"',
    )
    command = BoundedSourcingCommand(
        query=query,
        source_categories=(SourceCategory.HACKATHON,),
        allowed_domains=approved_domains,
        max_results=2,
        max_pages=2,
        max_bytes=5_000_000,
        timeout_seconds=60,
    )

    async with (
        httpx.AsyncClient(trust_env=False, follow_redirects=False) as public_pdf_client,
        httpx.AsyncClient(trust_env=False, follow_redirects=False) as mistral_client,
    ):
        public_pdf_acquisition = BoundedPublicPdfAcquisition(
            policy=PublicPdfAcquisitionPolicy(
                allowed_domains=approved_domains,
                max_bytes=5_000_000,
                timeout_seconds=60,
                max_redirects=5,
            ),
            now=now,
            client=public_pdf_client,
        )
        pdf_extractor = MistralOcrExtractor(
            settings=MistralOcrSettings(
                api_key=SecretStr(mistral_key),
                enabled=True,
                max_input_bytes=5_000_000,
                max_pages=20,
                timeout_seconds=120,
            ),
            client=mistral_client,
            clock=now,
            id_factory=lambda prefix: f"{prefix}:{next(coordinator_ids):04d}",
        )
        coordinator = MultiAdapterSourcingCoordinator(
            adapters=(
                SourceAdapterBinding(
                    adapter_id=tavily.adapter_id,
                    discovery=tavily,
                    acquisition=tavily,
                    source_categories=None,
                    authoritative=False,
                    artifact_kind=SourceArtifactKind.WEB_SNAPSHOT,
                    allowed_media_types=("text/markdown", "text/plain"),
                    policy=PublicSourceCollectionPolicy(
                        collection_purpose=("investor sourcing and evaluation live acceptance"),
                        lawful_basis=(
                            "operator-approved legitimate interests test with human review"
                        ),
                        source_terms=KnowledgeValue[str].unknown(
                            "The operator must approve the configured showcase source terms"
                        ),
                        robots_policy=KnowledgeValue[str].known(
                            "Tavily Search/Extract plus an operator-approved source URL"
                        ),
                    ),
                ),
            ),
            service=service,
            memory=memory,
            artifact_store=artifact_store,
            screening_bridge=bridge,
            now=now,
            id_factory=lambda prefix: f"{prefix}:{next(coordinator_ids):04d}",
            max_results=2,
            max_pages=2,
            max_bytes=5_000_000,
            timeout_seconds=60,
            cache_ttl_seconds=0,
            structured_page_extractor=structured_extractor,
            public_pdf_acquisition=public_pdf_acquisition,
            public_pdf_extractor=pdf_extractor,
            max_follow_up_rounds=0,
            max_discovery_calls=1,
        )
        accepted = coordinator.enqueue(command)
        await coordinator.execute(accepted.run.run_id, command)

        run = service.get_run(accepted.run.run_id)
        assert run.status is PipelineRunStatus.SUCCEEDED, run.model_dump(mode="json")
    artifacts = memory.list_records(RecordCategory.SOURCE_ARTIFACT)
    assert len(artifacts) >= 2
    canonical = memory.list_records(RecordCategory.CANONICAL_ENTITY)
    showcase_payload = next(
        record.payload
        for record in canonical
        if record.payload.get("projection_version") == "hackathon-showcase-projection.v0"
    )
    showcase = HackathonShowcaseProjection.model_validate_json(json.dumps(dict(showcase_payload)))
    assert showcase.event_name.state is KnowledgeState.KNOWN
    assert showcase.project_name.state is KnowledgeState.KNOWN
    assert showcase.participants
    assert all(
        item.identity_state is IdentityReviewState.NEEDS_REVIEW for item in showcase.participants
    )
    link_kinds = {item.kind.value for item in showcase.links}
    assert {"pitch_deck", "demo"}.issubset(link_kinds)
    pitch_deck = next(item for item in showcase.links if item.kind.value == "pitch_deck")
    assert pitch_deck.url == APPROVED_DECK_EDIT
    relationship_payload = next(
        record.payload
        for record in canonical
        if record.payload.get("record_type") == "public_hackathon_deck_relationship"
    )
    relationship = PublicHackathonDeckRelationship.model_validate_json(
        json.dumps(dict(relationship_payload))
    )
    assert relationship.showcase_source_artifact_id != relationship.deck_source_artifact_id
    assert relationship.deck_original_url == APPROVED_DECK_EDIT
    assert relationship.deck_acquisition_url == APPROVED_DECK_EXPORT
    assert relationship.deck_url_normalization == "google_slides_export_pdf"
    assert relationship.deck_acquisition_url in {
        record.payload["origin_locator"] for record in artifacts
    }
    ocr_payload = next(
        record.payload
        for record in canonical
        if record.payload.get("record_type") == "public_deck_ocr"
    )
    ocr = PublicDeckOcrRecord.model_validate_json(json.dumps(dict(ocr_payload)))
    assert ocr.state == "known"
    assert ocr.extraction is not None
    assert ocr.extraction.model_version.value is not None
    assert ocr.extraction.model_version.value.startswith("mistral-ocr-4")
    assert len(ocr.extraction.pages) == 7
    assert all(page.markdown.strip() for page in ocr.extraction.pages)
    candidate = service.list_candidates().items[0]
    assert candidate.founder_id.state is KnowledgeState.UNKNOWN
    assert candidate.preliminary_assessment is not None
    assert candidate.public_contact_routes
    assert all(route.classification == "public" for route in candidate.public_contact_routes)
    assert all(
        route.source_artifact_id == showcase.source_artifact_id
        for route in candidate.public_contact_routes
    )
    assert candidate.sourcing_audit is not None
    assert candidate.sourcing_audit.status.value == "stopped"
    assert candidate.sourcing_audit.rounds_completed == 1
    assert candidate.sourcing_audit.stop_reason == "call_budget_exhausted"
    assert candidate.sourcing_audit.run_id == accepted.run.run_id
    assert service.get_run_view(accepted.run.run_id).sourcing_audit == candidate.sourcing_audit
    structured_telemetry = [
        record.payload
        for record in memory.list_records(
            RecordCategory.COLLECTION_TELEMETRY,
            subject_id=accepted.run.run_id,
        )
        if record.payload.get("adapter_id") == "openai-structured-public-page-v0"
    ]
    assert structured_telemetry
    assert structured_telemetry[0]["status"] == "succeeded"
    run_telemetry = memory.list_records(
        RecordCategory.COLLECTION_TELEMETRY,
        subject_id=accepted.run.run_id,
    )
    resolution = next(
        record.payload
        for record in run_telemetry
        if record.payload.get("operation") == "resolve_public_deck_pdf_url"
    )
    assert resolution["source_url"] == APPROVED_DECK_EDIT
    assert resolution["acquisition_url"] == APPROVED_DECK_EXPORT
    assert resolution["normalization"] == "google_slides_export_pdf"
    ocr_telemetry = next(
        record.payload
        for record in run_telemetry
        if record.payload.get("operation") == "extract_public_pdf"
    )
    assert ocr_telemetry["status"] == "known"
    assert ocr_telemetry["safe_code"] is None
    serialized_telemetry = json.dumps(
        [dict(record.payload) for record in run_telemetry],
        sort_keys=True,
    )
    assert tavily_key not in serialized_telemetry
    assert openai_key not in serialized_telemetry
    assert mistral_key not in serialized_telemetry
    participant_claims = [
        record.payload
        for record in memory.list_records(RecordCategory.CLAIM)
        if record.payload["predicate"] == "hackathon.participant_display_name"
    ]
    assert participant_claims
    assert all(
        "identity remains unverified" in str(item["statement"]) for item in participant_claims
    )
