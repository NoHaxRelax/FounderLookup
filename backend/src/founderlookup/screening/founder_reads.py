"""Two separate, evidence-graded reads over the founder trait taxonomy (v0).

This module layers a versioned per-observation EVIDENCE GRADE onto the frozen factor
taxonomy in :mod:`founderlookup.screening.rubrics` and then reads that one graded
observation set through two deliberately different lenses:

1. :func:`builder_signal_read` - a read of building SUBSTANCE. It counts only the
   costly-to-fake, peer-validated, outcome-linked signals the taxonomy marks POSITIVE
   and subtracts verified negatives. It assigns EXACTLY ZERO to every gameable vanity or
   non-predictive attribute (follower reach, institutional pedigree, presentation
   polish, team size). No amount of vanity can raise builder substance.
2. :func:`fundability_read` - a read of what conventional VC pattern-matching rewards.
   It DOES give weight to network, pedigree, presentation, and team size, and it
   under-weights the deep, costly craft signals a VC never inspects (source-backed work
   quality, sustained follow-through, depth of technical writing). It can therefore
   legitimately diverge from builder substance.

3. :func:`builder_fundability_gap` - the signed gap between the two reads plus a
   qualitative label (``under_networked_strong_builder``, ``substance_light_but_fundable``
   or ``aligned``) and a short rationale, so a human reviewer sees the divergence rather
   than a single blended number.

The whole point is the GAP. A traditional VC pattern-matches on network, pedigree, and
presentation; real building substance is costly-to-fake, peer-validated, outcome-linked
evidence. When those diverge you get the two cases that matter most: an under-networked
strong builder (high substance, low conventional fundability) and a substance-light but
fundable profile (low substance, high conventional fundability). Surfacing that gap
explicitly is how this system counters known VC bias.

Everything here is a pure function of its inputs. No I/O, no live model, no randomness,
no hidden state; identical inputs always yield identical outputs, and every output
carries an explicit policy version id.

Evidence grades (documented, coarse, monotone)
----------------------------------------------
Each observation carries one of three grades. A weaker grade counts for strictly less;
a self-asserted claim never counts like a verified outcome. The grade multiplier scales
the MAGNITUDE of a factor's contribution, so a stronger grade never reduces how far a
factor moves a read (for a positive factor it adds at least as much; for the negative
factor it subtracts at least as much).

    grade                         letter  meaning                              multiplier
    EvidenceGrade.OUTCOME_BACKED  A       outcome-backed / independently        1.0
                                          verifiable
    EvidenceGrade.CORROBORATED    B       corroborated by an independent        0.6
                                          source
    EvidenceGrade.SELF_ASSERTED   C       self-asserted, not yet corroborated   0.3

The ladder is a deliberate anti-gaming defense, not just a discount. Because the
self-asserted multiplier is a hard 0.3, a builder read built ENTIRELY from self-asserted
claims tops out at 70.4 even at full tier on every positive, which stays in the MODERATE
band and can never reach STRONG (>=75). Only corroborated or outcome-backed evidence can
carry a founder into the STRONG builder band, so self-assertion counts a little but can
never inflate substance. Grade C is nevertheless strictly nonzero, so a weaker grade
still counts for less rather than nothing.

Contribution tiers reuse :class:`founderlookup.screening.rubrics.ContributionTier`
(FULL 1.0, PARTIAL 0.5, ABSENT 0.0).

Baseline and scale
------------------
Both reads start at the shared baseline 50.0 ("insufficient positive evidence to rate",
not "mediocre founder") and add each factor's signed contribution:

    contribution = read_weight * tier_multiplier * grade_multiplier   (sign by polarity)

Only a present, evidence-graded observation moves a read. An unobserved factor, an
observation whose value is not KNOWN, and an unsourced negative all contribute exactly
zero, so absence and thin history can never decrement either read. Cold-start founders
sit at the baseline, never below it. Scores clamp to the documented 0..100 scale.

Builder-signal read weights (v0)
--------------------------------
The builder read is the taxonomy's own weighting: every POSITIVE factor carries its
registry ``max_weight``, the verified negative subtracts its ``max_weight``, and every
NEUTRAL_CONTEXT attribute carries zero. Because the neutral-context weight is a hard
zero, no vanity observation at any grade or tier can lift builder substance.

    shipped_adopted_work            +18   (costly, outcome-linked)
    corroborated_domain_experience  +12   (costly, peer/registry-verified)
    sustained_follow_through        +12   (costly, conscientiousness over time)
    peer_validated_recognition      +10   (costly, peer-validated)
    work_product_quality            +10   (costly, source-backed)
    public_writing_depth             +6   (insight-scored writing; real but soft)
    follower_reach                    0   (gameable vanity)
    institutional_pedigree            0   (non-predictive of building)
    presentation_polish               0   (fluency, an anti-signal; never polish)
    team_size                         0   (predicts capital raised, not building)
    verified_negative_signal        -18   (present, sourced negatives only)

Fundability read weights (v0)
-----------------------------
The fundability read honestly models conventional VC pattern-matching. It rewards
pedigree, presentation, audience, and team size, gives traction and recognition real
weight, and assigns ZERO to the deep craft signals a VC never inspects. This is what
lets fundability diverge from substance in both directions.

    institutional_pedigree          +16   (brand-name school/employer)
    shipped_adopted_work            +14   (traction; also real substance)
    presentation_polish             +12   (slick deck, articulate founder)
    follower_reach                  +10   (audience / social proof)
    peer_validated_recognition       +8   (a recognized name)
    team_size                        +8   (a "real team"; predicts capital raised)
    corroborated_domain_experience   +6   (a credentialed domain expert)
    sustained_follow_through          0   (VCs do not count closed-issue ratios)
    work_product_quality              0   (VCs do not read the code)
    public_writing_depth              0   (deep writing does not move a term sheet)
    verified_negative_signal        -18   (a verified negative sinks fundraising too)

Gap labels (v0)
---------------
    gap = builder_score - fundability_score        (signed, both on the 0..100 scale)

    gap >  +15  -> under_networked_strong_builder   (substance outruns fundability)
    gap <  -15  -> substance_light_but_fundable      (fundability outruns substance)
    otherwise   -> aligned                           (within the +/-15 aligned band)

The band is coarse by design; it is symmetric, so the gap can flag both divergence
cases, and it never blends the two reads into one number.

Fairness and robustness invariants (adversarially tested, non-negotiable)
-------------------------------------------------------------------------
- In the builder read, costly-to-fake / peer-validated / outcome-linked signals weigh
  strictly above gameable vanity signals, and non-predictive attributes contribute
  exactly zero. No amount of vanity can raise builder substance.
- Missing history / absence never decrements either read. Only a present, evidence-graded
  observation moves a read; an unknown observation contributes zero.
- Evidence-grade monotonicity: for the same factor and tier, a stronger grade never
  contributes less magnitude than a weaker grade.
- The gap is honest and symmetric: it can flag both an under-networked strong builder
  and a substance-light but fundable profile.
- Deterministic and versioned. Coarse documented weights and bands, no false precision.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from founderlookup.domain.common import (
    KnowledgeState,
    KnowledgeValue,
    ScalarValue,
    StableId,
    VersionId,
)
from founderlookup.screening.rubrics import (
    FOUNDER_FACTOR_REGISTRY,
    ContributionTier,
    FounderBand,
    FounderFactorPolarity,
    classify_founder_band,
)

# --------------------------------------------------------------------------------------
# Versioning. Every produced read and gap carries this id so any output is exactly
# reproducible from its policy version. Bump on any change to grades, weights, or bands.
# --------------------------------------------------------------------------------------

FOUNDER_READS_POLICY_VERSION: Final[VersionId] = "founder-reads.v0"

READ_BASELINE: Final = 50.0

# Half-width of the "aligned" band on the signed gap, in score points. Coarse by design.
ALIGNED_BAND: Final = 15.0


def _clamp_score(value: float) -> float:
    """Clamp to the documented 0..100 scale and drop float noise to one decimal."""
    return round(min(100.0, max(0.0, value)), 1)


# ======================================================================================
# Evidence grades
# ======================================================================================


class EvidenceGrade(StrEnum):
    """Evidence strength of one observation. Weaker grades count for strictly less."""

    OUTCOME_BACKED = "outcome_backed"  # A: outcome-backed / independently verifiable
    CORROBORATED = "corroborated"  # B: corroborated by an independent source
    SELF_ASSERTED = "self_asserted"  # C: self-asserted, not yet corroborated


# Multiplier applied to a factor's contribution magnitude. Monotone and coarse: a
# self-asserted claim (0.3) never counts like a verified outcome (1.0), and the 0.3 cap
# keeps a purely self-asserted builder read out of the STRONG band (see module docstring).
_GRADE_MULTIPLIER: Final[dict[EvidenceGrade, float]] = {
    EvidenceGrade.OUTCOME_BACKED: 1.0,
    EvidenceGrade.CORROBORATED: 0.6,
    EvidenceGrade.SELF_ASSERTED: 0.3,
}

# Contribution tier multiplier, mirroring the rubric's coarse three-step tier.
_TIER_MULTIPLIER: Final[dict[ContributionTier, float]] = {
    ContributionTier.FULL: 1.0,
    ContributionTier.PARTIAL: 0.5,
    ContributionTier.ABSENT: 0.0,
}


# ======================================================================================
# Read weight tables
# ======================================================================================

# Builder-signal weights are the taxonomy's own magnitudes: every registry factor keeps
# its ``max_weight``. Positives add, the negative subtracts, and neutral-context vanity
# is a hard zero because its registry ``max_weight`` is already zero.
BUILDER_WEIGHTS: Final[Mapping[str, float]] = {
    spec.factor_key: spec.max_weight for spec in FOUNDER_FACTOR_REGISTRY
}

# Fundability weights model conventional VC pattern-matching. Magnitudes only; the sign
# is taken from each factor's polarity so the verified negative subtracts here too. Deep
# craft signals a VC never inspects are set to zero so the read can diverge from
# substance. Every registry key is listed explicitly for determinism and transparency.
FUNDABILITY_WEIGHTS: Final[Mapping[str, float]] = {
    "shipped_adopted_work": 14.0,
    "corroborated_domain_experience": 6.0,
    "sustained_follow_through": 0.0,
    "peer_validated_recognition": 8.0,
    "work_product_quality": 0.0,
    "public_writing_depth": 0.0,
    "follower_reach": 10.0,
    "institutional_pedigree": 16.0,
    "presentation_polish": 12.0,
    "team_size": 8.0,
    "verified_negative_signal": 18.0,
}


# ======================================================================================
# Inputs and outputs
# ======================================================================================


@dataclass(frozen=True)
class GradedObservation:
    """One evidence-graded observation of a founder factor, fed to both reads.

    ``factor_key`` must be a key in ``FOUNDER_FACTOR_REGISTRY``. ``observed_value`` carries
    the raw read as a ``KnowledgeValue``; when its state is not KNOWN the observation
    contributes zero to every read regardless of ``tier`` or ``grade``, which is how
    absence is kept from ever moving a score. ``grade`` records the evidence strength and
    ``tier`` the coarse contribution strength.
    """

    factor_key: str
    tier: ContributionTier
    grade: EvidenceGrade
    observed_value: KnowledgeValue[ScalarValue]
    rationale: str = "graded observation"
    evidence_ids: tuple[StableId, ...] = ()


class ReadKind(StrEnum):
    """Which lens a read applies over the same graded observation set."""

    BUILDER_SIGNAL = "builder_signal"
    FUNDABILITY = "fundability"


@dataclass(frozen=True)
class ReadFactorContribution:
    """One factor's disclosed contribution to a single read.

    ``contribution`` is the signed point delta this factor added to the baseline in this
    read. ``counted`` records whether the factor actually moved the read, so a reviewer
    can distinguish "observed but weightless here" (a vanity attribute in the builder
    read) from "moved the score". ``grade`` is ``None`` only for an unobserved factor.
    """

    factor_key: str
    label: str
    polarity: FounderFactorPolarity
    grade: EvidenceGrade | None
    tier: ContributionTier
    weight: float
    contribution: float
    counted: bool
    rationale: str


@dataclass(frozen=True)
class FounderRead:
    """One lens over the graded observation set: a score plus its factor breakdown.

    The full taxonomy is always emitted, one entry per registry factor in canonical
    order, so an unobserved factor is shown as an explicit zero rather than hidden. The
    score is on the 0..100 scale with baseline 50.0; ``band`` is the coarse readable band.
    """

    kind: ReadKind
    score: float
    baseline: float
    band: FounderBand
    factors: tuple[ReadFactorContribution, ...]
    observed_factor_count: int
    counted_factor_count: int
    policy_version: VersionId


class GapLabel(StrEnum):
    """Qualitative label for the signed builder-versus-fundability gap."""

    UNDER_NETWORKED_STRONG_BUILDER = "under_networked_strong_builder"
    SUBSTANCE_LIGHT_BUT_FUNDABLE = "substance_light_but_fundable"
    ALIGNED = "aligned"


@dataclass(frozen=True)
class BuilderFundabilityGap:
    """The signed gap between the two reads plus a label and a human rationale.

    ``gap`` is ``builder_score - fundability_score`` on the 0..100 scale: positive when
    building substance outruns conventional fundability (an underrated builder), negative
    when fundability outruns substance. ``label`` names the case and ``rationale`` explains
    it so a human reviewer sees the divergence rather than a blended number.
    """

    builder_score: float
    fundability_score: float
    gap: float
    magnitude: float
    label: GapLabel
    aligned_band: float
    rationale: str
    policy_version: VersionId


# ======================================================================================
# Core read computation
# ======================================================================================


def _index_observations(
    observations: Sequence[GradedObservation],
) -> dict[str, GradedObservation]:
    """Validate factor keys and reject duplicates, returning a key -> observation map."""
    by_key: dict[str, GradedObservation] = {}
    for obs in observations:
        if obs.factor_key not in BUILDER_WEIGHTS:
            raise ValueError(f"unknown founder factor key: {obs.factor_key}")
        if obs.factor_key in by_key:
            raise ValueError(f"duplicate founder factor observation: {obs.factor_key}")
        by_key[obs.factor_key] = obs
    return by_key


def _read(
    observations: Sequence[GradedObservation],
    *,
    kind: ReadKind,
    weights: Mapping[str, float],
) -> FounderRead:
    """Compute one read: baseline plus each factor's signed, graded contribution.

    Deterministic: factors are emitted in fixed registry order and the tier and grade
    multipliers are fixed, so identical observations always produce identical reads.
    """
    obs_by_key = _index_observations(observations)

    contributions: list[ReadFactorContribution] = []
    total = READ_BASELINE
    observed_count = 0
    counted_count = 0

    for spec in FOUNDER_FACTOR_REGISTRY:
        weight = weights.get(spec.factor_key, 0.0)
        obs = obs_by_key.get(spec.factor_key)

        if obs is None:
            contributions.append(
                ReadFactorContribution(
                    factor_key=spec.factor_key,
                    label=spec.label,
                    polarity=spec.polarity,
                    grade=None,
                    tier=ContributionTier.ABSENT,
                    weight=weight,
                    contribution=0.0,
                    counted=False,
                    rationale="Not observed; absence contributes zero and never decrements.",
                )
            )
            continue

        observed_count += 1
        present = obs.observed_value.state is KnowledgeState.KNOWN
        is_negative = spec.polarity is FounderFactorPolarity.NEGATIVE

        if not present:
            contribution = 0.0
            rationale = "Observed value is not known; absence contributes zero."
        elif is_negative and not obs.evidence_ids:
            contribution = 0.0
            rationale = "Negative signal lacks supporting evidence; it cannot subtract."
        elif weight == 0.0:
            contribution = 0.0
            rationale = f"Carries zero weight in the {kind.value} read; contributes nothing."
        else:
            magnitude = weight * _TIER_MULTIPLIER[obs.tier] * _GRADE_MULTIPLIER[obs.grade]
            signed = -magnitude if is_negative else magnitude
            contribution = round(signed, 1)
            rationale = obs.rationale

        total += contribution
        counted = contribution != 0.0
        if counted:
            counted_count += 1
        contributions.append(
            ReadFactorContribution(
                factor_key=spec.factor_key,
                label=spec.label,
                polarity=spec.polarity,
                grade=obs.grade,
                tier=obs.tier,
                weight=weight,
                contribution=contribution,
                counted=counted,
                rationale=rationale,
            )
        )

    score = _clamp_score(total)
    return FounderRead(
        kind=kind,
        score=score,
        baseline=READ_BASELINE,
        band=classify_founder_band(score),
        factors=tuple(contributions),
        observed_factor_count=observed_count,
        counted_factor_count=counted_count,
        policy_version=FOUNDER_READS_POLICY_VERSION,
    )


def builder_signal_read(observations: Sequence[GradedObservation]) -> FounderRead:
    """Read building SUBSTANCE from costly-to-fake, peer-validated, outcome-linked signals.

    Every gameable vanity or non-predictive attribute (follower reach, institutional
    pedigree, presentation polish, team size) carries a hard-zero weight, so no vanity
    observation at any grade or tier can raise builder substance. Only present,
    evidence-graded positives add and only a present, sourced negative subtracts, so
    absence never decrements the read and cold-start founders sit at the baseline.
    """
    return _read(observations, kind=ReadKind.BUILDER_SIGNAL, weights=BUILDER_WEIGHTS)


def fundability_read(observations: Sequence[GradedObservation]) -> FounderRead:
    """Read what conventional VC pattern-matching rewards: network, pedigree, presentation.

    Unlike the builder read this DOES weight pedigree, audience, presentation polish, and
    team size, and it under-weights the deep craft signals a VC never inspects. It can
    therefore legitimately diverge from builder substance. Absence still never decrements
    the read; only present, evidence-graded observations move it.
    """
    return _read(observations, kind=ReadKind.FUNDABILITY, weights=FUNDABILITY_WEIGHTS)


# ======================================================================================
# Gap
# ======================================================================================


def _gap_rationale(
    label: GapLabel, *, builder_score: float, fundability_score: float, magnitude: float
) -> str:
    """Build a short, deterministic human rationale for the labelled gap."""
    if label is GapLabel.UNDER_NETWORKED_STRONG_BUILDER:
        return (
            f"Builder substance ({builder_score}) exceeds conventional fundability "
            f"({fundability_score}) by {magnitude} points: costly-to-fake building signals "
            "outrun network, pedigree, and presentation. Likely an under-networked strong "
            "builder a traditional VC screen would underrate."
        )
    if label is GapLabel.SUBSTANCE_LIGHT_BUT_FUNDABLE:
        # Be honest about the ABSOLUTE builder level: a below-baseline score is
        # substantiated-low, exactly baseline is unrated (no positive evidence either
        # way), and above baseline is real-but-modest substance. Calling any of these
        # "substance-light" would overclaim, so gate the wording on the builder score.
        if builder_score < READ_BASELINE:
            substance_phrase = f"demonstrated building substance is low ({builder_score})"
        elif builder_score == READ_BASELINE:
            substance_phrase = (
                f"building substance is not yet rated ({builder_score}); there is no "
                "positive building evidence either way"
            )
        else:
            substance_phrase = (
                f"demonstrated building substance ({builder_score}) is real but more "
                "modest than the fundability read"
            )
        return (
            f"Conventional fundability ({fundability_score}) exceeds builder substance by "
            f"{magnitude} points: network, pedigree, and presentation are inflating the "
            f"read relative to costly-to-fake building signals, and {substance_phrase}. "
            "Verify the building substance before pattern-matching on fundability."
        )
    return (
        f"Builder substance ({builder_score}) and conventional fundability "
        f"({fundability_score}) agree within the {ALIGNED_BAND}-point aligned band; the two "
        "lenses do not materially diverge."
    )


def builder_fundability_gap(
    builder: FounderRead, fundability: FounderRead
) -> BuilderFundabilityGap:
    """Return the signed builder-minus-fundability gap with a label and a rationale.

    ``builder`` must be a builder-signal read and ``fundability`` a fundability read; the
    kinds are validated so the two lenses are never transposed. A positive gap flags an
    under-networked strong builder, a negative gap flags a substance-light but fundable
    profile, and a gap within the +/-``ALIGNED_BAND`` band is aligned. Deterministic:
    the label follows fixed coarse thresholds over the two scores.
    """
    if builder.kind is not ReadKind.BUILDER_SIGNAL:
        raise ValueError("builder argument must be a builder-signal read")
    if fundability.kind is not ReadKind.FUNDABILITY:
        raise ValueError("fundability argument must be a fundability read")

    gap = round(builder.score - fundability.score, 1)
    magnitude = round(abs(gap), 1)
    if gap > ALIGNED_BAND:
        label = GapLabel.UNDER_NETWORKED_STRONG_BUILDER
    elif gap < -ALIGNED_BAND:
        label = GapLabel.SUBSTANCE_LIGHT_BUT_FUNDABLE
    else:
        label = GapLabel.ALIGNED

    return BuilderFundabilityGap(
        builder_score=builder.score,
        fundability_score=fundability.score,
        gap=gap,
        magnitude=magnitude,
        label=label,
        aligned_band=ALIGNED_BAND,
        rationale=_gap_rationale(
            label,
            builder_score=builder.score,
            fundability_score=fundability.score,
            magnitude=magnitude,
        ),
        policy_version=FOUNDER_READS_POLICY_VERSION,
    )


__all__ = [
    "ALIGNED_BAND",
    "BUILDER_WEIGHTS",
    "FOUNDER_READS_POLICY_VERSION",
    "FUNDABILITY_WEIGHTS",
    "READ_BASELINE",
    "BuilderFundabilityGap",
    "EvidenceGrade",
    "FounderRead",
    "GapLabel",
    "GradedObservation",
    "ReadFactorContribution",
    "ReadKind",
    "builder_fundability_gap",
    "builder_signal_read",
    "fundability_read",
]
