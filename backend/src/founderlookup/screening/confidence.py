"""Deterministic, framework-neutral Area-of-Research-1 confidence estimation (v0).

This module turns score samples that a caller already produced into three honest,
reproducible readouts. It never calls a model, never samples randomness, performs no
I/O, and adds no dependency beyond the Python standard library (``math`` and
``statistics``). Every output is a pure function of its inputs, so identical inputs
always yield identical outputs.

The three public entry points are:

1. :func:`estimate_confidence_band` - a robust point estimate plus a coarse uncertainty
   band and a scalar confidence, built from the repeated reasoned sub-score reads a
   model produced under self-consistency. It abstains explicitly rather than guess when
   coverage is thin or too few samples were provided.
2. :func:`identity_swap_bias_check` - a counterfactual fairness check that measures how
   far the point estimate moves when identity attributes are swapped.
3. :func:`subgroup_calibration_report` - a coarse per-subgroup calibration (binned
   expected calibration error) with a simple reliability summary, honest about
   small-sample subgroups.

Robustness and honesty invariants (adversarially tested, non-negotiable)
------------------------------------------------------------------------
- Deterministic. No randomness, no wall clock, no hashing or ``id``. Any "resampling"
  is a fixed quantile read over the provided samples only. Non-finite inputs (NaN or
  infinity) are rejected with a ``ValueError`` at the boundary rather than silently
  coerced, because a NaN would break the sort-order-independence guarantee and an
  infinity would poison the arithmetic; garbage-in surfaces instead of hiding.
- Thin evidence never lowers the point estimate. Missing history, low coverage, or few
  samples only widen the band, lower the scalar confidence, or trigger explicit
  abstention. The point estimate is always the robust center of the samples that exist.
- Explicit abstention over silent guessing. When the estimator cannot support a
  confident band it sets ``abstained=True`` with machine-readable reason codes and a
  human message. It does not emit a quietly lowered number in place of saying so.
- No false precision. The point estimate is reported to one decimal, the band endpoints
  are quantized outward to whole score points so coarsening never understates
  uncertainty, and the scalar confidence is reported to two decimals.

Confidence mapping (documented, not fabricated)
-----------------------------------------------
The scalar confidence in ``[0, 1]`` is the product of four sub-factors, each in
``[0, 1]`` and each monotonic in the intended direction, so any single weak channel
pulls the whole value down (a deliberately conservative combination):

    dispersion_factor  = 1 - min(1, sample_spread / 20.0)
    divergence_factor  = 1 - min(1, |snap_score - point| / 40.0)   (1.0 if no snap)
    coverage_factor    = clamp(coverage_level, 0, 1)
    sample_factor      = min(1, n_samples / 5)

``sample_spread`` is the robust half-width of the central ``interval_width`` quantile
interval of the samples (0.0 when fewer than two samples exist). The ``sample_factor``
is included so a single sample, whose dispersion is trivially zero, cannot masquerade as
high confidence. Confidence decreases as sample dispersion rises, as the snap-versus
reasoned divergence rises, and as coverage thins, exactly as required.

Band construction
-----------------
The point estimate is the sample median (a robust center). The band is symmetric around
the median with half-width

    half_width = sample_spread + coverage_widening + sample_widening

where ``coverage_widening = 15.0 * (1 - clamp(coverage_level, 0, 1))`` and
``sample_widening = 15.0 * max(0, (5 - n_samples) / 5)``. Both extra terms are
non-negative and touch only the width, never the center, so thinner evidence can only
widen the band. Endpoints are clamped to the 0..100 scale and quantized outward.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

# --------------------------------------------------------------------------------------
# Method version. Every produced record carries this id so any output is reproducible
# from its method version. Bump on any change to the formulas or thresholds below.
# --------------------------------------------------------------------------------------

AR1_CONFIDENCE_METHOD_VERSION: Final = "ar1-confidence.v0"

# Defaults for the confidence-band estimator.
DEFAULT_MIN_SAMPLES: Final = 3
DEFAULT_COVERAGE_THRESHOLD: Final = 0.5
DEFAULT_INTERVAL_WIDTH: Final = 0.8

# Confidence-mapping and band-widening scales (all on the 0..100 sub-score scale unless
# noted). These are coarse by design; see the module docstring for the full mapping.
_DISPERSION_HALF_WIDTH_AT_ZERO: Final = 20.0
_DIVERGENCE_AT_ZERO: Final = 40.0
_FULL_CONFIDENCE_SAMPLES: Final = 5
_MAX_COVERAGE_WIDENING: Final = 15.0
_MAX_SAMPLE_WIDENING: Final = 15.0

# Output coarseness. The band is quantized outward to whole score points.
_BAND_QUANTIZE: Final = 1.0

# Defaults for the identity-swap bias check (points on the 0..100 scale).
DEFAULT_BIAS_THRESHOLD: Final = 5.0

# Defaults for the subgroup calibration report.
DEFAULT_CALIBRATION_BINS: Final = 5
DEFAULT_MIN_SUBGROUP_SIZE: Final = 10

_SCORE_MIN: Final = 0.0
_SCORE_MAX: Final = 100.0


# --------------------------------------------------------------------------------------
# Small deterministic numeric helpers
# --------------------------------------------------------------------------------------


def _clamp(value: float, low: float, high: float) -> float:
    """Clamp ``value`` into the inclusive ``[low, high]`` range."""
    return min(high, max(low, value))


def _clamp01(value: float) -> float:
    """Clamp a value into the unit interval."""
    return _clamp(value, 0.0, 1.0)


def _clamp_score(value: float) -> float:
    """Clamp to the 0..100 scale and drop float noise to one decimal."""
    return round(_clamp(value, _SCORE_MIN, _SCORE_MAX), 1)


def _require_finite(value: float, label: str) -> float:
    """Reject a non-finite scalar (NaN or infinity) so garbage surfaces, not coerces.

    Silent coercion of a NaN or infinity would violate the honesty invariants: a NaN
    slips through comparisons and would let a poisoned read masquerade as a real number,
    and an infinity would break the arithmetic. Rejecting is deterministic (identical
    inputs always raise identically) and keeps garbage-in from becoming a quiet output.
    """
    if not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number, got {value!r}")
    return value


def _require_finite_samples(samples: Sequence[float], label: str) -> None:
    """Reject any non-finite entry in a sample sequence before it can be sorted.

    A NaN breaks ``sorted`` ordering (comparisons against NaN are all false), so it would
    silently defeat the order-independence guarantee; an infinity would overflow the band
    arithmetic. Both are surfaced as an explicit error instead.
    """
    for index, value in enumerate(samples):
        if not math.isfinite(value):
            raise ValueError(f"{label}[{index}] must be a finite number, got {value!r}")


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile of already-sorted values, ``q`` in ``[0, 1]``.

    This matches the common "type 7" / NumPy ``linear`` convention and is fully
    deterministic. It requires at least one value; a single value maps to itself.
    """
    count = len(sorted_values)
    if count == 1:
        return sorted_values[0]
    position = _clamp01(q) * (count - 1)
    low_index = math.floor(position)
    high_index = math.ceil(position)
    fraction = position - low_index
    low_value = sorted_values[low_index]
    high_value = sorted_values[high_index]
    return low_value + (high_value - low_value) * fraction


