"""Deterministic tests for the two evidence-graded founder reads (task 3.10).

The suite pins the fairness invariants the two reads must hold: vanity can never raise
builder substance and non-predictive attributes contribute exactly zero; absence never
decrements either read; a stronger evidence grade never contributes less; and the gap is
honest and symmetric, flagging both an under-networked strong builder and a
substance-light but fundable profile. Every output carries an explicit policy version.
"""

from __future__ import annotations

import pytest

from founderlookup.domain.common import (
    KnowledgeAlternative,
    KnowledgeValue,
    ScalarValue,
)
from founderlookup.screening.founder_reads import (
    ALIGNED_BAND,
    BUILDER_WEIGHTS,
    FOUNDER_READS_POLICY_VERSION,
    FUNDABILITY_WEIGHTS,
    READ_BASELINE,
    EvidenceGrade,
    FounderRead,
    GapLabel,
    GradedObservation,
    ReadKind,
    builder_fundability_gap,
    builder_signal_read,
    fundability_read,
)
from founderlookup.screening.rubrics import (
    FOUNDER_FACTOR_REGISTRY,
    ContributionTier,
    FounderBand,
    FounderFactorPolarity,
    classify_founder_band,
)

# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------

# The four gameable vanity / non-predictive attributes the builder read must zero.
_VANITY_KEYS = (
    "follower_reach",
    "institutional_pedigree",
    "presentation_polish",
    "team_size",
)
# The five costly-to-fake, peer-validated, outcome-linked positive signals.
_COSTLY_KEYS = (
    "shipped_adopted_work",
    "corroborated_domain_experience",
    "sustained_follow_through",
    "peer_validated_recognition",
    "work_product_quality",
)


def _obs(
    factor_key: str,
    *,
    tier: ContributionTier = ContributionTier.FULL,
    grade: EvidenceGrade = EvidenceGrade.OUTCOME_BACKED,
    value: ScalarValue = "observed",
    evidence: tuple[str, ...] = ("evidence:1",),
    known: bool = True,
) -> GradedObservation:
    observed = (
        KnowledgeValue[ScalarValue].known(value)
        if known
        else KnowledgeValue[ScalarValue].unknown("not observed")
    )
    return GradedObservation(
        factor_key=factor_key,
        tier=tier,
        grade=grade,
        observed_value=observed,
        rationale="test observation",
        evidence_ids=evidence,
    )


def _contribution(read: FounderRead, factor_key: str) -> float:
    return next(f.contribution for f in read.factors if f.factor_key == factor_key)


# ======================================================================================
# Cold start and absence
# ======================================================================================


def test_cold_start_sits_at_baseline_for_both_reads() -> None:
    builder = builder_signal_read([])
    fundability = fundability_read([])
    assert builder.score == READ_BASELINE
    assert fundability.score == READ_BASELINE
    assert builder.kind is ReadKind.BUILDER_SIGNAL
    assert fundability.kind is ReadKind.FUNDABILITY
    assert builder.policy_version == FOUNDER_READS_POLICY_VERSION
    assert fundability.policy_version == FOUNDER_READS_POLICY_VERSION
    # The full taxonomy is emitted, with every unobserved factor an explicit zero.
    assert len(builder.factors) == len(FOUNDER_FACTOR_REGISTRY)
    assert all(f.contribution == 0.0 and not f.counted for f in builder.factors)
    assert builder.observed_factor_count == 0
    assert builder.counted_factor_count == 0


def test_full_taxonomy_emitted_in_registry_order() -> None:
    read = builder_signal_read([_obs("work_product_quality")])
    assert tuple(f.factor_key for f in read.factors) == tuple(
        spec.factor_key for spec in FOUNDER_FACTOR_REGISTRY
    )


def test_absence_never_decrements_either_read() -> None:
    # A factor whose observed value is unknown must contribute exactly zero, never below.
    for read_fn in (builder_signal_read, fundability_read):
        read = read_fn([_obs("shipped_adopted_work", known=False)])
        assert read.score == READ_BASELINE
        assert _contribution(read, "shipped_adopted_work") == 0.0


