"""Deterministic tests for the conviction threshold and preliminary envelope assembler.

The suite pins the fairness invariants task 3.3 must hold: conviction is never an average
of the three axes, a hard-negative on any axis is always surfaced and blocks PURSUE,
absence (an UNKNOWN axis, LOW coverage, or a provisional/unknown founder score) routes to
GATHER_MORE and never to a hard PASS, missing history never lowers conviction below what
present evidence supports, and the assembled preliminary envelope satisfies every frozen
validator.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from founderlookup.domain.assessment import (
    FounderAxisAssessment,
    FounderAxisRating,
    IdeaVsMarketAxisAssessment,
    IdeaVsMarketAxisRating,
    IndependentAxes,
    MarketAxisAssessment,
    MarketAxisRating,
    PreliminaryAssessmentIdentity,
    Trend,
)
from founderlookup.domain.common import KnowledgeValue, VersionManifest
from founderlookup.domain.scoring import (
    CoverageLevel,
    CoverageSummary,
    FounderScoreSnapshot,
    QualitativeUncertainty,
)
from founderlookup.screening.preliminary import (
    CONVICTION_POLICY_VERSION,
    ConvictionLevel,
    assemble_preliminary_assessment,
    decide_conviction,
    evaluate_preliminary_candidate,
)

NOW = datetime(2026, 7, 19, 10, 0, tzinfo=UTC)


# --------------------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------------------


def _conf(value: float | None) -> KnowledgeValue[float]:
    if value is None:
        return KnowledgeValue[float].unknown("not assessed")
    return KnowledgeValue[float].known(value)


def _coverage(level: CoverageLevel) -> CoverageSummary:
    return CoverageSummary(
        level=level,
        source_count=3,
        artifact_count=3,
        evidence_count=3,
        freshest_evidence_at=KnowledgeValue[datetime].known(NOW),
    )


def _axes(
    *,
    founder: FounderAxisRating,
    market: MarketAxisRating,
    idea: IdeaVsMarketAxisRating,
    founder_conf: float | None = 0.7,
    market_conf: float | None = 0.7,
    idea_conf: float | None = 0.7,
    coverage: CoverageSummary | None = None,
) -> IndependentAxes:
    cov = coverage if coverage is not None else _coverage(CoverageLevel.HIGH)
    return IndependentAxes(
        founder=FounderAxisAssessment(
            assessment_id="axis:founder",
            assessment_version_id="axis-version:founder",
            rubric_version="axis-rubric.v0",
            trend=Trend.UNKNOWN,
            confidence=_conf(founder_conf),
            coverage=cov,
            rating=founder,
        ),
        market=MarketAxisAssessment(
            assessment_id="axis:market",
            assessment_version_id="axis-version:market",
            rubric_version="axis-rubric.v0",
            trend=Trend.UNKNOWN,
            confidence=_conf(market_conf),
            coverage=cov,
            rating=market,
        ),
        idea_vs_market=IdeaVsMarketAxisAssessment(
            assessment_id="axis:idea",
            assessment_version_id="axis-version:idea",
            rubric_version="axis-rubric.v0",
            trend=Trend.UNKNOWN,
            confidence=_conf(idea_conf),
            coverage=cov,
            rating=idea,
        ),
    )


def _all_positive(coverage: CoverageSummary | None = None) -> IndependentAxes:
    return _axes(
        founder=FounderAxisRating.STRONG,
        market=MarketAxisRating.BULLISH,
        idea=IdeaVsMarketAxisRating.VIABLE,
        coverage=coverage,
    )


def _all_negative(coverage: CoverageSummary | None = None) -> IndependentAxes:
    return _axes(
        founder=FounderAxisRating.WEAK,
        market=MarketAxisRating.BEAR,
        idea=IdeaVsMarketAxisRating.WEAK,
        coverage=coverage,
    )


def _all_unknown() -> IndependentAxes:
    return _axes(
        founder=FounderAxisRating.UNKNOWN,
        market=MarketAxisRating.UNKNOWN,
        idea=IdeaVsMarketAxisRating.UNKNOWN,
        founder_conf=None,
        market_conf=None,
        idea_conf=None,
        coverage=_coverage(CoverageLevel.LOW),
    )


def _founder_score_known(
    founder_id: str = "founder:1",
    *,
    provisional: bool = False,
    score: float = 72.0,
) -> KnowledgeValue[FounderScoreSnapshot]:
    return KnowledgeValue[FounderScoreSnapshot].known(
        FounderScoreSnapshot(
            snapshot_id="snap:1",
            snapshot_version_id="snap-version:1",
            founder_id=founder_id,
            score_policy_version="founder-score-rubric.v0",
            as_of=NOW,
            score=score,
            factors=(),
            coverage=_coverage(CoverageLevel.HIGH),
            uncertainty=QualitativeUncertainty.LOW,
            provisional=provisional,
        )
    )


def _founder_score_unknown() -> KnowledgeValue[FounderScoreSnapshot]:
    return KnowledgeValue[FounderScoreSnapshot].unknown("founder_identity_unresolved")


def _identity(
    *,
    founder_id: KnowledgeValue[str] | None = None,
) -> PreliminaryAssessmentIdentity:
    return PreliminaryAssessmentIdentity(
        outbound_candidate_id="candidate:1",
        founder_id=founder_id if founder_id is not None else KnowledgeValue[str].known("founder:1"),
        company_id=KnowledgeValue[str].known("company:1"),
    )


# --------------------------------------------------------------------------------------
# Conviction threshold: no averaging, hard-negative always surfaced
# --------------------------------------------------------------------------------------


def test_two_positives_and_one_hard_negative_never_pursue() -> None:
    """Two strong axes cannot average away a weak third; the negative blocks pursuit."""
    axes = _axes(
        founder=FounderAxisRating.STRONG,
        market=MarketAxisRating.BULLISH,
        idea=IdeaVsMarketAxisRating.WEAK,
    )
    decision = decide_conviction(
        axes=axes,
        founder_score=_founder_score_known(),
        coverage=_coverage(CoverageLevel.HIGH),
    )
    assert decision.level is ConvictionLevel.HOLD
    assert decision.clears_bar is False
    assert decision.hard_negative_axes == ("idea_vs_market",)
    # The weak axis is named in the rationale, never hidden by the two strong axes.
    assert "idea_vs_market" in decision.rationale
    assert "not averaged" in decision.rationale


def test_third_positive_axis_with_thin_confidence_does_not_demote_pursue() -> None:
    """Monotonicity: two confident positives clear the bar, and adding a positive-but-thin
    third axis must never demote the verdict below what the two confident positives support.
    """
    base = decide_conviction(
        axes=_axes(
            founder=FounderAxisRating.STRONG,
            market=MarketAxisRating.BULLISH,
            idea=IdeaVsMarketAxisRating.UNKNOWN,
            idea_conf=None,
        ),
        founder_score=_founder_score_known(),
        coverage=_coverage(CoverageLevel.HIGH),
    )
    assert base.level is ConvictionLevel.PURSUE

    # Same two confident positives, but the third axis is now POSITIVE with an unknown
    # confidence read. Strictly more favorable information must not drop PURSUE.
    with_unknown_positive = decide_conviction(
        axes=_axes(
            founder=FounderAxisRating.STRONG,
            market=MarketAxisRating.BULLISH,
            idea=IdeaVsMarketAxisRating.VIABLE,
            idea_conf=None,
        ),
        founder_score=_founder_score_known(),
        coverage=_coverage(CoverageLevel.HIGH),
    )
    assert with_unknown_positive.level is ConvictionLevel.PURSUE
    assert with_unknown_positive.clears_bar is True

    # A positive third axis with a KNOWN but below-floor confidence likewise never demotes.
    with_below_floor = decide_conviction(
        axes=_axes(
            founder=FounderAxisRating.STRONG,
            market=MarketAxisRating.BULLISH,
            idea=IdeaVsMarketAxisRating.VIABLE,
            idea_conf=0.20,
        ),
        founder_score=_founder_score_known(),
        coverage=_coverage(CoverageLevel.HIGH),
    )
    assert with_below_floor.level is ConvictionLevel.PURSUE


def test_hard_negative_surfaced_even_when_gathering_more() -> None:
    """A hard-negative alongside an unknown axis is still named while more is requested."""
    axes = _axes(
        founder=FounderAxisRating.WEAK,
        market=MarketAxisRating.BEAR,
        idea=IdeaVsMarketAxisRating.UNKNOWN,
        idea_conf=None,
    )
    decision = decide_conviction(
        axes=axes,
        founder_score=_founder_score_known(),
        coverage=_coverage(CoverageLevel.HIGH),
    )
    assert decision.level is not ConvictionLevel.PASS
    assert decision.level is ConvictionLevel.GATHER_MORE
    assert decision.clears_bar is False
    assert set(decision.hard_negative_axes) == {"founder", "market"}
    assert decision.unknown_axes == ("idea_vs_market",)
    assert "founder=weak" in decision.rationale


def test_all_positive_high_coverage_pursues() -> None:
    decision = decide_conviction(
        axes=_all_positive(),
        founder_score=_founder_score_known(),
        coverage=_coverage(CoverageLevel.HIGH),
    )
    assert decision.level is ConvictionLevel.PURSUE
    assert decision.clears_bar is True
    assert decision.hard_negative_axes == ()


def test_two_positives_one_mixed_still_pursues() -> None:
    axes = _axes(
        founder=FounderAxisRating.STRONG,
        market=MarketAxisRating.BULLISH,
        idea=IdeaVsMarketAxisRating.PIVOTABLE,
    )
    decision = decide_conviction(
        axes=axes,
        founder_score=_founder_score_known(),
        coverage=_coverage(CoverageLevel.HIGH),
    )
    assert decision.level is ConvictionLevel.PURSUE
    assert decision.clears_bar is True


# --------------------------------------------------------------------------------------
# Affirmative cold-start: absence never rejects, never lowers below present evidence
# --------------------------------------------------------------------------------------


def test_all_unknown_routes_to_gather_more_never_pass() -> None:
    decision = decide_conviction(
        axes=_all_unknown(),
        founder_score=_founder_score_unknown(),
        coverage=_coverage(CoverageLevel.LOW),
    )
    assert decision.level is not ConvictionLevel.PASS
    assert decision.level is ConvictionLevel.GATHER_MORE
    assert decision.clears_bar is False
    assert set(decision.unknown_axes) == {"founder", "market", "idea_vs_market"}
    assert decision.evidence_requests  # concrete gaps are requested, not a rejection


def test_missing_axis_and_unknown_founder_do_not_lower_below_present_evidence() -> None:
    """Two strong present axes still pursue while the third axis and founder are unknown."""
    axes = _axes(
        founder=FounderAxisRating.STRONG,
        market=MarketAxisRating.BULLISH,
        idea=IdeaVsMarketAxisRating.UNKNOWN,
        idea_conf=None,
        coverage=_coverage(CoverageLevel.MEDIUM),
    )
    decision = decide_conviction(
        axes=axes,
        founder_score=_founder_score_unknown(),
        coverage=_coverage(CoverageLevel.MEDIUM),
    )
    assert decision.level is ConvictionLevel.PURSUE
    assert decision.clears_bar is True
    assert decision.unknown_axes == ("idea_vs_market",)


def test_low_coverage_positive_read_gathers_more_rather_than_pursues() -> None:
    """Thin coverage withholds pursuit but never rejects; it asks for more evidence."""
    axes = _all_positive(coverage=_coverage(CoverageLevel.LOW))
    decision = decide_conviction(
        axes=_axes(
            founder=FounderAxisRating.STRONG,
            market=MarketAxisRating.BULLISH,
            idea=IdeaVsMarketAxisRating.VIABLE,
            founder_conf=0.30,
            market_conf=0.30,
            idea_conf=0.30,
            coverage=_coverage(CoverageLevel.LOW),
        ),
        founder_score=_founder_score_known(provisional=True),
        coverage=_coverage(CoverageLevel.LOW),
    )
    assert axes.founder.rating is FounderAxisRating.STRONG  # builder sanity
    assert decision.level is not ConvictionLevel.PASS
    assert decision.level is ConvictionLevel.GATHER_MORE
    assert decision.clears_bar is False


def test_provisional_founder_score_blocks_pass_even_with_full_negative() -> None:
    """A provisional founder score is a cold-start signal: negative reads gather more."""
    decision = decide_conviction(
        axes=_all_negative(),
        founder_score=_founder_score_known(provisional=True),
        coverage=_coverage(CoverageLevel.HIGH),
    )
    assert decision.level is not ConvictionLevel.PASS
    assert decision.level is ConvictionLevel.GATHER_MORE
    assert decision.clears_bar is False
    # The hard negatives are still surfaced, never hidden by routing to gather-more.
    assert set(decision.hard_negative_axes) == {"founder", "market", "idea_vs_market"}


def test_unknown_founder_score_blocks_pass_even_with_full_negative() -> None:
    decision = decide_conviction(
        axes=_all_negative(),
        founder_score=_founder_score_unknown(),
        coverage=_coverage(CoverageLevel.HIGH),
    )
    assert decision.level is not ConvictionLevel.PASS
    assert decision.level is ConvictionLevel.GATHER_MORE


# --------------------------------------------------------------------------------------
# PASS is reachable only through a complete, well-covered, uncontested negative read
# --------------------------------------------------------------------------------------


def test_complete_uncontested_negative_reaches_pass() -> None:
    decision = decide_conviction(
        axes=_all_negative(),
        founder_score=_founder_score_known(provisional=False),
        coverage=_coverage(CoverageLevel.HIGH),
    )
    assert decision.level is ConvictionLevel.PASS
    assert decision.clears_bar is False
    assert set(decision.hard_negative_axes) == {"founder", "market", "idea_vs_market"}


def test_negative_with_one_positive_holds_not_passes() -> None:
    """Any positive read blocks a firm reject; the tension is held for a human."""
    axes = _axes(
        founder=FounderAxisRating.STRONG,
        market=MarketAxisRating.BEAR,
        idea=IdeaVsMarketAxisRating.WEAK,
    )
    decision = decide_conviction(
        axes=axes,
        founder_score=_founder_score_known(),
        coverage=_coverage(CoverageLevel.HIGH),
    )
    assert decision.level is not ConvictionLevel.PASS
    assert decision.level is ConvictionLevel.HOLD
    assert decision.clears_bar is False


# --------------------------------------------------------------------------------------
# Determinism and versioning
# --------------------------------------------------------------------------------------


def test_conviction_is_deterministic_and_versioned() -> None:
    axes = _all_positive()
    score = _founder_score_known()
    coverage = _coverage(CoverageLevel.HIGH)
    first = decide_conviction(axes=axes, founder_score=score, coverage=coverage)
    second = decide_conviction(axes=axes, founder_score=score, coverage=coverage)
    assert first == second
    assert first.policy_version == CONVICTION_POLICY_VERSION


def test_only_pursue_clears_the_bar() -> None:
    pursue = decide_conviction(
        axes=_all_positive(),
        founder_score=_founder_score_known(),
        coverage=_coverage(CoverageLevel.HIGH),
    )
    hold = decide_conviction(
        axes=_axes(
            founder=FounderAxisRating.STRONG,
            market=MarketAxisRating.BULLISH,
            idea=IdeaVsMarketAxisRating.WEAK,
        ),
        founder_score=_founder_score_known(),
        coverage=_coverage(CoverageLevel.HIGH),
    )
    passed = decide_conviction(
        axes=_all_negative(),
        founder_score=_founder_score_known(),
        coverage=_coverage(CoverageLevel.HIGH),
    )
    gather = decide_conviction(
        axes=_all_unknown(),
        founder_score=_founder_score_unknown(),
        coverage=_coverage(CoverageLevel.LOW),
    )
    assert pursue.clears_bar is True
    assert hold.clears_bar is False
    assert passed.clears_bar is False
    assert gather.clears_bar is False


# --------------------------------------------------------------------------------------
# Preliminary Assessment Envelope assembly
# --------------------------------------------------------------------------------------


def test_assemble_preliminary_envelope_is_contract_valid() -> None:
    envelope = assemble_preliminary_assessment(
        identity=_identity(founder_id=KnowledgeValue[str].unknown("founder_identity_unresolved")),
        assessment_id="assessment:1",
        assessment_version_id="assessment-version:1",
        versions=VersionManifest(),
        input_snapshot_id="snapshot:1",
        input_snapshot_as_of=NOW,
        coverage=_coverage(CoverageLevel.LOW),
        founder_score=_founder_score_unknown(),
        axes=_all_unknown(),
        claim_ids=("claim:1",),
        evidence_ids=("evidence:1",),
        run_id="run:1",
        created_at=NOW,
    )
    assert envelope.identity.mode == "preliminary"
    assert envelope.identity.origin == "outbound"
    # A preliminary read can never claim full-case readiness or a memo.
    assert envelope.decision_readiness is None
    assert envelope.memo is None
    assert envelope.recommendation is None


def test_assemble_accepts_known_founder_score_matching_identity() -> None:
    envelope = assemble_preliminary_assessment(
        identity=_identity(founder_id=KnowledgeValue[str].known("founder:1")),
        assessment_id="assessment:1",
        assessment_version_id="assessment-version:1",
        versions=VersionManifest(),
        input_snapshot_id="snapshot:1",
        input_snapshot_as_of=NOW,
        coverage=_coverage(CoverageLevel.HIGH),
        founder_score=_founder_score_known("founder:1"),
        axes=_all_positive(),
        claim_ids=(),
        evidence_ids=(),
        run_id="run:1",
        created_at=NOW,
    )
    assert envelope.founder_score.value is not None
    assert envelope.founder_score.value.founder_id == "founder:1"


def test_assemble_rejects_founder_score_that_does_not_match_identity() -> None:
    with pytest.raises(ValidationError, match="founder score must belong to the assessed founder"):
        assemble_preliminary_assessment(
            identity=_identity(founder_id=KnowledgeValue[str].known("founder:1")),
            assessment_id="assessment:1",
            assessment_version_id="assessment-version:1",
            versions=VersionManifest(),
            input_snapshot_id="snapshot:1",
            input_snapshot_as_of=NOW,
            coverage=_coverage(CoverageLevel.HIGH),
            founder_score=_founder_score_known("founder:other"),
            axes=_all_positive(),
            claim_ids=(),
            evidence_ids=(),
            run_id="run:1",
            created_at=NOW,
        )


def test_assemble_rejects_known_score_with_unknown_founder_identity() -> None:
    with pytest.raises(ValidationError, match="founder identity is unknown"):
        assemble_preliminary_assessment(
            identity=_identity(
                founder_id=KnowledgeValue[str].unknown("founder_identity_unresolved")
            ),
            assessment_id="assessment:1",
            assessment_version_id="assessment-version:1",
            versions=VersionManifest(),
            input_snapshot_id="snapshot:1",
            input_snapshot_as_of=NOW,
            coverage=_coverage(CoverageLevel.HIGH),
            founder_score=_founder_score_known("founder:1"),
            axes=_all_positive(),
            claim_ids=(),
            evidence_ids=(),
            run_id="run:1",
            created_at=NOW,
        )


def test_evaluate_returns_both_conviction_and_envelope() -> None:
    outcome = evaluate_preliminary_candidate(
        identity=_identity(founder_id=KnowledgeValue[str].known("founder:1")),
        assessment_id="assessment:1",
        assessment_version_id="assessment-version:1",
        versions=VersionManifest(),
        input_snapshot_id="snapshot:1",
        input_snapshot_as_of=NOW,
        coverage=_coverage(CoverageLevel.HIGH),
        founder_score=_founder_score_known("founder:1"),
        axes=_all_positive(),
        claim_ids=("claim:1",),
        evidence_ids=("evidence:1",),
        run_id="run:1",
        created_at=NOW,
    )
    assert outcome.conviction.level is ConvictionLevel.PURSUE
    assert outcome.conviction.clears_bar is True
    assert outcome.envelope.identity.mode == "preliminary"
    assert outcome.envelope.coverage.level is CoverageLevel.HIGH
    assert outcome.envelope.decision_readiness is None
