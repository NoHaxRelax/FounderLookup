"""Deterministic tests for the v0 claim-trust and founder-score rubrics.

The suite pins the fairness and robustness invariants that task 3.4 must hold:
absence never decrements, sparse coverage only flags provisional and widens
uncertainty, unknown signals are neutral rather than weakening, and every output
carries an explicit policy version.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

import pytest

from founderlookup.domain.common import KnowledgeAlternative, KnowledgeValue, ScalarValue
from founderlookup.domain.scoring import (
    CoverageLevel,
    CoverageSummary,
    FounderScoreSnapshot,
    QualitativeUncertainty,
    TrustFactorKind,
    TrustFactorSignal,
    TrustScoreState,
)
from founderlookup.screening.rubrics import (
    CLAIM_TRUST_BASELINE,
    CLAIM_TRUST_POLICY_VERSION,
    FOUNDER_FACTOR_REGISTRY,
    FOUNDER_SCORE_BASELINE,
    FOUNDER_SCORE_POLICY_VERSION,
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

NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def _trust_factors(
    signals: Mapping[TrustFactorKind, TrustFactorSignal | None] | None = None,
) -> list[TrustFactorInput]:
    """Build the six factor reads; a mapped None means an explicit unknown read."""
    overrides = signals or {}
    factors: list[TrustFactorInput] = []
    for kind in TrustFactorKind:
        if kind in overrides and overrides[kind] is None:
            signal = KnowledgeValue[TrustFactorSignal].unknown("not assessed")
        elif kind in overrides:
            value = overrides[kind]
            assert value is not None
            signal = KnowledgeValue[TrustFactorSignal].known(value)
        else:
            signal = KnowledgeValue[TrustFactorSignal].known(TrustFactorSignal.NEUTRAL)
        factors.append(TrustFactorInput(kind=kind, signal=signal, rationale="test read"))
    return factors


def _trust_factors_with(
    kind: TrustFactorKind,
    signal: KnowledgeValue[TrustFactorSignal],
) -> list[TrustFactorInput]:
    """All-neutral reads with one kind replaced by an arbitrary KnowledgeValue read."""
    return [
        TrustFactorInput(kind=factor.kind, signal=signal, rationale=factor.rationale)
        if factor.kind is kind
        else factor
        for factor in _trust_factors()
    ]


def _conflicted_signal() -> KnowledgeValue[TrustFactorSignal]:
    """A read whose sources genuinely disagree between strengthens and weakens."""
    return KnowledgeValue[TrustFactorSignal].conflicted(
        "sources disagree on this factor",
        (
            KnowledgeAlternative[TrustFactorSignal](
                value=TrustFactorSignal.STRENGTHENS, evidence_ids=("evidence:s",)
            ),
            KnowledgeAlternative[TrustFactorSignal](
                value=TrustFactorSignal.WEAKENS, evidence_ids=("evidence:w",)
            ),
        ),
    )


def _non_known_signals() -> list[KnowledgeValue[TrustFactorSignal]]:
    """Every non-known KnowledgeValue state a factor read can carry."""
    return [
        KnowledgeValue[TrustFactorSignal].unknown("not assessed"),
        KnowledgeValue[TrustFactorSignal].not_disclosed("subject withheld it"),
        KnowledgeValue[TrustFactorSignal].not_applicable("not applicable here"),
        _conflicted_signal(),
    ]


def _coverage(
    level: CoverageLevel,
    *,
    source_count: int = 1,
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


def _obs(
    factor_key: str,
    tier: ContributionTier,
    *,
    value: ScalarValue = "observed",
    evidence: tuple[str, ...] = ("evidence:1",),
    known: bool = True,
) -> FounderFactorObservation:
    observed = (
        KnowledgeValue[ScalarValue].known(value)
        if known
        else KnowledgeValue[ScalarValue].unknown("not observed")
    )
    return FounderFactorObservation(
        factor_key=factor_key,
        tier=tier,
        observed_value=observed,
        rationale="test observation",
        evidence_ids=evidence,
    )


def _founder(
    observations: list[FounderFactorObservation],
    coverage: CoverageSummary,
) -> FounderScoreSnapshot:
    return score_founder(
        founder_id="founder:1",
        snapshot_id="snapshot:1",
        snapshot_version_id="snapshot-version:1",
        as_of=NOW,
        coverage=coverage,
        observations=observations,
    )


# ======================================================================================
# Claim Trust rubric
# ======================================================================================


def test_all_neutral_known_factors_score_at_baseline() -> None:
    trust = score_claim_trust(_trust_factors(), has_supporting_evidence=True)
    assert trust.state is TrustScoreState.SCORED
    assert trust.score == CLAIM_TRUST_BASELINE
    assert trust.trust_policy_version == CLAIM_TRUST_POLICY_VERSION
    assert classify_trust_band(trust.score) is TrustBand.MODERATE
    assert {factor.kind for factor in trust.factors} == set(TrustFactorKind)


def test_strong_evidence_lifts_trust_into_high_band() -> None:
    trust = score_claim_trust(
        _trust_factors(
            {
                TrustFactorKind.PROVENANCE: TrustFactorSignal.STRENGTHENS,
                TrustFactorKind.INDEPENDENCE: TrustFactorSignal.STRENGTHENS,
                TrustFactorKind.CORROBORATION: TrustFactorSignal.STRENGTHENS,
                TrustFactorKind.RECENCY: TrustFactorSignal.STRENGTHENS,
            }
        ),
        has_supporting_evidence=True,
    )
    # 50 + 8 + 10 + 12 + 6 = 86
    assert trust.score == 86.0
    assert classify_trust_band(trust.score) is TrustBand.VERY_HIGH


def test_founder_asserted_non_independent_source_scores_below_baseline() -> None:
    trust = score_claim_trust(
        _trust_factors({TrustFactorKind.INDEPENDENCE: TrustFactorSignal.WEAKENS}),
        has_supporting_evidence=True,
    )
    assert trust.score == 44.0
    assert classify_trust_band(trust.score) is TrustBand.LOW


def test_corroboration_weakens_signal_cannot_decrement() -> None:
    # A "weakens" on the positive-only corroboration factor must be treated as neutral:
    # a missing corroborating source can never be a penalty.
    trust = score_claim_trust(
        _trust_factors({TrustFactorKind.CORROBORATION: TrustFactorSignal.WEAKENS}),
        has_supporting_evidence=True,
    )
    assert trust.score == CLAIM_TRUST_BASELINE


def test_contradiction_strengthens_signal_cannot_increment() -> None:
    # Contradiction is negative-only; a "strengthens" read cannot manufacture trust.
    trust = score_claim_trust(
        _trust_factors({TrustFactorKind.CONTRADICTION: TrustFactorSignal.STRENGTHENS}),
        has_supporting_evidence=True,
    )
    assert trust.score == CLAIM_TRUST_BASELINE


def test_non_blocking_contradiction_lowers_but_still_scores() -> None:
    trust = score_claim_trust(
        _trust_factors({TrustFactorKind.CONTRADICTION: TrustFactorSignal.WEAKENS}),
        has_supporting_evidence=True,
    )
    assert trust.state is TrustScoreState.SCORED
    assert trust.score == 30.0
    assert classify_trust_band(trust.score) is TrustBand.LOW


def test_unknown_signal_matches_neutral_delta() -> None:
    scored_neutral = score_claim_trust(
        _trust_factors({TrustFactorKind.INDEPENDENCE: TrustFactorSignal.STRENGTHENS}),
        has_supporting_evidence=True,
    )
    scored_with_unknown = score_claim_trust(
        _trust_factors(
            {
                TrustFactorKind.INDEPENDENCE: TrustFactorSignal.STRENGTHENS,
                TrustFactorKind.RECENCY: None,
            }
        ),
        has_supporting_evidence=True,
    )
    assert scored_neutral.score == scored_with_unknown.score == 60.0


def test_all_unknown_factors_cannot_be_scored_yet() -> None:
    trust = score_claim_trust(
        _trust_factors({kind: None for kind in TrustFactorKind}),
        has_supporting_evidence=True,
    )
    assert trust.state is TrustScoreState.UNSCORED
    assert trust.score is None
    assert trust.reason is not None
    assert trust.trust_policy_version == CLAIM_TRUST_POLICY_VERSION


def test_every_non_known_state_is_neutral_never_weakens() -> None:
    # The central fairness invariant generalized past `unknown`: no absence-flavored
    # state (unknown, not_disclosed, not_applicable, conflicted) may be read as WEAKENS.
    # Placed on provenance, whose WEAKENS delta is -8, every one must leave the baseline.
    for signal in _non_known_signals():
        trust = score_claim_trust(
            _trust_factors_with(TrustFactorKind.PROVENANCE, signal),
            has_supporting_evidence=True,
        )
        assert trust.state is TrustScoreState.SCORED
        assert trust.score == CLAIM_TRUST_BASELINE


def test_conflicted_signal_does_not_manufacture_or_destroy_trust() -> None:
    # A conflicted read whose alternatives include STRENGTHENS must not add points, and
    # on a symmetric factor whose alternatives include WEAKENS must not subtract them.
    conflicted = _conflicted_signal()
    on_independence = score_claim_trust(
        _trust_factors_with(TrustFactorKind.INDEPENDENCE, conflicted),
        has_supporting_evidence=True,
    )
    assert on_independence.score == CLAIM_TRUST_BASELINE


def test_all_non_known_states_cannot_be_scored_yet() -> None:
    # "Cannot score yet" must trigger for any all-non-known combination, not just when
    # every factor is literally `unknown`; here every factor is not_disclosed.
    factors = [
        TrustFactorInput(
            kind=kind,
            signal=KnowledgeValue[TrustFactorSignal].not_disclosed("withheld"),
            rationale="test read",
        )
        for kind in TrustFactorKind
    ]
    trust = score_claim_trust(factors, has_supporting_evidence=True)
    assert trust.state is TrustScoreState.UNSCORED
    assert trust.score is None
    assert trust.reason is not None


def test_scored_trust_preserves_input_signal_and_evidence() -> None:
    factors = _trust_factors()
    provenance = next(i for i, f in enumerate(factors) if f.kind is TrustFactorKind.PROVENANCE)
    factors[provenance] = TrustFactorInput(
        kind=TrustFactorKind.PROVENANCE,
        signal=KnowledgeValue[TrustFactorSignal].known(
            TrustFactorSignal.STRENGTHENS, evidence_ids=("evidence:p",)
        ),
        rationale="signed commit history",
        evidence_ids=("evidence:p",),
    )
    trust = score_claim_trust(factors, has_supporting_evidence=True)
    assert trust.state is TrustScoreState.SCORED
    emitted = {f.kind: f for f in trust.factors}
    assert emitted[TrustFactorKind.PROVENANCE].evidence_ids == ("evidence:p",)
    assert emitted[TrustFactorKind.PROVENANCE].signal.value is TrustFactorSignal.STRENGTHENS


def test_missing_supporting_evidence_is_unsupported() -> None:
    trust = score_claim_trust(_trust_factors(), has_supporting_evidence=False)
    assert trust.state is TrustScoreState.UNSUPPORTED
    assert trust.score is None
    assert trust.reason is not None


def test_blocking_contradiction_withholds_the_score() -> None:
    trust = score_claim_trust(
        _trust_factors(
            {
                TrustFactorKind.CORROBORATION: TrustFactorSignal.STRENGTHENS,
                TrustFactorKind.PROVENANCE: TrustFactorSignal.STRENGTHENS,
            }
        ),
        has_supporting_evidence=True,
        unresolved_blocking_contradiction=True,
    )
    assert trust.state is TrustScoreState.UNSCORED
    assert trust.score is None
    assert trust.reason is not None


def test_claim_trust_is_deterministic() -> None:
    signals = {
        TrustFactorKind.PROVENANCE: TrustFactorSignal.STRENGTHENS,
        TrustFactorKind.CONTRADICTION: TrustFactorSignal.WEAKENS,
    }
    first = score_claim_trust(_trust_factors(signals), has_supporting_evidence=True)
    second = score_claim_trust(_trust_factors(signals), has_supporting_evidence=True)
    assert first == second


def test_missing_factor_kind_is_rejected() -> None:
    factors = _trust_factors()[:-1]
    with pytest.raises(ValueError, match="all six factor kinds"):
        score_claim_trust(factors, has_supporting_evidence=True)


def test_duplicate_factor_kind_is_rejected() -> None:
    factors = _trust_factors()
    factors.append(factors[0])
    with pytest.raises(ValueError, match="duplicate trust factor kind"):
        score_claim_trust(factors, has_supporting_evidence=True)


def test_scored_trust_score_never_leaves_the_scale() -> None:
    lifted = score_claim_trust(
        _trust_factors({kind: TrustFactorSignal.STRENGTHENS for kind in TrustFactorKind}),
        has_supporting_evidence=True,
    )
    dropped = score_claim_trust(
        _trust_factors({kind: TrustFactorSignal.WEAKENS for kind in TrustFactorKind}),
        has_supporting_evidence=True,
    )
    assert 0.0 <= dropped.score <= lifted.score <= 100.0  # type: ignore[operator]


# ======================================================================================
# Founder Score rubric
# ======================================================================================


def test_cold_start_founder_sits_at_baseline_not_zero() -> None:
    snapshot = _founder([], _coverage(CoverageLevel.LOW))
    assert snapshot.score == FOUNDER_SCORE_BASELINE
    assert snapshot.provisional is True
    assert snapshot.uncertainty is QualitativeUncertainty.HIGH
    assert snapshot.score_policy_version == FOUNDER_SCORE_POLICY_VERSION
    assert classify_founder_band(snapshot.score) is FounderBand.BASELINE
    # The full taxonomy is emitted, with unobserved factors shown as explicit zeros.
    assert len(snapshot.factors) == len(FOUNDER_FACTOR_REGISTRY)
    assert all(f.contribution.value == 0.0 for f in snapshot.factors)


def test_cold_start_work_product_raises_score_without_penalizing_absence() -> None:
    snapshot = _founder(
        [
            _obs("work_product_quality", ContributionTier.FULL),
            _obs("peer_validated_recognition", ContributionTier.FULL),
        ],
        _coverage(CoverageLevel.LOW),
    )
    # 50 + 10 + 10 = 70; positive evidence lifts, sparse coverage only flags provisional.
    assert snapshot.score == 70.0
    assert snapshot.provisional is True
    assert snapshot.uncertainty is QualitativeUncertainty.HIGH
    assert classify_founder_band(snapshot.score) is FounderBand.MODERATE


def test_sparse_coverage_never_changes_the_score() -> None:
    observations = [_obs("work_product_quality", ContributionTier.FULL)]
    low = _founder(observations, _coverage(CoverageLevel.LOW))
    high = _founder(observations, _coverage(CoverageLevel.HIGH))
    # The core invariant: coverage never moves the number.
    assert low.score == high.score == 60.0
    # A single costly-to-fake positive is not yet an established record, so the score
    # stays provisional under either coverage level.
    assert low.provisional is True
    assert high.provisional is True
    # Coverage drives only uncertainty, and only ever narrows it: sparse coverage is the
    # widest band and richer coverage is never wider than sparse coverage.
    _width = {
        QualitativeUncertainty.LOW: 0,
        QualitativeUncertainty.MODERATE: 1,
        QualitativeUncertainty.HIGH: 2,
    }
    assert low.uncertainty is QualitativeUncertainty.HIGH
    assert _width[high.uncertainty] <= _width[low.uncertainty]


def test_established_founder_is_not_provisional() -> None:
    snapshot = _founder(
        [
            _obs("shipped_adopted_work", ContributionTier.FULL),
            _obs("corroborated_domain_experience", ContributionTier.FULL),
        ],
        _coverage(CoverageLevel.MEDIUM, source_count=2),
    )
    # 50 + 18 + 12 = 80
    assert snapshot.score == 80.0
    assert snapshot.provisional is False
    assert snapshot.uncertainty is QualitativeUncertainty.MODERATE
    assert classify_founder_band(snapshot.score) is FounderBand.STRONG


def test_conflict_flags_provisional_and_widens_uncertainty() -> None:
    snapshot = _founder(
        [
            _obs("shipped_adopted_work", ContributionTier.FULL),
            _obs("corroborated_domain_experience", ContributionTier.FULL),
        ],
        _coverage(
            CoverageLevel.MEDIUM,
            source_count=2,
            conflicted_fields=("current_arr",),
        ),
    )
    # Conflict raises uncertainty and forces provisional but leaves the score untouched.
    assert snapshot.score == 80.0
    assert snapshot.provisional is True
    assert snapshot.uncertainty is QualitativeUncertainty.HIGH


def test_vanity_signal_contributes_nothing() -> None:
    snapshot = _founder(
        [_obs("follower_reach", ContributionTier.FULL, value=100000)],
        _coverage(CoverageLevel.MEDIUM, source_count=2),
    )
    assert snapshot.score == FOUNDER_SCORE_BASELINE
    follower = next(f for f in snapshot.factors if f.factor_key == "follower_reach")
    assert follower.contribution.value == 0.0


def test_verified_negative_signal_with_evidence_subtracts() -> None:
    snapshot = _founder(
        [_obs("verified_negative_signal", ContributionTier.FULL)],
        _coverage(CoverageLevel.MEDIUM, source_count=2),
    )
    # 50 - 18 = 32; the only route below baseline is present, evidence-backed negatives.
    assert snapshot.score == 32.0
    assert classify_founder_band(snapshot.score) is FounderBand.WEAK


def test_negative_signal_without_evidence_cannot_subtract() -> None:
    snapshot = _founder(
        [_obs("verified_negative_signal", ContributionTier.FULL, evidence=())],
        _coverage(CoverageLevel.MEDIUM, source_count=2),
    )
    assert snapshot.score == FOUNDER_SCORE_BASELINE


def test_negative_signal_with_unknown_observation_cannot_subtract() -> None:
    snapshot = _founder(
        [_obs("verified_negative_signal", ContributionTier.FULL, known=False)],
        _coverage(CoverageLevel.MEDIUM, source_count=2),
    )
    assert snapshot.score == FOUNDER_SCORE_BASELINE


def test_positive_factor_with_non_known_value_contributes_zero() -> None:
    # A positive factor graded FULL but whose observed value is any non-known state must
    # collapse to zero: absence in any flavor can never lift the score above baseline.
    for observed in (
        KnowledgeValue[ScalarValue].not_disclosed("subject withheld it"),
        KnowledgeValue[ScalarValue].conflicted(
            "sources disagree",
            (
                KnowledgeAlternative[ScalarValue](value="a", evidence_ids=("evidence:a",)),
                KnowledgeAlternative[ScalarValue](value="b", evidence_ids=("evidence:b",)),
            ),
        ),
    ):
        snapshot = _founder(
            [
                FounderFactorObservation(
                    factor_key="work_product_quality",
                    tier=ContributionTier.FULL,
                    observed_value=observed,
                    rationale="observed value is not known",
                    evidence_ids=("evidence:1",),
                )
            ],
            _coverage(CoverageLevel.MEDIUM, source_count=2),
        )
        assert snapshot.score == FOUNDER_SCORE_BASELINE
        work = next(f for f in snapshot.factors if f.factor_key == "work_product_quality")
        assert work.contribution.value == 0.0


def test_negative_signal_partial_with_evidence_subtracts_half() -> None:
    snapshot = _founder(
        [_obs("verified_negative_signal", ContributionTier.PARTIAL)],
        _coverage(CoverageLevel.MEDIUM, source_count=2),
    )
    # 50 - 18 * 0.5 = 41; a partial substantiated negative subtracts half its weight.
    assert snapshot.score == 41.0
    negative = next(f for f in snapshot.factors if f.factor_key == "verified_negative_signal")
    assert negative.contribution.value == -9.0


def test_all_positive_factors_saturate_and_clamp() -> None:
    observations = [
        _obs("shipped_adopted_work", ContributionTier.FULL),
        _obs("corroborated_domain_experience", ContributionTier.FULL),
        _obs("sustained_follow_through", ContributionTier.FULL),
        _obs("peer_validated_recognition", ContributionTier.FULL),
        _obs("work_product_quality", ContributionTier.FULL),
        _obs("public_writing_depth", ContributionTier.FULL),
    ]
    snapshot = _founder(observations, _coverage(CoverageLevel.HIGH, source_count=5))
    # 50 + 68 = 118 clamps to 100.
    assert snapshot.score == 100.0
    assert snapshot.provisional is False
    assert snapshot.uncertainty is QualitativeUncertainty.LOW


def test_partial_tier_is_half_weight() -> None:
    snapshot = _founder(
        [_obs("shipped_adopted_work", ContributionTier.PARTIAL)],
        _coverage(CoverageLevel.LOW),
    )
    # 50 + 18 * 0.5 = 59
    assert snapshot.score == 59.0


def test_founder_score_is_deterministic() -> None:
    observations = [
        _obs("shipped_adopted_work", ContributionTier.FULL),
        _obs("verified_negative_signal", ContributionTier.PARTIAL),
    ]
    coverage = _coverage(CoverageLevel.MEDIUM, source_count=2)
    assert _founder(observations, coverage) == _founder(observations, coverage)


def test_unknown_founder_factor_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown founder factor key"):
        _founder([_obs("not_a_real_factor", ContributionTier.FULL)], _coverage(CoverageLevel.LOW))


def test_duplicate_founder_observation_is_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate founder factor observation"):
        _founder(
            [
                _obs("work_product_quality", ContributionTier.FULL),
                _obs("work_product_quality", ContributionTier.PARTIAL),
            ],
            _coverage(CoverageLevel.LOW),
        )


def test_high_coverage_without_positive_evidence_stays_provisional() -> None:
    # Rich sources but no costly-to-fake positive still cannot claim an established score.
    snapshot = _founder([], _coverage(CoverageLevel.HIGH, source_count=5))
    assert snapshot.provisional is True
    assert snapshot.uncertainty is QualitativeUncertainty.MODERATE
    assert snapshot.score == FOUNDER_SCORE_BASELINE


@pytest.mark.parametrize(
    ("score", "band"),
    [
        (10.0, FounderBand.WEAK),
        (40.0, FounderBand.BELOW_BASELINE),
        (55.0, FounderBand.BASELINE),
        (70.0, FounderBand.MODERATE),
        (90.0, FounderBand.STRONG),
    ],
)
def test_founder_band_boundaries(score: float, band: FounderBand) -> None:
    assert classify_founder_band(score) is band


@pytest.mark.parametrize(
    ("score", "band"),
    [
        (10.0, TrustBand.VERY_LOW),
        (30.0, TrustBand.LOW),
        (50.0, TrustBand.MODERATE),
        (70.0, TrustBand.HIGH),
        (90.0, TrustBand.VERY_HIGH),
    ],
)
def test_trust_band_boundaries(score: float, band: TrustBand) -> None:
    assert classify_trust_band(score) is band
