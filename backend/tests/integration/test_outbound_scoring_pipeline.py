"""Integration walkthrough: the OUTBOUND scoring engine composes end to end, no model.

This suite threads a single fictional outbound candidate ("Ada Reyes") through the
Data/ML modules already on main and asserts a coherent, contract-valid, decision-ready
result. There is no model, no framework, no network, and no randomness: every step is a
pure, deterministic function over hand-built fixtures, and every carried domain object is
produced by the real rubric functions so nothing is hand-forged.

The pipeline reads as a walkthrough:

1. plan a natural-language thesis and assert it is VALIDATED with criteria;
2. resolve the candidate's footprints and assert they collapse to one entity;
3. read a strong, substance-backed founder (builder read STRONG, the underrated-builder
   gap), score the flagship claim's trust, and take a person-level score snapshot;
4. assemble the three INDEPENDENT axes from positive signals;
5. estimate a confidence band from tight reasoned samples (a stand-in for the model reads)
   and assert it is confident and not abstaining;
6. run ``evaluate_preliminary_candidate`` and assert the conviction clears the bar
   (PURSUE) and the returned envelope is a valid preliminary envelope (memo None,
   readiness None).

Finally the whole pipeline is run twice and the outputs are asserted equal, which pins the
no-model determinism guarantee: identical fixtures always yield identical decisions.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from founderlookup.domain.assessment import (
    FounderAxisRating,
    IdeaVsMarketAxisRating,
    IndependentAxes,
    MarketAxisRating,
    PreliminaryAssessmentIdentity,
)
from founderlookup.domain.common import (
    KnowledgeValue,
    ScalarValue,
    VersionManifest,
)
from founderlookup.domain.evidence import SourceCategory
from founderlookup.domain.query import (
    OpportunityQueryPlan,
    QueryCriterionField,
    QueryPlanningMode,
    QueryPlanState,
)
from founderlookup.domain.scoring import (
    ClaimTrustScore,
    CoverageLevel,
    CoverageSummary,
    FounderScoreSnapshot,
    TrustFactorKind,
    TrustFactorSignal,
    TrustScoreState,
)
from founderlookup.ingestion.identity import (
    IdentitySignal,
    IdentitySignalKind,
    ResolutionStatus,
    ResolvedEntity,
    resolve_identities,
)
from founderlookup.ingestion.query_planner import (
    DeterministicQueryPlanner,
    QueryPlanRequest,
)
from founderlookup.screening.axes import (
    AxisSignal,
    SignalReading,
    assemble_independent_axes,
    assess_founder_axis,
    assess_idea_vs_market_axis,
    assess_market_axis,
)
from founderlookup.screening.confidence import ConfidenceBand, estimate_confidence_band
from founderlookup.screening.founder_reads import (
    BuilderFundabilityGap,
    EvidenceGrade,
    FounderRead,
    GapLabel,
    GradedObservation,
    builder_fundability_gap,
    builder_signal_read,
    fundability_read,
)
from founderlookup.screening.preliminary import (
    PURSUE_MIN_CONFIDENCE,
    ConvictionLevel,
    PreliminaryAssessmentOutcome,
    evaluate_preliminary_candidate,
)
from founderlookup.screening.rubrics import (
    ContributionTier,
    FounderBand,
    FounderFactorObservation,
    TrustBand,
    TrustFactorInput,
    classify_founder_band,
    classify_trust_band,
    score_claim_trust,
    score_founder,
)

# A single fixed clock value; the pipeline reads no other wall-clock time.
NOW = datetime(2026, 7, 19, 10, 0, tzinfo=UTC)

# The one fictional outbound candidate the whole walkthrough is about.
CANDIDATE_ID = "candidate:ada-reyes"
FOUNDER_ID = "founder:ada-reyes"
COMPANY_ID = "company:ada-labs"


def _coverage(level: CoverageLevel) -> CoverageSummary:
    """A rich, conflict-free coverage summary shared by the axes and the snapshot."""
    return CoverageSummary(
        level=level,
        source_count=4,
        artifact_count=6,
        evidence_count=6,
        freshest_evidence_at=KnowledgeValue[datetime].known(NOW),
    )


def _substance_observations() -> list[GradedObservation]:
    """Costly-to-fake, outcome-backed building signals for a genuinely strong builder."""
    return [
        GradedObservation(
            "shipped_adopted_work",
            ContributionTier.FULL,
            EvidenceGrade.OUTCOME_BACKED,
            KnowledgeValue[ScalarValue].known("Shipped an inference runtime adopted by 12k users."),
            "shipped and externally adopted",
            ("evidence:shipped",),
        ),
        GradedObservation(
            "corroborated_domain_experience",
            ContributionTier.FULL,
            EvidenceGrade.OUTCOME_BACKED,
            KnowledgeValue[ScalarValue].known("Nine years of registry-verified ML systems work."),
            "corroborated domain experience",
            ("evidence:domain",),
        ),
        GradedObservation(
            "sustained_follow_through",
            ContributionTier.FULL,
            EvidenceGrade.OUTCOME_BACKED,
            KnowledgeValue[ScalarValue].known("Six years maintaining a live open-source project."),
            "sustained follow-through",
        ),
    ]


def _founder_observations() -> list[FounderFactorObservation]:
    """The same substance, shaped for the person-level founder score snapshot."""
    return [
        FounderFactorObservation(
            factor_key="shipped_adopted_work",
            tier=ContributionTier.FULL,
            observed_value=KnowledgeValue[ScalarValue].known(
                "Shipped an inference runtime adopted by 12k users."
            ),
            rationale="shipped and externally adopted",
            evidence_ids=("evidence:shipped",),
        ),
        FounderFactorObservation(
            factor_key="corroborated_domain_experience",
            tier=ContributionTier.FULL,
            observed_value=KnowledgeValue[ScalarValue].known(
                "Nine years of registry-verified ML systems work."
            ),
            rationale="corroborated domain experience",
            evidence_ids=("evidence:domain",),
        ),
        FounderFactorObservation(
            factor_key="sustained_follow_through",
            tier=ContributionTier.FULL,
            observed_value=KnowledgeValue[ScalarValue].known(
                "Six years maintaining a live open-source project."
            ),
            rationale="sustained follow-through",
        ),
    ]


def _flagship_claim_trust() -> ClaimTrustScore:
    """Trust-score the founder's flagship 'shipped adopted work' claim from six factors."""
    factors = [
        TrustFactorInput(
            TrustFactorKind.PROVENANCE,
            KnowledgeValue[TrustFactorSignal].known(TrustFactorSignal.STRENGTHENS),
            "First-party release record.",
            ("evidence:shipped",),
        ),
        TrustFactorInput(
            TrustFactorKind.INDEPENDENCE,
            KnowledgeValue[TrustFactorSignal].known(TrustFactorSignal.STRENGTHENS),
            "Adoption confirmed by an independent registry.",
        ),
        TrustFactorInput(
            TrustFactorKind.RECENCY,
            KnowledgeValue[TrustFactorSignal].known(TrustFactorSignal.STRENGTHENS),
            "Release is recent.",
        ),
        TrustFactorInput(
            TrustFactorKind.EXTRACTION_CERTAINTY,
            KnowledgeValue[TrustFactorSignal].known(TrustFactorSignal.STRENGTHENS),
            "Figures read verbatim from the source.",
        ),
        TrustFactorInput(
            TrustFactorKind.CORROBORATION,
            KnowledgeValue[TrustFactorSignal].known(TrustFactorSignal.STRENGTHENS),
            "Independently corroborated by download telemetry.",
            ("evidence:domain",),
        ),
        TrustFactorInput(
            TrustFactorKind.CONTRADICTION,
            KnowledgeValue[TrustFactorSignal].known(TrustFactorSignal.NEUTRAL),
            "No contradicting source.",
        ),
    ]
    return score_claim_trust(factors, has_supporting_evidence=True)