def _sample_spread(sorted_values: Sequence[float], interval_width: float) -> float:
    """Robust half-width of the central ``interval_width`` quantile interval.

    Returns 0.0 when fewer than two samples exist (dispersion is unmeasurable) or when
    every sample is equal. Never negative.
    """
    if len(sorted_values) < 2:
        return 0.0
    width = _clamp01(interval_width)
    lower_q = _percentile(sorted_values, (1.0 - width) / 2.0)
    upper_q = _percentile(sorted_values, (1.0 + width) / 2.0)
    return max(0.0, (upper_q - lower_q) / 2.0)


def _quantize_outward(value: float, step: float, *, direction: int) -> float:
    """Snap ``value`` to a multiple of ``step`` away from center (down or up).

    ``direction`` is -1 to floor (lower endpoint) or +1 to ceil (upper endpoint), so the
    resulting band is never narrower than the raw band.
    """
    if direction < 0:
        return math.floor(value / step) * step
    return math.ceil(value / step) * step


# ======================================================================================
# 1. Confidence-band estimator
# ======================================================================================


class AbstentionCode(StrEnum):
    """Machine-readable reasons the estimator declined to assert a confident band."""

    NO_SAMPLES = "no_samples"
    TOO_FEW_SAMPLES = "too_few_samples"
    COVERAGE_BELOW_THRESHOLD = "coverage_below_threshold"


