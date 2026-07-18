"""Deterministic, versioned v0 scoring rubrics for the two foundational containers.

This module owns the numeric weighting for exactly two FROZEN score containers:

1. :class:`ClaimTrustScore` - a per-claim trust read (never company- or founder-wide).
2. :class:`FounderScoreSnapshot` - a person-level provisional score snapshot.

The domain contracts in ``founderlookup.domain.scoring`` fix the shapes; this module
supplies the v0 numbers. Both rubrics are pure functions with no I/O, no live model,
and no hidden state, so identical inputs always yield identical outputs, and every
output carries an explicit policy version id.

Fairness and robustness invariants (adversarially tested, non-negotiable)
------------------------------------------------------------------------
- Absence never decrements. A missing observation, an unknown factor signal, or low
  coverage contributes exactly zero. Only a present, evidence-backed negative signal
  can pull a score below its neutral baseline.
- Sparse coverage sets ``provisional=True`` and raises qualitative uncertainty. It
  never subtracts from the score.
- Cold-start subjects are not penalized for having no public history. A founder with
  no observed factors sits at the neutral baseline, flagged provisional, with high
  uncertainty, not at zero.
- Unknown is its own case. An unknown factor signal is treated as neutral (zero
  delta), and when nothing at all has been assessed the claim trust state is
  ``UNSCORED`` rather than a fabricated low number. Unknown is never silently
  treated as a weakening signal.
- No false precision. Weights are small whole numbers and scores are reported on
  coarse documented bands, so the output does not imply accuracy the evidence
  cannot support.

Claim Trust rubric (v0)
-----------------------
Baseline 50.0 ("asserted, plausible, not yet corroborated"). Each of the six trust
factors applies a bounded additive delta by signal. Two factors are single-sided by
design so that absence can never harm a claim: corroboration can only strengthen or
be neutral, and contradiction can only weaken or be neutral. The other four are
symmetric because their negative side is a present property of real evidence (for
example a source that is known to be non-independent), not an absence of evidence.

    factor                signal deltas (strengthens / neutral / weakens)
    provenance            +8.0 /  0.0 /  -8.0
    independence         +10.0 /  0.0 /  -6.0
    recency               +6.0 /  0.0 /  -6.0
    extraction_certainty  +6.0 /  0.0 /  -8.0
    corroboration        +12.0 /  0.0 /   0.0   (positive-only)
    contradiction          0.0 /  0.0 / -20.0   (negative-only)

An unknown factor signal always maps to the neutral 0.0 delta. The reachable score
range is roughly [2, 92] before clamping to [0, 100].

Claim Trust bands: very_low <25, low <45, moderate <60, high <80, very_high >=80.

Founder Score rubric (v0)
-------------------------
Baseline 50.0 ("insufficient positive evidence to rate", not "mediocre founder").
The taxonomy is evidence-graded: costly-to-fake, peer-validated, outcome-linked
signals carry real positive weight, while gameable vanity signals and non-predictive
attributes (follower reach, institutional pedigree, presentation polish, team size)
carry zero weight and are recorded only as neutral context. Exactly one factor can
subtract, and only when it is backed by a known observation and present evidence.

    factor                          polarity          max weight  costly-to-fake
    shipped_adopted_work            positive              18.0    yes
    corroborated_domain_experience  positive              12.0    yes
    sustained_follow_through        positive              12.0    yes
    peer_validated_recognition      positive              10.0    yes
    work_product_quality            positive              10.0    yes
    public_writing_depth            positive               6.0    no
    follower_reach                  neutral_context        0.0    no
    institutional_pedigree          neutral_context        0.0    no
    presentation_polish             neutral_context        0.0    no
    team_size                       neutral_context        0.0    no
    verified_negative_signal        negative              18.0    no

Each observed factor is graded on a coarse three-step tier (full 1.0, partial 0.5,
absent 0.0) applied to its max weight. A factor whose observed value is not known
contributes zero regardless of tier, and a negative factor additionally contributes
zero unless it carries present evidence. Because only present positives add and only
present, evidence-backed negatives subtract, the sub-50 region is reachable only
through substantiated negative evidence, never through absence.

Founder bands: weak <35, below_baseline <50, baseline <60, moderate <75, strong >=75.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final

from founderlookup.domain.common import (
    KnowledgeState,
    KnowledgeValue,
    ScalarValue,
    Score100,
    StableId,
    VersionId,
)
from founderlookup.domain.scoring import (
    ClaimTrustFactor,
    ClaimTrustScore,
    CoverageLevel,
    CoverageSummary,
    FounderScoreFactor,
    FounderScoreSnapshot,
    QualitativeUncertainty,
    TrustFactorKind,
    TrustFactorSignal,
    TrustScoreState,
)

# --------------------------------------------------------------------------------------
# Versioning. Every produced container carries one of these ids so any output is exactly
# reproducible from its policy version. Bump these strings on any weighting change.
# --------------------------------------------------------------------------------------

CLAIM_TRUST_POLICY_VERSION: Final[VersionId] = "claim-trust-rubric.v0"
FOUNDER_SCORE_POLICY_VERSION: Final[VersionId] = "founder-score-rubric.v0"


def _clamp_score(value: float) -> Score100:
    """Clamp to the documented 0..100 scale and drop float noise to one decimal."""
    bounded = min(100.0, max(0.0, value))
    return round(bounded, 1)


# ======================================================================================
# Claim Trust rubric
# ======================================================================================

CLAIM_TRUST_BASELINE: Final = 50.0

# Additive delta by (factor kind, present signal). An unknown signal maps to the neutral
# 0.0 entry. Corroboration is positive-only and contradiction is negative-only so that a
# missing corroborating source or a missing contradiction can never move a score at all.
_TRUST_DELTAS: Final[dict[TrustFactorKind, dict[TrustFactorSignal, float]]] = {
    TrustFactorKind.PROVENANCE: {
        TrustFactorSignal.STRENGTHENS: 8.0,
        TrustFactorSignal.NEUTRAL: 0.0,
        TrustFactorSignal.WEAKENS: -8.0,
    },
    TrustFactorKind.INDEPENDENCE: {
        TrustFactorSignal.STRENGTHENS: 10.0,
        TrustFactorSignal.NEUTRAL: 0.0,
        TrustFactorSignal.WEAKENS: -6.0,
    },
    TrustFactorKind.RECENCY: {
        TrustFactorSignal.STRENGTHENS: 6.0,
        TrustFactorSignal.NEUTRAL: 0.0,
        TrustFactorSignal.WEAKENS: -6.0,
    },
    TrustFactorKind.EXTRACTION_CERTAINTY: {
        TrustFactorSignal.STRENGTHENS: 6.0,
        TrustFactorSignal.NEUTRAL: 0.0,
        TrustFactorSignal.WEAKENS: -8.0,
    },
    TrustFactorKind.CORROBORATION: {
        TrustFactorSignal.STRENGTHENS: 12.0,
        TrustFactorSignal.NEUTRAL: 0.0,
        TrustFactorSignal.WEAKENS: 0.0,
    },
    TrustFactorKind.CONTRADICTION: {
        TrustFactorSignal.STRENGTHENS: 0.0,
        TrustFactorSignal.NEUTRAL: 0.0,
        TrustFactorSignal.WEAKENS: -20.0,
    },
}


class TrustBand(StrEnum):
    """Coarse label for a claim trust score, used for display and tests."""

    VERY_LOW = "very_low"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    VERY_HIGH = "very_high"


def classify_trust_band(score: float) -> TrustBand:
    """Map a 0..100 trust score to its documented coarse band."""
    if score < 25.0:
        return TrustBand.VERY_LOW
    if score < 45.0:
        return TrustBand.LOW
    if score < 60.0:
        return TrustBand.MODERATE
    if score < 80.0:
        return TrustBand.HIGH
    return TrustBand.VERY_HIGH


@dataclass(frozen=True)
class TrustFactorInput:
    """One qualitative trust-factor read handed to the claim trust rubric.

    ``signal`` is a ``KnowledgeValue`` so an unassessed factor is an explicit unknown
    rather than a fabricated neutral. An unknown signal always contributes 0.0.
    """

    kind: TrustFactorKind
    signal: KnowledgeValue[TrustFactorSignal]
    rationale: str
    evidence_ids: tuple[StableId, ...] = ()


def _signal_of(value: KnowledgeValue[TrustFactorSignal]) -> TrustFactorSignal | None:
    """Return the present signal, or None when the read is any non-known state."""
    if value.state is KnowledgeState.KNOWN:
        return value.value
    return None


def _trust_delta(kind: TrustFactorKind, signal: TrustFactorSignal | None) -> float:
    """Deterministic per-factor delta; an unknown (None) signal is neutral by rule."""
    if signal is None:
        return 0.0
    return _TRUST_DELTAS[kind][signal]


def _ordered_trust_inputs(
    factors: Sequence[TrustFactorInput],
) -> tuple[TrustFactorInput, ...]:
    """Validate the six required factor kinds and return them in canonical order."""
    by_kind: dict[TrustFactorKind, TrustFactorInput] = {}
    for factor in factors:
        if factor.kind in by_kind:
            raise ValueError(f"duplicate trust factor kind: {factor.kind.value}")
        by_kind[factor.kind] = factor
    missing = [kind.value for kind in TrustFactorKind if kind not in by_kind]
    if missing:
        raise ValueError(f"claim trust requires all six factor kinds; missing: {missing}")
    return tuple(by_kind[kind] for kind in TrustFactorKind)


def score_claim_trust(
    factors: Sequence[TrustFactorInput],
    *,
    has_supporting_evidence: bool,
    unresolved_blocking_contradiction: bool = False,
) -> ClaimTrustScore:
    """Score one claim's trust, or return an explicit unscored/unsupported state.

    The caller always supplies exactly the six trust-factor reads (unknown reads are
    allowed and treated as neutral). State is chosen before any number is produced:

    - no supporting evidence at all -> UNSUPPORTED (a conclusion with nothing behind it);
    - an unresolved blocking contradiction -> UNSCORED (trust is withheld, not lowered
      to a misleadingly precise number);
    - nothing assessed yet (every factor signal unknown) -> UNSCORED;
    - otherwise -> SCORED from the baseline plus the six bounded factor deltas.

    Deterministic: factors are consumed in a fixed canonical order and unknown signals
    always map to a zero delta, so identical inputs always produce identical output.
    """
    ordered = _ordered_trust_inputs(factors)

    if not has_supporting_evidence:
        return ClaimTrustScore(
            state=TrustScoreState.UNSUPPORTED,
            trust_policy_version=CLAIM_TRUST_POLICY_VERSION,
            reason="No valid supporting evidence; the claim cannot be trust-scored.",
        )

    if unresolved_blocking_contradiction:
        return ClaimTrustScore(
            state=TrustScoreState.UNSCORED,
            trust_policy_version=CLAIM_TRUST_POLICY_VERSION,
            reason=(
                "An unresolved blocking contradiction is present; trust is withheld "
                "until the conflict is resolved rather than reported as a number."
            ),
        )

    signals = {factor.kind: _signal_of(factor.signal) for factor in ordered}
    known_count = sum(1 for signal in signals.values() if signal is not None)
    if known_count == 0:
        return ClaimTrustScore(
            state=TrustScoreState.UNSCORED,
            trust_policy_version=CLAIM_TRUST_POLICY_VERSION,
            reason="No trust factor has been assessed yet; cannot score the claim.",
        )

    total = CLAIM_TRUST_BASELINE + sum(
        _trust_delta(factor.kind, signals[factor.kind]) for factor in ordered
    )
    trust_factors = tuple(
        ClaimTrustFactor(
            kind=factor.kind,
            signal=factor.signal,
            evidence_ids=factor.evidence_ids,
            rationale=factor.rationale,
        )
        for factor in ordered
    )
    return ClaimTrustScore(
        state=TrustScoreState.SCORED,
        trust_policy_version=CLAIM_TRUST_POLICY_VERSION,
        score=_clamp_score(total),
        factors=trust_factors,
    )


# ======================================================================================
# Founder Score rubric
# ======================================================================================

FOUNDER_SCORE_BASELINE: Final = 50.0

# Below this many present costly-to-fake positive factors the snapshot is provisional
# even at higher coverage, because a single strong signal is too thin to be established.
_MIN_COSTLY_FOR_ESTABLISHED: Final = 2


class ContributionTier(StrEnum):
    """Coarse strength of one observed founder factor. Absent contributes nothing."""

    FULL = "full"
    PARTIAL = "partial"
    ABSENT = "absent"


_TIER_MULTIPLIER: Final[dict[ContributionTier, float]] = {
    ContributionTier.FULL: 1.0,
    ContributionTier.PARTIAL: 0.5,
    ContributionTier.ABSENT: 0.0,
}


class FounderFactorPolarity(StrEnum):
    """How a founder factor may move the score."""

    POSITIVE = "positive"
    NEUTRAL_CONTEXT = "neutral_context"
    NEGATIVE = "negative"


@dataclass(frozen=True)
class FounderFactorSpec:
    """Fixed v0 definition of one founder factor: its weight, polarity, and role."""

    factor_key: str
    label: str
    polarity: FounderFactorPolarity
    max_weight: float
    costly_to_fake: bool


# Canonical taxonomy, emitted in this exact order for every snapshot so the factor tuple
# is stable and deterministic. Costly-to-fake, outcome-linked positives carry weight;
# gameable vanity and non-predictive attributes carry zero and are neutral context only.
FOUNDER_FACTOR_REGISTRY: Final[tuple[FounderFactorSpec, ...]] = (
    FounderFactorSpec(
        factor_key="shipped_adopted_work",
        label="Shipped and externally adopted work",
        polarity=FounderFactorPolarity.POSITIVE,
        max_weight=18.0,
        costly_to_fake=True,
    ),
    FounderFactorSpec(
        factor_key="corroborated_domain_experience",
        label="Corroborated domain experience",
        polarity=FounderFactorPolarity.POSITIVE,
        max_weight=12.0,
        costly_to_fake=True,
    ),
    FounderFactorSpec(
        factor_key="sustained_follow_through",
        label="Sustained building follow-through",
        polarity=FounderFactorPolarity.POSITIVE,
        max_weight=12.0,
        costly_to_fake=True,
    ),
    FounderFactorSpec(
        factor_key="peer_validated_recognition",
        label="Peer-validated technical recognition",
        polarity=FounderFactorPolarity.POSITIVE,
        max_weight=10.0,
        costly_to_fake=True,
    ),
    FounderFactorSpec(
        factor_key="work_product_quality",
        label="Source-backed work-product quality",
        polarity=FounderFactorPolarity.POSITIVE,
        max_weight=10.0,
        costly_to_fake=True,
    ),
    FounderFactorSpec(
        factor_key="public_writing_depth",
        label="Depth of public technical writing",
        polarity=FounderFactorPolarity.POSITIVE,
        max_weight=6.0,
        costly_to_fake=False,
    ),
    FounderFactorSpec(
        factor_key="follower_reach",
        label="Follower reach (context only)",
        polarity=FounderFactorPolarity.NEUTRAL_CONTEXT,
        max_weight=0.0,
        costly_to_fake=False,
    ),
    FounderFactorSpec(
        factor_key="institutional_pedigree",
        label="Institutional pedigree (context only)",
        polarity=FounderFactorPolarity.NEUTRAL_CONTEXT,
        max_weight=0.0,
        costly_to_fake=False,
    ),
    FounderFactorSpec(
        factor_key="presentation_polish",
        label="Presentation polish (context only)",
        polarity=FounderFactorPolarity.NEUTRAL_CONTEXT,
        max_weight=0.0,
        costly_to_fake=False,
    ),
    FounderFactorSpec(
        factor_key="team_size",
        label="Team size (context only)",
        polarity=FounderFactorPolarity.NEUTRAL_CONTEXT,
        max_weight=0.0,
        costly_to_fake=False,
    ),
    FounderFactorSpec(
        factor_key="verified_negative_signal",
        label="Verified negative signal",
        polarity=FounderFactorPolarity.NEGATIVE,
        max_weight=18.0,
        costly_to_fake=False,
    ),
)

_REGISTRY_BY_KEY: Final[dict[str, FounderFactorSpec]] = {
    spec.factor_key: spec for spec in FOUNDER_FACTOR_REGISTRY
}


class FounderBand(StrEnum):
    """Coarse label for a founder score, used for display and tests."""

    WEAK = "weak"
    BELOW_BASELINE = "below_baseline"
    BASELINE = "baseline"
    MODERATE = "moderate"
    STRONG = "strong"


def classify_founder_band(score: float) -> FounderBand:
    """Map a 0..100 founder score to its documented coarse band."""
    if score < 35.0:
        return FounderBand.WEAK
    if score < 50.0:
        return FounderBand.BELOW_BASELINE
    if score < 60.0:
        return FounderBand.BASELINE
    if score < 75.0:
        return FounderBand.MODERATE
    return FounderBand.STRONG


@dataclass(frozen=True)
class FounderFactorObservation:
    """One observed founder factor handed to the founder score rubric.

    ``observed_value`` carries the raw read as a ``KnowledgeValue``; when it is not
    known the factor contributes zero regardless of ``tier``, which is how absence is
    kept from ever lowering the score. A negative factor additionally contributes zero
    unless ``evidence_ids`` is non-empty, so only evidence-backed negatives subtract.
    """

    factor_key: str
    tier: ContributionTier
    observed_value: KnowledgeValue[ScalarValue]
    rationale: str
    evidence_ids: tuple[StableId, ...] = field(default_factory=tuple)


def _effective_tier(spec: FounderFactorSpec, obs: FounderFactorObservation) -> ContributionTier:
    """Resolve the tier that actually applies after the fairness guards.

    Absence and unknowns collapse to ABSENT (zero contribution). A negative factor
    without present evidence also collapses to ABSENT so it cannot subtract.
    """
    if obs.observed_value.state is not KnowledgeState.KNOWN:
        return ContributionTier.ABSENT
    if spec.polarity is FounderFactorPolarity.NEGATIVE and not obs.evidence_ids:
        return ContributionTier.ABSENT
    return obs.tier


def _contribution_value(spec: FounderFactorSpec, tier: ContributionTier) -> float:
    """Signed point contribution for one factor at a resolved tier."""
    if spec.polarity is FounderFactorPolarity.NEUTRAL_CONTEXT:
        return 0.0
    magnitude = spec.max_weight * _TIER_MULTIPLIER[tier]
    if spec.polarity is FounderFactorPolarity.NEGATIVE:
        return round(-magnitude, 1)
    return round(magnitude, 1)


def _bump_uncertainty(level: QualitativeUncertainty) -> QualitativeUncertainty:
    """Raise qualitative uncertainty by one step toward HIGH, never past it."""
    order = (
        QualitativeUncertainty.LOW,
        QualitativeUncertainty.MODERATE,
        QualitativeUncertainty.HIGH,
    )
    return order[min(order.index(level) + 1, len(order) - 1)]


def _base_uncertainty(level: CoverageLevel) -> QualitativeUncertainty:
    """Coverage richness sets the starting uncertainty; it never touches the score."""
    if level is CoverageLevel.LOW:
        return QualitativeUncertainty.HIGH
    if level is CoverageLevel.MEDIUM:
        return QualitativeUncertainty.MODERATE
    return QualitativeUncertainty.LOW


def score_founder(
    *,
    founder_id: StableId,
    snapshot_id: StableId,
    snapshot_version_id: StableId,
    as_of: datetime,
    coverage: CoverageSummary,
    observations: Sequence[FounderFactorObservation],
) -> FounderScoreSnapshot:
    """Produce a person-level founder score snapshot from graded factor observations.

    The full taxonomy is always emitted, one factor per registry entry in canonical
    order, so an unobserved factor is shown as an explicit zero rather than hidden.
    Coverage drives only ``provisional`` and ``uncertainty``; it is never subtracted
    from the score. The sub-baseline region is reachable only through a present,
    evidence-backed negative factor, so cold-start absence cannot push a founder down.

    Deterministic: registry order and the coarse tier multipliers are fixed, so
    identical observations and coverage always yield the same snapshot.
    """
    seen: set[str] = set()
    obs_by_key: dict[str, FounderFactorObservation] = {}
    for entry in observations:
        if entry.factor_key not in _REGISTRY_BY_KEY:
            raise ValueError(f"unknown founder factor key: {entry.factor_key}")
        if entry.factor_key in seen:
            raise ValueError(f"duplicate founder factor observation: {entry.factor_key}")
        seen.add(entry.factor_key)
        obs_by_key[entry.factor_key] = entry

    factors: list[FounderScoreFactor] = []
    total = FOUNDER_SCORE_BASELINE
    present_costly_positive = 0

    for spec in FOUNDER_FACTOR_REGISTRY:
        observed = obs_by_key.get(spec.factor_key)
        if observed is None:
            factors.append(
                FounderScoreFactor(
                    factor_key=spec.factor_key,
                    label=spec.label,
                    observed_value=KnowledgeValue[ScalarValue].unknown(
                        "Not observed in this snapshot."
                    ),
                    contribution=KnowledgeValue[float].known(0.0),
                    rationale=(
                        "Not observed; contributes zero so its absence does not lower the score."
                    ),
                )
            )
            continue

        tier = _effective_tier(spec, observed)
        contribution = _contribution_value(spec, tier)
        total += contribution
        if (
            spec.polarity is FounderFactorPolarity.POSITIVE
            and spec.costly_to_fake
            and contribution > 0.0
        ):
            present_costly_positive += 1
        factors.append(
            FounderScoreFactor(
                factor_key=spec.factor_key,
                label=spec.label,
                observed_value=observed.observed_value,
                contribution=KnowledgeValue[float].known(contribution),
                evidence_ids=observed.evidence_ids,
                rationale=observed.rationale,
            )
        )

    has_conflict = bool(coverage.conflicted_fields)
    provisional = (
        coverage.level is CoverageLevel.LOW
        or has_conflict
        or present_costly_positive < _MIN_COSTLY_FOR_ESTABLISHED
    )
    # Coverage sets the base uncertainty and never touches the score: richer
    # coverage narrows the band. It widens by one step only when sources actively
    # conflict or when there is no costly-to-fake positive to anchor the read at
    # all. Uncertainty (confidence given the observed sources) is a separate axis
    # from provisional (whether an established costly-to-fake record exists), so a
    # single strong signal can be low-uncertainty under rich coverage yet still
    # provisional.
    uncertainty = _base_uncertainty(coverage.level)
    if has_conflict or present_costly_positive == 0:
        uncertainty = _bump_uncertainty(uncertainty)

    return FounderScoreSnapshot(
        snapshot_id=snapshot_id,
        snapshot_version_id=snapshot_version_id,
        founder_id=founder_id,
        score_policy_version=FOUNDER_SCORE_POLICY_VERSION,
        as_of=as_of,
        score=_clamp_score(total),
        factors=tuple(factors),
        coverage=coverage,
        uncertainty=uncertainty,
        provisional=provisional,
    )


__all__ = [
    "CLAIM_TRUST_BASELINE",
    "CLAIM_TRUST_POLICY_VERSION",
    "FOUNDER_FACTOR_REGISTRY",
    "FOUNDER_SCORE_BASELINE",
    "FOUNDER_SCORE_POLICY_VERSION",
    "ContributionTier",
    "FounderBand",
    "FounderFactorObservation",
    "FounderFactorPolarity",
    "FounderFactorSpec",
    "TrustBand",
    "TrustFactorInput",
    "classify_founder_band",
    "classify_trust_band",
    "score_claim_trust",
    "score_founder",
]