def test_positive_non_known_value_contributes_zero() -> None:
    # Not-disclosed and conflicted are absence-flavored; neither may lift a read.
    for observed in (
        KnowledgeValue[ScalarValue].not_disclosed("withheld"),
        KnowledgeValue[ScalarValue].conflicted(
            "sources disagree",
            (
                KnowledgeAlternative[ScalarValue](value="a", evidence_ids=("evidence:a",)),
                KnowledgeAlternative[ScalarValue](value="b", evidence_ids=("evidence:b",)),
            ),
        ),
    ):
        read = builder_signal_read(
            [
                GradedObservation(
                    factor_key="work_product_quality",
                    tier=ContributionTier.FULL,
                    grade=EvidenceGrade.OUTCOME_BACKED,
                    observed_value=observed,
                    rationale="value not known",
                    evidence_ids=("evidence:1",),
                )
            ]
        )
        assert read.score == READ_BASELINE


# ======================================================================================
# Builder read: vanity is powerless, costly signals dominate
# ======================================================================================


def test_maxed_vanity_cannot_raise_builder_substance() -> None:
    # Every vanity attribute at the strongest grade and tier still leaves the baseline.
    observations = [
        _obs(key, tier=ContributionTier.FULL, grade=EvidenceGrade.OUTCOME_BACKED)
        for key in _VANITY_KEYS
    ]
    builder = builder_signal_read(observations)
    assert builder.score == READ_BASELINE
    for key in _VANITY_KEYS:
        assert _contribution(builder, key) == 0.0


def test_single_weak_costly_signal_beats_maxed_vanity() -> None:
    # Costly-to-fake signals weigh strictly above gameable vanity: a lone self-asserted,
    # partial costly signal already outscores a founder maxed out on vanity.
    vanity = builder_signal_read(
        [_obs(key, grade=EvidenceGrade.OUTCOME_BACKED) for key in _VANITY_KEYS]
    )
    lone_costly = builder_signal_read(
        [
            _obs(
                "work_product_quality",
                tier=ContributionTier.PARTIAL,
                grade=EvidenceGrade.SELF_ASSERTED,
            )
        ]
    )
    assert vanity.score == READ_BASELINE
    assert lone_costly.score > vanity.score


def test_every_neutral_context_factor_is_zero_weight_in_builder_read() -> None:
    for spec in FOUNDER_FACTOR_REGISTRY:
        if spec.polarity is FounderFactorPolarity.NEUTRAL_CONTEXT:
            assert BUILDER_WEIGHTS[spec.factor_key] == 0.0


def test_builder_read_matches_registry_weights_at_grade_a_full() -> None:
    # At outcome-backed grade and full tier the builder read reproduces the taxonomy's
    # own weighting, so a strong builder saturates and clamps to 100.
    observations = [_obs(key) for key in _COSTLY_KEYS] + [_obs("public_writing_depth")]
    builder = builder_signal_read(observations)
    # 50 + 18 + 12 + 12 + 10 + 10 + 6 = 118 -> clamps to 100.
    assert builder.score == 100.0


# ======================================================================================
# Evidence grades
# ======================================================================================


def test_self_asserted_counts_strictly_less_than_outcome_backed() -> None:
    strong = builder_signal_read([_obs("shipped_adopted_work", grade=EvidenceGrade.OUTCOME_BACKED)])
    weak = builder_signal_read([_obs("shipped_adopted_work", grade=EvidenceGrade.SELF_ASSERTED)])
    # 50 + 18 = 68 versus 50 + 18*0.3 = 55.4
    assert strong.score == 68.0
    assert weak.score == 55.4
    assert weak.score < strong.score