@dataclass(frozen=True)
class ConfidenceBand:
    """A robust point estimate, a coarse uncertainty band, and a scalar confidence.

    ``point``, ``lower``, and ``upper`` are on the 0..100 sub-score scale and are
    ``None`` only when no samples were provided (the honest representation of "no
    estimate", never a fabricated default). ``confidence`` is in ``[0, 1]``.
    ``abstained`` is orthogonal to the numbers: it flags that the system declines to
    assert a confident band, and it never changes ``point``.
    """

    point: float | None
    lower: float | None
    upper: float | None
    confidence: float
    sample_count: int
    coverage_level: float
    dispersion: float
    snap_divergence: float | None
    abstained: bool
    abstention_codes: tuple[AbstentionCode, ...]
    reason: str | None
    method_version: str = AR1_CONFIDENCE_METHOD_VERSION


_ABSTENTION_MESSAGES: Final[dict[AbstentionCode, str]] = {
    AbstentionCode.NO_SAMPLES: "no reasoned samples were provided",
    AbstentionCode.TOO_FEW_SAMPLES: "fewer reasoned samples than the minimum required",
    AbstentionCode.COVERAGE_BELOW_THRESHOLD: "coverage is below the abstention threshold",
}


def _abstention_reason(codes: Sequence[AbstentionCode]) -> str | None:
    """Join the fixed per-code messages in canonical order into one human string."""
    if not codes:
        return None
    ordered = [code for code in AbstentionCode if code in set(codes)]
    return "; ".join(_ABSTENTION_MESSAGES[code] for code in ordered)


