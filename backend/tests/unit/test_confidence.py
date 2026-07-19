"""Deterministic tests for the Area-of-Research-1 confidence estimator (task 3.11).

The suite pins the robustness and honesty invariants the method must hold: thin
evidence (low coverage, few samples, missing history) never lowers the point estimate,
it only widens the band, lowers the scalar confidence, or triggers explicit abstention;
identity-swap shifts are measured and flagged; and subgroup calibration is coarse,
deterministic, and honest about small samples.
"""

from __future__ import annotations

import math

import pytest

from founderlookup.screening.confidence import (
    AR1_CONFIDENCE_METHOD_VERSION,
    AbstentionCode,
    CalibrationEntry,
    ConfidenceBand,
    estimate_confidence_band,
    identity_swap_bias_check,
    subgroup_calibration_report,
)

_NAN = math.nan
_INF = math.inf


def _band(
    samples: list[float],
    *,
    coverage: float = 1.0,
    snap: float | None = None,
    min_samples: int = 3,
    coverage_threshold: float = 0.5,
) -> ConfidenceBand:
    return estimate_confidence_band(
        samples,
        snap_score=snap,
        coverage_level=coverage,
        min_samples=min_samples,
        coverage_threshold=coverage_threshold,
    )


# ======================================================================================
# Confidence-band estimator: happy path and basic shape
# ======================================================================================


def test_tight_full_coverage_samples_are_confident_and_narrow() -> None:
    band = _band([70.0, 70.0, 70.0, 70.0, 70.0])
    assert band.point == 70.0
    assert band.lower == 70.0
    assert band.upper == 70.0
    assert band.confidence == 1.0
    assert band.abstained is False
    assert band.abstention_codes == ()
    assert band.reason is None
    assert band.method_version == AR1_CONFIDENCE_METHOD_VERSION


def test_point_estimate_is_the_median() -> None:
    band = _band([40.0, 60.0, 80.0])
    assert band.point == 60.0


def test_band_always_contains_the_point() -> None:
    for samples, coverage in (
        ([50.0], 0.9),
        ([10.0, 30.0, 50.0, 70.0, 90.0], 1.0),
        ([5.0, 5.0], 0.1),
        ([95.0, 99.0, 100.0], 0.3),
    ):
        band = _band(samples, coverage=coverage)
        assert band.point is not None
        assert band.lower is not None
        assert band.upper is not None
        assert band.lower <= band.point <= band.upper


# ======================================================================================
# Core invariant: thin evidence never lowers the point estimate
# ======================================================================================


def test_thin_coverage_does_not_move_the_point() -> None:
    rich = _band([40.0, 60.0, 80.0], coverage=1.0)
    thin = _band([40.0, 60.0, 80.0], coverage=0.1)
    assert rich.point == thin.point == 60.0


def test_few_samples_do_not_lower_the_point() -> None:
    # Two samples (below the default minimum) still report their median, not a guess.
    band = _band([80.0, 80.0])
    assert band.point == 80.0
    assert band.abstained is True
    assert AbstentionCode.TOO_FEW_SAMPLES in band.abstention_codes


def test_single_sample_reports_its_value_but_is_not_confident() -> None:
    # A lone sample has zero dispersion; the sample-count factor keeps it from looking
    # deceptively certain, and it abstains for being below the minimum.
    band = _band([70.0])
    assert band.point == 70.0
    assert band.abstained is True
    assert AbstentionCode.TOO_FEW_SAMPLES in band.abstention_codes
    assert band.confidence == pytest.approx(0.2)


# ======================================================================================
# Thin evidence widens the band and lowers confidence
# ======================================================================================


def test_dispersion_widens_band_and_drops_confidence() -> None:
    tight = _band([50.0, 50.0, 50.0, 50.0, 50.0])
    spread = _band([10.0, 30.0, 50.0, 70.0, 90.0])
    assert tight.point == spread.point == 50.0
    assert spread.dispersion == 32.0
    assert (spread.upper - spread.lower) > (tight.upper - tight.lower)  # type: ignore[operator]
    assert spread.confidence < tight.confidence
    assert spread.confidence == 0.0


