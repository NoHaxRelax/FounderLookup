"""Deterministic, versioned v0 rubrics for the three INDEPENDENT screening axes.

This module maps caller-supplied structured signals to the three frozen axis
assessments in :mod:`founderlookup.domain.assessment`:

1. :func:`assess_founder_axis`      -> :class:`FounderAxisAssessment`
   (STRONG / MIXED / WEAK / UNKNOWN)
2. :func:`assess_market_axis`       -> :class:`MarketAxisAssessment`
   (BULLISH / NEUTRAL / BEAR / UNKNOWN)
3. :func:`assess_idea_vs_market_axis` -> :class:`IdeaVsMarketAxisAssessment`
   (VIABLE / PIVOTABLE / WEAK / UNKNOWN)

and :func:`assemble_independent_axes` bundles the three into an
:class:`IndependentAxes`. Everything here is a pure function of its inputs: no I/O,
no live model, no randomness, no hidden state, so identical inputs always yield
identical assessments, and every assessment carries an explicit ``rubric_version``.

Independence is the core product principle
------------------------------------------
The three axes are never averaged, blended, or collapsed into a single number. Each
axis is computed from ONLY its own signals and its own coverage, mapped through its
own vocabulary, and reported on its own. :func:`assemble_independent_axes` performs no
arithmetic; it only groups the three finished assessments. One axis cannot leak into
another because no function ever reads a second axis's signals, and there is no shared
mutable state between calls.

A single internal read, three vocabularies
------------------------------------------
Each axis's signals are reduced ONCE to a coarse, shared :class:`AxisPosition`
(``STRONG_POSITIVE`` / ``MIXED`` / ``NEGATIVE`` / ``UNKNOWN``). That one position is
then mapped to the axis's own four-value rating. Computing the position with shared
logic keeps the three axes consistent, while the per-axis mapping tables keep their
vocabularies distinct.

    AxisPosition        founder   market    idea_vs_market
    STRONG_POSITIVE     STRONG    BULLISH   VIABLE
    MIXED               MIXED     NEUTRAL   PIVOTABLE
    NEGATIVE            WEAK      BEAR      WEAK
    UNKNOWN             UNKNOWN   UNKNOWN   UNKNOWN

Signal shape
------------
Each :class:`AxisSignal` is one coarse directional read toward the axis's positive
pole. The read is a ``KnowledgeValue[SignalReading]`` so an unassessed signal is an
explicit unknown, never a fabricated neutral. The reading's sign is the direction
(which pole the evidence points to) and its word is the strength tier:

    reading                 points
    STRONG_POSITIVE          +3
    MODERATE_POSITIVE        +2
    SLIGHT_POSITIVE          +1
    NEUTRAL                   0
    SLIGHT_NEGATIVE          -1
    MODERATE_NEGATIVE        -2
    STRONG_NEGATIVE          -3

Any non-known reading (unknown, not_disclosed, not_applicable, conflicted) contributes
nothing and is counted only as an unassessed signal, never as evidence toward a pole.

Position thresholds (coarse, documented)
-----------------------------------------
The known readings are summed into ``positive_sum`` (total positive points),
``negative_sum`` (total negative magnitude), and ``net = positive_sum - negative_sum``.

First a sufficiency gate protects against too little signal. A position is asserted only
when there are enough present reads: at least ``MIN_SIGNALS_FOR_POSITION`` (2) under
MEDIUM or HIGH coverage, or ``LOW_COVERAGE_MIN_SIGNALS`` (3) under LOW coverage.
Otherwise the position is ``UNKNOWN``. Absence therefore never produces a directional
read: with no present reads at all the axis is always ``UNKNOWN``.

When the gate passes:

    net >= +3 and negative_sum <= 1   -> STRONG_POSITIVE
    net <= -3 and positive_sum <= 1   -> NEGATIVE
    otherwise                          -> MIXED

Meaningful opposition (more than one point against the lean) always collapses a would-be
pole to ``MIXED``, so genuine conflict is surfaced, not blended.

A second, strict coverage gate then protects the fairness rule that thin coverage may
never condemn: a would-be ``NEGATIVE`` under LOW coverage is downgraded to ``UNKNOWN``.
The positive pole is deliberately left reachable under LOW coverage so a
well-corroborated cold-start read is credited, but the negative pole is withheld until
coverage is at least MEDIUM. Combined with the sufficiency gate this makes the downside
strictly harder to reach than the upside, and it guarantees that LOW coverage can only
ever produce ``UNKNOWN``, ``MIXED``, or ``STRONG_POSITIVE`` (never WEAK or BEAR).

Confidence
----------
``confidence`` is a ``KnowledgeValue[Confidence]`` in ``[0, 1]``. When no signal at all
has been assessed it is an explicit unknown (no fabricated number). Otherwise it is the
product of four monotone sub-factors in ``[0, 1]``, so any weak channel pulls the whole
value down:

    coverage_factor    = {LOW: 0.30, MEDIUM: 0.65, HIGH: 1.00}[coverage.level]
    agreement_factor   = 1 - minority / total   (1.0 when one-sided; 0.5 at an even split)
    evidence_factor    = min(1, known_count / 3)
    consistency_factor = 0.70 when coverage has conflicted_fields, else 1.00

where ``minority = min(positive_sum, negative_sum)`` and ``total = positive_sum +
negative_sum`` (``agreement_factor`` is 1.0 when ``total`` is 0, since nothing
disagrees). Confidence therefore rises with coverage richness, more present signals,
and signal agreement, and falls with sparse coverage, conflicting signals, and
conflicted coverage fields, exactly as required.

Trend (derived from dated observations, honest about thin history)
------------------------------------------------------------------
``trend`` is derived from a sequence of dated :class:`TrendPoint` observations of the
axis position over time, never from a single point-in-time read (a lone snapshot has no
trajectory). Each observation pairs a UTC timestamp with the :class:`AxisPosition`
recorded at that time; a caller builds the history by keeping prior assessments'
positions. Observations whose position is ``UNKNOWN`` carry no direction and are ignored.
With fewer than ``MIN_TREND_OBSERVATIONS`` (2) dated directional observations the trend is
``Trend.UNKNOWN`` rather than ``Trend.STABLE``, so missing history never masquerades as a
stable trajectory. Otherwise the earliest and latest observations are compared on the
ordinal NEGATIVE < MIXED < STRONG_POSITIVE (sorting by timestamp, then by that ordinal
for a deterministic tie-break): a rise is ``IMPROVING``, a fall is ``DECLINING``, and no
change is ``STABLE``.

Fairness and robustness invariants (adversarially tested, non-negotiable)
-------------------------------------------------------------------------
- Absence is UNKNOWN, never negative. Thin coverage or all-unknown signals yield the
  axis's UNKNOWN rating with low confidence; missing history never yields WEAK or BEAR.
- Trend needs dated history. Fewer than MIN_TREND_OBSERVATIONS dated directional
  observations yield UNKNOWN, never STABLE, so absence of history is never read as a
  stable trajectory.
- Independence. Each axis is computed from only its own signals and coverage. No
  averaging, no blended score, no single number, no cross-axis leakage.
- Confidence in ``[0, 1]``, honest about absence. When nothing is assessable the
  confidence is an explicit unknown rather than a fabricated low number.
- Deterministic and versioned. Signals are consumed in input order, thresholds are
  fixed integers, and every assessment carries ``rubric_version``.
- Contract-clean. Supporting and counter claim id sets never overlap (a claim routed to
  both poles is rejected), confidence stays within ``[0, 1]``, and ratings come only
  from the frozen enums.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

from founderlookup.domain.assessment import (
    Confidence,
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
    KnowledgeState,
    KnowledgeValue,
    StableId,
)
from founderlookup.domain.scoring import CoverageLevel, CoverageSummary

# --------------------------------------------------------------------------------------
# Versioning. Every produced assessment carries this id so any output is exactly
# reproducible from its rubric version. Bump on any change to signals, thresholds, the
# confidence mapping, or the position-to-rating tables below.
# --------------------------------------------------------------------------------------

AXIS_RUBRIC_VERSION: Final = "axis-rubric.v0"


# ======================================================================================
# Signal input
# ======================================================================================


class SignalReading(StrEnum):
    """One coarse directional read toward an axis's positive pole with a strength tier.

    The sign is the direction (which pole the evidence points to) and the word is the
    strength tier. ``NEUTRAL`` is a present read that leans toward neither pole.
    """

    STRONG_POSITIVE = "strong_positive"
    MODERATE_POSITIVE = "moderate_positive"
    SLIGHT_POSITIVE = "slight_positive"
    NEUTRAL = "neutral"
    SLIGHT_NEGATIVE = "slight_negative"
    MODERATE_NEGATIVE = "moderate_negative"
    STRONG_NEGATIVE = "strong_negative"


# Signed point value for one present reading. Any non-known reading contributes nothing
# and never appears here, so absence can never move a position.
_READING_POINTS: Final[dict[SignalReading, int]] = {
    SignalReading.STRONG_POSITIVE: 3,
    SignalReading.MODERATE_POSITIVE: 2,
    SignalReading.SLIGHT_POSITIVE: 1,
    SignalReading.NEUTRAL: 0,
    SignalReading.SLIGHT_NEGATIVE: -1,
    SignalReading.MODERATE_NEGATIVE: -2,
    SignalReading.STRONG_NEGATIVE: -3,
}


@dataclass(frozen=True)
class AxisSignal:
    """One coarse directional read handed to a single axis rubric.

    ``reading`` is a ``KnowledgeValue`` so an unassessed signal is an explicit unknown
    rather than a fabricated neutral; any non-known state contributes nothing to the
    position and is surfaced only as an open question. ``claim_ids`` are the claims that
    back this directional read; a present positive read routes them to the assessment's
    supporting evidence and a present negative read routes them to its counter evidence.
    """

    key: str
    reading: KnowledgeValue[SignalReading]
    rationale: str = "axis signal"
    claim_ids: tuple[StableId, ...] = ()


@dataclass(frozen=True)
class TrendPoint:
    """One dated observation of an axis position, used only to derive the trend.

    ``observed_at`` must be a timezone-aware UTC datetime. A ``position`` of ``UNKNOWN``
    carries no direction and is ignored when the trend is derived, so a run of unknown
    history never manufactures a trajectory.
    """

    observed_at: datetime
    position: AxisPosition


# ======================================================================================
# Shared internal position
# ======================================================================================


class AxisPosition(StrEnum):
    """The single coarse read computed once per axis and mapped to each vocabulary."""

    STRONG_POSITIVE = "strong_positive"
    MIXED = "mixed"
    NEGATIVE = "negative"
    UNKNOWN = "unknown"


# Sufficiency gate: how many present reads are needed to assert any non-UNKNOWN position.
# LOW coverage demands more present reads, so thin coverage defaults to UNKNOWN.
MIN_SIGNALS_FOR_POSITION: Final = 2
LOW_COVERAGE_MIN_SIGNALS: Final = 3

# Position thresholds on the signed net and the opposing magnitude. Coarse by design.
STRONG_POSITION_NET: Final = 3
NEGATIVE_POSITION_NET: Final = -3
MINOR_OPPOSITION_MAGNITUDE: Final = 1

# Trend needs at least this many dated directional observations; below it the trend is
# UNKNOWN rather than STABLE, so absence of history is never read as a stable trajectory.
MIN_TREND_OBSERVATIONS: Final = 2

# Ordinal used only to compare two dated positions. UNKNOWN carries no direction and is
# excluded, so it never participates in a trend comparison.
_TREND_POSITION_ORDINAL: Final[dict[AxisPosition, int]] = {
    AxisPosition.NEGATIVE: -1,
    AxisPosition.MIXED: 0,
    AxisPosition.STRONG_POSITIVE: 1,
}

# Confidence sub-factor scales (all in [0, 1]); see the module docstring for the mapping.
_COVERAGE_CONFIDENCE: Final[dict[CoverageLevel, float]] = {
    CoverageLevel.LOW: 0.30,
    CoverageLevel.MEDIUM: 0.65,
    CoverageLevel.HIGH: 1.00,
}
TARGET_SIGNAL_COUNT: Final = 3
CONFLICTED_FIELDS_FACTOR: Final = 0.70


# Position -> rating tables. Every axis maps the same four positions to its own four
# ratings; the vocabularies differ but the four cases are exhaustive and fixed.
FOUNDER_POSITION_TO_RATING: Final[dict[AxisPosition, FounderAxisRating]] = {
    AxisPosition.STRONG_POSITIVE: FounderAxisRating.STRONG,
    AxisPosition.MIXED: FounderAxisRating.MIXED,
    AxisPosition.NEGATIVE: FounderAxisRating.WEAK,
    AxisPosition.UNKNOWN: FounderAxisRating.UNKNOWN,
}
MARKET_POSITION_TO_RATING: Final[dict[AxisPosition, MarketAxisRating]] = {
    AxisPosition.STRONG_POSITIVE: MarketAxisRating.BULLISH,
    AxisPosition.MIXED: MarketAxisRating.NEUTRAL,
    AxisPosition.NEGATIVE: MarketAxisRating.BEAR,
    AxisPosition.UNKNOWN: MarketAxisRating.UNKNOWN,
}
IDEA_VS_MARKET_POSITION_TO_RATING: Final[dict[AxisPosition, IdeaVsMarketAxisRating]] = {
    AxisPosition.STRONG_POSITIVE: IdeaVsMarketAxisRating.VIABLE,
    AxisPosition.MIXED: IdeaVsMarketAxisRating.PIVOTABLE,
    AxisPosition.NEGATIVE: IdeaVsMarketAxisRating.WEAK,
    AxisPosition.UNKNOWN: IdeaVsMarketAxisRating.UNKNOWN,
}


def _clamp01(value: float) -> float:
    """Clamp a value into the unit interval."""
    return min(1.0, max(0.0, value))


def _present_reading(reading: KnowledgeValue[SignalReading]) -> SignalReading | None:
    """Return the present reading, or None when the signal is any non-known state."""
    if reading.state is KnowledgeState.KNOWN:
        return reading.value
    return None


def _has_sufficient_signals(known_count: int, level: CoverageLevel) -> bool:
    """Whether there are enough present reads to assert a non-UNKNOWN position.

    LOW coverage raises the floor, so thin coverage yields UNKNOWN rather than a
    directional read built on too little evidence.
    """
    floor = LOW_COVERAGE_MIN_SIGNALS if level is CoverageLevel.LOW else MIN_SIGNALS_FOR_POSITION
    return known_count >= floor


def _position_from_totals(net: int, positive_sum: int, negative_sum: int) -> AxisPosition:
    """Map aggregated signal totals to a position via fixed coarse thresholds.

    A pole is reached only with a clear net lean AND at most minor opposition; anything
    else is MIXED, so genuine conflict is surfaced rather than blended into a pole.
    """
    if net >= STRONG_POSITION_NET and negative_sum <= MINOR_OPPOSITION_MAGNITUDE:
        return AxisPosition.STRONG_POSITIVE
    if net <= NEGATIVE_POSITION_NET and positive_sum <= MINOR_OPPOSITION_MAGNITUDE:
        return AxisPosition.NEGATIVE
    return AxisPosition.MIXED


def _derive_trend(trend_points: Sequence[TrendPoint]) -> Trend:
    """Derive the trend from dated position observations, honest about thin history.

    Non-known positions carry no direction and are dropped. Fewer than
    ``MIN_TREND_OBSERVATIONS`` dated directional observations yield ``Trend.UNKNOWN``
    rather than ``Trend.STABLE``. Otherwise the earliest and latest observations are
    compared on the position ordinal. Deterministic: observations are sorted by timestamp
    with the position ordinal as a fixed tie-break, so input order never changes the read.
    """
    directional = [point for point in trend_points if point.position in _TREND_POSITION_ORDINAL]
    if len(directional) < MIN_TREND_OBSERVATIONS:
        return Trend.UNKNOWN
    ordered = sorted(
        directional,
        key=lambda point: (point.observed_at, _TREND_POSITION_ORDINAL[point.position]),
    )
    first = _TREND_POSITION_ORDINAL[ordered[0].position]
    last = _TREND_POSITION_ORDINAL[ordered[-1].position]
    if last > first:
        return Trend.IMPROVING
    if last < first:
        return Trend.DECLINING
    return Trend.STABLE


def _confidence_value(
    coverage: CoverageSummary,
    *,
    known_count: int,
    positive_sum: int,
    negative_sum: int,
) -> float:
    """Product of four monotone sub-factors in [0, 1]; see the module docstring."""
    coverage_factor = _COVERAGE_CONFIDENCE[coverage.level]
    total = positive_sum + negative_sum
    if total == 0:
        agreement_factor = 1.0
    else:
        minority = min(positive_sum, negative_sum)
        agreement_factor = 1.0 - (minority / total)
    evidence_factor = min(1.0, known_count / TARGET_SIGNAL_COUNT)
    consistency_factor = CONFLICTED_FIELDS_FACTOR if coverage.conflicted_fields else 1.0
    value = coverage_factor * agreement_factor * evidence_factor * consistency_factor
    return round(_clamp01(value), 2)


def _extend_unique(target: list[str], ids: Sequence[str]) -> None:
    """Append ids not already present, preserving first-seen order."""
    for claim_id in ids:
        if claim_id not in target:
            target.append(claim_id)


def _route_claims(
    known: Sequence[tuple[AxisSignal, SignalReading]],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Route each present signal's claims to supporting or counter by its direction.

    A present positive read supports the axis; a present negative read counters it; a
    present neutral read routes nowhere. A claim that lands in both sets is a caller
    inconsistency and is rejected so the frozen non-overlap contract always holds.
    """
    supporting: list[str] = []
    counter: list[str] = []
    for signal, reading in known:
        points = _READING_POINTS[reading]
        if points > 0:
            _extend_unique(supporting, signal.claim_ids)
        elif points < 0:
            _extend_unique(counter, signal.claim_ids)
    overlap = set(supporting) & set(counter)
    if overlap:
        raise ValueError(
            "a claim cannot both support and counter the same axis read: "
            + ", ".join(sorted(overlap))
        )
    return tuple(supporting), tuple(counter)