def estimate_confidence_band(
    reasoned_samples: Sequence[float],
    *,
    snap_score: float | None = None,
    coverage_level: float,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    coverage_threshold: float = DEFAULT_COVERAGE_THRESHOLD,
    interval_width: float = DEFAULT_INTERVAL_WIDTH,
) -> ConfidenceBand:
    """Estimate a robust confidence band from repeated reasoned sub-score reads.

    ``reasoned_samples`` are the repeated reasoned sub-score reads produced under
    self-consistency, on the 0..100 scale. The point estimate is their median (a robust
    center). The band is the median plus or minus a robust half-width that widens as the
    samples disperse, as coverage thins, and as the sample count drops. The scalar
    ``confidence`` follows the documented four-factor mapping in the module docstring.

    Abstention is explicit and additive: the estimator sets ``abstained=True`` when
    coverage is below ``coverage_threshold`` and/or fewer than ``min_samples`` reads were
    provided. Abstention never lowers the point estimate; with at least one sample the
    median is always emitted regardless of abstention. With zero samples there is no
    estimate to assert, so the numeric fields are ``None`` rather than a guessed default.

    Deterministic: the median and the quantile interval are fixed reads over the
    provided samples, so identical inputs always produce identical outputs. Non-finite
    samples, ``snap_score``, or ``coverage_level`` are rejected with a ``ValueError``.
    """
    _require_finite_samples(reasoned_samples, "reasoned_samples")
    _require_finite(coverage_level, "coverage_level")
    if snap_score is not None:
        _require_finite(snap_score, "snap_score")

    sample_count = len(reasoned_samples)
    coverage_clamped = _clamp01(coverage_level)

    codes: list[AbstentionCode] = []
    if sample_count == 0:
        codes.append(AbstentionCode.NO_SAMPLES)
    elif sample_count < min_samples:
        codes.append(AbstentionCode.TOO_FEW_SAMPLES)
    if coverage_clamped < coverage_threshold:
        codes.append(AbstentionCode.COVERAGE_BELOW_THRESHOLD)

    if sample_count == 0:
        # No estimate exists. Report the honest empty band; do not fabricate a center.
        return ConfidenceBand(
            point=None,
            lower=None,
            upper=None,
            confidence=0.0,
            sample_count=0,
            coverage_level=round(coverage_clamped, 3),
            dispersion=0.0,
            snap_divergence=None,
            abstained=True,
            abstention_codes=tuple(codes),
            reason=_abstention_reason(codes),
        )

    sorted_samples = sorted(reasoned_samples)
    center = _clamp(statistics.median(sorted_samples), _SCORE_MIN, _SCORE_MAX)
    spread = _sample_spread(sorted_samples, interval_width)

    # Band half-width. Only the width absorbs thin-evidence penalties; the center never
    # moves, so thin evidence can only widen the band, never lower the point estimate.
    coverage_widening = _MAX_COVERAGE_WIDENING * (1.0 - coverage_clamped)
    sample_shortfall = max(0, _FULL_CONFIDENCE_SAMPLES - sample_count)
    sample_widening = _MAX_SAMPLE_WIDENING * (sample_shortfall / _FULL_CONFIDENCE_SAMPLES)
    half_width = spread + coverage_widening + sample_widening

    raw_lower = _quantize_outward(center - half_width, _BAND_QUANTIZE, direction=-1)
    raw_upper = _quantize_outward(center + half_width, _BAND_QUANTIZE, direction=1)
    point = _clamp_score(center)
    # Clamp to scale, then guarantee the band always contains the reported point.
    lower = min(_clamp_score(raw_lower), point)
    upper = max(_clamp_score(raw_upper), point)

    # Scalar confidence: product of four monotonic sub-factors in [0, 1].
    dispersion_factor = 1.0 - min(1.0, spread / _DISPERSION_HALF_WIDTH_AT_ZERO)
    if snap_score is None:
        snap_divergence: float | None = None
        divergence_factor = 1.0
    else:
        snap_divergence = abs(snap_score - center)
        divergence_factor = 1.0 - min(1.0, snap_divergence / _DIVERGENCE_AT_ZERO)
    coverage_factor = coverage_clamped
    sample_factor = min(1.0, sample_count / _FULL_CONFIDENCE_SAMPLES)
    confidence = round(
        _clamp01(dispersion_factor * divergence_factor * coverage_factor * sample_factor),
        2,
    )

    return ConfidenceBand(
        point=point,
        lower=lower,
        upper=upper,
        confidence=confidence,
        sample_count=sample_count,
        coverage_level=round(coverage_clamped, 3),
        dispersion=round(spread, 3),
        snap_divergence=None if snap_divergence is None else round(snap_divergence, 3),
        abstained=bool(codes),
        abstention_codes=tuple(codes),
        reason=_abstention_reason(codes),
    )


# ======================================================================================
# 2. Identity-swap counterfactual bias check
# ======================================================================================


@dataclass(frozen=True)
class BiasCheckResult:
    """How far the robust point estimate moves when identity attributes are swapped.

    ``shift`` is the signed move (swapped minus baseline) and ``magnitude`` is its
    absolute value, both on the 0..100 scale. ``biased`` is set when the magnitude
    exceeds ``threshold``: a large move means the score depends on identity, which is
    bias. ``comparable`` is ``False`` (with a ``reason``) when either sample set is empty
    and no comparison can be made; the check then abstains rather than guess.
    """

    baseline_point: float | None
    swapped_point: float | None
    shift: float | None
    magnitude: float | None
    threshold: float
    biased: bool
    comparable: bool
    reason: str | None
    method_version: str = AR1_CONFIDENCE_METHOD_VERSION


