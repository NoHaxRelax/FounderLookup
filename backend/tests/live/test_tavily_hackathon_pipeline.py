"""Opt-in live Tavily Search/Extract acceptance for a public showcase and deck.

This module never auto-runs. To exercise it deliberately, provide all of:

* ``FOUNDERLOOKUP_RUN_LIVE_TESTS=1``
* ``TAVILY_API_KEY``
* ``FOUNDERLOOKUP_LIVE_HACKATHON_URL`` pointing to an approved public showcase
  that explicitly publishes an event, project, participant display name, repository,
  demo, and exactly one directly accessible public pitch-deck link.

The source URL remains operator-selected because terms/robots approval is source-specific.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from urllib.parse import urlsplit

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
    BoundedSourcingCommand,
    MultiAdapterSourcingCoordinator,
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
from founderlookup.ingestion.tavily import TavilyPolicy, TavilySource

pytestmark = pytest.mark.skipif(
    os.getenv("FOUNDERLOOKUP_RUN_LIVE_TESTS") != "1",
    reason="live provider tests require FOUNDERLOOKUP_RUN_LIVE_TESTS=1",
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


def _required_environment() -> tuple[str, str, str]:
    key = os.getenv("TAVILY_API_KEY", "").strip()
    showcase_url = os.getenv("FOUNDERLOOKUP_LIVE_HACKATHON_URL", "").strip()
    if not key:
        pytest.skip("TAVILY_API_KEY is required for the opt-in live test")
    if not showcase_url:
        pytest.skip(
            "FOUNDERLOOKUP_LIVE_HACKATHON_URL is required so source terms are explicit"
        )
    host = (urlsplit(showcase_url).hostname or "").casefold()
    if not host:
        pytest.fail("FOUNDERLOOKUP_LIVE_HACKATHON_URL must be an absolute public URL")
    return key, showcase_url, host


@pytest.mark.anyio
async def test_live_tavily_showcase_to_separate_deck_and_candidate(tmp_path: Path) -> None:
    key, showcase_url, host = _required_environment()

    def now() -> datetime:
        return datetime.now(UTC)

    tavily = TavilySource(
        api_key=SecretStr(key),
        policy=TavilyPolicy(
            max_queries=1,
            max_results=2,
            max_pages=2,
            max_content_bytes=500_000,
            max_response_bytes=2_000_000,
            timeout_seconds=20,
            allowed_domains=(host,),
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
                    collection_purpose="investor sourcing and evaluation live acceptance",
                    lawful_basis="operator-approved legitimate interests test with human review",
                    source_terms=KnowledgeValue[str].unknown(
                        "The operator must approve the configured showcase source terms"
                    ),
                    robots_policy=KnowledgeValue[str].known(
                        "Tavily public Search/Extract plus an operator-approved source URL"
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
        max_bytes=500_000,
        timeout_seconds=20,
        cache_ttl_seconds=0,
    )
    query = os.getenv(
        "FOUNDERLOOKUP_LIVE_HACKATHON_QUERY",
        f'"{showcase_url}" public hackathon pitch deck',
    )
    command = BoundedSourcingCommand(
        query=query,
        source_categories=(SourceCategory.HACKATHON,),
        allowed_domains=(host,),
        max_results=2,
        max_pages=2,
        max_bytes=500_000,
        timeout_seconds=20,
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
    showcase = HackathonShowcaseProjection.model_validate_json(
        json.dumps(dict(showcase_payload))
    )
    assert showcase.event_name.state is KnowledgeState.KNOWN
    assert showcase.project_name.state is KnowledgeState.KNOWN
    assert showcase.participants
    assert all(
        item.identity_state is IdentityReviewState.NEEDS_REVIEW
        for item in showcase.participants
    )
    link_kinds = {item.kind.value for item in showcase.links}
    assert {"pitch_deck", "repository", "demo"}.issubset(link_kinds)
    relationship_payload = next(
        record.payload
        for record in canonical
        if record.payload.get("record_type") == "public_hackathon_deck_relationship"
    )
    relationship = PublicHackathonDeckRelationship.model_validate_json(
        json.dumps(dict(relationship_payload))
    )
    assert relationship.showcase_source_artifact_id != relationship.deck_source_artifact_id
    assert relationship.deck_original_url in {
        record.payload["origin_locator"] for record in artifacts
    }
    candidate = service.list_candidates().items[0]
    assert candidate.founder_id.state is KnowledgeState.UNKNOWN
    assert candidate.preliminary_assessment is not None
    participant_claims = [
        record.payload
        for record in memory.list_records(RecordCategory.CLAIM)
        if record.payload["predicate"] == "hackathon.participant_display_name"
    ]
    assert participant_claims
    assert all(
        "identity remains unverified" in str(item["statement"])
        for item in participant_claims
    )
