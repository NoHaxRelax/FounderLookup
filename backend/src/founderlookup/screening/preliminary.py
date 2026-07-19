"""Conviction threshold and preliminary Assessment Envelope assembly for OUTBOUND.

This module is the outbound integration piece for task 3.3. It takes pieces that other
components already computed (a founder score snapshot, the three INDEPENDENT axis
assessments, coverage, claim and evidence ids, versions, run id, timestamp, and the
outbound candidate identity) and does exactly two things:

1. :func:`decide_conviction` applies a deterministic conviction threshold over the
   pattern of the three axis ratings, their confidences, the founder score state, and
   coverage, returning this module's own :class:`ConvictionDecision`.
2. :func:`assemble_preliminary_assessment` builds a schema-valid PRELIMINARY
   :class:`AssessmentEnvelope` from the provided pieces, with ``decision_readiness=None``
   and ``memo=None`` so a preliminary read can never claim full-case readiness or a memo.

:func:`evaluate_preliminary_candidate` runs both together and returns both.

Everything here is a pure function of its inputs: no I/O, no live model, no randomness,
no hidden state, so identical inputs always yield identical output, and every conviction
decision carries an explicit :data:`CONVICTION_POLICY_VERSION`.

Conviction is a pattern over three ratings, never an average
------------------------------------------------------------
The three axes are frozen as INDEPENDENT on purpose (:class:`IndependentAxes` has no
aggregate field). This module keeps that promise: it never averages, sums, or blends the
three ratings into a single number. Each rating is first mapped to a coarse shared
:class:`AxisStance` (``POSITIVE`` / ``MIXED`` / ``NEGATIVE`` / ``UNKNOWN``) using only
that axis's own vocabulary, and the decision then reasons over the multiset of stances
with fixed gates. A hard-negative on ANY axis is always surfaced by name in the rationale
and always blocks ``PURSUE``; it is never cancelled out by a strong reading on another
axis.

    axis            POSITIVE   MIXED       NEGATIVE   UNKNOWN
    founder         strong     mixed       weak       unknown
    market          bullish    neutral     bear       unknown
    idea_vs_market  viable     pivotable   weak       unknown

Conviction levels
-----------------
:class:`ConvictionLevel` has four values and exactly one of them clears the bar:

    pursue        clears_bar=True   present positive case is strong and uncontested
    hold          clears_bar=False  genuine, well-covered tension a human must weigh
    gather_more   clears_bar=False  the affirmative cold-start default: evidence is
                                    missing or too thin to reach a firm verdict
    pass          clears_bar=False  a complete, well-covered, uncontested negative read

The decision procedure is a fixed precedence, checked top to bottom:

    1. PASS        every axis is read (no UNKNOWN), at least one is a hard-negative, none
                   is positive, coverage is not LOW, and the founder score is KNOWN and
                   not provisional. Only a present, complete, uncontested negative picture
                   can reject; absence never can.
    2. PURSUE      no hard-negative on any axis, at least PURSUE_MIN_POSITIVE_AXES (2)
                   positive axes carry known confidence at or above PURSUE_MIN_CONFIDENCE,
                   and coverage is not LOW. A third positive axis with thin or unknown
                   confidence never blocks this, so the bar is monotonic in added evidence.
    3. HOLD        a present positive and a present hard-negative coexist under a complete,
                   non-thin picture (no UNKNOWN axis, coverage not LOW, founder identity
                   resolved). Real tension the rubric refuses to average, routed to a human.
    4. GATHER_MORE the catch-all. Any remaining pattern is dominated by absence or is too
                   thin to clear the pursue bar, so more evidence is requested. Any present
                   hard-negative is still surfaced here and must be resolved.

Affirmative cold-start and fairness invariants (adversarially tested, non-negotiable)
-------------------------------------------------------------------------------------
- No averaging. Conviction is never a mean of the three axes. A hard-negative on any axis
  is always surfaced and always blocks PURSUE; strong axes never hide a weak one.
- Absence is a request for evidence, never a rejection. Sparse coverage (LOW), any UNKNOWN
  axis, or a provisional/unknown founder score can route only to GATHER_MORE, never to a
  hard PASS. A PASS is reachable solely through a present, complete, uncontested negative
  read, so missing history can never trigger a reject.
- Missing history never lowers conviction below what present evidence supports. Unknown
  axes are ignored when deciding PURSUE, so two strong present axes still clear the bar
  even while a third axis or the founder record is unresolved.
- Deterministic and versioned. Ratings are mapped through fixed tables, every gate is a
  fixed constant, and each decision carries CONVICTION_POLICY_VERSION.
- Contract-valid envelope. :func:`assemble_preliminary_assessment` constructs the frozen
  :class:`AssessmentEnvelope` directly, so every frozen validator (preliminary identity,
  no memo, no readiness, founder-score/identity consistency) is enforced as-is; an
  inconsistent input raises rather than producing an invalid envelope.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from founderlookup.domain.assessment import (
    AssessmentEnvelope,
    DeterministicRuleResult,
    FounderAxisRating,
    IdeaVsMarketAxisRating,
    IndependentAxes,
    MarketAxisRating,
    PreliminaryAssessmentIdentity,
)
from founderlookup.domain.common import (
    KnowledgeState,
    KnowledgeValue,
    StableId,
    UTCDateTime,
    VersionManifest,
)
from founderlookup.domain.scoring import (
    CoverageLevel,
    CoverageSummary,
    FounderScoreSnapshot,
)

# --------------------------------------------------------------------------------------
# Versioning. Every conviction decision carries this id so any output is exactly
# reproducible from its policy version. Bump on any change to the stance tables, the
# decision precedence, or the gate constants below.
# --------------------------------------------------------------------------------------

CONVICTION_POLICY_VERSION: Final = "conviction-threshold.v0"

# Gate constants. All coarse and fixed so identical inputs always decide the same way.
PURSUE_MIN_POSITIVE_AXES: Final = 2
# Present positive axes must carry at least this known confidence to clear the pursue bar.
# The axis rubric caps confidence at 0.30 under LOW coverage, so this threshold makes the
# pursue bar require at least MEDIUM coverage while still crediting a thinner MEDIUM read.
PURSUE_MIN_CONFIDENCE: Final = 0.35

# Canonical axis order, used for every deterministic rationale and id list.
_AXIS_ORDER: Final = ("founder", "market", "idea_vs_market")


class AxisStance(StrEnum):
    """The coarse shared read for one axis, mapped from its own rating vocabulary.

    ``NEGATIVE`` is a hard-negative: a present, adverse read. ``UNKNOWN`` is absence, an
    explicit request for evidence that can never on its own condemn a candidate.
    """

    POSITIVE = "positive"
    MIXED = "mixed"
    NEGATIVE = "negative"
    UNKNOWN = "unknown"


class ConvictionLevel(StrEnum):
    """The conviction verdict. Exactly one value (``PURSUE``) clears the bar."""

    PURSUE = "pursue"
    HOLD = "hold"
    GATHER_MORE = "gather_more"
    PASS = "pass"


# Per-axis rating -> shared stance. These invert the axis rubric's position-to-rating
# tables, so the coarse meaning is preserved without ever reading a second axis.
_FOUNDER_STANCE: Final[dict[FounderAxisRating, AxisStance]] = {
    FounderAxisRating.STRONG: AxisStance.POSITIVE,
    FounderAxisRating.MIXED: AxisStance.MIXED,
    FounderAxisRating.WEAK: AxisStance.NEGATIVE,
    FounderAxisRating.UNKNOWN: AxisStance.UNKNOWN,
}
_MARKET_STANCE: Final[dict[MarketAxisRating, AxisStance]] = {
    MarketAxisRating.BULLISH: AxisStance.POSITIVE,
    MarketAxisRating.NEUTRAL: AxisStance.MIXED,
    MarketAxisRating.BEAR: AxisStance.NEGATIVE,
    MarketAxisRating.UNKNOWN: AxisStance.UNKNOWN,
}
_IDEA_STANCE: Final[dict[IdeaVsMarketAxisRating, AxisStance]] = {
    IdeaVsMarketAxisRating.VIABLE: AxisStance.POSITIVE,
    IdeaVsMarketAxisRating.PIVOTABLE: AxisStance.MIXED,
    IdeaVsMarketAxisRating.WEAK: AxisStance.NEGATIVE,
    IdeaVsMarketAxisRating.UNKNOWN: AxisStance.UNKNOWN,
}


@dataclass(frozen=True)
class _AxisRead:
    """One axis reduced to its coarse stance plus its known confidence, if any."""

    name: str
    rating: str
    stance: AxisStance
    confidence: float | None


@dataclass(frozen=True)
class ConvictionDecision:
    """This module's own conviction verdict over the three INDEPENDENT axes.

    ``clears_bar`` is ``True`` only for :attr:`ConvictionLevel.PURSUE`. ``rationale`` is a
    deterministic sentence that always names any hard-negative axis, so a weak axis is
    never hidden. ``hard_negative_axes`` and ``unknown_axes`` expose the surfaced pattern,
    and ``evidence_requests`` lists the concrete gaps a cold-start read should fill.
    """

    level: ConvictionLevel
    clears_bar: bool
    rationale: str
    hard_negative_axes: tuple[str, ...]
    unknown_axes: tuple[str, ...]
    positive_axes: tuple[str, ...]
    evidence_requests: tuple[str, ...]
    policy_version: str = CONVICTION_POLICY_VERSION


def _known_confidence(confidence: KnowledgeValue[float]) -> float | None:
    """Return the present confidence, or None when it is any non-known state."""
    if confidence.state is KnowledgeState.KNOWN:
        return confidence.value
    return None


def _axis_reads(axes: IndependentAxes) -> tuple[_AxisRead, ...]:
    """Reduce each axis to its coarse stance and known confidence, in canonical order."""
    return (
        _AxisRead(
            name="founder",
            rating=axes.founder.rating.value,
            stance=_FOUNDER_STANCE[axes.founder.rating],
            confidence=_known_confidence(axes.founder.confidence),
        ),
        _AxisRead(
            name="market",
            rating=axes.market.rating.value,
            stance=_MARKET_STANCE[axes.market.rating],
            confidence=_known_confidence(axes.market.confidence),
        ),
        _AxisRead(
            name="idea_vs_market",
            rating=axes.idea_vs_market.rating.value,
            stance=_IDEA_STANCE[axes.idea_vs_market.rating],
            confidence=_known_confidence(axes.idea_vs_market.confidence),
        ),
    )


def _names(reads: tuple[_AxisRead, ...], stance: AxisStance) -> tuple[str, ...]:
    """Axis names holding a given stance, in canonical order."""
    return tuple(read.name for read in reads if read.stance is stance)


def _labels(reads: tuple[_AxisRead, ...], stance: AxisStance) -> tuple[str, ...]:
    """Human ``axis=rating`` labels for a given stance, in canonical order."""
    return tuple(f"{read.name}={read.rating}" for read in reads if read.stance is stance)


def _confident_positive_count(reads: tuple[_AxisRead, ...]) -> int:
    """Count the positive axes carrying known confidence at or above the pursue bar.

    Pursue requires at least ``PURSUE_MIN_POSITIVE_AXES`` such axes. A positive axis with
    unknown or thin confidence simply does not count toward that floor; it never blocks a
    pursuit the other confident positives already justify. This keeps the decision
    monotonic: adding a positive-but-thin axis can only help or leave the verdict
    unchanged, never demote it, so missing confidence never lowers conviction below what
    the present, confident positives support.
    """
    return sum(
        1
        for read in reads
        if read.stance is AxisStance.POSITIVE
        and read.confidence is not None
        and read.confidence >= PURSUE_MIN_CONFIDENCE
    )


def decide_conviction(
    *,
    axes: IndependentAxes,
    founder_score: KnowledgeValue[FounderScoreSnapshot],
    coverage: CoverageSummary,
) -> ConvictionDecision:
    """Decide conviction from the pattern of the three axis ratings, never their average.

    The three ratings are mapped to coarse stances and reasoned over with fixed gates (see
    the module docstring for the full precedence). A hard-negative on any axis is always
    surfaced and always blocks ``PURSUE``. Absence (an UNKNOWN axis, LOW coverage, or a
    provisional/unknown founder score) can route only to ``GATHER_MORE``, never to a hard
    ``PASS``, and it never drags conviction below what the present axes already support.
    """
    reads = _axis_reads(axes)

    positive_axes = _names(reads, AxisStance.POSITIVE)
    hard_negative_axes = _names(reads, AxisStance.NEGATIVE)
    unknown_axes = _names(reads, AxisStance.UNKNOWN)
    n_positive = len(positive_axes)
    n_negative = len(hard_negative_axes)
    n_unknown = len(unknown_axes)

    low_coverage = coverage.level is CoverageLevel.LOW
    founder_known = founder_score.state is KnowledgeState.KNOWN
    founder_snapshot = founder_score.value if founder_known else None
    founder_provisional = founder_snapshot is not None and founder_snapshot.provisional
    # A complete picture: every axis is read, coverage is not thin, and the founder record
    # is resolved and established. Only a complete picture may reach a firm PASS.
    complete = n_unknown == 0 and not low_coverage and founder_known and not founder_provisional

    evidence_requests = _evidence_requests(
        reads,
        low_coverage=low_coverage,
        founder_known=founder_known,
        founder_provisional=founder_provisional,
    )

    if n_negative >= 1 and n_positive == 0 and complete:
        level = ConvictionLevel.PASS
    elif (
        n_negative == 0
        and not low_coverage
        and _confident_positive_count(reads) >= PURSUE_MIN_POSITIVE_AXES
    ):
        level = ConvictionLevel.PURSUE
    elif n_negative >= 1 and n_positive >= 1 and complete:
        level = ConvictionLevel.HOLD
    else:
        level = ConvictionLevel.GATHER_MORE

    rationale = _compose_rationale(
        level,
        hard_negative_labels=_labels(reads, AxisStance.NEGATIVE),
        positive_labels=_labels(reads, AxisStance.POSITIVE),
        unknown_axes=unknown_axes,
        low_coverage=low_coverage,
        founder_known=founder_known,
        founder_provisional=founder_provisional,
    )

    return ConvictionDecision(
        level=level,
        clears_bar=level is ConvictionLevel.PURSUE,
        rationale=rationale,
        hard_negative_axes=hard_negative_axes,
        unknown_axes=unknown_axes,
        positive_axes=positive_axes,
        evidence_requests=evidence_requests,
    )


def _evidence_requests(
    reads: tuple[_AxisRead, ...],
    *,
    low_coverage: bool,
    founder_known: bool,
    founder_provisional: bool,
) -> tuple[str, ...]:
    """Concrete, deterministic gaps a cold-start read should fill, in a fixed order."""
    requests: list[str] = []
    for read in reads:
        if read.stance is AxisStance.UNKNOWN:
            requests.append(
                f"Gather independent evidence for the {read.name} axis; it is still unknown."
            )
    if low_coverage:
        requests.append(
            "Broaden source coverage; it is sparse (low), so present reads carry low confidence."
        )
    if not founder_known:
        requests.append("Resolve the founder identity so a founder score snapshot can be produced.")
    elif founder_provisional:
        requests.append(
            "Corroborate the founder record; the score is provisional and not yet established."
        )
    return tuple(requests)


def _compose_rationale(
    level: ConvictionLevel,
    *,
    hard_negative_labels: tuple[str, ...],
    positive_labels: tuple[str, ...],
    unknown_axes: tuple[str, ...],
    low_coverage: bool,
    founder_known: bool,
    founder_provisional: bool,
) -> str:
    """Build a deterministic rationale that always names any hard-negative axis."""
    leads = {
        ConvictionLevel.PURSUE: (
            "Present positive reads are strong and uncontested, so the candidate clears "
            "the bar to pursue."
        ),
        ConvictionLevel.HOLD: (
            "Present evidence is genuinely mixed and complete, so the candidate is held "
            "for human judgment rather than pursued or rejected."
        ),
        ConvictionLevel.GATHER_MORE: (
            "Evidence is missing or too thin to reach a firm verdict, so more evidence is "
            "requested rather than rejecting the candidate."
        ),
        ConvictionLevel.PASS: (
            "The picture is complete and well covered with no positive read, so the "
            "candidate does not clear the bar."
        ),
    }
    parts: list[str] = [leads[level]]
    if hard_negative_labels:
        joined = ", ".join(hard_negative_labels)
        parts.append(
            f"Hard-negative reads are surfaced and not averaged against the other axes: {joined}."
        )
    if positive_labels:
        parts.append("Positive reads: " + ", ".join(positive_labels) + ".")
    if unknown_axes:
        parts.append("Axes still unknown: " + ", ".join(unknown_axes) + ".")
    if low_coverage:
        parts.append("Coverage is sparse (low).")
    if not founder_known:
        parts.append("The founder score is unknown (identity unresolved).")
    elif founder_provisional:
        parts.append("The founder score is provisional (record not yet established).")
    return " ".join(parts)


def assemble_preliminary_assessment(
    *,
    identity: PreliminaryAssessmentIdentity,
    assessment_id: StableId,
    assessment_version_id: StableId,
    versions: VersionManifest,
    input_snapshot_id: StableId,
    input_snapshot_as_of: UTCDateTime,
    coverage: CoverageSummary,
    founder_score: KnowledgeValue[FounderScoreSnapshot],
    axes: IndependentAxes,
    claim_ids: tuple[StableId, ...],
    evidence_ids: tuple[StableId, ...],
    run_id: StableId,
    created_at: UTCDateTime,
    deterministic_results: tuple[DeterministicRuleResult, ...] = (),
) -> AssessmentEnvelope:
    """Assemble a schema-valid PRELIMINARY :class:`AssessmentEnvelope` for an outbound candidate.

    This ASSEMBLES from the provided pieces; it never recomputes the axes or the founder
    score. ``decision_readiness`` and ``memo`` are pinned to ``None`` and
    ``recommendation`` is left ``None`` so a preliminary read can never claim full-case
    readiness or a memo. The envelope is constructed directly so every frozen validator is
    enforced as-is: a preliminary identity, no readiness, no memo, and (when the founder
    score is KNOWN) a founder score whose ``founder_id`` matches the identity's KNOWN
    founder id. Inconsistent inputs raise a ``ValidationError`` rather than yielding an
    invalid envelope.
    """
    return AssessmentEnvelope(
        assessment_id=assessment_id,
        assessment_version_id=assessment_version_id,
        identity=identity,
        versions=versions,
        input_snapshot_id=input_snapshot_id,
        input_snapshot_as_of=input_snapshot_as_of,
        coverage=coverage,
        deterministic_results=deterministic_results,
        founder_score=founder_score,
        axes=axes,
        claim_ids=claim_ids,
        evidence_ids=evidence_ids,
        decision_readiness=None,
        memo=None,
        recommendation=None,
        run_id=run_id,
        created_at=created_at,
    )


@dataclass(frozen=True)
class PreliminaryAssessmentOutcome:
    """The paired output of a preliminary run: the conviction verdict and the envelope."""

    conviction: ConvictionDecision
    envelope: AssessmentEnvelope


def evaluate_preliminary_candidate(
    *,
    identity: PreliminaryAssessmentIdentity,
    assessment_id: StableId,
    assessment_version_id: StableId,
    versions: VersionManifest,
    input_snapshot_id: StableId,
    input_snapshot_as_of: UTCDateTime,
    coverage: CoverageSummary,
    founder_score: KnowledgeValue[FounderScoreSnapshot],
    axes: IndependentAxes,
    claim_ids: tuple[StableId, ...],
    evidence_ids: tuple[StableId, ...],
    run_id: StableId,
    created_at: UTCDateTime,
    deterministic_results: tuple[DeterministicRuleResult, ...] = (),
) -> PreliminaryAssessmentOutcome:
    """Run the conviction threshold and assemble the envelope in one deterministic step.

    The conviction decision reads the axes, founder score, and the same coverage that is
    stamped on the envelope, so the verdict and the envelope always describe the same
    evidence.
    """
    conviction = decide_conviction(
        axes=axes,
        founder_score=founder_score,
        coverage=coverage,
    )
    envelope = assemble_preliminary_assessment(
        identity=identity,
        assessment_id=assessment_id,
        assessment_version_id=assessment_version_id,
        versions=versions,
        input_snapshot_id=input_snapshot_id,
        input_snapshot_as_of=input_snapshot_as_of,
        coverage=coverage,
        founder_score=founder_score,
        axes=axes,
        claim_ids=claim_ids,
        evidence_ids=evidence_ids,
        run_id=run_id,
        created_at=created_at,
        deterministic_results=deterministic_results,
    )
    return PreliminaryAssessmentOutcome(conviction=conviction, envelope=envelope)


__all__ = [
    "CONVICTION_POLICY_VERSION",
    "PURSUE_MIN_CONFIDENCE",
    "PURSUE_MIN_POSITIVE_AXES",
    "AxisStance",
    "ConvictionDecision",
    "ConvictionLevel",
    "PreliminaryAssessmentOutcome",
    "assemble_preliminary_assessment",
    "decide_conviction",
    "evaluate_preliminary_candidate",
]