def _open_questions(
    axis_label: str,
    signals: Sequence[AxisSignal],
    coverage: CoverageSummary,
    *,
    position: AxisPosition,
    known_count: int,
    trend: Trend,
) -> tuple[str, ...]:
    """Deterministic, coarse open questions describing what is missing or conflicting."""
    questions: list[str] = []
    if position is AxisPosition.UNKNOWN:
        if known_count == 0:
            questions.append(
                f"No signals have been assessed for the {axis_label} axis; "
                "gather independent evidence before rating it."
            )
        else:
            questions.append(
                f"Coverage is too thin to rate the {axis_label} axis; "
                "more independent signals are required."
            )
    for signal in signals:
        if _present_reading(signal.reading) is None:
            questions.append(f"Assess the {axis_label} signal '{signal.key}': it is not yet known.")
    if trend is Trend.UNKNOWN:
        questions.append(
            f"The {axis_label} trend is unknown: supply at least {MIN_TREND_OBSERVATIONS} "
            "dated observations to establish a trajectory."
        )
    if coverage.conflicted_fields:
        joined = ", ".join(coverage.conflicted_fields)
        questions.append(f"Reconcile conflicting {axis_label} evidence on: {joined}.")

    ordered: list[str] = []
    _extend_unique(ordered, questions)
    return tuple(ordered)