def _pos(key: str, claim_id: str) -> AxisSignal:
    """One strong-positive axis signal backed by a single claim."""
    return AxisSignal(
        key=key,
        reading=KnowledgeValue[SignalReading].known(SignalReading.STRONG_POSITIVE),
        claim_ids=(claim_id,),
    )


@dataclass(frozen=True)
class _OutboundPipeline:
    """Every salient output of one pass, so two passes can be compared for equality."""

    plan: OpportunityQueryPlan
    entities: tuple[ResolvedEntity, ...]
    flagship_trust: ClaimTrustScore
    builder: FounderRead
    fundability: FounderRead
    gap: BuilderFundabilityGap
    snapshot: FounderScoreSnapshot
    axes: IndependentAxes
    band: ConfidenceBand
    outcome: PreliminaryAssessmentOutcome


def _run_pipeline() -> _OutboundPipeline:
    """Thread one fictional outbound candidate through the whole Data/ML lane, no model."""
    coverage = _coverage(CoverageLevel.HIGH)

    # (1) Plan the sourcing thesis from natural language.
    plan = asyncio.run(
        DeterministicQueryPlanner().plan(
            QueryPlanRequest(
                raw_query=(
                    "Technical founders in AI/ML with paying enterprise customers, "
                    "active on GitHub."
                ),
                query_plan_id="query-plan:ada-thesis",
                query_plan_version_id="query-plan-version:ada-thesis.v1",
                created_at=NOW,
                allowed_source_categories=(SourceCategory.DEVELOPER_ACTIVITY,),
            )
        )
    )

    # (2) Collapse the candidate's two footprints (they share the "adareyes" handle).
    entities = resolve_identities(
        [
            IdentitySignal(
                kind=IdentitySignalKind.HANDLE,
                value="adareyes",
                source_category=SourceCategory.DEVELOPER_ACTIVITY,
                source_ref="github:adareyes",
            ),
            IdentitySignal(
                kind=IdentitySignalKind.HANDLE,
                value="adareyes",
                source_category=SourceCategory.PUBLIC_SOCIAL,
                source_ref="social:adareyes",
            ),
        ]
    )

    # (3) Read building substance, its gap versus conventional fundability, the flagship
    #     claim's trust, and a person-level founder score snapshot.
    observations = _substance_observations()
    builder = builder_signal_read(observations)
    fundability = fundability_read(observations)
    gap = builder_fundability_gap(builder, fundability)
    flagship_trust = _flagship_claim_trust()
    snapshot = score_founder(
        founder_id=FOUNDER_ID,
        snapshot_id="snapshot:ada-reyes",
        snapshot_version_id="snapshot-version:ada-reyes.v1",
        as_of=NOW,
        coverage=coverage,
        observations=_founder_observations(),
    )

    # (4) Assemble the three INDEPENDENT axes from positive signals.
    axes = assemble_independent_axes(
        founder=assess_founder_axis(
            [_pos("shipped", "claim:shipped"), _pos("domain", "claim:domain")],
            coverage=coverage,
            assessment_id="axis:founder",
            assessment_version_id="axis-version:founder.v1",
        ),
        market=assess_market_axis(
            [_pos("tam", "claim:tam"), _pos("growth", "claim:growth")],
            coverage=coverage,
            assessment_id="axis:market",
            assessment_version_id="axis-version:market.v1",
        ),
        idea_vs_market=assess_idea_vs_market_axis(
            [_pos("fit", "claim:fit"), _pos("wedge", "claim:wedge")],
            coverage=coverage,
            assessment_id="axis:idea",
            assessment_version_id="axis-version:idea.v1",
        ),
    )

    # (5) Estimate a confidence band from tight reasoned samples (a model-read stand-in).
    band = estimate_confidence_band(
        (87.0, 88.0, 88.0, 88.0, 89.0),
        coverage_level=0.9,
    )

    # (6) Decide conviction and assemble the preliminary envelope in one deterministic step.
    outcome = evaluate_preliminary_candidate(
        identity=PreliminaryAssessmentIdentity(
            outbound_candidate_id=CANDIDATE_ID,
            founder_id=KnowledgeValue[str].known(FOUNDER_ID),
            company_id=KnowledgeValue[str].known(COMPANY_ID),
        ),
        assessment_id="assessment:ada-reyes",
        assessment_version_id="assessment-version:ada-reyes.v1",
        versions=VersionManifest(),
        input_snapshot_id="snapshot:ada-reyes",
        input_snapshot_as_of=NOW,
        coverage=coverage,
        founder_score=KnowledgeValue[FounderScoreSnapshot].known(snapshot),
        axes=axes,
        claim_ids=(
            "claim:shipped",
            "claim:domain",
            "claim:tam",
            "claim:growth",
            "claim:fit",
            "claim:wedge",
        ),
        evidence_ids=("evidence:shipped", "evidence:domain"),
        run_id="run:ada-reyes",
        created_at=NOW,
    )

    return _OutboundPipeline(
        plan=plan,
        entities=entities,
        flagship_trust=flagship_trust,
        builder=builder,
        fundability=fundability,
        gap=gap,
        snapshot=snapshot,
        axes=axes,
        band=band,
        outcome=outcome,
    )


