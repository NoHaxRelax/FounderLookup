"""Coordinator-level contracts for bounded, multi-adapter public sourcing."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from itertools import count
from pathlib import Path

import pytest

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
    OutboundSearchLoopAudit,
    OutboundSearchStopReason,
    PublicSourceCollectionPolicy,
    SourceAdapterBinding,
)
from founderlookup.domain.common import KnowledgeState, KnowledgeValue
from founderlookup.domain.discovery import (
    AcquisitionRequest,
    AcquisitionResult,
    AcquisitionStatus,
    CollectionFailure,
    CollectionResultStatus,
    DiscoveryLead,
    DiscoveryRequest,
    DiscoveryResult,
    ProviderUsage,
)
from founderlookup.domain.evidence import SourceArtifactKind, SourceCategory
from founderlookup.domain.lifecycles import PipelineRunStatus
from founderlookup.domain.query import QueryOperator, UnknownValuePolicy
from founderlookup.infrastructure.artifacts import PrivateArtifactStore
from founderlookup.infrastructure.persistence import RecordCategory, SQLiteMemory
from founderlookup.ingestion.hackathons import (
    HackathonShowcaseProjection,
    IdentityReviewState,
    PublicHackathonDeckRelationship,
)
from founderlookup.ingestion.policy import PublicSourcePolicyRecord

NOW = datetime(2026, 7, 19, 14, tzinfo=UTC)


def _criterion(
    mode: ThesisCriterionMode,
    *,
    operator: QueryOperator | None = None,
    values: tuple[str | int | float | bool, ...] = (),
) -> ThesisCriterion:
    return ThesisCriterion(
        mode=mode,
        operator=operator,
        values=values,
        unknown_policy=UnknownValuePolicy.MANUAL_REVIEW,
    )


def _thesis() -> ThesisDraft:
    no_preference = _criterion(ThesisCriterionMode.NO_PREFERENCE)
    return ThesisDraft(
        sector=_criterion(
            ThesisCriterionMode.SCORED_PREFERENCE,
            operator=QueryOperator.CONTAINS,
            values=("AI infrastructure",),
        ),
        stage=no_preference,
        geography=no_preference,
        check_size=no_preference,
        ownership_target=no_preference,
        risk_appetite=no_preference,
    )


def _policy(terms: str = "https://example.test/terms") -> PublicSourceCollectionPolicy:
    return PublicSourceCollectionPolicy(
        collection_purpose="investor sourcing and evaluation",
        lawful_basis="legitimate interests with human review and removal",
        source_terms=KnowledgeValue[str].known(terms),
        robots_policy=KnowledgeValue[str].known("approved API or provider-respected robots"),
    )


class _RecordedSource:
    def __init__(
        self,
        adapter_id: str,
        category: SourceCategory,
        leads: tuple[tuple[str, str], ...],
        content: dict[str, tuple[bytes, str]],
        *,
        discovery_failure: CollectionFailure | None = None,
        raise_discovery: bool = False,
        acquisition_failures: dict[str, CollectionFailure] | None = None,
    ) -> None:
        self.adapter_id = adapter_id
        self.category = category
        self._leads = leads
        self._content = content
        self._discovery_failure = discovery_failure
        self._raise_discovery = raise_discovery
        self._acquisition_failures = acquisition_failures or {}
        self.discovery_requests: list[DiscoveryRequest] = []
        self.acquisition_requests: list[AcquisitionRequest] = []

    async def discover(self, request: DiscoveryRequest) -> DiscoveryResult:
        self.discovery_requests.append(request)
        if self._raise_discovery:
            raise RuntimeError("private upstream detail must not escape")
        retrieval = request.retrieval_requests[0]
        leads = tuple(
            DiscoveryLead(
                lead_id=f"{self.adapter_id}:lead:{index}",
                retrieval_request_id=retrieval.retrieval_request_id,
                original_url=url,
                source_category=self.category,
                discovered_at=NOW,
                rank=index,
                title=KnowledgeValue[str].known(title),
                provider_summary=KnowledgeValue[str].unknown(
                    "provider result is retrieval metadata only"
                ),
                retrieval_relevance=KnowledgeValue[float].unknown(
                    "recorded source has no relevance score"
                ),
            )
            for index, (url, title) in enumerate(self._leads, start=1)
        )
        failures = (() if self._discovery_failure is None else (self._discovery_failure,))
        return DiscoveryResult(
            result_id=f"{self.adapter_id}:result:{request.request_id}",
            request_id=request.request_id,
            status=(
                CollectionResultStatus.PARTIALLY_SUCCEEDED
                if leads and failures
                else CollectionResultStatus.FAILED
                if failures
                else CollectionResultStatus.SUCCEEDED
            ),
            completed_at=NOW,
            leads=leads,
            failures=failures,
            usage=ProviderUsage(
                adapter_id=self.adapter_id,
                operation_id=f"{self.adapter_id}:discover",
                request_count=1,
                result_count=len(leads),
                elapsed_milliseconds=1,
                cost_amount=KnowledgeValue[float].known(0.0),
                cost_currency=KnowledgeValue[str].known("USD"),
            ),
        )

    async def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        self.acquisition_requests.append(request)
        failure = self._acquisition_failures.get(request.original_url)
        if failure is not None:
            return AcquisitionResult(
                result_id=f"{self.adapter_id}:failed:{request.acquisition_request_id}",
                acquisition_request_id=request.acquisition_request_id,
                original_url=request.original_url,
                status=AcquisitionStatus.FAILED,
                completed_at=NOW,
                source_event_time=KnowledgeValue[datetime].unknown(
                    "the source acquisition failed"
                ),
                failure=failure,
            )
        body, media_type = self._content[request.original_url]
        return AcquisitionResult(
            result_id=f"{self.adapter_id}:acquired:{request.acquisition_request_id}",
            acquisition_request_id=request.acquisition_request_id,
            original_url=request.original_url,
            status=AcquisitionStatus.ACQUIRED,
            completed_at=NOW,
            content=body,
            media_type=media_type,
            content_sha256=sha256(body).hexdigest(),
            source_event_time=KnowledgeValue[datetime].unknown(
                "the recorded source has no authoritative event time"
            ),
        )


def _runtime(
    tmp_path: Path,
    bindings: tuple[SourceAdapterBinding, ...],
    *,
    max_pages: int = 5,
    max_follow_up_rounds: int = 0,
    max_discovery_calls: int = 12,
) -> tuple[MultiAdapterSourcingCoordinator, FakeVCBrainService, SQLiteMemory]:
    service_ids = count(1)
    coordinator_ids = count(1)
    bridge = DeterministicScreeningBridge()
    service = FakeVCBrainService(
        clock=lambda: NOW,
        id_factory=lambda: f"service-id-{next(service_ids):04d}",
        capability_pepper=b"sourcing-coordinator-tests" * 2,
        screening_bridge=bridge,
    )
    service.create_thesis(_thesis(), actor_id="investor-01")
    memory = SQLiteMemory(tmp_path / "memory.sqlite3")
    artifacts = PrivateArtifactStore(
        (tmp_path / "artifacts").absolute(),
        authorize_read=lambda _principal, _artifact: True,
    )
    coordinator = MultiAdapterSourcingCoordinator(
        adapters=bindings,
        service=service,
        memory=memory,
        artifact_store=artifacts,
        screening_bridge=bridge,
        now=lambda: NOW,
        id_factory=lambda prefix: f"{prefix}:{next(coordinator_ids):04d}",
        max_results=10,
        max_pages=max_pages,
        max_bytes=500_000,
        timeout_seconds=20,
        cache_ttl_seconds=900,
        max_follow_up_rounds=max_follow_up_rounds,
        max_discovery_calls=max_discovery_calls,
    )
    return coordinator, service, memory


def _binding(
    source: _RecordedSource,
    *,
    categories: tuple[SourceCategory, ...] | None,
    authoritative: bool,
    kind: SourceArtifactKind,
    media_types: tuple[str, ...],
) -> SourceAdapterBinding:
    return SourceAdapterBinding(
        adapter_id=source.adapter_id,
        discovery=source,
        acquisition=source,
        source_categories=categories,
        authoritative=authoritative,
        artifact_kind=kind,
        allowed_media_types=media_types,
        policy=_policy(),
    )


def _command(
    categories: tuple[SourceCategory, ...],
    *,
    max_pages: int = 5,
) -> BoundedSourcingCommand:
    return BoundedSourcingCommand(
        query="technical founder building infrastructure",
        source_categories=categories,
        max_results=10,
        max_pages=max_pages,
        max_bytes=500_000,
        timeout_seconds=20,
    )


@pytest.mark.anyio
async def test_authoritative_duplicate_wins_and_projects_claim_evidence(tmp_path: Path) -> None:
    github_url = "https://github.com/ada"
    generic = _RecordedSource(
        "generic-web-v0",
        SourceCategory.DEVELOPER_ACTIVITY,
        ((github_url, "Generic GitHub result"),),
        {github_url: (b"generic text", "text/markdown; charset=utf-8")},
    )
    snapshot = json.dumps(
        {
            "schema_version": "github-developer-activity-snapshot.v0",
            "subject": {
                "kind": "user",
                "owner": "ada",
                "repository": None,
                "original_url": github_url,
            },
            "records": {
                "profile": {"login": "ada", "html_url": github_url},
                "repositories": [
                    {"full_name": "ada/compiler", "html_url": "https://github.com/ada/compiler"}
                ],
                "public_events": [{"id": "event-1", "type": "PushEvent"}],
            },
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    github = _RecordedSource(
        "github-developer-activity-v0",
        SourceCategory.DEVELOPER_ACTIVITY,
        ((github_url, "ada"),),
        {github_url: (snapshot, "application/json")},
    )
    coordinator, service, memory = _runtime(
        tmp_path,
        (
            _binding(
                generic,
                categories=None,
                authoritative=False,
                kind=SourceArtifactKind.WEB_SNAPSHOT,
                media_types=("text/markdown", "text/plain"),
            ),
            _binding(
                github,
                categories=(SourceCategory.DEVELOPER_ACTIVITY,),
                authoritative=True,
                kind=SourceArtifactKind.REPOSITORY_RECORD,
                media_types=("application/json",),
            ),
        ),
    )

    accepted = coordinator.enqueue(_command((SourceCategory.DEVELOPER_ACTIVITY,)))
    await coordinator.execute(
        accepted.run.run_id,
        _command((SourceCategory.DEVELOPER_ACTIVITY,)),
    )

    run = service.get_run(accepted.run.run_id)
    assert run.status is PipelineRunStatus.SUCCEEDED
    assert generic.acquisition_requests == []
    assert [request.original_url for request in github.acquisition_requests] == [github_url]
    artifacts = memory.list_records(RecordCategory.SOURCE_ARTIFACT)
    assert len(artifacts) == 1
    assert artifacts[0].payload["kind"] == SourceArtifactKind.REPOSITORY_RECORD.value
    assert len(memory.list_records(RecordCategory.OBSERVATION)) >= 3
    assert len(memory.list_records(RecordCategory.EVIDENCE)) >= 3
    claims = memory.list_records(RecordCategory.CLAIM)
    assert any(
        record.payload["predicate"] == "developer_activity.github_handle"
        for record in claims
    )
    assert all(record.payload["status"] == "asserted_unverified" for record in claims)
    policy_records = [
        record
        for record in memory.list_records(RecordCategory.CANONICAL_ENTITY)
        if record.payload.get("record_type") == "public_source_policy"
    ]
    assert len(policy_records) == 1
    policy_record = PublicSourcePolicyRecord.model_validate_json(
        json.dumps(dict(policy_records[0].payload))
    )
    assert policy_record.policy.contact_details_collected is False
    candidate = service.list_candidates().items[0]
    assert candidate.founder_id.state is KnowledgeState.UNKNOWN
    assert candidate.founder_id.reason == "founder_identity_unresolved"
    assert candidate.preliminary_assessment is not None
    assert candidate.preliminary_assessment.coverage.evidence_count >= 3


@pytest.mark.anyio
async def test_adapter_failure_is_partial_and_never_erases_success(tmp_path: Path) -> None:
    good_url = "https://example.test/public-startup"
    good = _RecordedSource(
        "working-generic-v0",
        SourceCategory.COMPANY_UPDATE,
        ((good_url, "Public startup update"),),
        {good_url: (b"# Public startup\n", "text/markdown")},
    )
    broken = _RecordedSource(
        "broken-company-registry-v0",
        SourceCategory.COMPANY_UPDATE,
        (),
        {},
        raise_discovery=True,
    )
    coordinator, service, memory = _runtime(
        tmp_path,
        (
            _binding(
                good,
                categories=None,
                authoritative=False,
                kind=SourceArtifactKind.WEB_SNAPSHOT,
                media_types=("text/markdown",),
            ),
            _binding(
                broken,
                categories=(SourceCategory.COMPANY_UPDATE,),
                authoritative=True,
                kind=SourceArtifactKind.SOURCE_API_RECORD,
                media_types=("application/json",),
            ),
        ),
    )

    command = _command((SourceCategory.COMPANY_UPDATE,))
    accepted = coordinator.enqueue(command)
    await coordinator.execute(accepted.run.run_id, command)

    run = service.get_run(accepted.run.run_id)
    assert run.status is PipelineRunStatus.PARTIALLY_SUCCEEDED
    assert len(run.failures) == 1
    assert run.failures[0].safe_code == "adapter_discovery_failed"
    assert "private upstream detail" not in run.model_dump_json()
    assert len(memory.list_records(RecordCategory.SOURCE_ARTIFACT)) == 1
    assert len(service.list_candidates().items) == 1
    telemetry = memory.list_records(
        RecordCategory.COLLECTION_TELEMETRY,
        subject_id=accepted.run.run_id,
    )
    assert any(record.payload.get("adapter_id") == broken.adapter_id for record in telemetry)


@pytest.mark.anyio
async def test_recurring_replay_hits_cache_without_duplicate_artifact(tmp_path: Path) -> None:
    url = "https://example.test/unchanged"
    source = _RecordedSource(
        "cacheable-generic-v0",
        SourceCategory.COMPANY_UPDATE,
        ((url, "Unchanged public source"),),
        {url: (b"unchanged public content", "text/plain")},
    )
    coordinator, service, memory = _runtime(
        tmp_path,
        (
            _binding(
                source,
                categories=None,
                authoritative=False,
                kind=SourceArtifactKind.WEB_SNAPSHOT,
                media_types=("text/plain",),
            ),
        ),
    )
    command = _command((SourceCategory.COMPANY_UPDATE,))

    first = coordinator.enqueue(command)
    await coordinator.execute(first.run.run_id, command)
    second = coordinator.enqueue(command)
    await coordinator.execute(second.run.run_id, command)

    assert service.get_run(first.run.run_id).status is PipelineRunStatus.SUCCEEDED
    assert service.get_run(second.run.run_id).status is PipelineRunStatus.SUCCEEDED
    assert len(source.discovery_requests) == 2
    assert len(source.acquisition_requests) == 1
    assert len(memory.list_records(RecordCategory.SOURCE_ARTIFACT)) == 1
    assert len(service.list_candidates().items) == 1
    cache_telemetry = memory.list_records(
        RecordCategory.COLLECTION_TELEMETRY,
        subject_id=second.run.run_id,
    )
    assert any(record.payload.get("status") == "cache_hit" for record in cache_telemetry)


@pytest.mark.anyio
async def test_agentic_loop_follows_one_gap_then_stops_on_no_new_evidence(
    tmp_path: Path,
) -> None:
    """The graph may refine retrieval, but it must converge deterministically."""

    url = "https://example.test/one-public-update"
    source = _RecordedSource(
        "bounded-loop-source-v0",
        SourceCategory.COMPANY_UPDATE,
        ((url, "One source-backed company update"),),
        {url: (b"# One immutable public update\n", "text/markdown")},
    )
    coordinator, service, memory = _runtime(
        tmp_path,
        (
            _binding(
                source,
                categories=(SourceCategory.COMPANY_UPDATE,),
                authoritative=True,
                kind=SourceArtifactKind.WEB_SNAPSHOT,
                media_types=("text/markdown",),
            ),
        ),
        max_follow_up_rounds=2,
        max_discovery_calls=4,
    )
    # The second requested category has no configured adapter. That explicit gap causes one
    # refined query; the unchanged artifact then proves the no-new-evidence stop condition.
    command = _command(
        (SourceCategory.COMPANY_UPDATE, SourceCategory.DEVELOPER_ACTIVITY),
        max_pages=4,
    )

    accepted = coordinator.enqueue(command)
    await coordinator.execute(accepted.run.run_id, command)

    run = service.get_run(accepted.run.run_id)
    assert run.status is PipelineRunStatus.SUCCEEDED
    assert len(source.discovery_requests) == 2
    queries = [request.retrieval_requests[0].query for request in source.discovery_requests]
    assert queries[0] == command.query
    assert queries[1] != queries[0]
    assert "developer activity" in queries[1]
    assert len(memory.list_records(RecordCategory.SOURCE_ARTIFACT)) == 1

    audit_payload = next(
        record.payload
        for record in memory.list_records(
            RecordCategory.COLLECTION_TELEMETRY,
            subject_id=accepted.run.run_id,
        )
        if record.payload.get("record_type") == "outbound_search_loop"
    )
    audit = OutboundSearchLoopAudit.model_validate_json(json.dumps(dict(audit_payload)))
    assert audit.stop_reason is OutboundSearchStopReason.NO_NEW_EVIDENCE
    assert audit.maximum_follow_up_rounds == 2
    assert audit.maximum_discovery_calls == 4
    assert [round_item.round_index for round_item in audit.rounds] == [0, 1]
    assert audit.rounds[0].new_evidence_count > 0
    assert audit.rounds[1].new_evidence_count == 0
    assert audit.outreach_action == "none"


@pytest.mark.anyio
async def test_hackathon_deck_is_separate_and_keeps_display_names_unverified(
    tmp_path: Path,
) -> None:
    showcase_url = "https://showcase.example.test/projects/signal-forge"
    deck_url = "https://showcase.example.test/decks/signal-forge.pdf"
    showcase = b"""# Signal Forge