@dataclass(frozen=True)
class _AxisComputation:
    """The shared per-axis read before it is mapped to an axis-specific rating."""

    position: AxisPosition
    trend: Trend
    confidence: KnowledgeValue[Confidence]
    supporting_claim_ids: tuple[str, ...]
    counter_claim_ids: tuple[str, ...]
    open_questions: tuple[str, ...]


def _compute_axis(
    axis_label: str,
    signals: Sequence[AxisSignal],
    coverage: CoverageSummary,
    trend_points: Sequence[TrendPoint],
) -> _AxisComputation:
    """Reduce one axis's signals to the shared position, trend, confidence, and evidence.

    Uses ONLY this axis's signals, coverage, and dated trend history; it never reads
    another axis, so axes cannot leak into one another. Deterministic: signals are
    consumed in input order and every threshold is a fixed integer.
    """
    known: list[tuple[AxisSignal, SignalReading]] = []
    for signal in signals:
        reading = _present_reading(signal.reading)
        if reading is not None:
            known.append((signal, reading))
    known_count = len(known)

    positive_sum = sum(_READING_POINTS[r] for _, r in known if _READING_POINTS[r] > 0)
    negative_sum = -sum(_READING_POINTS[r] for _, r in known if _READING_POINTS[r] < 0)
    net = positive_sum - negative_sum

    if not _has_sufficient_signals(known_count, coverage.level):
        position = AxisPosition.UNKNOWN
    else:
        position = _position_from_totals(net, positive_sum, negative_sum)
        # Thin coverage may never condemn. A would-be NEGATIVE read under LOW coverage is
        # downgraded to UNKNOWN so that absence or thin sourcing can never yield WEAK or
        # BEAR. The positive pole is left reachable on purpose: a well-corroborated
        # cold-start read is credited, but a negative one is withheld until coverage is at
        # least MEDIUM. This keeps the fairness rule strict: missing history never lowers a
        # rating, only present, well-covered adverse evidence can.
        if position is AxisPosition.NEGATIVE and coverage.level is CoverageLevel.LOW:
            position = AxisPosition.UNKNOWN

    if known_count == 0:
        confidence: KnowledgeValue[Confidence] = KnowledgeValue[Confidence].unknown(
            f"No signals have been assessed for the {axis_label} axis."
        )
    else:
        confidence = KnowledgeValue[Confidence].known(
            _confidence_value(
                coverage,
                known_count=known_count,
                positive_sum=positive_sum,
                negative_sum=negative_sum,
            )
        )

    trend = _derive_trend(trend_points)
    supporting, counter = _route_claims(known)
    questions = _open_questions(
        axis_label,
        signals,
        coverage,
        position=position,
        known_count=known_count,
        trend=trend,
    )
    return _AxisComputation(
        position=position,
        trend=trend,
        confidence=confidence,
        supporting_claim_ids=supporting,
        counter_claim_ids=counter,
        open_questions=questions,
    )