def test_grade_monotonic_for_every_factor_and_tier() -> None:
    # For the same factor and tier, a stronger grade never contributes less magnitude.
    grades = (
        EvidenceGrade.OUTCOME_BACKED,
        EvidenceGrade.CORROBORATED,
        EvidenceGrade.SELF_ASSERTED,
    )
    for spec in FOUNDER_FACTOR_REGISTRY:
        for tier in (ContributionTier.FULL, ContributionTier.PARTIAL):
            magnitudes = []
            for grade in grades:
                for read_fn in (builder_signal_read, fundability_read):
                    read = read_fn([_obs(spec.factor_key, tier=tier, grade=grade)])
                    magnitudes.append(abs(_contribution(read, spec.factor_key)))
            # Pairwise: A >= B >= C magnitude within each read.
            a_builder, a_fund, b_builder, b_fund, c_builder, c_fund = magnitudes
            assert a_builder >= b_builder >= c_builder
            assert a_fund >= b_fund >= c_fund


def test_partial_tier_is_half_of_full() -> None:
    full = builder_signal_read([_obs("shipped_adopted_work", tier=ContributionTier.FULL)])
    partial = builder_signal_read([_obs("shipped_adopted_work", tier=ContributionTier.PARTIAL)])
    assert full.score == 68.0
    assert partial.score == 59.0  # 50 + 18*0.5


def test_pure_self_assertion_cannot_reach_the_strong_builder_band() -> None:
    # The anti-gaming ceiling: a founder whose entire builder profile is self-asserted,
    # at the strongest tier on every positive factor, must not reach STRONG. Only
    # corroborated or outcome-backed evidence can carry substance into the STRONG band.
    positives = (*_COSTLY_KEYS, "public_writing_depth")
    self_asserted = builder_signal_read(
        [_obs(key, grade=EvidenceGrade.SELF_ASSERTED) for key in positives]
    )
    outcome_backed = builder_signal_read(
        [_obs(key, grade=EvidenceGrade.OUTCOME_BACKED) for key in positives]
    )
    # 50 + 0.3 * (18+12+12+10+10+6) = 70.4, which stays in MODERATE, below STRONG (>=75).
    assert self_asserted.score == 70.4
    assert classify_founder_band(self_asserted.score) is not FounderBand.STRONG
    # The identical profile at outcome-backed grade saturates and reaches STRONG.
    assert outcome_backed.score == 100.0
    assert classify_founder_band(outcome_backed.score) is FounderBand.STRONG


# ======================================================================================
# Negative signal
# ======================================================================================


def test_verified_negative_with_evidence_subtracts_in_both_reads() -> None:
    for read_fn in (builder_signal_read, fundability_read):
        read = read_fn([_obs("verified_negative_signal")])
        # 50 - 18 = 32 in both reads.
        assert read.score == 32.0


def test_negative_without_evidence_cannot_subtract() -> None:
    for read_fn in (builder_signal_read, fundability_read):
        read = read_fn([_obs("verified_negative_signal", evidence=())])
        assert read.score == READ_BASELINE


def test_negative_with_unknown_observation_cannot_subtract() -> None:
    for read_fn in (builder_signal_read, fundability_read):
        read = read_fn([_obs("verified_negative_signal", known=False)])
        assert read.score == READ_BASELINE


def test_self_asserted_negative_subtracts_less_than_verified() -> None:
    strong = builder_signal_read(
        [_obs("verified_negative_signal", grade=EvidenceGrade.OUTCOME_BACKED)]
    )
    weak = builder_signal_read(
        [_obs("verified_negative_signal", grade=EvidenceGrade.SELF_ASSERTED)]
    )
    # 50 - 18 = 32 versus 50 - 18*0.3 = 44.6: the weaker grade is closer to baseline.
    assert strong.score == 32.0
    assert weak.score == 44.6
    assert weak.score > strong.score


# ======================================================================================
# Fundability read: the divergence mechanism
# ======================================================================================


def test_fundability_rewards_pedigree_that_builder_ignores() -> None:
    observations = [_obs("institutional_pedigree")]
    builder = builder_signal_read(observations)
    fundability = fundability_read(observations)
    assert builder.score == READ_BASELINE  # pedigree is zero substance
    assert fundability.score == 66.0  # 50 + 16