def identity_swap_bias_check(
    baseline_samples: Sequence[float],
    swapped_samples: Sequence[float],
    *,
    threshold: float = DEFAULT_BIAS_THRESHOLD,
) -> BiasCheckResult:
    """Measure the counterfactual point-estimate shift under an identity swap.

    Both inputs are reasoned sub-score reads on the 0..100 scale: ``baseline_samples``
    with the original identity attributes and ``swapped_samples`` with those attributes
    counterfactually swapped. The robust center (median) of each set is compared. The
    magnitude of the shift is flagged as bias when it strictly exceeds ``threshold``.

    When either set is empty the check cannot compare and returns ``comparable=False``
    with an explanatory reason rather than inventing a shift. Deterministic: medians are
    fixed reads over the provided samples. Non-finite samples or a non-finite
    ``threshold`` are rejected with a ``ValueError``.
    """
    _require_finite_samples(baseline_samples, "baseline_samples")
    _require_finite_samples(swapped_samples, "swapped_samples")
    _require_finite(threshold, "threshold")

    if not baseline_samples or not swapped_samples:
        missing = []
        if not baseline_samples:
            missing.append("baseline")
        if not swapped_samples:
            missing.append("swapped")
        reason = f"cannot compare: no samples for {' and '.join(missing)} identity"
        return BiasCheckResult(
            baseline_point=(
                None if not baseline_samples else round(statistics.median(baseline_samples), 1)
            ),
            swapped_point=(
                None if not swapped_samples else round(statistics.median(swapped_samples), 1)
            ),
            shift=None,
            magnitude=None,
            threshold=threshold,
            biased=False,
            comparable=False,
            reason=reason,
        )

    baseline_point = statistics.median(baseline_samples)
    swapped_point = statistics.median(swapped_samples)
    shift = swapped_point - baseline_point
    magnitude = abs(shift)
    return BiasCheckResult(
        baseline_point=round(baseline_point, 1),
        swapped_point=round(swapped_point, 1),
        shift=round(shift, 1),
        magnitude=round(magnitude, 1),
        threshold=threshold,
        biased=magnitude > threshold,
        comparable=True,
        reason=None,
    )


# ======================================================================================
# 3. Subgroup calibration report
# ======================================================================================


@dataclass(frozen=True)
class CalibrationEntry:
    """One (predicted confidence, observed outcome) pair tagged with its subgroup.

    ``predicted_confidence`` is on the ``[0, 1]`` scale (values outside are clamped);
    ``actual_outcome`` is the realized binary outcome the confidence was predicting.
    """

    subgroup: str
    predicted_confidence: float
    actual_outcome: bool


@dataclass(frozen=True)
class CalibrationBin:
    """One reliability bin within a subgroup; empty bins are omitted from the report."""

    lower: float
    upper: float
    count: int
    mean_predicted_confidence: float
    observed_outcome_rate: float


@dataclass(frozen=True)
class SubgroupCalibration:
    """Coarse calibration for one subgroup, honest about its sample size.

    ``expected_calibration_error`` is the size-weighted mean gap between predicted
    confidence and observed outcome rate across the non-empty bins. ``calibration_gap``
    is the simpler whole-subgroup gap between mean predicted confidence and observed
    outcome rate. ``sufficient_sample`` is ``False`` when the subgroup is too small for
    its calibration numbers to be trusted.
    """

    subgroup: str
    sample_size: int
    expected_calibration_error: float
    mean_predicted_confidence: float
    observed_outcome_rate: float
    calibration_gap: float
    sufficient_sample: bool
    bins: tuple[CalibrationBin, ...]


@dataclass(frozen=True)
class CalibrationReport:
    """Deterministic per-subgroup calibration summary with a coverage rollup."""

    subgroups: tuple[SubgroupCalibration, ...]
    total_entries: int
    subgroup_count: int
    sufficient_subgroup_count: int
    bin_count: int
    min_subgroup_size: int
    method_version: str = AR1_CONFIDENCE_METHOD_VERSION


def _bin_index(confidence: float, bin_count: int) -> int:
    """Map a clamped confidence in ``[0, 1]`` to its bin, with 1.0 landing in the last."""
    return min(bin_count - 1, int(_clamp01(confidence) * bin_count))


