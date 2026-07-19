"""Deterministic tests for the screening evaluation harness (Area-of-Research-3 scaffolding).

The suite pins the metrics-correctness and honesty invariants the harness must hold:
Kendall tau-b rank agreement with the documented tie rule and hand-computable values;
band coverage as a plain fraction with an exposed denominator and inclusive containment;
explicit ``None`` for degenerate concordance (fewer than two records, or no variation);
thin subgroups flagged insufficient rather than dropped; baseline lift measured on a shared
subset; determinism and permutation-invariance; and a boundary that rejects non-finite,
out-of-range, or ill-formed input rather than coercing it.
"""

from __future__ import annotations

import math

import pytest

from founderlookup.screening.evaluation import (
    EVALUATION_POLICY_VERSION,
    EvalRecord,
    evaluate_predictions,
)

_NAN = math.nan
_INF = math.inf


def _record(
    subject_id: str,
    *,
    subgroup: str = "g",
    predicted_score: float = 50.0,
    predicted_confidence: float = 0.5,
    band: tuple[float, float] | None = None,
    outcome_value: float | None = 50.0,
    outcome_success: bool | None = True,
) -> EvalRecord:
    return EvalRecord(
        subject_id=subject_id,
        subgroup=subgroup,
        predicted_score=predicted_score,
        predicted_confidence=predicted_confidence,
        predicted_band=band,
        outcome_value=outcome_value,
        outcome_success=outcome_success,
    )


def _ranked(scores_outcomes: list[tuple[float, float]]) -> list[EvalRecord]:
    """Records carrying only what rank agreement needs: predicted score and outcome value."""
    return [
        _record(f"s{index}", predicted_score=score, outcome_value=outcome)
        for index, (score, outcome) in enumerate(scores_outcomes)
    ]


# ======================================================================================
# Rank agreement: Kendall tau-b, hand-computable values
# ======================================================================================


def test_perfect_concordance_is_one() -> None:
    report = evaluate_predictions(_ranked([(1.0, 10.0), (2.0, 20.0), (3.0, 30.0)]))
    ra = report.overall_rank_agreement
    assert ra.tau_b == 1.0
    assert ra.concordant == 3
    assert ra.discordant == 0
    assert ra.tied_predicted == ra.tied_outcome == ra.tied_both == 0


def test_perfect_discordance_is_minus_one() -> None:
    report = evaluate_predictions(_ranked([(1.0, 30.0), (2.0, 20.0), (3.0, 10.0)]))
    ra = report.overall_rank_agreement
    assert ra.tau_b == -1.0
    assert ra.concordant == 0
    assert ra.discordant == 3


def test_mixed_pairs_match_hand_computed_tau() -> None:
    # predicted [1,2,3] vs outcome [1,3,2]: two concordant pairs, one discordant.
    report = evaluate_predictions(_ranked([(1.0, 1.0), (2.0, 3.0), (3.0, 2.0)]))
    ra = report.overall_rank_agreement
    assert ra.concordant == 2
    assert ra.discordant == 1
    assert ra.tau_b == pytest.approx(1.0 / 3.0, abs=5e-4)  # (2-1)/sqrt(3*3)


def test_tie_on_outcome_uses_tau_b_correction() -> None:
    # predicted [1,2,3,4] vs outcome [1,2,2,3]: five concordant, one pair tied on outcome.
    report = evaluate_predictions(_ranked([(1.0, 1.0), (2.0, 2.0), (3.0, 2.0), (4.0, 3.0)]))
    ra = report.overall_rank_agreement
    assert ra.concordant == 5
    assert ra.discordant == 0
    assert ra.tied_outcome == 1
    assert ra.tied_predicted == 0
    # tau_b = (5 - 0) / sqrt((5 + 1) * (5 + 0)) = 5 / sqrt(30) = 0.913.
    assert ra.tau_b == pytest.approx(5.0 / math.sqrt(30.0), abs=5e-4)