def test_fundability_underweights_deep_craft_that_builder_rewards() -> None:
    # Work-product quality and follow-through are real substance but invisible to VCs.
    for key in ("work_product_quality", "sustained_follow_through", "public_writing_depth"):
        assert FUNDABILITY_WEIGHTS[key] == 0.0
        observations = [_obs(key)]
        assert builder_signal_read(observations).score > READ_BASELINE
        assert fundability_read(observations).score == READ_BASELINE


# ======================================================================================
# The gap: honest and symmetric
# ======================================================================================


def _under_networked_builder_obs() -> list[GradedObservation]:
    # Deep costly substance, no network / pedigree / presentation, pre-launch.
    return [
        _obs("work_product_quality"),
        _obs("sustained_follow_through"),
        _obs("peer_validated_recognition"),
        _obs("corroborated_domain_experience"),
        _obs("public_writing_depth"),
    ]


def _substance_light_fundable_obs() -> list[GradedObservation]:
    # All the conventional pattern-match signals, none of the costly craft.
    return [
        _obs("institutional_pedigree"),
        _obs("presentation_polish"),
        _obs("follower_reach"),
        _obs("team_size"),
    ]


def test_gap_flags_under_networked_strong_builder() -> None:
    observations = _under_networked_builder_obs()
    builder = builder_signal_read(observations)
    fundability = fundability_read(observations)
    gap = builder_fundability_gap(builder, fundability)
    assert gap.label is GapLabel.UNDER_NETWORKED_STRONG_BUILDER
    assert gap.gap > 0.0
    assert gap.builder_score > gap.fundability_score
    assert "under-networked" in gap.rationale
    assert gap.policy_version == FOUNDER_READS_POLICY_VERSION


def test_gap_flags_substance_light_but_fundable() -> None:
    observations = _substance_light_fundable_obs()
    builder = builder_signal_read(observations)
    fundability = fundability_read(observations)
    gap = builder_fundability_gap(builder, fundability)
    assert gap.label is GapLabel.SUBSTANCE_LIGHT_BUT_FUNDABLE
    assert gap.gap < 0.0
    assert gap.builder_score == READ_BASELINE
    assert gap.fundability_score > gap.builder_score
    # Builder is at the unrated baseline here, not "low"; the rationale must say so
    # honestly instead of overclaiming that real substance is "substance-light".
    assert "not yet rated" in gap.rationale
    assert "substance-light" not in gap.rationale


def test_substance_light_rationale_does_not_overclaim_moderate_substance() -> None:
    # A founder who shipped outcome-backed adopted work (real, moderate substance) but is
    # heavy on network / pedigree / presentation still flags substance_light_but_fundable.
    # The rationale must not call that moderate, demonstrated substance "substance-light".
    observations = [
        _obs("shipped_adopted_work"),
        _obs("institutional_pedigree"),
        _obs("presentation_polish"),
        _obs("follower_reach"),
        _obs("team_size"),
    ]
    builder = builder_signal_read(observations)
    fundability = fundability_read(observations)
    gap = builder_fundability_gap(builder, fundability)
    assert gap.label is GapLabel.SUBSTANCE_LIGHT_BUT_FUNDABLE
    assert builder.score > READ_BASELINE
    assert "modest" in gap.rationale
    assert "substance-light" not in gap.rationale


