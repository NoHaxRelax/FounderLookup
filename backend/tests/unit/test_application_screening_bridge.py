"""Application-level integration tests for the deterministic Data/ML bridge."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count

from founderlookup.application.models import (
    ThesisCriterion,
    ThesisCriterionMode,
    ThesisDraft,
)
from founderlookup.application.screening_bridge import (
    ConfidenceInputs,
    DeterministicScreeningBridge,
    FounderSignalObservation,
    ScreeningSignalBundle,
)
from founderlookup.application.service import FakeVCBrainService
from founderlookup.domain.assessment import (
    AssessmentEnvelope,
    FounderAxisRating,
    IdeaVsMarketAxisRating,
    MarketAxisRating,
    RecommendationAction,
)
from founderlookup.domain.common import (
    KnowledgeState,
    KnowledgeValue,
    ScalarValue,
    VersionComponent,
)
from founderlookup.domain.query import QueryOperator, UnknownValuePolicy
from founderlookup.domain.scoring import CoverageLevel, CoverageSummary
from founderlookup.screening.axes import AXIS_RUBRIC_VERSION, AxisSignal, SignalReading
from founderlookup.screening.founder_reads import EvidenceGrade
from founderlookup.screening.rubrics import (
    FOUNDER_SCORE_BASELINE,
    FOUNDER_SCORE_POLICY_VERSION,
    ContributionTier,
)

NOW = datetime(2026, 7, 19, 12, tzinfo=UTC)


def _service(bridge: DeterministicScreeningBridge) -> FakeVCBrainService:
    identifiers = count(1)
    return FakeVCBrainService(
        clock=lambda: NOW,
        id_factory=lambda: f"bridge-id-{next(identifiers):04d}",
        capability_pepper=b"bridge-test-pepper" * 2,
        screening_bridge=bridge,
    )


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


def _draft() -> ThesisDraft:
    no_preference = _criterion(ThesisCriterionMode.NO_PREFERENCE)
    return ThesisDraft(
        sector=_criterion(
            ThesisCriterionMode.SCORED_PREFERENCE,
            operator=QueryOperator.CONTAINS,
            values=("AI infrastructure",),
        ),
        stage=_criterion(
            ThesisCriterionMode.HARD_CONSTRAINT,
            operator=QueryOperator.ANY_OF,
            values=("pre_seed", "seed"),
        ),
        geography=no_preference,
        check_size=no_preference,
        ownership_target=no_preference,
        risk_appetite=no_preference,
    )


def _coverage(
    level: CoverageLevel,
    *,
    source_count: int,
    artifact_count: int,
    evidence_count: int,
) -> CoverageSummary:
    return CoverageSummary(
        level=level,
        source_count=source_count,
        artifact_count=artifact_count,
        evidence_count=evidence_count,
        freshest_evidence_at=(
            KnowledgeValue[datetime].known(NOW)
            if evidence_count
            else KnowledgeValue[datetime].unknown("no canonical Evidence is registered")
        ),
    )


def _founder_signal(
    factor_key: str,
    *,
    known: bool = True,
    evidence_id: str = "evidence-founder-01",
) -> FounderSignalObservation:
    observed_value = (
        KnowledgeValue[ScalarValue].known("source-backed observation")
        if known
        else KnowledgeValue[ScalarValue].unknown("history not observed")
    )
    return FounderSignalObservation(
        factor_key=factor_key,
        tier=ContributionTier.FULL,
        grade=EvidenceGrade.OUTCOME_BACKED,
        observed_value=observed_value,
        rationale="The canonical Evidence supports this explicit signal.",
        evidence_ids=((evidence_id,) if known else ()),
    )


def _positive_axis_signals(prefix: str) -> tuple[AxisSignal, ...]:
    return (
        AxisSignal(
            key=f"{prefix}-primary",
            reading=KnowledgeValue[SignalReading].known(SignalReading.MODERATE_POSITIVE),
            rationale="Primary source-backed directional read.",
            claim_ids=(f"claim-{prefix}-01",),
        ),
        AxisSignal(
            key=f"{prefix}-corroboration",
            reading=KnowledgeValue[SignalReading].known(SignalReading.MODERATE_POSITIVE),
            rationale="Independent source-backed corroboration.",
            claim_ids=(f"claim-{prefix}-02",),
        ),
    )


def _component_version(envelope: AssessmentEnvelope, component: VersionComponent) -> str:
    return next(
        item.version_id for item in envelope.versions.components if item.component is component
    )


def test_known_founder_preliminary_assessment_uses_real_rubrics_and_diagnostics() -> None:
    bridge = DeterministicScreeningBridge()
    service = _service(bridge)
    service.create_thesis(_draft(), actor_id="investor-01")
    candidate = service.seed_outbound_candidate(
        company_name="Evidence Systems",
        founder_id="founder-01",
    )
    bridge.register(
        candidate.outbound_candidate_id,
        ScreeningSignalBundle(
            coverage=_coverage(
                CoverageLevel.HIGH,
                source_count=3,
                artifact_count=3,
                evidence_count=8,
            ),
            founder_signals=(
                _founder_signal("shipped_adopted_work", evidence_id="evidence-founder-01"),
                _founder_signal(
                    "corroborated_domain_experience",
                    evidence_id="evidence-founder-02",
                ),
            ),
            founder_axis_signals=_positive_axis_signals("founder"),
            market_axis_signals=_positive_axis_signals("market"),
            idea_vs_market_axis_signals=_positive_axis_signals("idea"),
            confidence_inputs=ConfidenceInputs(
                reasoned_samples=(78.0, 80.0, 82.0, 81.0, 79.0),
                snap_score=75.0,
                coverage_level=1.0,
            ),
        ),
    )

    accepted = service.start_preliminary_assessment(candidate.outbound_candidate_id)
    assessment = service.list_candidates().items[0].preliminary_assessment

    assert assessment is not None
    assert accepted.run.versions == assessment.versions
    assert assessment.founder_score.state is KnowledgeState.KNOWN
    founder_score = assessment.founder_score.value
    assert founder_score is not None
    assert founder_score.founder_id == "founder-01"
    assert founder_score.score_policy_version == FOUNDER_SCORE_POLICY_VERSION
    assert founder_score.score > FOUNDER_SCORE_BASELINE
    assert founder_score.provisional is False
    assert assessment.axes.founder.rating is FounderAxisRating.STRONG
    assert assessment.axes.market.rating is MarketAxisRating.BULLISH
    assert assessment.axes.idea_vs_market.rating is IdeaVsMarketAxisRating.VIABLE
    assert assessment.axes.founder.rubric_version == AXIS_RUBRIC_VERSION
    assert assessment.axes.market.rubric_version == AXIS_RUBRIC_VERSION
    assert assessment.axes.idea_vs_market.rubric_version == AXIS_RUBRIC_VERSION
    assert (
        _component_version(assessment, VersionComponent.FOUNDER_SCORE)
        == FOUNDER_SCORE_POLICY_VERSION
    )
    assert _component_version(assessment, VersionComponent.AXIS_RUBRIC) == AXIS_RUBRIC_VERSION

    diagnostics = bridge.diagnostics_for(candidate.outbound_candidate_id)
    assert diagnostics is not None
    assert diagnostics.builder_signal is not None
    assert diagnostics.fundability is not None
    assert diagnostics.builder_fundability_gap is not None
    assert diagnostics.confidence is not None
    assert diagnostics.confidence.abstained is False
    assert diagnostics.confidence.point == 80.0

    envelope_fields = AssessmentEnvelope.model_fields
    dumped = assessment.model_dump(mode="json")
    for internal_name in (
        "builder_signal",
        "fundability",
        "builder_fundability_gap",
        "confidence",
    ):
        assert internal_name not in envelope_fields
        assert internal_name not in dumped


def test_missing_founder_history_never_decrements_score_or_internal_reads() -> None:
    bridge = DeterministicScreeningBridge()
    coverage = _coverage(
        CoverageLevel.LOW,
        source_count=1,
        artifact_count=1,
        evidence_count=1,
    )
    observed = _founder_signal("shipped_adopted_work")
    missing_history = _founder_signal("verified_negative_signal", known=False)
    bridge.register(
        "subject-observed-only",
        ScreeningSignalBundle(coverage=coverage, founder_signals=(observed,)),
    )
    bridge.register(
        "subject-with-missing-history",
        ScreeningSignalBundle(
            coverage=coverage,
            founder_signals=(observed, missing_history),
        ),
    )
    identifiers = count(1)

    def id_factory() -> str:
        return f"projection-id-{next(identifiers):04d}"

    observed_projection = bridge.project(
        "subject-observed-only",
        founder_identity=KnowledgeValue[str].known("founder-01"),
        as_of=NOW,
        id_factory=id_factory,
    )
    missing_projection = bridge.project(
        "subject-with-missing-history",
        founder_identity=KnowledgeValue[str].known("founder-01"),
        as_of=NOW,
        id_factory=id_factory,
    )

    assert observed_projection is not None
    assert missing_projection is not None
    observed_score = observed_projection.founder_score.value
    missing_score = missing_projection.founder_score.value
    assert observed_score is not None
    assert missing_score is not None
    assert missing_score.score == observed_score.score
    assert missing_score.score >= FOUNDER_SCORE_BASELINE
    assert missing_score.provisional is True
    observed_diagnostics = bridge.diagnostics_for("subject-observed-only")
    missing_diagnostics = bridge.diagnostics_for("subject-with-missing-history")
    assert observed_diagnostics is not None
    assert missing_diagnostics is not None
    assert observed_diagnostics.builder_signal is not None
    assert missing_diagnostics.builder_signal is not None
    assert observed_diagnostics.fundability is not None
    assert missing_diagnostics.fundability is not None
    assert missing_diagnostics.builder_signal.score == observed_diagnostics.builder_signal.score
    assert missing_diagnostics.fundability.score == observed_diagnostics.fundability.score


def test_artifact_presence_without_registered_signals_stays_unknown() -> None:
    bridge = DeterministicScreeningBridge()
    service = _service(bridge)
    service.create_thesis(_draft(), actor_id="investor-01")
    candidate = service.seed_outbound_candidate(
        company_name="Artifact Only Systems",
        source_artifact_ids=("source-artifact-without-observations",),
    )
    bridge.register(
        candidate.outbound_candidate_id,
        ScreeningSignalBundle(
            coverage=_coverage(
                CoverageLevel.LOW,
                source_count=0,
                artifact_count=0,
                evidence_count=0,
            )
        ),
    )

    service.start_preliminary_assessment(candidate.outbound_candidate_id)
    assessed = service.list_candidates().items[0]
    assessment = assessed.preliminary_assessment

    assert assessment is not None
    assert assessment.coverage.artifact_count == 0
    assert assessment.coverage.evidence_count == 0
    assert assessment.founder_score.state is KnowledgeState.UNKNOWN
    assert assessment.founder_score.reason == "founder_identity_unresolved"
    assert assessment.axes.founder.rating is FounderAxisRating.UNKNOWN
    assert assessment.axes.market.rating is MarketAxisRating.UNKNOWN
    assert assessment.axes.idea_vs_market.rating is IdeaVsMarketAxisRating.UNKNOWN
    assert assessment.axes.founder.confidence.state is KnowledgeState.UNKNOWN
    assert assessment.recommendation is not None
    assert assessment.recommendation.action is RecommendationAction.NEEDS_INFORMATION
    diagnostics = bridge.diagnostics_for(candidate.outbound_candidate_id)
    assert diagnostics is not None
    assert diagnostics.builder_signal is None
    assert diagnostics.fundability is None
    assert diagnostics.builder_fundability_gap is None
    assert diagnostics.confidence is None


def test_full_assessment_uses_real_axes_but_unresolved_identity_blocks_founder_reads() -> None:
    bridge = DeterministicScreeningBridge()
    service = _service(bridge)
    service.create_thesis(_draft(), actor_id="investor-01")
    service.accept_application(
        company_name="Unresolved Founder Systems",
        display_name="deck.pdf",
        media_type="application/pdf",
        deck_content=b"%PDF-1.7 fictional",
        idempotency_key="full-bridge-assessment",
    )
    opportunity_id = service.list_opportunities().items[0].opportunity_id
    bridge.register(
        opportunity_id,
        ScreeningSignalBundle(
            coverage=_coverage(
                CoverageLevel.HIGH,
                source_count=2,
                artifact_count=2,
                evidence_count=6,
            ),
            founder_signals=(_founder_signal("shipped_adopted_work"),),
            founder_axis_signals=_positive_axis_signals("founder-unresolved"),
            market_axis_signals=_positive_axis_signals("market-full"),
            idea_vs_market_axis_signals=_positive_axis_signals("idea-full"),
            confidence_inputs=ConfidenceInputs(
                reasoned_samples=(60.0, 62.0, 61.0),
                coverage_level=0.9,
            ),
        ),
    )

    service.start_screening(opportunity_id)
    assessment = service.get_opportunity(opportunity_id).latest_assessment

    assert assessment is not None
    assert assessment.identity.mode == "full"
    assert assessment.founder_score.state is KnowledgeState.UNKNOWN
    assert assessment.founder_score.reason == "founder_identity_unresolved"
    assert assessment.axes.founder.rating is FounderAxisRating.UNKNOWN
    assert assessment.axes.market.rating is MarketAxisRating.BULLISH
    assert assessment.axes.idea_vs_market.rating is IdeaVsMarketAxisRating.VIABLE
    assert (
        _component_version(assessment, VersionComponent.FOUNDER_SCORE)
        == FOUNDER_SCORE_POLICY_VERSION
    )
    assert _component_version(assessment, VersionComponent.AXIS_RUBRIC) == AXIS_RUBRIC_VERSION
    assert assessment.decision_readiness is not None
    assert assessment.decision_readiness.status.value == "blocked"
    diagnostics = bridge.diagnostics_for(opportunity_id)
    assert diagnostics is not None
    assert diagnostics.builder_signal is None
    assert diagnostics.fundability is None
    assert diagnostics.builder_fundability_gap is None
    assert diagnostics.confidence is not None