def test_thin_coverage_widens_band_and_drops_confidence() -> None:
    rich = _band([70.0, 70.0, 70.0, 70.0, 70.0], coverage=1.0)
    thin = _band([70.0, 70.0, 70.0, 70.0, 70.0], coverage=0.2)
    assert rich.point == thin.point == 70.0
    assert (thin.upper - thin.lower) > (rich.upper - rich.lower)  # type: ignore[operator]
    assert thin.confidence < rich.confidence
    # coverage_level 0.2 scales the confidence directly through the coverage factor.
    assert thin.confidence == pytest.approx(0.2)


def test_snap_divergence_lowers_confidence_without_moving_point_or_band() -> None:
    aligned = _band([60.0, 60.0, 60.0, 60.0, 60.0], snap=60.0)
    partial = _band([60.0, 60.0, 60.0, 60.0, 60.0], snap=80.0)
    far = _band([60.0, 60.0, 60.0, 60.0, 60.0], snap=100.0)
    # Snap divergence is a confidence channel only; it never moves the point or band.
    assert aligned.point == partial.point == far.point == 60.0
    assert aligned.lower == far.lower and aligned.upper == far.upper
    assert aligned.confidence > partial.confidence > far.confidence
    assert far.snap_divergence == 40.0
    assert far.confidence == 0.0


# ======================================================================================
# Explicit abstention
# ======================================================================================


def test_coverage_below_threshold_abstains_but_keeps_point() -> None:
    band = _band([65.0, 65.0, 65.0, 65.0, 65.0], coverage=0.3, coverage_threshold=0.5)
    assert band.abstained is True
    assert band.abstention_codes == (AbstentionCode.COVERAGE_BELOW_THRESHOLD,)
    assert band.reason is not None
    assert band.point == 65.0  # abstention did not lower the point


def test_coverage_exactly_at_threshold_does_not_abstain() -> None:
    band = _band([65.0, 65.0, 65.0, 65.0, 65.0], coverage=0.5, coverage_threshold=0.5)
    assert AbstentionCode.COVERAGE_BELOW_THRESHOLD not in band.abstention_codes


def test_sample_count_exactly_at_minimum_does_not_abstain_for_count() -> None:
    band = _band([50.0, 50.0, 50.0], min_samples=3)
    assert AbstentionCode.TOO_FEW_SAMPLES not in band.abstention_codes


def test_both_abstention_reasons_can_stack() -> None:
    band = _band([50.0, 50.0], coverage=0.1, min_samples=3, coverage_threshold=0.5)
    assert band.abstained is True
    assert set(band.abstention_codes) == {
        AbstentionCode.TOO_FEW_SAMPLES,
        AbstentionCode.COVERAGE_BELOW_THRESHOLD,
    }
    assert band.point == 50.0


def test_empty_samples_yield_an_honest_empty_band() -> None:
    band = estimate_confidence_band([], coverage_level=1.0)
    assert band.point is None
    assert band.lower is None
    assert band.upper is None
    assert band.confidence == 0.0
    assert band.sample_count == 0
    assert band.abstained is True
    assert band.abstention_codes == (AbstentionCode.NO_SAMPLES,)
    assert band.reason is not None
    assert band.snap_divergence is None


# ======================================================================================
# Determinism
# ======================================================================================


def test_confidence_band_is_deterministic() -> None:
    first = _band([40.0, 55.0, 70.0], coverage=0.7, snap=52.0)
    second = _band([40.0, 55.0, 70.0], coverage=0.7, snap=52.0)
    assert first == second


def test_confidence_band_is_order_independent() -> None:
    ascending = _band([40.0, 60.0, 80.0])
    shuffled = _band([80.0, 40.0, 60.0])
    assert ascending == shuffled