# ======================================================================================
# Public per-axis rubrics
# ======================================================================================


def assess_founder_axis(
    signals: Sequence[AxisSignal],
    *,
    coverage: CoverageSummary,
    assessment_id: StableId,
    assessment_version_id: StableId,
    trend_points: Sequence[TrendPoint] = (),
) -> FounderAxisAssessment:
    """Assess the founder axis from its own signals only.

    Thin coverage or all-unknown signals yield ``FounderAxisRating.UNKNOWN`` with low
    confidence, never ``WEAK``. Absence never produces a negative rating. ``trend`` is
    derived from ``trend_points`` (dated axis-position observations) and is ``UNKNOWN``
    until at least ``MIN_TREND_OBSERVATIONS`` dated directional observations exist.
    """
    computation = _compute_axis("founder", signals, coverage, trend_points)
    return FounderAxisAssessment(
        assessment_id=assessment_id,
        assessment_version_id=assessment_version_id,
        rubric_version=AXIS_RUBRIC_VERSION,
        trend=computation.trend,
        rating=FOUNDER_POSITION_TO_RATING[computation.position],
        confidence=computation.confidence,
        coverage=coverage,
        supporting_claim_ids=computation.supporting_claim_ids,
        counter_claim_ids=computation.counter_claim_ids,
        open_questions=computation.open_questions,
    )