def test_tie_on_predicted_uses_tau_b_correction() -> None:
    # predicted [1,2,2,3] vs outcome [1,2,3,4]: symmetric mirror of the outcome-tie case.
    report = evaluate_predictions(_ranked([(1.0, 1.0), (2.0, 2.0), (2.0, 3.0), (3.0, 4.0)]))
    ra = report.overall_rank_agreement
    assert ra.concordant == 5
    assert ra.tied_predicted == 1
    assert ra.tied_outcome == 0
    assert ra.tau_b == pytest.approx(5.0 / math.sqrt(30.0), abs=5e-4)


# ======================================================================================
# Rank agreement: degenerate inputs are explicitly None, never fabricated
# ======================================================================================


def test_single_record_rank_agreement_is_none() -> None:
    report = evaluate_predictions(_ranked([(50.0, 10.0)]))
    ra = report.overall_rank_agreement
    assert ra.tau_b is None
    assert ra.n == 1


def test_all_equal_predicted_scores_is_none() -> None:
    # No variation on the predicted side: concordance is undefined, not zero.
    report = evaluate_predictions(_ranked([(50.0, 10.0), (50.0, 20.0), (50.0, 30.0)]))
    ra = report.overall_rank_agreement
    assert ra.tau_b is None
    assert ra.tied_predicted == 3


def test_all_equal_outcomes_is_none() -> None:
    report = evaluate_predictions(_ranked([(10.0, 50.0), (20.0, 50.0), (30.0, 50.0)]))
    ra = report.overall_rank_agreement
    assert ra.tau_b is None
    assert ra.tied_outcome == 3


def test_all_identical_records_are_none_not_perfect() -> None:
    report = evaluate_predictions(_ranked([(50.0, 50.0), (50.0, 50.0)]))
    ra = report.overall_rank_agreement
    assert ra.tau_b is None
    assert ra.tied_both == 1


# ======================================================================================
# Confidence-band coverage: plain fraction, inclusive containment, exposed denominator
# ======================================================================================


def test_coverage_is_a_plain_fraction_with_inclusive_boundaries() -> None:
    records = [
        _record("a", band=(10.0, 20.0), outcome_value=15.0),  # inside
        _record("b", band=(10.0, 20.0), outcome_value=20.0),  # upper boundary, covered
        _record("c", band=(10.0, 20.0), outcome_value=10.0),  # lower boundary, covered
        _record("d", band=(10.0, 20.0), outcome_value=25.0),  # outside, not covered
        _record("e", band=None, outcome_value=50.0),  # no band, excluded
    ]
    cov = evaluate_predictions(records).overall_coverage
    assert cov.covered == 3
    assert cov.with_band == 4
    assert cov.without_band == 1
    assert cov.coverage == 0.75


def test_coverage_gap_is_signed_against_nominal_level() -> None:
    records = [
        _record("a", band=(10.0, 20.0), outcome_value=15.0),
        _record("b", band=(10.0, 20.0), outcome_value=15.0),
        _record("c", band=(10.0, 20.0), outcome_value=15.0),
        _record("d", band=(10.0, 20.0), outcome_value=99.0),
    ]
    cov = evaluate_predictions(records, nominal_band_level=0.9).overall_coverage
    assert cov.coverage == 0.75
    assert cov.nominal_level == 0.9
    # Empirical below nominal: bands were too narrow (over-confident), so the gap is negative.
    assert cov.coverage_gap == pytest.approx(-0.15)


def test_coverage_is_none_when_no_record_has_a_band() -> None:
    cov = evaluate_predictions(_ranked([(1.0, 1.0), (2.0, 2.0)])).overall_coverage
    assert cov.with_band == 0
    assert cov.without_band == 2
    assert cov.coverage is None
    assert cov.coverage_gap is None


def test_coverage_gap_is_none_without_a_nominal_level() -> None:
    records = [_record("a", band=(10.0, 20.0), outcome_value=15.0)]
    cov = evaluate_predictions(records).overall_coverage
    assert cov.coverage == 1.0
    assert cov.coverage_gap is None


# ======================================================================================
# Calibration: reuse of subgroup_calibration_report over (subgroup, confidence, success)
# ======================================================================================


