"""Focused contract tests for the metadata-only local demo workspace."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import count

import pytest

from founderlookup.application.models import TargetState
from founderlookup.application.screening_bridge import DeterministicScreeningBridge
from founderlookup.application.service import ArtifactUnavailableError, FakeVCBrainService
from founderlookup.demo.bootstrap import DemoBootstrapResult, seed_local_demo
from founderlookup.domain.assessment import (
    FounderAxisRating,
    IdeaVsMarketAxisRating,
    MarketAxisRating,
    MemoSectionKind,
    RecommendationAction,
)
from founderlookup.domain.common import KnowledgeState
from founderlookup.domain.evidence import ArtifactAvailability, SourceLocatorKind
from founderlookup.domain.lifecycles import (
    DecisionReadinessStatus,
    OpportunityOrigin,
    OutboundCandidateStatus,
    PipelineRunStatus,
    ScreeningCaseStatus,
)

NOW = datetime(2026, 7, 19, 12, tzinfo=UTC)


def _seed() -> tuple[FakeVCBrainService, DemoBootstrapResult]:
    identifiers = count(1)
    bridge = DeterministicScreeningBridge()
    service = FakeVCBrainService(
        clock=lambda: NOW,
        id_factory=lambda: f"demo-test-id-{next(identifiers):04d}",
        capability_pepper=b"fictional-demo-test-pepper",
        screening_bridge=bridge,
    )
    result = seed_local_demo(service, screening_bridge=bridge)
    return service, result


def test_demo_seed_is_idempotent_and_returns_complete_stable_handles() -> None:
    identifiers = count(1)
    bridge = DeterministicScreeningBridge()
    service = FakeVCBrainService(
        clock=lambda: NOW,
        id_factory=lambda: f"demo-test-id-{next(identifiers):04d}",
        capability_pepper=b"fictional-demo-test-pepper",
        screening_bridge=bridge,
    )

    first = seed_local_demo(service, screening_bridge=bridge)
    second = seed_local_demo(service, screening_bridge=bridge)

    assert second == first
    assert first.inbound_application_id == "demo:application:fictional-inbound-001"
    assert first.inbound_ingestion_run_id == "demo:run:fictional-inbound-ingestion-001"
    assert first.inbound_source_artifact_id == (
        "demo:source-artifact:fictional-inbound-deck-001"
    )
    assert len(service.thesis_history()) == 1
    assert len(service.list_candidates().items) == 1
    assert len(service.list_opportunities().items) == 1
    detail = service.get_opportunity(first.inbound_opportunity_id)
    assert len(detail.assessment_history) == 1
    assert len(detail.memo_revisions) == 1
    assert service.get_run(first.preliminary_run_id).status is PipelineRunStatus.SUCCEEDED
    assert service.get_run(first.inbound_ingestion_run_id).status is PipelineRunStatus.SUCCEEDED
    assert service.get_run(first.inbound_screening_run_id).status is PipelineRunStatus.SUCCEEDED


def test_demo_keeps_the_outbound_candidate_ready_for_human_activation() -> None:
    service, result = _seed()

    candidates = service.list_candidates().items
    opportunities = service.list_opportunities().items

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.outbound_candidate_id == result.outbound_candidate_id
    assert candidate.status is OutboundCandidateStatus.READY_FOR_ACTIVATION
    assert candidate.preliminary_assessment is not None
    assert candidate.preliminary_assessment.identity.mode == "preliminary"
    assert candidate.preliminary_assessment.recommendation is not None
    assert candidate.preliminary_assessment.recommendation.action is RecommendationAction.ACTIVATE
    assert candidate.application_id is None
    assert candidate.outreach_draft is None
    assert len(opportunities) == 1
    assert opportunities[0].origin is OpportunityOrigin.INBOUND


def test_demo_inbound_opportunity_is_cited_screened_and_awaits_human_action() -> None:
    service, result = _seed()

    detail = service.get_opportunity(
        result.inbound_opportunity_id,
        include_claims=True,
        include_evidence=True,
    )
    assessment = detail.latest_assessment

    assert detail.origin is OpportunityOrigin.INBOUND
    assert detail.application_id == result.inbound_application_id
    assert detail.outbound_candidate_id is None
    assert detail.screening_status is ScreeningCaseStatus.BLOCKED
    assert assessment is not None
    assert assessment.identity.mode == "full"
    assert assessment.founder_score.state is KnowledgeState.UNKNOWN
    assert detail.founder_id.state is KnowledgeState.UNKNOWN
    assert assessment.axes.founder.rating is FounderAxisRating.UNKNOWN
    assert assessment.axes.market.rating is MarketAxisRating.BEAR
    assert assessment.axes.idea_vs_market.rating is IdeaVsMarketAxisRating.PIVOTABLE

    assert len(detail.claims) == 7
    assert len(detail.evidence) == 7
    assert set(assessment.claim_ids) == {claim.claim_id for claim in detail.claims}
    assert set(assessment.evidence_ids) == {evidence.evidence_id for evidence in detail.evidence}
    assert all(
        evidence.locator.kind is SourceLocatorKind.DOCUMENT_PAGE
        for evidence in detail.evidence
    )
    assert {evidence.locator.locator.split("#", 1)[0] for evidence in detail.evidence} == {
        "page:0",
        "page:1",
        "page:2",
        "page:3",
    }
    assert all(
        evidence.source_event_time.state is KnowledgeState.UNKNOWN
        for evidence in detail.evidence
    )
    assert all(
        evidence.availability is ArtifactAvailability.ACCESS_RESTRICTED
        for evidence in detail.evidence
    )

    assert assessment.contradictions
    assert all(contradiction.blocking for contradiction in assessment.contradictions)
    assert assessment.diligence_actions
    assert any(action.resolves_contradiction_ids for action in assessment.diligence_actions)
    assert assessment.decision_readiness is not None
    assert assessment.decision_readiness.status is DecisionReadinessStatus.BLOCKED
    assert assessment.decision_readiness.blockers

    memo = detail.latest_memo
    assert memo is not None
    assert tuple(section.kind for section in memo.sections) == (
        MemoSectionKind.COMPANY_SNAPSHOT,
        MemoSectionKind.INVESTMENT_HYPOTHESES,
        MemoSectionKind.SWOT,
        MemoSectionKind.PROBLEM_AND_PRODUCT,
        MemoSectionKind.TRACTION_AND_KPIS,
    )
    section_states = {section.kind: section.content.state for section in memo.sections}
    assert section_states[MemoSectionKind.COMPANY_SNAPSHOT] is KnowledgeState.CONFLICTED
    assert section_states[MemoSectionKind.INVESTMENT_HYPOTHESES] is KnowledgeState.KNOWN
    assert section_states[MemoSectionKind.SWOT] is KnowledgeState.KNOWN
    assert section_states[MemoSectionKind.PROBLEM_AND_PRODUCT] is KnowledgeState.KNOWN
    assert section_states[MemoSectionKind.TRACTION_AND_KPIS] is KnowledgeState.KNOWN

    recommendation = detail.latest_recommendation
    assert recommendation is not None
    assert recommendation.action is RecommendationAction.NEEDS_INFORMATION
    assert detail.human_decisions == ()
    assert detail.timing.decision_readiness_target_at == detail.timing.started_at + timedelta(
        hours=24
    )
    assert detail.timing.target_state is TargetState.ON_TRACK
    assert set(detail.related_run_ids) == {
        result.inbound_ingestion_run_id,
        result.inbound_screening_run_id,
    }

    descriptor = service.artifact_descriptor(result.inbound_source_artifact_id)
    assert descriptor.display_name == "fictional-verdant-relay-deck.pdf"
    with pytest.raises(ArtifactUnavailableError, match="byte storage is not configured"):
        service.read_artifact(result.inbound_source_artifact_id, principal_id="investor:demo")