def assess_market_axis(
    signals: Sequence[AxisSignal],
    *,
    coverage: CoverageSummary,
    assessment_id: StableId,
    assessment_version_id: StableId,
    trend_points: Sequence[TrendPoint] = (),
) -> MarketAxisAssessment:
    """Assess the market axis from its own signals only.

    Thin coverage or all-unknown signals yield ``MarketAxisRating.UNKNOWN`` with low
    confidence, never ``BEAR``. Absence never produces a negative rating. ``trend`` is
    derived from ``trend_points`` and is ``UNKNOWN`` until enough dated observations exist.
    """
    computation = _compute_axis("market", signals, coverage, trend_points)
    return MarketAxisAssessment(
        assessment_id=assessment_id,
        assessment_version_id=assessment_version_id,
        rubric_version=AXIS_RUBRIC_VERSION,
        trend=computation.trend,
        rating=MARKET_POSITION_TO_RATING[computation.position],
        confidence=computation.confidence,
        coverage=coverage,
        supporting_claim_ids=computation.supporting_claim_ids,
        counter_claim_ids=computation.counter_claim_ids,
        open_questions=computation.open_questions,
    )


def assess_idea_vs_market_axis(
    signals: Sequence[AxisSignal],
    *,
    coverage: CoverageSummary,
    assessment_id: StableId,
    assessment_version_id: StableId,
    trend_points: Sequence[TrendPoint] = (),
) -> IdeaVsMarketAxisAssessment:
    """Assess the idea-versus-market axis from its own signals only.

    Thin coverage or all-unknown signals yield ``IdeaVsMarketAxisRating.UNKNOWN`` with
    low confidence, never ``WEAK``. A mixed read maps to ``PIVOTABLE`` (a partial fit
    that can be steered), and only clear, well-covered negative evidence maps to
    ``WEAK``. ``trend`` is derived from ``trend_points``.
    """
    computation = _compute_axis("idea_vs_market", signals, coverage, trend_points)
    return IdeaVsMarketAxisAssessment(
        assessment_id=assessment_id,
        assessment_version_id=assessment_version_id,
        rubric_version=AXIS_RUBRIC_VERSION,
        trend=computation.trend,
        rating=IDEA_VS_MARKET_POSITION_TO_RATING[computation.position],
        confidence=computation.confidence,
        coverage=coverage,
        supporting_claim_ids=computation.supporting_claim_ids,
        counter_claim_ids=computation.counter_claim_ids,
        open_questions=computation.open_questions,
    )