# ======================================================================================
# Identity-swap bias check
# ======================================================================================


def test_no_identity_shift_is_not_biased() -> None:
    result = identity_swap_bias_check([50.0, 50.0, 50.0], [50.0, 50.0, 50.0])
    assert result.comparable is True
    assert result.shift == 0.0
    assert result.magnitude == 0.0
    assert result.biased is False
    assert result.method_version == AR1_CONFIDENCE_METHOD_VERSION


def test_large_identity_shift_is_flagged_as_biased() -> None:
    result = identity_swap_bias_check([50.0, 50.0, 50.0], [70.0, 70.0, 70.0], threshold=5.0)
    assert result.baseline_point == 50.0
    assert result.swapped_point == 70.0
    assert result.shift == 20.0
    assert result.magnitude == 20.0
    assert result.biased is True


def test_bias_check_is_symmetric_in_magnitude() -> None:
    forward = identity_swap_bias_check([50.0], [70.0], threshold=5.0)
    backward = identity_swap_bias_check([70.0], [50.0], threshold=5.0)
    assert forward.magnitude == backward.magnitude == 20.0
    assert forward.biased == backward.biased is True
    assert forward.shift is not None and backward.shift is not None
    assert forward.shift == -backward.shift


def test_shift_exactly_at_threshold_is_not_biased() -> None:
    result = identity_swap_bias_check([50.0], [55.0], threshold=5.0)
    assert result.magnitude == 5.0
    assert result.biased is False  # flagged only when it strictly exceeds the threshold


def test_bias_check_abstains_when_a_side_is_empty() -> None:
    result = identity_swap_bias_check([], [50.0])
    assert result.comparable is False
    assert result.biased is False
    assert result.shift is None
    assert result.magnitude is None
    assert result.baseline_point is None
    assert result.swapped_point == 50.0
    assert result.reason is not None
    assert "baseline" in result.reason


def test_bias_check_is_deterministic() -> None:
    first = identity_swap_bias_check([40.0, 60.0], [45.0, 65.0])
    second = identity_swap_bias_check([40.0, 60.0], [45.0, 65.0])
    assert first == second


# ======================================================================================
# Subgroup calibration report
# ======================================================================================


def _entries(subgroup: str, pairs: list[tuple[float, bool]]) -> list[CalibrationEntry]:
    return [
        CalibrationEntry(subgroup=subgroup, predicted_confidence=p, actual_outcome=o)
        for p, o in pairs
    ]


def test_empty_calibration_report_is_empty() -> None:
    report = subgroup_calibration_report([])
    assert report.subgroups == ()
    assert report.total_entries == 0
    assert report.subgroup_count == 0
    assert report.sufficient_subgroup_count == 0
    assert report.method_version == AR1_CONFIDENCE_METHOD_VERSION


def test_perfectly_calibrated_subgroup_has_zero_error() -> None:
    pairs = [(1.0, True)] * 5 + [(0.0, False)] * 5
    report = subgroup_calibration_report(_entries("a", pairs), min_subgroup_size=10)
    assert report.subgroup_count == 1
    group = report.subgroups[0]
    assert group.expected_calibration_error == 0.0
    assert group.calibration_gap == 0.0
    assert group.sufficient_sample is True


def test_overconfident_subgroup_has_maximal_error() -> None:
    # Always predicts full confidence but every outcome is False.
    report = subgroup_calibration_report(_entries("b", [(1.0, False)] * 10))
    group = report.subgroups[0]
    assert group.expected_calibration_error == 1.0
    assert group.calibration_gap == 1.0
    assert group.observed_outcome_rate == 0.0
    assert group.mean_predicted_confidence == 1.0


def test_binned_expected_calibration_error_is_size_weighted() -> None:
    pairs = [(0.25, False), (0.25, True), (0.75, True), (0.75, True)]
    report = subgroup_calibration_report(_entries("x", pairs), bin_count=2)
    group = report.subgroups[0]
    assert len(group.bins) == 2
    assert group.expected_calibration_error == 0.25
    assert group.calibration_gap == 0.25
    assert group.mean_predicted_confidence == 0.5
    assert group.observed_outcome_rate == 0.75