def test_folklore_famous_but_unsubstantiated_reads_low_builder_substance() -> None:
    # The Tier C folklore trap: a famous founder whose fame is real and verifiable
    # (brand-name pedigree, big audience, a slick deck) but whose actual building claims
    # are only SELF_ASSERTED. Fame is costly-to-fake evidence of nothing about building,
    # so it contributes exactly zero to substance, and the self-asserted craft claims
    # count only a little. Builder substance therefore stays low while fundability soars.
    observations = [
        _obs("institutional_pedigree", grade=EvidenceGrade.OUTCOME_BACKED),
        _obs("follower_reach", grade=EvidenceGrade.OUTCOME_BACKED),
        _obs("presentation_polish", grade=EvidenceGrade.OUTCOME_BACKED),
        _obs("shipped_adopted_work", grade=EvidenceGrade.SELF_ASSERTED),
        _obs("sustained_follow_through", grade=EvidenceGrade.SELF_ASSERTED),
    ]
    builder = builder_signal_read(observations)
    fundability = fundability_read(observations)

    # Fame contributes exactly zero to builder substance, at any grade.
    for key in ("institutional_pedigree", "follower_reach", "presentation_polish"):
        assert _contribution(builder, key) == 0.0
    # The self-asserted craft claims count for only a fraction of their weight.
    assert _contribution(builder, "shipped_adopted_work") == 5.4  # 18 * 0.3
    assert _contribution(builder, "sustained_follow_through") == 3.6  # 12 * 0.3
    # 50 + 5.4 + 3.6 = 59.0: low substance, nowhere near STRONG despite the fame.
    assert builder.score == 59.0
    assert classify_founder_band(builder.score) is FounderBand.BASELINE
    assert classify_founder_band(builder.score) is not FounderBand.STRONG
    # Fame and the deck drive fundability well above substance: 50 + 16 + 10 + 12 + 4.2.
    assert fundability.score == 92.2

    gap = builder_fundability_gap(builder, fundability)
    assert gap.label is GapLabel.SUBSTANCE_LIGHT_BUT_FUNDABLE
    assert gap.gap < 0.0


def test_gap_is_symmetric_in_both_directions() -> None:
    builder_high = builder_fundability_gap(
        builder_signal_read(_under_networked_builder_obs()),
        fundability_read(_under_networked_builder_obs()),
    )
    fundable_high = builder_fundability_gap(
        builder_signal_read(_substance_light_fundable_obs()),
        fundability_read(_substance_light_fundable_obs()),
    )
    # The two divergence cases are reachable and carry opposite signs.
    assert builder_high.gap > 0.0
    assert fundable_high.gap < 0.0


def test_aligned_when_the_two_reads_agree() -> None:
    # A founder with real traction and a recognized name reads similarly on both lenses.
    observations = [_obs("shipped_adopted_work"), _obs("peer_validated_recognition")]
    builder = builder_signal_read(observations)
    fundability = fundability_read(observations)
    gap = builder_fundability_gap(builder, fundability)
    assert abs(gap.gap) <= ALIGNED_BAND
    assert gap.label is GapLabel.ALIGNED


def test_cold_start_gap_is_aligned_at_zero() -> None:
    gap = builder_fundability_gap(builder_signal_read([]), fundability_read([]))
    assert gap.gap == 0.0
    assert gap.label is GapLabel.ALIGNED


def test_gap_rejects_transposed_reads() -> None:
    builder = builder_signal_read([])
    fundability = fundability_read([])
    with pytest.raises(ValueError, match="builder-signal read"):
        builder_fundability_gap(fundability, builder)
    with pytest.raises(ValueError, match="fundability read"):
        builder_fundability_gap(builder, builder)


# ======================================================================================
# Determinism, validation, and clamping
# ======================================================================================


def test_reads_and_gap_are_deterministic() -> None:
    observations = _under_networked_builder_obs()
    assert builder_signal_read(observations) == builder_signal_read(observations)
    assert fundability_read(observations) == fundability_read(observations)
    first = builder_fundability_gap(
        builder_signal_read(observations), fundability_read(observations)
    )
    second = builder_fundability_gap(
        builder_signal_read(observations), fundability_read(observations)
    )
    assert first == second


def test_unknown_factor_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown founder factor key"):
        builder_signal_read([_obs("not_a_real_factor")])


def test_duplicate_observation_is_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate founder factor observation"):
        fundability_read(
            [
                _obs("institutional_pedigree", tier=ContributionTier.FULL),
                _obs("institutional_pedigree", tier=ContributionTier.PARTIAL),
            ]
        )


def test_scores_stay_on_scale() -> None:
    all_positive = builder_signal_read([_obs(key) for key in _COSTLY_KEYS])
    only_negative = builder_signal_read([_obs("verified_negative_signal")])
    assert 0.0 <= only_negative.score <= all_positive.score <= 100.0