def test_outbound_scoring_pipeline_composes_into_a_pursue_decision() -> None:
    """The whole outbound engine composes into a coherent, contract-valid PURSUE."""
    result = _run_pipeline()

    # (1) The thesis is validated with concrete criteria and a deterministic plan.
    assert result.plan.state is QueryPlanState.VALIDATED
    assert result.plan.planning_mode is QueryPlanningMode.DETERMINISTIC
    planned_fields = {criterion.field for criterion in result.plan.criteria}
    assert {
        QueryCriterionField.TECHNICAL_FOUNDER,
        QueryCriterionField.SECTOR,
        QueryCriterionField.ENTERPRISE_TRACTION,
    } <= planned_fields
    # The GitHub cue drove exactly one bounded developer-activity retrieval.
    assert tuple(
        category
        for request in result.plan.retrieval_requests
        for category in request.source_categories
    ) == (SourceCategory.DEVELOPER_ACTIVITY,)

    # (2) The two shared-handle footprints collapse to a single resolved entity.
    assert len(result.entities) == 1
    entity = result.entities[0]
    assert entity.status is ResolutionStatus.RESOLVED
    assert entity.source_refs == {"github:adareyes", "social:adareyes"}
    assert len(entity.source_categories) == 2  # linked across two independent sources
    assert entity.confidence == 0.9

    # (3) Building substance is genuinely STRONG, and it outruns conventional fundability,
    #     so the underrated-builder gap fires rather than a substance-light one.
    assert result.builder.band is FounderBand.STRONG
    assert result.builder.score >= 75.0
    assert result.builder.score > result.fundability.score
    assert result.gap.label is GapLabel.UNDER_NETWORKED_STRONG_BUILDER
    assert result.gap.gap > 15.0

    # The flagship claim is trust-scored (not merely asserted) and lands in the top band.
    assert result.flagship_trust.state is TrustScoreState.SCORED
    assert result.flagship_trust.score is not None
    assert classify_trust_band(result.flagship_trust.score) is TrustBand.VERY_HIGH

    # The person-level snapshot is established (not provisional) and strong.
    assert result.snapshot.founder_id == FOUNDER_ID
    assert result.snapshot.provisional is False
    assert classify_founder_band(result.snapshot.score) is FounderBand.STRONG

    # (4) All three INDEPENDENT axes read positive with confidence above the pursue bar.
    assert result.axes.founder.rating is FounderAxisRating.STRONG
    assert result.axes.market.rating is MarketAxisRating.BULLISH
    assert result.axes.idea_vs_market.rating is IdeaVsMarketAxisRating.VIABLE
    for assessment in (result.axes.founder, result.axes.market, result.axes.idea_vs_market):
        assert assessment.confidence.value is not None
        assert assessment.confidence.value >= PURSUE_MIN_CONFIDENCE

    # (5) The confidence band is asserted, not abstained, and confident about its center.
    assert result.band.abstained is False
    assert result.band.abstention_codes == ()
    assert result.band.point == 88.0
    assert result.band.confidence > 0.6

    # (6) Conviction clears the bar with no hard-negative axis, and the envelope is a valid
    #     PRELIMINARY envelope: no memo and no readiness can ride in on a preliminary read.
    conviction = result.outcome.conviction
    assert conviction.level is ConvictionLevel.PURSUE
    assert conviction.clears_bar is True
    assert conviction.hard_negative_axes == ()
    assert set(conviction.positive_axes) == {"founder", "market", "idea_vs_market"}

    envelope = result.outcome.envelope
    assert envelope.identity.mode == "preliminary"
    assert envelope.identity.origin == "outbound"
    assert envelope.memo is None
    assert envelope.decision_readiness is None
    assert envelope.recommendation is None
    # The envelope's founder score and its identity describe the same founder.
    assert envelope.founder_score.value is not None
    assert envelope.founder_score.value.founder_id == FOUNDER_ID
    assert envelope.coverage.level is CoverageLevel.HIGH


def test_outbound_scoring_pipeline_is_deterministic_without_a_model() -> None:
    """No model, no framework, no network: two full passes are byte-for-byte identical."""
    first = _run_pipeline()
    second = _run_pipeline()

    # Whole-pipeline equality pins determinism across every composed module at once.
    assert first == second
    # And the decision it produces is the same clears-the-bar PURSUE both times.
    assert first.outcome.conviction.level is ConvictionLevel.PURSUE
    assert second.outcome.conviction.level is ConvictionLevel.PURSUE