def subgroup_calibration_report(
    entries: Sequence[CalibrationEntry],
    *,
    bin_count: int = DEFAULT_CALIBRATION_BINS,
    min_subgroup_size: int = DEFAULT_MIN_SUBGROUP_SIZE,
) -> CalibrationReport:
    """Compute a coarse binned calibration error per subgroup, deterministically.

    Entries are grouped by ``subgroup``; each subgroup gets a size-weighted expected
    calibration error over ``bin_count`` equal-width confidence bins, a whole-subgroup
    calibration gap, and a ``sufficient_sample`` flag (``sample_size >=
    min_subgroup_size``) so small, unreliable subgroups are marked rather than hidden.
    Subgroups are emitted in sorted name order and empty bins are omitted, so identical
    inputs always produce identical reports. An empty ``entries`` sequence yields an
    empty report. A non-finite ``predicted_confidence`` is rejected with a ``ValueError``.
    """
    if bin_count < 1:
        raise ValueError("bin_count must be at least 1")
    for index, entry in enumerate(entries):
        _require_finite(entry.predicted_confidence, f"entries[{index}].predicted_confidence")

    grouped: dict[str, list[CalibrationEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.subgroup, []).append(entry)

    subgroups: list[SubgroupCalibration] = []
    for name in sorted(grouped):
        members = grouped[name]
        size = len(members)
        predictions = [_clamp01(item.predicted_confidence) for item in members]
        outcomes = [1.0 if item.actual_outcome else 0.0 for item in members]
        mean_predicted = statistics.fmean(predictions)
        observed_rate = statistics.fmean(outcomes)

        buckets: list[list[int]] = [[] for _ in range(bin_count)]
        for index, prediction in enumerate(predictions):
            buckets[_bin_index(prediction, bin_count)].append(index)

        bins: list[CalibrationBin] = []
        ece = 0.0
        for bucket_index, indices in enumerate(buckets):
            if not indices:
                continue
            bin_predictions = [predictions[i] for i in indices]
            bin_outcomes = [outcomes[i] for i in indices]
            bin_predicted = statistics.fmean(bin_predictions)
            bin_observed = statistics.fmean(bin_outcomes)
            ece += (len(indices) / size) * abs(bin_predicted - bin_observed)
            bins.append(
                CalibrationBin(
                    lower=round(bucket_index / bin_count, 3),
                    upper=round((bucket_index + 1) / bin_count, 3),
                    count=len(indices),
                    mean_predicted_confidence=round(bin_predicted, 3),
                    observed_outcome_rate=round(bin_observed, 3),
                )
            )

        subgroups.append(
            SubgroupCalibration(
                subgroup=name,
                sample_size=size,
                expected_calibration_error=round(ece, 3),
                mean_predicted_confidence=round(mean_predicted, 3),
                observed_outcome_rate=round(observed_rate, 3),
                calibration_gap=round(abs(mean_predicted - observed_rate), 3),
                sufficient_sample=size >= min_subgroup_size,
                bins=tuple(bins),
            )
        )

    sufficient = sum(1 for group in subgroups if group.sufficient_sample)
    return CalibrationReport(
        subgroups=tuple(subgroups),
        total_entries=len(entries),
        subgroup_count=len(subgroups),
        sufficient_subgroup_count=sufficient,
        bin_count=bin_count,
        min_subgroup_size=min_subgroup_size,
    )


__all__ = [
    "AR1_CONFIDENCE_METHOD_VERSION",
    "DEFAULT_BIAS_THRESHOLD",
    "DEFAULT_CALIBRATION_BINS",
    "DEFAULT_COVERAGE_THRESHOLD",
    "DEFAULT_INTERVAL_WIDTH",
    "DEFAULT_MIN_SAMPLES",
    "DEFAULT_MIN_SUBGROUP_SIZE",
    "AbstentionCode",
    "BiasCheckResult",
    "CalibrationBin",
    "CalibrationEntry",
    "CalibrationReport",
    "ConfidenceBand",
    "SubgroupCalibration",
    "estimate_confidence_band",
    "identity_swap_bias_check",
    "subgroup_calibration_report",
]