def test_small_subgroup_is_flagged_insufficient() -> None:
    report = subgroup_calibration_report(
        _entries("tiny", [(0.5, True), (0.5, False)]),
        min_subgroup_size=10,
    )
    group = report.subgroups[0]
    assert group.sample_size == 2
    assert group.sufficient_sample is False
    assert report.sufficient_subgroup_count == 0


def test_subgroups_are_emitted_in_sorted_order() -> None:
    report = subgroup_calibration_report(
        _entries("b", [(0.5, True)]) + _entries("a", [(0.5, True)]),
    )
    assert [group.subgroup for group in report.subgroups] == ["a", "b"]


def test_predicted_confidence_is_clamped_into_the_unit_interval() -> None:
    # Out-of-range predictions are clamped rather than crashing or escaping their bin.
    report = subgroup_calibration_report(
        _entries("c", [(1.5, True), (-0.3, False)]),
        bin_count=5,
    )
    group = report.subgroups[0]
    assert group.mean_predicted_confidence == 0.5  # clamped to (1.0 + 0.0) / 2
    assert all(0.0 <= b.lower <= b.upper <= 1.0 for b in group.bins)


def test_invalid_bin_count_is_rejected() -> None:
    with pytest.raises(ValueError, match="bin_count"):
        subgroup_calibration_report(_entries("a", [(0.5, True)]), bin_count=0)


def test_calibration_report_is_deterministic() -> None:
    pairs = [(0.2, False), (0.8, True), (0.6, True), (0.4, False)]
    first = subgroup_calibration_report(_entries("g", pairs))
    second = subgroup_calibration_report(_entries("g", pairs))
    assert first == second


# ======================================================================================
# Non-finite inputs are rejected, not silently coerced
# ======================================================================================
# A NaN would defeat sort-order-independence (comparisons against NaN are all false) and
# an infinity would poison the band arithmetic, so both must surface as an explicit error
# rather than a quietly-produced number.


@pytest.mark.parametrize("bad", [_NAN, _INF, -_INF])
def test_estimate_rejects_non_finite_samples(bad: float) -> None:
    with pytest.raises(ValueError, match="reasoned_samples"):
        estimate_confidence_band([50.0, bad, 50.0], coverage_level=1.0)


def test_estimate_rejects_non_finite_coverage() -> None:
    with pytest.raises(ValueError, match="coverage_level"):
        estimate_confidence_band([50.0, 50.0, 50.0], coverage_level=_NAN)


def test_estimate_rejects_non_finite_snap_score() -> None:
    with pytest.raises(ValueError, match="snap_score"):
        estimate_confidence_band([50.0, 50.0, 50.0], snap_score=_INF, coverage_level=1.0)


def test_estimate_rejects_non_finite_even_with_no_other_samples() -> None:
    # The guard runs before the empty-sample short-circuit, so a lone NaN still surfaces.
    with pytest.raises(ValueError, match="reasoned_samples"):
        estimate_confidence_band([_NAN], coverage_level=1.0)


@pytest.mark.parametrize("bad", [_NAN, _INF, -_INF])
def test_bias_check_rejects_non_finite_baseline(bad: float) -> None:
    with pytest.raises(ValueError, match="baseline_samples"):
        identity_swap_bias_check([50.0, bad], [50.0, 50.0])


def test_bias_check_rejects_non_finite_swapped() -> None:
    with pytest.raises(ValueError, match="swapped_samples"):
        identity_swap_bias_check([50.0, 50.0], [50.0, _NAN])


def test_calibration_rejects_non_finite_predicted_confidence() -> None:
    with pytest.raises(ValueError, match="predicted_confidence"):
        subgroup_calibration_report(_entries("a", [(0.5, True), (_NAN, False)]))