def test_calibration_is_reused_and_reflects_confidence_and_success() -> None:
    records = [
        _record(f"hi{i}", predicted_confidence=1.0, outcome_success=True) for i in range(5)
    ] + [_record(f"lo{i}", predicted_confidence=0.0, outcome_success=False) for i in range(5)]
    report = evaluate_predictions(records, min_subgroup_size=10)
    calibration = report.calibration
    assert calibration.subgroup_count == 1
    group = calibration.subgroups[0]
    # Perfectly calibrated: confident-and-true plus unconfident-and-false.
    assert group.expected_calibration_error == 0.0
    assert group.sufficient_sample is True


# ======================================================================================
# Baseline lift: system rank agreement versus a naive baseline, on a shared subset
# ======================================================================================


def test_baseline_lift_rewards_beating_the_naive_ranking() -> None:
    # System ranks perfectly with the outcome; the baseline ranks perfectly against it.
    records = _ranked([(1.0, 1.0), (2.0, 2.0), (3.0, 3.0), (4.0, 4.0)])
    baseline = {"s0": 4.0, "s1": 3.0, "s2": 2.0, "s3": 1.0}
    report = evaluate_predictions(records, baseline_scores=baseline, baseline_label="vanity")
    bl = report.baseline_lift
    assert bl is not None
    assert bl.label == "vanity"
    assert bl.compared == 4
    assert bl.without_baseline == 0
    assert bl.system_rank_agreement == 1.0
    assert bl.baseline_rank_agreement == -1.0
    assert bl.lift == 2.0


def test_baseline_lift_counts_records_without_a_baseline_score() -> None:
    records = _ranked([(1.0, 1.0), (2.0, 2.0), (3.0, 3.0)])
    baseline = {"s0": 1.0, "s1": 2.0}  # s2 has no baseline score
    bl = evaluate_predictions(records, baseline_scores=baseline).baseline_lift
    assert bl is not None
    assert bl.compared == 2
    assert bl.without_baseline == 1


def test_baseline_lift_is_none_when_no_baseline_supplied() -> None:
    report = evaluate_predictions(_ranked([(1.0, 1.0), (2.0, 2.0)]))
    assert report.baseline_lift is None


def test_baseline_lift_is_none_when_baseline_is_degenerate() -> None:
    records = _ranked([(1.0, 1.0), (2.0, 2.0), (3.0, 3.0)])
    baseline = {"s0": 5.0, "s1": 5.0, "s2": 5.0}  # no variation: undefined baseline tau
    bl = evaluate_predictions(records, baseline_scores=baseline).baseline_lift
    assert bl is not None
    assert bl.baseline_rank_agreement is None
    assert bl.lift is None


# ======================================================================================
# Per-subgroup breakdown: sorted, thin subgroups flagged not dropped
# ======================================================================================


def test_subgroups_are_sorted_and_thin_ones_flagged_not_dropped() -> None:
    records = [
        _record("b1", subgroup="beta", predicted_score=10.0, outcome_value=10.0),
        _record("b2", subgroup="beta", predicted_score=20.0, outcome_value=20.0),
        _record("a1", subgroup="alpha", predicted_score=30.0, outcome_value=30.0),
    ]
    subgroups = evaluate_predictions(records, min_subgroup_size=10).subgroups
    assert [group.subgroup for group in subgroups] == ["alpha", "beta"]
    alpha, beta = subgroups
    assert alpha.sample_size == 1
    assert alpha.sufficient_sample is False
    assert alpha.rank_agreement.tau_b is None  # single record, undefined
    assert beta.sample_size == 2
    assert beta.sufficient_sample is False
    assert beta.rank_agreement.tau_b == 1.0


def test_subgroup_marked_sufficient_at_threshold() -> None:
    records = [
        _record(f"x{i}", subgroup="big", predicted_score=float(i), outcome_value=float(i))
        for i in range(10)
    ]
    subgroups = evaluate_predictions(records, min_subgroup_size=10).subgroups
    assert subgroups[0].sufficient_sample is True


# ======================================================================================
# Counts: total, evaluable, excluded (pending predictions are counted, not evaluated)
# ======================================================================================