Event: Alpine AI Hack 2026
Project: Signal Forge
Participants: [Ada Demo](https://showcase.example.test/people/ada), Bo Demo
Repository: [GitHub](https://github.com/example/signal-forge)
Demo: [Try it](https://signal-forge.example.test/)
Pitch deck: [Public slides](https://showcase.example.test/decks/signal-forge.pdf)
"""
    source = _RecordedSource(
        "showcase-generic-v0",
        SourceCategory.HACKATHON,
        ((showcase_url, "Signal Forge showcase"),),
        {
            showcase_url: (showcase, "text/markdown; charset=utf-8"),
            deck_url: (b"# Signal Forge public deck\n", "text/markdown; charset=utf-8"),
        },
    )
    coordinator, service, memory = _runtime(
        tmp_path,
        (
            _binding(
                source,
                categories=None,
                authoritative=False,
                kind=SourceArtifactKind.WEB_SNAPSHOT,
                media_types=("text/markdown", "text/plain"),
            ),
        ),
        max_pages=2,
    )
    command = _command((SourceCategory.HACKATHON,), max_pages=2)

    accepted = coordinator.enqueue(command)
    await coordinator.execute(accepted.run.run_id, command)

    run = service.get_run(accepted.run.run_id)
    assert run.status is PipelineRunStatus.SUCCEEDED
    assert [request.original_url for request in source.acquisition_requests] == [
        showcase_url,
        deck_url,
    ]
    assert all(request.max_bytes == 500_000 for request in source.acquisition_requests)
    artifacts = memory.list_records(RecordCategory.SOURCE_ARTIFACT)
    assert len(artifacts) == 2
    by_url = {record.payload["origin_locator"]: record.payload for record in artifacts}
    assert by_url[showcase_url]["kind"] == SourceArtifactKind.WEB_SNAPSHOT.value
    assert by_url[deck_url]["kind"] == SourceArtifactKind.DOCUMENT.value
    canonical = memory.list_records(RecordCategory.CANONICAL_ENTITY)
    projection_payload = next(
        record.payload
        for record in canonical
        if record.payload.get("projection_version") == "hackathon-showcase-projection.v0"
    )
    projection = HackathonShowcaseProjection.model_validate_json(
        json.dumps(dict(projection_payload))
    )
    assert projection.event_name.value == "Alpine AI Hack 2026"
    assert projection.project_name.value == "Signal Forge"
    assert [item.display_name for item in projection.participants] == [
        "Ada Demo",
        "Bo Demo",
    ]
    assert all(
        item.identity_state is IdentityReviewState.NEEDS_REVIEW
        for item in projection.participants
    )
    relationship_payload = next(
        record.payload
        for record in canonical
        if record.payload.get("record_type") == "public_hackathon_deck_relationship"
    )
    relationship = PublicHackathonDeckRelationship.model_validate_json(
        json.dumps(dict(relationship_payload))
    )
    assert relationship.showcase_source_artifact_id != relationship.deck_source_artifact_id
    assert relationship.deck_original_url == deck_url
    assert relationship.showcase_locator.locator == "line:8"
    candidate = service.list_candidates().items[0]
    assert candidate.company_name == "Showcase project: Signal Forge"
    assert candidate.founder_id.state is KnowledgeState.UNKNOWN
    participant_claims = [
        record.payload
        for record in memory.list_records(RecordCategory.CLAIM)
        if record.payload["predicate"] == "hackathon.participant_display_name"
    ]
    assert len(participant_claims) == 2
    assert all(
        "identity remains unverified" in str(item["statement"])
        for item in participant_claims
    )
