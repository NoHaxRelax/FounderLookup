"""Deterministic tests for the v0 three-axis screening rubrics.

The suite pins the independence and fairness invariants task 3.4 must hold: the three
axes are never averaged or blended, absence yields UNKNOWN (never a negative rating),
thin coverage never produces WEAK or BEAR, confidence stays in [0, 1] and rises with
coverage and agreement, one axis never leaks into another, and every assessment carries
an explicit rubric version.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from founderlookup.domain.assessment import (
    FounderAxisAssessment,
    FounderAxisRating,
    IdeaVsMarketAxisAssessment,
    IdeaVsMarketAxisRating,
    IndependentAxes,
    MarketAxisAssessment,
    MarketAxisRating,
    Trend,
)
from founderlookup.domain.common import (
    KnowledgeAlternative,
    KnowledgeState,
    KnowledgeValue,
)
from founderlookup.domain.scoring import CoverageLevel, CoverageSummary
from founderlookup.screening.axes import (
    AXIS_RUBRIC_VERSION,
    FOUNDER_POSITION_TO_RATING,
    IDEA_VS_MARKET_POSITION_TO_RATING,
    MARKET_POSITION_TO_RATING,
    MIN_TREND_OBSERVATIONS,
    AxisPosition,
    AxisSignal,
    SignalReading,
    TrendPoint,
    assemble_independent_axes,
    assess_founder_axis,
    assess_idea_vs_market_axis,
    assess_market_axis,
)

NOW = datetime(2026, 7, 19, 10, 0, tzinfo=UTC)


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def _reading(value: SignalReading | None) -> KnowledgeValue[SignalReading]:
    """A known reading, or an explicit unknown when value is None."""
    if value is None:
        return KnowledgeValue[SignalReading].unknown("not assessed")
    return KnowledgeValue[SignalReading].known(value)


def _signal(
    key: str,
    value: SignalReading | None,
    *,
    claim_ids: tuple[str, ...] = (),
) -> AxisSignal:
    return AxisSignal(key=key, reading=_reading(value), claim_ids=claim_ids)


def _signals(*values: SignalReading | None) -> list[AxisSignal]:
    """A list of signals with generated keys, one per reading."""
    return [_signal(f"signal-{index}", value) for index, value in enumerate(values)]


def _conflicted_reading() -> KnowledgeValue[SignalReading]:
    return KnowledgeValue[SignalReading].conflicted(
        "sources disagree on this signal",
        (
            KnowledgeAlternative[SignalReading](
                value=SignalReading.STRONG_POSITIVE, evidence_ids=("evidence:s",)
            ),
            KnowledgeAlternative[SignalReading](
                value=SignalReading.STRONG_NEGATIVE, evidence_ids=("evidence:w",)
            ),
        ),
    )


def _non_known_readings() -> list[KnowledgeValue[SignalReading]]:
    return [
        KnowledgeValue[SignalReading].unknown("not assessed"),
        KnowledgeValue[SignalReading].not_disclosed("subject withheld it"),
        KnowledgeValue[SignalReading].not_applicable("not applicable here"),
        _conflicted_reading(),
    ]


def _coverage(
    level: CoverageLevel,
    *,
    source_count: int = 2,
    conflicted_fields: tuple[str, ...] = (),
) -> CoverageSummary:
    return CoverageSummary(
        level=level,
        source_count=source_count,
        artifact_count=source_count,
        evidence_count=source_count,
        conflicted_fields=conflicted_fields,
        freshest_evidence_at=KnowledgeValue[datetime].known(NOW),
    )


def _founder(
    signals: list[AxisSignal],
    coverage: CoverageSummary,
    *,
    trend_points: tuple[TrendPoint, ...] = (),
) -> FounderAxisAssessment:
    return assess_founder_axis(
        signals,
        coverage=coverage,
        assessment_id="assessment:1",
        assessment_version_id="assessment-version:1",
        trend_points=trend_points,
    )


def _market(
    signals: list[AxisSignal],
    coverage: CoverageSummary,
    *,
    trend_points: tuple[TrendPoint, ...] = (),
) -> MarketAxisAssessment:
    return assess_market_axis(
        signals,
        coverage=coverage,
        assessment_id="assessment:1",
        assessment_version_id="assessment-version:1",
        trend_points=trend_points,
    )


def _idea(
    signals: list[AxisSignal],
    coverage: CoverageSummary,
    *,
    trend_points: tuple[TrendPoint, ...] = (),
) -> IdeaVsMarketAxisAssessment:
    return assess_idea_vs_market_axis(
        signals,
        coverage=coverage,
        assessment_id="assessment:1",
        assessment_version_id="assessment-version:1",
        trend_points=trend_points,
    )


def _point(day: int, position: AxisPosition) -> TrendPoint:
    """A dated axis-position observation on the given July 2026 day (UTC)."""
    return TrendPoint(observed_at=datetime(2026, 7, day, 10, 0, tzinfo=UTC), position=position)


# ======================================================================================
# Absence is UNKNOWN, never negative
# ======================================================================================


def test_no_signals_is_unknown_with_unknown_confidence() -> None:
    for assessment in (
        _founder([], _coverage(CoverageLevel.LOW)),
        _market([], _coverage(CoverageLevel.LOW)),
        _idea([], _coverage(CoverageLevel.LOW)),
    ):
        assert assessment.rating.value == "unknown"
        # Nothing assessable: confidence is an explicit unknown, not a fabricated number.
        assert assessment.confidence.state is KnowledgeState.UNKNOWN
        assert assessment.confidence.value is None
        assert assessment.rubric_version == AXIS_RUBRIC_VERSION
        assert assessment.open_questions  # an open question explains the gap


def test_all_unknown_signals_are_unknown_never_negative() -> None:
    signals = _signals(None, None, None)
    assert _founder(signals, _coverage(CoverageLevel.HIGH)).rating is FounderAxisRating.UNKNOWN
    assert _market(signals, _coverage(CoverageLevel.HIGH)).rating is MarketAxisRating.UNKNOWN
    idea = _idea(signals, _coverage(CoverageLevel.HIGH))
    assert idea.rating is IdeaVsMarketAxisRating.UNKNOWN


def test_every_non_known_state_contributes_nothing_and_never_goes_negative() -> None:
    # Each non-known state, even carrying a would-be negative intent, must be treated as
    # absent: the axis stays UNKNOWN with unknown confidence, never BEAR or WEAK.
    for reading in _non_known_readings():
        signals = [AxisSignal(key=f"signal-{index}", reading=reading) for index in range(3)]
        market = _market(signals, _coverage(CoverageLevel.HIGH))
        assert market.rating is MarketAxisRating.UNKNOWN
        assert market.confidence.state is KnowledgeState.UNKNOWN


def test_thin_coverage_with_negative_signal_is_unknown_not_bear() -> None:
    # A single present negative read under rich coverage is still too thin to rate: it
    # must yield UNKNOWN, never BEAR. Missing history never produces a bad rating.
    signals = _signals(SignalReading.STRONG_NEGATIVE)
    assert _market(signals, _coverage(CoverageLevel.HIGH)).rating is MarketAxisRating.UNKNOWN


def test_low_coverage_needs_more_signals_than_medium() -> None:
    two_negatives = _signals(SignalReading.MODERATE_NEGATIVE, SignalReading.MODERATE_NEGATIVE)
    # Under LOW coverage two present reads are too thin -> UNKNOWN (never BEAR).
    assert _market(two_negatives, _coverage(CoverageLevel.LOW)).rating is MarketAxisRating.UNKNOWN
    # Under MEDIUM coverage the same two dominant negatives are enough to rate BEAR.
    assert _market(two_negatives, _coverage(CoverageLevel.MEDIUM)).rating is MarketAxisRating.BEAR


def test_negative_needs_dominant_present_evidence() -> None:
    # Two moderate negatives (net -4, no positive) is a genuine, covered negative read.
    signals = _signals(SignalReading.MODERATE_NEGATIVE, SignalReading.MODERATE_NEGATIVE)
    assert _founder(signals, _coverage(CoverageLevel.MEDIUM)).rating is FounderAxisRating.WEAK
    assert _idea(signals, _coverage(CoverageLevel.MEDIUM)).rating is IdeaVsMarketAxisRating.WEAK


# ======================================================================================
# Positive and mixed positions
# ======================================================================================


def test_clear_positive_reaches_the_positive_pole() -> None:
    signals = _signals(SignalReading.MODERATE_POSITIVE, SignalReading.MODERATE_POSITIVE)
    assert _founder(signals, _coverage(CoverageLevel.MEDIUM)).rating is FounderAxisRating.STRONG
    assert _market(signals, _coverage(CoverageLevel.MEDIUM)).rating is MarketAxisRating.BULLISH
    assert _idea(signals, _coverage(CoverageLevel.MEDIUM)).rating is IdeaVsMarketAxisRating.VIABLE


def test_conflicting_signals_are_mixed_not_a_pole() -> None:
    signals = _signals(SignalReading.STRONG_POSITIVE, SignalReading.STRONG_NEGATIVE)
    assert _founder(signals, _coverage(CoverageLevel.HIGH)).rating is FounderAxisRating.MIXED
    assert _market(signals, _coverage(CoverageLevel.HIGH)).rating is MarketAxisRating.NEUTRAL
    idea = _idea(signals, _coverage(CoverageLevel.HIGH))
    assert idea.rating is IdeaVsMarketAxisRating.PIVOTABLE


def test_meaningful_opposition_downgrades_a_pole_to_mixed() -> None:
    # Strong positive net (+4) but with more than minor opposition -> MIXED, not STRONG.
    signals = _signals(
        SignalReading.STRONG_POSITIVE,
        SignalReading.STRONG_POSITIVE,
        SignalReading.MODERATE_NEGATIVE,
    )
    assert _founder(signals, _coverage(CoverageLevel.HIGH)).rating is FounderAxisRating.MIXED


def test_mild_positive_is_mixed_not_strong() -> None:
    # Two slight positives (net +2) are a lukewarm read, not the strong pole.
    signals = _signals(SignalReading.SLIGHT_POSITIVE, SignalReading.SLIGHT_POSITIVE)
    assert _market(signals, _coverage(CoverageLevel.HIGH)).rating is MarketAxisRating.NEUTRAL


def test_non_known_signal_does_not_disturb_a_positive_read() -> None:
    signals = [
        _signal("shipped", SignalReading.MODERATE_POSITIVE),
        _signal("adopted", SignalReading.MODERATE_POSITIVE),
        _signal("pending", None),
    ]
    founder = _founder(signals, _coverage(CoverageLevel.HIGH))
    assert founder.rating is FounderAxisRating.STRONG
    # The unassessed signal is surfaced as an open question rather than counted.
    assert any("pending" in question for question in founder.open_questions)


# ======================================================================================
# Confidence
# ======================================================================================


def test_confidence_rises_with_coverage() -> None:
    signals = _signals(
        SignalReading.SLIGHT_POSITIVE,
        SignalReading.SLIGHT_POSITIVE,
        SignalReading.SLIGHT_POSITIVE,
    )
    low = _founder(signals, _coverage(CoverageLevel.LOW))
    medium = _founder(signals, _coverage(CoverageLevel.MEDIUM))
    high = _founder(signals, _coverage(CoverageLevel.HIGH))
    # Same STRONG position at every coverage level; only confidence changes.
    assert low.rating is medium.rating is high.rating is FounderAxisRating.STRONG
    assert low.confidence.value == 0.3
    assert medium.confidence.value == 0.65
    assert high.confidence.value == 1.0


def test_conflict_lowers_confidence() -> None:
    agreeing = _signals(SignalReading.MODERATE_POSITIVE, SignalReading.MODERATE_POSITIVE)
    conflicting = _signals(SignalReading.STRONG_POSITIVE, SignalReading.STRONG_NEGATIVE)
    high = _coverage(CoverageLevel.HIGH)
    assert _founder(agreeing, high).confidence.value == 0.67
    # A perfectly split read is genuinely more uncertain; confidence is lower.
    assert _founder(conflicting, high).confidence.value == 0.33


def test_conflicted_coverage_fields_lower_confidence() -> None:
    signals = _signals(SignalReading.MODERATE_POSITIVE, SignalReading.MODERATE_POSITIVE)
    clean = _founder(signals, _coverage(CoverageLevel.HIGH))
    conflicted = _founder(signals, _coverage(CoverageLevel.HIGH, conflicted_fields=("tam",)))
    assert clean.confidence.value == 0.67
    assert conflicted.confidence.value == 0.47
    assert conflicted.confidence.value < clean.confidence.value


def test_thin_coverage_unknown_carries_low_but_known_confidence() -> None:
    # One present read under LOW coverage is too thin to rate (UNKNOWN), but we still
    # have a little evidence, so confidence is a low known number, not an unknown.
    signals = _signals(SignalReading.STRONG_POSITIVE)
    founder = _founder(signals, _coverage(CoverageLevel.LOW))
    assert founder.rating is FounderAxisRating.UNKNOWN
    assert founder.confidence.state is KnowledgeState.KNOWN
    assert founder.confidence.value == 0.1


def test_confidence_never_leaves_the_unit_interval() -> None:
    signals = _signals(
        SignalReading.STRONG_POSITIVE,
        SignalReading.STRONG_POSITIVE,
        SignalReading.STRONG_POSITIVE,
    )
    value = _market(signals, _coverage(CoverageLevel.HIGH)).confidence.value
    assert value is not None
    assert 0.0 <= value <= 1.0


# ======================================================================================
# Claim routing and the non-overlap contract
# ======================================================================================


def test_present_signals_route_claims_by_direction() -> None:
    signals = [
        _signal("positive", SignalReading.STRONG_POSITIVE, claim_ids=("claim:a", "claim:b")),
        _signal("negative", SignalReading.STRONG_NEGATIVE, claim_ids=("claim:c",)),
        _signal("neutral", SignalReading.NEUTRAL, claim_ids=("claim:d",)),
    ]
    founder = _founder(signals, _coverage(CoverageLevel.HIGH))
    assert founder.supporting_claim_ids == ("claim:a", "claim:b")
    assert founder.counter_claim_ids == ("claim:c",)
    # A neutral read cites context but routes to neither pole.
    assert "claim:d" not in founder.supporting_claim_ids
    assert "claim:d" not in founder.counter_claim_ids


def test_claim_on_both_poles_is_rejected() -> None:
    signals = [
        _signal("positive", SignalReading.MODERATE_POSITIVE, claim_ids=("claim:x",)),
        _signal("negative", SignalReading.MODERATE_NEGATIVE, claim_ids=("claim:x",)),
    ]
    with pytest.raises(ValueError, match="cannot both support and counter"):
        _founder(signals, _coverage(CoverageLevel.HIGH))


def test_duplicate_claim_ids_are_deduplicated_within_a_pole() -> None:
    signals = [
        _signal("a", SignalReading.MODERATE_POSITIVE, claim_ids=("claim:a", "claim:a")),
        _signal("b", SignalReading.MODERATE_POSITIVE, claim_ids=("claim:a", "claim:b")),
    ]
    founder = _founder(signals, _coverage(CoverageLevel.HIGH))
    assert founder.supporting_claim_ids == ("claim:a", "claim:b")


# ======================================================================================
# Trend, open questions, determinism, and versioning
# ======================================================================================


def test_trend_is_unknown_without_enough_dated_history() -> None:
    signals = _signals(SignalReading.MODERATE_POSITIVE, SignalReading.MODERATE_POSITIVE)
    coverage = _coverage(CoverageLevel.HIGH)
    # No history at all, and a single dated observation, are both too thin for a trend.
    assert _founder(signals, coverage).trend is Trend.UNKNOWN
    one_point = (_point(1, AxisPosition.MIXED),)
    assert _founder(signals, coverage, trend_points=one_point).trend is Trend.UNKNOWN
    assert MIN_TREND_OBSERVATIONS == 2


def test_trend_is_derived_from_dated_positions() -> None:
    signals = _signals(SignalReading.MODERATE_POSITIVE, SignalReading.MODERATE_POSITIVE)
    coverage = _coverage(CoverageLevel.HIGH)
    rising = (_point(1, AxisPosition.NEGATIVE), _point(5, AxisPosition.STRONG_POSITIVE))
    falling = (_point(1, AxisPosition.STRONG_POSITIVE), _point(5, AxisPosition.MIXED))
    flat = (_point(1, AxisPosition.MIXED), _point(5, AxisPosition.MIXED))
    assert _founder(signals, coverage, trend_points=rising).trend is Trend.IMPROVING
    assert _market(signals, coverage, trend_points=falling).trend is Trend.DECLINING
    assert _idea(signals, coverage, trend_points=flat).trend is Trend.STABLE


def test_trend_ignores_unknown_positions_and_input_order() -> None:
    signals = _signals(SignalReading.MODERATE_POSITIVE, SignalReading.MODERATE_POSITIVE)
    coverage = _coverage(CoverageLevel.HIGH)
    # Unknown positions carry no direction; only the two directional points count, and the
    # latest-by-date (day 5, STRONG_POSITIVE) beats the earliest (day 1, NEGATIVE).
    points = (
        _point(5, AxisPosition.STRONG_POSITIVE),
        _point(3, AxisPosition.UNKNOWN),
        _point(1, AxisPosition.NEGATIVE),
    )
    assert _founder(signals, coverage, trend_points=points).trend is Trend.IMPROVING
    # A lone directional point among unknowns is still too thin to establish a trend.
    thin = (_point(1, AxisPosition.UNKNOWN), _point(5, AxisPosition.STRONG_POSITIVE))
    assert _founder(signals, coverage, trend_points=thin).trend is Trend.UNKNOWN


def test_low_coverage_never_condemns_even_with_many_negatives() -> None:
    # Three present strong-negative reads clear the LOW-coverage signal floor, so the raw
    # position would be NEGATIVE; the strict coverage gate downgrades it to UNKNOWN. Thin
    # coverage can never yield WEAK or BEAR no matter how many present negatives exist.
    signals = _signals(
        SignalReading.STRONG_NEGATIVE,
        SignalReading.STRONG_NEGATIVE,
        SignalReading.STRONG_NEGATIVE,
    )
    assert _market(signals, _coverage(CoverageLevel.LOW)).rating is MarketAxisRating.UNKNOWN
    assert _founder(signals, _coverage(CoverageLevel.LOW)).rating is FounderAxisRating.UNKNOWN
    idea = _idea(signals, _coverage(CoverageLevel.LOW))
    assert idea.rating is IdeaVsMarketAxisRating.UNKNOWN
    # The same three dominant negatives under MEDIUM coverage are a genuine negative read.
    assert _market(signals, _coverage(CoverageLevel.MEDIUM)).rating is MarketAxisRating.BEAR


def test_open_questions_list_unknown_signals_and_conflicts() -> None:
    signals = [
        _signal("assessed", SignalReading.MODERATE_POSITIVE),
        _signal("missing", None),
    ]
    market = _market(signals, _coverage(CoverageLevel.HIGH, conflicted_fields=("cac",)))
    joined = " ".join(market.open_questions)
    assert "missing" in joined
    assert "cac" in joined


def test_axes_are_deterministic() -> None:
    signals = _signals(SignalReading.STRONG_POSITIVE, SignalReading.SLIGHT_NEGATIVE)
    coverage = _coverage(CoverageLevel.MEDIUM)
    assert _founder(signals, coverage) == _founder(signals, coverage)
    assert _market(signals, coverage) == _market(signals, coverage)
    assert _idea(signals, coverage) == _idea(signals, coverage)


@pytest.mark.parametrize(
    ("mapping", "expected"),
    [
        (FOUNDER_POSITION_TO_RATING, {"strong", "mixed", "weak", "unknown"}),
        (MARKET_POSITION_TO_RATING, {"bullish", "neutral", "bear", "unknown"}),
        (IDEA_VS_MARKET_POSITION_TO_RATING, {"viable", "pivotable", "weak", "unknown"}),
    ],
)
def test_every_position_maps_to_its_axis_vocabulary(
    mapping: dict[AxisPosition, object], expected: set[str]
) -> None:
    # Every one of the four positions is mapped, and to that axis's own vocabulary.
    assert set(mapping) == set(AxisPosition)
    assert {rating.value for rating in mapping.values()} == expected  # type: ignore[attr-defined]


# ======================================================================================
# Independence: no averaging, no blended score, no leakage
# ======================================================================================


def test_assemble_bundles_without_blending() -> None:
    founder = _founder(
        _signals(SignalReading.MODERATE_POSITIVE, SignalReading.MODERATE_POSITIVE),
        _coverage(CoverageLevel.HIGH),
    )
    market = _market(
        _signals(SignalReading.MODERATE_NEGATIVE, SignalReading.MODERATE_NEGATIVE),
        _coverage(CoverageLevel.MEDIUM),
    )
    idea = _idea([], _coverage(CoverageLevel.LOW))

    axes = assemble_independent_axes(founder=founder, market=market, idea_vs_market=idea)
    assert isinstance(axes, IndependentAxes)
    # Each axis is reported on its own; the assemble step performs no arithmetic.
    assert axes.founder.rating is FounderAxisRating.STRONG
    assert axes.market.rating is MarketAxisRating.BEAR
    assert axes.idea_vs_market.rating is IdeaVsMarketAxisRating.UNKNOWN
    assert axes.founder is founder
    assert axes.market is market
    assert axes.idea_vs_market is idea


def test_independent_axes_has_no_aggregate_field() -> None:
    # The independence principle is structural: there is no blended field to read.
    assert set(IndependentAxes.model_fields) == {"founder", "market", "idea_vs_market"}


def test_one_axis_does_not_leak_into_another() -> None:
    coverage = _coverage(CoverageLevel.HIGH)
    positive = _signals(SignalReading.MODERATE_POSITIVE, SignalReading.MODERATE_POSITIVE)
    negative = _signals(SignalReading.MODERATE_NEGATIVE, SignalReading.MODERATE_NEGATIVE)

    # The founder axis is identical whether the market axis is strong or weak: it reads
    # only its own signals, so a different market input cannot move it.
    founder_alone = _founder(positive, coverage)
    _market(positive, coverage)
    founder_again = _founder(positive, coverage)
    assert founder_alone == founder_again
    # And the market axis over its own negative signals is BEAR regardless.
    assert _market(negative, coverage).rating is MarketAxisRating.BEAR
