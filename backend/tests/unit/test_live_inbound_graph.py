"""Synthetic tests for strict live adapters and bounded inbound orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest
from pydantic import BaseModel

from founderlookup.domain.assessment import REQUIRED_MEMO_SECTIONS
from founderlookup.domain.common import EntityKind, KnowledgeState, KnowledgeValue, SubjectRef
from founderlookup.domain.evidence import (
    Claim,
    ClaimOrigin,
    ClaimStatus,
    Evidence,
    EvidenceStance,
    SourceLocator,
    SourceLocatorKind,
)
from founderlookup.domain.lifecycles import AssessmentMode
from founderlookup.domain.scoring import (
    ClaimTrustScore,
    CoverageLevel,
    CoverageSummary,
    TrustScoreState,
)
from founderlookup.screening.inbound_analysis import AnalysisRequest
from founderlookup.screening.inbound_graph import (
    BoundedInboundAnalysisGraph,
    InboundGraphLimits,
)
from founderlookup.screening.live_analyses import (
    DeterministicInboundFallbackAdapter,
    InboundAnalysisSnapshot,
    InMemoryAnalysisSnapshotResolver,
    MemoIdentity,
    OpenAIInboundAnalysisAdapter,
)

NOW = datetime(2026, 7, 19, 12, tzinfo=UTC)
SUBJECT = SubjectRef(kind=EntityKind.OPPORTUNITY, subject_id="opportunity:synthetic")


def _claim() -> Claim:
    return Claim(
        claim_id="claim:synthetic-product",
        claim_version_id="claim-version:synthetic-product:1",
        subject=SUBJECT,
        predicate="shipped_product",
        statement="The fictional team shipped an audit automation pilot.",
        status=ClaimStatus.SUPPORTED,
        origin=ClaimOrigin.SOURCE_ASSERTION,
        as_of=NOW,
        created_at=NOW,
        supporting_evidence_ids=("evidence:synthetic-product",),
        trust=ClaimTrustScore(
            state=TrustScoreState.UNSCORED,
            trust_policy_version="trust-policy.v0",
            reason="Synthetic fixture has not been calibrated.",
        ),
    )


def _evidence() -> Evidence:
    return Evidence(
        evidence_id="evidence:synthetic-product",
        claim_id="claim:synthetic-product",
        source_artifact_id="artifact:synthetic-deck",
        stance=EvidenceStance.SUPPORTS,
        locator=SourceLocator(
            kind=SourceLocatorKind.DOCUMENT_PAGE,
            locator="synthetic-deck.pdf#page=2",
            excerpt="Pilot shipped to one fictional operator.",
        ),
        collected_at=NOW,
        source_event_time=KnowledgeValue[datetime].known(NOW),
    )


def _snapshot() -> InboundAnalysisSnapshot:
    return InboundAnalysisSnapshot(
        input_snapshot_id="snapshot:synthetic",
        claims=(_claim(),),
        evidence=(_evidence(),),
        coverage=CoverageSummary(
            level=CoverageLevel.LOW,
            source_count=1,
            artifact_count=1,
            evidence_count=1,
            source_categories=("founder_provided_deck",),
            missing_fields=("independent_corroboration",),
            freshest_evidence_at=KnowledgeValue[datetime].known(NOW),
        ),
        memo_identity=MemoIdentity(
            opportunity_id=SUBJECT.subject_id,
            screening_case_id="screening-case:synthetic",
            assessment_id="assessment:synthetic",
            run_id="run:synthetic",
            thesis_version="thesis.v1",
            evidence_as_of=NOW,
        ),
    )


def _request() -> AnalysisRequest:
    return AnalysisRequest(
        request_id="request:synthetic",
        input_snapshot_id="snapshot:synthetic",
        subject=SUBJECT,
        mode=AssessmentMode.FULL,
    )


def _payloads() -> dict[str, object]:
    citation = {
        "trend": "unknown",
        "confidence": 0.61,
        "supporting_claim_ids": ["claim:synthetic-product"],
        "counter_claim_ids": [],
        "open_questions": ["Can the pilot be independently corroborated?"],
    }
    sections = [
        {
            "kind": kind.value,
            "content": f"Synthetic cited analysis for {kind.value}.",
            "material_claim_ids": ["claim:synthetic-product"],
            "gap_reason": None,
            "requested_evidence": None,
        }
        for kind in sorted(REQUIRED_MEMO_SECTIONS, key=lambda item: item.value)
    ]
    return {
        "_MarketProposal": {"rating": "neutral", **citation},
        "_IdeaProposal": {"rating": "pivotable", **citation},
        "_FounderProposal": {"rating": "mixed", **citation},
        "_AdversarialProposal": {
            "contradiction_ids": [],
            "confidence": 0.55,
            "open_questions": ["Which claim still lacks outside corroboration?"],
        },
        "_MemoProposal": {
            "sections": sections,
            "recommendation": {
                "action": "advance",
                "reasons": [
                    {
                        "summary": "The shipped pilot warrants human partner review.",
                        "claim_ids": ["claim:synthetic-product"],
                    }
                ],
                "next_actions": ["Ask a human reviewer to verify the pilot."],
            },
            "contradiction_ids": [],
            "confidence": 0.58,
            "open_questions": ["Can a customer reference corroborate the pilot?"],
        },
    }


class _Reasoner:
    def __init__(self, *, invalid_market: bool = False) -> None:
        self._payloads = _payloads()
        if invalid_market:
            market = cast(dict[str, object], self._payloads["_MarketProposal"])
            market["supporting_claim_ids"] = ["claim:not-in-snapshot"]
        self.calls: list[str] = []

    async def extract[SchemaT: BaseModel](
        self,
        *,
        schema: type[SchemaT],
        instructions: str,
        content: str,
    ) -> SchemaT:
        assert "never a Decision" in instructions or "not a Decision" in instructions
        assert "snapshot:synthetic" in content
        self.calls.append(schema.__name__)
        return schema.model_validate(self._payloads[schema.__name__])


def _graph(*, invalid_market: bool = False) -> tuple[BoundedInboundAnalysisGraph, _Reasoner]:
    resolver = InMemoryAnalysisSnapshotResolver({_snapshot().input_snapshot_id: _snapshot()})
    reasoner = _Reasoner(invalid_market=invalid_market)
    live = OpenAIInboundAnalysisAdapter(
        reasoner,
        resolver,
        clock=lambda: NOW,
        max_input_bytes=100_000,
    )
    fallback = DeterministicInboundFallbackAdapter(resolver, clock=lambda: NOW)
    graph = BoundedInboundAnalysisGraph(
        market=live,
        idea=live,
        founder=live,
        adversarial=live,
        memo=live,
        fallback_market=fallback,
        fallback_idea=fallback,
        fallback_founder=fallback,
        fallback_adversarial=fallback,
        fallback_memo=fallback,
        limits=InboundGraphLimits(
            max_model_calls=5,
            stage_timeout_seconds=1,
            total_timeout_seconds=3,
        ),
    )
    return graph, reasoner


@pytest.mark.anyio
async def test_five_specialist_graph_returns_cited_proposals_without_a_decision() -> None:
    graph, reasoner = _graph()

    result = await graph.run(_request())

    assert set(reasoner.calls) == {
        "_MarketProposal",
        "_IdeaProposal",
        "_FounderProposal",
        "_AdversarialProposal",
        "_MemoProposal",
    }
    assert graph.topology == (
        ("start", "market"),
        ("start", "idea"),
        ("start", "founder"),
        ("market+idea+founder", "adversarial"),
        ("adversarial", "memo"),
        ("memo", "end"),
    )
    assert result.used_fallback is False
    assert {item.status for item in result.audit} == {"live"}
    assert result.market.market_read.supporting_claim_ids == ("claim:synthetic-product",)
    assert result.memo.material_claims == (_claim(),)
    assert len(result.memo.memo.sections) == 5
    assert result.memo.recommendation.action.value == "advance"
    assert not hasattr(result, "decision")
    assert not hasattr(result.memo, "human_decision")


@pytest.mark.anyio
async def test_invalid_model_citation_falls_back_to_explicit_unknown_only_for_stage() -> None:
    graph, _reasoner = _graph(invalid_market=True)

    result = await graph.run(_request())

    market_audit = next(item for item in result.audit if item.stage == "market")
    assert market_audit.status == "fallback"
    assert market_audit.safe_code == "invalid_or_unavailable_live_output"
    assert result.market.market_read.rating.value == "unknown"
    assert result.market.confidence.state is KnowledgeState.UNKNOWN
    assert all(item.status == "live" for item in result.audit if item.stage != "market")
    assert "not-in-snapshot" not in repr(result)


def test_api_key_or_enablement_is_not_part_of_the_analysis_snapshot_contract() -> None:
    fields = InboundAnalysisSnapshot.__dataclass_fields__

    assert "api_key" not in fields
    assert "provider" not in fields
    assert set(fields) >= {"claims", "evidence", "coverage", "memo_identity"}