def assemble_independent_axes(
    *,
    founder: FounderAxisAssessment,
    market: MarketAxisAssessment,
    idea_vs_market: IdeaVsMarketAxisAssessment,
) -> IndependentAxes:
    """Bundle the three finished axis assessments with no arithmetic between them.

    This performs no averaging or blending; it only groups the three assessments so the
    independence of the axes is preserved by construction.
    """
    return IndependentAxes(
        founder=founder,
        market=market,
        idea_vs_market=idea_vs_market,
    )


__all__ = [
    "AXIS_RUBRIC_VERSION",
    "CONFLICTED_FIELDS_FACTOR",
    "FOUNDER_POSITION_TO_RATING",
    "IDEA_VS_MARKET_POSITION_TO_RATING",
    "LOW_COVERAGE_MIN_SIGNALS",
    "MARKET_POSITION_TO_RATING",
    "MINOR_OPPOSITION_MAGNITUDE",
    "MIN_SIGNALS_FOR_POSITION",
    "MIN_TREND_OBSERVATIONS",
    "NEGATIVE_POSITION_NET",
    "STRONG_POSITION_NET",
    "TARGET_SIGNAL_COUNT",
    "AxisPosition",
    "AxisSignal",
    "SignalReading",
    "TrendPoint",
    "assemble_independent_axes",
    "assess_founder_axis",
    "assess_idea_vs_market_axis",
    "assess_market_axis",
]