def test_pending_predictions_are_excluded_but_counted() -> None:
    records = [
        _record("a", predicted_score=10.0, outcome_value=10.0),
        _record("b", predicted_score=20.0, outcome_value=20.0),
        _record("c", outcome_value=None, outcome_success=None),  # pending
    ]
    report = evaluate_predictions(records)
    assert report.total == 3
    assert report.evaluable == 2
    assert report.excluded == 1
    assert report.overall_rank_agreement.n == 2
    assert report.policy_version == EVALUATION_POLICY_VERSION


def test_empty_input_yields_an_honest_empty_report() -> None:
    report = evaluate_predictions([])
    assert report.total == 0
    assert report.evaluable == 0
    assert report.excluded == 0
    assert report.overall_rank_agreement.tau_b is None
    assert report.overall_rank_agreement.n == 0
    assert report.overall_coverage.coverage is None
    assert report.overall_coverage.with_band == 0
    assert report.calibration.subgroup_count == 0
    assert report.baseline_lift is None
    assert report.subgroups == ()


# ======================================================================================
# Determinism and permutation-invariance
# ======================================================================================


def test_report_is_deterministic() -> None:
    records = _ranked([(10.0, 5.0), (20.0, 30.0), (30.0, 25.0), (40.0, 40.0)])
    first = evaluate_predictions(records, nominal_band_level=0.8)
    second = evaluate_predictions(records, nominal_band_level=0.8)
    assert first == second


def test_report_is_permutation_invariant() -> None:
    records = _ranked([(10.0, 5.0), (20.0, 30.0), (30.0, 25.0), (40.0, 40.0)])
    forward = evaluate_predictions(records)
    reversed_report = evaluate_predictions(list(reversed(records)))
    assert forward.overall_rank_agreement == reversed_report.overall_rank_agreement
    assert forward.overall_coverage == reversed_report.overall_coverage
    assert forward.subgroups == reversed_report.subgroups


# ======================================================================================
# Boundary: non-finite, out-of-range, and ill-formed input are rejected
# ======================================================================================


@pytest.mark.parametrize("bad", [_NAN, _INF, -_INF])
def test_non_finite_predicted_score_is_rejected(bad: float) -> None:
    with pytest.raises(ValueError, match="predicted_score"):
        evaluate_predictions([_record("a", predicted_score=bad)])


def test_non_finite_outcome_value_is_rejected() -> None:
    with pytest.raises(ValueError, match="outcome_value"):
        evaluate_predictions([_record("a", outcome_value=_INF, outcome_success=True)])


def test_out_of_range_predicted_score_is_rejected() -> None:
    with pytest.raises(ValueError, match="predicted_score"):
        evaluate_predictions([_record("a", predicted_score=150.0)])


def test_out_of_range_predicted_confidence_is_rejected() -> None:
    with pytest.raises(ValueError, match="predicted_confidence"):
        evaluate_predictions([_record("a", predicted_confidence=1.5)])


def test_inverted_band_is_rejected() -> None:
    with pytest.raises(ValueError, match="lower must not exceed upper"):
        evaluate_predictions([_record("a", band=(30.0, 10.0), outcome_value=20.0)])


def test_band_endpoint_out_of_range_is_rejected() -> None:
    with pytest.raises(ValueError, match="predicted_band upper"):
        evaluate_predictions([_record("a", band=(10.0, 150.0), outcome_value=20.0)])


def test_half_populated_outcome_is_rejected() -> None:
    with pytest.raises(ValueError, match="both be set or both be None"):
        evaluate_predictions([_record("a", outcome_value=10.0, outcome_success=None)])


def test_non_finite_nominal_band_level_is_rejected() -> None:
    with pytest.raises(ValueError, match="nominal_band_level"):
        evaluate_predictions(_ranked([(1.0, 1.0)]), nominal_band_level=_NAN)


def test_non_finite_baseline_score_is_rejected() -> None:
    records = _ranked([(1.0, 1.0), (2.0, 2.0)])
    with pytest.raises(ValueError, match="baseline_scores"):
        evaluate_predictions(records, baseline_scores={"s0": _INF, "s1": 2.0})
