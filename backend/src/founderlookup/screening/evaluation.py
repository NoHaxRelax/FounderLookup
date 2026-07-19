"""Deterministic evaluation harness for FounderLookup screening reads (v0).

This module is the self-improvement / Area-of-Research-3 deliverable in code form. Given a
set of past predictions paired with realized outcomes (the fixture outcomes), it measures
whether the system's reads actually tracked reality, so the system can be checked and
(later) recalibrated. It never calls a model, never samples randomness, performs no I/O,
and adds no dependency beyond the Python standard library (``math`` and the reused
calibration report). Every output is a pure function of its inputs, so identical inputs
always yield identical reports.

The single public entry point is :func:`evaluate_predictions`, which takes a sequence of
:class:`EvalRecord` (each a past prediction optionally paired with a realized outcome) and
returns one :class:`EvaluationReport` bundling five deterministic readouts:

1. Rank agreement between the predicted score and the realized continuous outcome, using
   Kendall tau-b (a concordance measure that corrects for ties on either variable). It is
   ``None`` (never a fabricated 0 or 1) for degenerate inputs: fewer than two comparable
   records, or no variation on either side.
2. Confidence-band coverage: the fraction of records whose realized outcome falls inside
   the predicted band, reported against a caller-supplied nominal band level so over- or
   under-confidence is visible. Records without a band are excluded and counted.
3. Calibration: the existing :func:`subgroup_calibration_report` over
   ``(subgroup, predicted_confidence, outcome_success)``. Calibration is not reimplemented
   here; it is reused verbatim.
4. Baseline lift: the system's rank agreement compared, on the same record subset, against
   a naive baseline ranking the caller supplies (for example a single vanity signal), so
   the harness shows lift rather than an absolute number in isolation.
5. Per-subgroup breakdown of rank agreement and coverage, honest about thin subgroups: a
   subgroup below ``min_subgroup_size`` is flagged insufficient and never silently dropped.

Robustness and honesty invariants (adversarially tested, non-negotiable)
------------------------------------------------------------------------
- Deterministic. No randomness, no wall clock, no hashing or ``id``. Every metric here is
  permutation-invariant over the records, so record order cannot change any number; still,
  records are processed in ``subject_id`` order and subgroups are emitted in name order so
  any incidental ordering is fixed. Non-finite inputs (NaN or infinity) are rejected with a
  ``ValueError`` at the boundary rather than silently coerced, exactly as the confidence
  module does, because a NaN would defeat comparison-based concordance and an infinity would
  poison the arithmetic. Garbage-in surfaces instead of hiding.
- No leakage / pure over fixtures. The harness only COMPARES provided predictions to
  provided outcomes. It never recomputes a prediction from an outcome and never rescales
  either side. It is a pure function of its inputs.
- Honest about small samples and degenerate inputs. Rank agreement is explicitly ``None``
  when undefined; thin subgroups are flagged insufficient rather than dropped; empty input
  yields an honest empty report (all counts zero, every metric ``None`` or empty).
- No false precision. Concordance, coverage, and lift are rounded to a documented
  coarseness (:data:`_METRIC_DECIMALS` decimal places). Coverage is a plain fraction whose
  numerator and denominator are exposed as integers so the exact value is always
  recoverable, never a lone rounded float.

Rank agreement: Kendall tau-b (documented tie rule)
---------------------------------------------------
Over all unordered pairs of records ``(i, j)`` compare the predicted score ``x`` and the
realized outcome value ``y``. A pair is:

- concordant when ``x`` and ``y`` move the same way (``sign(x_i - x_j) == sign(y_i - y_j)``,
  both nonzero);
- discordant when they move oppositely;
- tied on the predicted score only (``x_i == x_j``, ``y_i != y_j``);
- tied on the outcome only (``y_i == y_j``, ``x_i != x_j``);
- tied on both (excluded from the denominator of both variables).

With ``C`` concordant and ``D`` discordant pairs, and letting ``Tx`` be the count of pairs
tied on the predicted score only and ``Ty`` the count tied on the outcome only, the tie-
corrected coefficient is

    tau_b = (C - D) / sqrt((C + D + Ty) * (C + D + Tx))

The two denominator factors are the number of pairs not tied on the predicted score and
the number not tied on the outcome. This is the standard Kendall tau-b (the same tie
correction SciPy's ``kendalltau`` applies): ties inflate neither concordance nor
discordance, and a variable with no variation drives its denominator factor to zero, which
is reported honestly as ``None`` (undefined) rather than a fabricated value. The pairwise
scan is O(n^2), which is intended and acceptable at fixture scale.

Coverage (documented containment rule)
--------------------------------------
A record's realized outcome is "covered" when its ``outcome_value`` lies within the closed
predicted band ``lower <= outcome_value <= upper`` (endpoints included). Coverage is
``covered / with_band`` over the records that carry a band; records without a band are
excluded and counted, never treated as a miss. The predicted band is on the 0..100 scale,
so coverage is only meaningful when ``outcome_value`` is a realized quality score on that
same scale; the harness performs the literal containment check without rescaling either
side (no leakage), and leaves scale alignment to the caller. When a nominal band level is
supplied, ``coverage_gap = empirical_coverage - nominal_level``: a positive gap means the
bands were wider than needed (under-confident), a negative gap means they were too narrow
(over-confident).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from founderlookup.screening.confidence import (
    DEFAULT_CALIBRATION_BINS,
    DEFAULT_MIN_SUBGROUP_SIZE,
    CalibrationEntry,
    CalibrationReport,
    subgroup_calibration_report,
)

# --------------------------------------------------------------------------------------
# Policy version. Every produced report carries this id so any output is reproducible from
# its policy version. Bump on any change to the formulas, tie rule, or thresholds below.
# --------------------------------------------------------------------------------------

EVALUATION_POLICY_VERSION: Final = "eval-harness.v0"

# Output coarseness. Concordance, coverage, and lift are rounded to this many decimals so
# the harness never reports false precision. Coverage additionally exposes its integer
# numerator and denominator, so the exact fraction is always recoverable.
_METRIC_DECIMALS: Final = 3

# Predicted scores and band endpoints live on this closed sub-score scale. Predicted
# confidence lives on the closed unit interval. The realized outcome value is unconstrained
# in range (it may be, for example, capital raised) and is only required to be finite.
_SCORE_MIN: Final = 0.0
_SCORE_MAX: Final = 100.0


# ======================================================================================
# Input record
# ======================================================================================


@dataclass(frozen=True)
class EvalRecord:
    """One past prediction, optionally paired with its realized outcome.

    ``predicted_score`` is on the 0..100 scale and ``predicted_confidence`` on the unit
    interval. ``predicted_band``, when present, is ``(lower, upper)`` on the 0..100 scale
    with ``lower <= upper``; it is ``None`` when no band was asserted, in which case the
    record is excluded from coverage (and counted) rather than treated as a miss.

    The realized outcome is modelled as two aligned fields: ``outcome_value`` is the
    continuous realized outcome (for example capital raised or a realized quality score)
    and ``outcome_success`` is its binary form. An outcome is realized as a whole, so both
    are present together or both are ``None`` together; a record with neither is a pending
    prediction that is counted but excluded from every metric. A record with only one of
    the two is rejected as ill-formed.
    """

    subject_id: str
    subgroup: str
    predicted_score: float
    predicted_confidence: float
    predicted_band: tuple[float, float] | None = None
    outcome_value: float | None = None
    outcome_success: bool | None = None


# ======================================================================================
# Result records (this module's own frozen dataclasses)
# ======================================================================================


@dataclass(frozen=True)
class RankAgreement:
    """Kendall tau-b between a ranking and the realized outcome, with its pair counts.

    ``tau_b`` is in ``[-1, 1]`` when defined and ``None`` when undefined (fewer than two
    records, or no variation on either variable so a denominator factor is zero). The raw
    pair counts are exposed so the coefficient is auditable and hand-checkable and so a
    caller may derive a different concordance index without recomputation.
    """

    tau_b: float | None
    n: int
    concordant: int
    discordant: int
    tied_predicted: int
    tied_outcome: int
    tied_both: int


@dataclass(frozen=True)
class BandCoverage:
    """Empirical coverage of the predicted bands against a nominal level.

    ``coverage`` is the plain fraction ``covered / with_band`` (rounded), or ``None`` when
    no record carried a band. ``without_band`` is the number of records excluded for
    lacking a band. ``coverage_gap`` is ``coverage - nominal_level`` when both are known,
    positive for under-confident (too-wide) bands and negative for over-confident (too-
    narrow) bands, else ``None``.
    """

    nominal_level: float | None
    covered: int
    with_band: int
    without_band: int
    coverage: float | None
    coverage_gap: float | None


@dataclass(frozen=True)
class BaselineLift:
    """The system's rank agreement measured against a naive baseline on one subset.

    Both coefficients are computed over exactly the records that carry a baseline score, so
    the comparison is apples to apples. ``lift`` is ``system_rank_agreement -
    baseline_rank_agreement`` when both are defined, else ``None``. ``without_baseline`` is
    the number of evaluable records that had no baseline score and so sat out the
    comparison.
    """

    label: str | None
    compared: int
    without_baseline: int
    baseline_rank_agreement: float | None
    system_rank_agreement: float | None
    lift: float | None


@dataclass(frozen=True)
class SubgroupEvaluation:
    """Rank agreement and coverage for one subgroup, honest about its sample size.

    ``sufficient_sample`` is ``False`` when ``sample_size < min_subgroup_size``; such a
    subgroup is flagged rather than dropped, and its (likely undefined) rank agreement and
    thin coverage are reported as-is.
    """

    subgroup: str
    sample_size: int
    sufficient_sample: bool
    rank_agreement: RankAgreement
    coverage: BandCoverage


@dataclass(frozen=True)
class EvaluationReport:
    """Deterministic bundle of the overall and per-subgroup evaluation readouts.

    ``total`` counts every input record; ``evaluable`` counts those with a realized outcome
    (both outcome fields present); ``excluded`` counts the pending remainder. Every metric
    below is computed over the evaluable records only.
    """

    total: int
    evaluable: int
    excluded: int
    overall_rank_agreement: RankAgreement
    overall_coverage: BandCoverage
    calibration: CalibrationReport
    baseline_lift: BaselineLift | None
    subgroups: tuple[SubgroupEvaluation, ...]
    min_subgroup_size: int
    policy_version: str = EVALUATION_POLICY_VERSION


# ======================================================================================
# Internal resolved view of an evaluable record
# ======================================================================================


@dataclass(frozen=True)
class _Resolved:
    """An evaluable record with its outcome narrowed to concrete (non-None) values."""

    subject_id: str
    subgroup: str
    predicted_score: float
    predicted_confidence: float
    band: tuple[float, float] | None
    outcome_value: float
    outcome_success: bool


# ======================================================================================
# Small deterministic validation helpers
# ======================================================================================


def _require_finite(value: float, label: str) -> float:
    """Reject a non-finite scalar (NaN or infinity) so garbage surfaces, not coerces.

    A NaN would defeat the comparison-based concordance (every comparison against NaN is
    false, which would silently corrupt the pair counts and the sort order) and an infinity
    would poison the arithmetic. Rejecting is deterministic and keeps garbage-in from
    becoming a quiet output, matching the confidence module's boundary discipline.
    """
    if not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number, got {value!r}")
    return value


def _require_in_range(value: float, low: float, high: float, label: str) -> float:
    """Reject a value outside the inclusive ``[low, high]`` range after a finiteness check."""
    _require_finite(value, label)
    if not (low <= value <= high):
        raise ValueError(f"{label} must lie in [{low}, {high}], got {value!r}")
    return value


def _resolve(records: Sequence[EvalRecord]) -> list[_Resolved]:
    """Validate records and return the evaluable ones, ordered by ``subject_id``.

    Structural and finiteness checks run on every record (evaluable or pending) so
    ill-formed input surfaces regardless of whether it would have contributed to a metric.
    An outcome must be present in both fields or neither; a half-populated outcome is
    rejected. Records are returned in ``subject_id`` order so any downstream iteration is
    deterministic even though every metric is permutation-invariant.
    """
    resolved: list[_Resolved] = []
    for index, record in enumerate(records):
        prefix = f"records[{index}]"
        _require_in_range(
            record.predicted_score, _SCORE_MIN, _SCORE_MAX, f"{prefix}.predicted_score"
        )
        _require_in_range(record.predicted_confidence, 0.0, 1.0, f"{prefix}.predicted_confidence")
        if record.predicted_band is not None:
            lower, upper = record.predicted_band
            _require_in_range(lower, _SCORE_MIN, _SCORE_MAX, f"{prefix}.predicted_band lower")
            _require_in_range(upper, _SCORE_MIN, _SCORE_MAX, f"{prefix}.predicted_band upper")
            if lower > upper:
                raise ValueError(
                    f"{prefix}.predicted_band lower must not exceed upper, "
                    f"got {record.predicted_band!r}"
                )

        has_value = record.outcome_value is not None
        has_success = record.outcome_success is not None
        if has_value != has_success:
            raise ValueError(
                f"{prefix} outcome_value and outcome_success must both be set or both be None"
            )
        if not has_value:
            continue

        outcome_value = record.outcome_value
        outcome_success = record.outcome_success
        assert outcome_value is not None  # narrowed by has_value; guards mypy --strict
        assert outcome_success is not None
        _require_finite(outcome_value, f"{prefix}.outcome_value")
        resolved.append(
            _Resolved(
                subject_id=record.subject_id,
                subgroup=record.subgroup,
                predicted_score=record.predicted_score,
                predicted_confidence=record.predicted_confidence,
                band=record.predicted_band,
                outcome_value=outcome_value,
                outcome_success=outcome_success,
            )
        )
    resolved.sort(key=lambda item: item.subject_id)
    return resolved


# ======================================================================================
# Rank agreement (Kendall tau-b)
# ======================================================================================


def _kendall_tau_b(xs: Sequence[float], ys: Sequence[float]) -> RankAgreement:
    """Kendall tau-b between two aligned rankings, with the full pair-count breakdown.

    ``xs`` and ``ys`` are paired sample by sample. The coefficient and the tie rule are
    exactly as documented in the module docstring. The result is ``None`` for fewer than
    two samples or when either variable has no variation (a zero denominator factor), so a
    degenerate input is reported as undefined rather than a fabricated 0 or 1.
    """
    n = len(xs)
    concordant = 0
    discordant = 0
    tied_predicted = 0
    tied_outcome = 0
    tied_both = 0
    for i in range(n):
        xi = xs[i]
        yi = ys[i]
        for j in range(i + 1, n):
            dx = xi - xs[j]
            dy = yi - ys[j]
            if dx == 0.0 and dy == 0.0:
                tied_both += 1
            elif dx == 0.0:
                tied_predicted += 1
            elif dy == 0.0:
                tied_outcome += 1
            elif (dx > 0.0) == (dy > 0.0):
                concordant += 1
            else:
                discordant += 1

    # Denominator factors: pairs not tied on the predicted score, and pairs not tied on the
    # outcome. Either being zero means that variable has no variation, so tau-b is undefined.
    not_tied_predicted = concordant + discordant + tied_outcome
    not_tied_outcome = concordant + discordant + tied_predicted
    if n < 2 or not_tied_predicted == 0 or not_tied_outcome == 0:
        tau_b: float | None = None
    else:
        tau_b = round(
            (concordant - discordant) / math.sqrt(not_tied_predicted * not_tied_outcome),
            _METRIC_DECIMALS,
        )

    return RankAgreement(
        tau_b=tau_b,
        n=n,
        concordant=concordant,
        discordant=discordant,
        tied_predicted=tied_predicted,
        tied_outcome=tied_outcome,
        tied_both=tied_both,
    )


def _rank_agreement(resolved: Sequence[_Resolved]) -> RankAgreement:
    """Rank agreement of the predicted score against the realized outcome value."""
    xs = [item.predicted_score for item in resolved]
    ys = [item.outcome_value for item in resolved]
    return _kendall_tau_b(xs, ys)


# ======================================================================================
# Confidence-band coverage
# ======================================================================================


def _band_coverage(resolved: Sequence[_Resolved], nominal_level: float | None) -> BandCoverage:
    """Fraction of realized outcomes falling inside their predicted band.

    Containment is the closed interval ``lower <= outcome_value <= upper``. Records without
    a band are excluded and counted in ``without_band``. Coverage is ``None`` when no record
    carried a band; the gap is computed from the exact (unrounded) fraction to avoid
    compounding rounding, then rounded once.
    """
    covered = 0
    with_band = 0
    without_band = 0
    for item in resolved:
        if item.band is None:
            without_band += 1
            continue
        with_band += 1
        lower, upper = item.band
        if lower <= item.outcome_value <= upper:
            covered += 1

    if with_band == 0:
        coverage: float | None = None
        coverage_gap: float | None = None
    else:
        exact = covered / with_band
        coverage = round(exact, _METRIC_DECIMALS)
        coverage_gap = (
            None if nominal_level is None else round(exact - nominal_level, _METRIC_DECIMALS)
        )

    return BandCoverage(
        nominal_level=nominal_level,
        covered=covered,
        with_band=with_band,
        without_band=without_band,
        coverage=coverage,
        coverage_gap=coverage_gap,
    )


# ======================================================================================
# Baseline lift
# ======================================================================================


def _baseline_lift(
    resolved: Sequence[_Resolved],
    baseline_scores: Mapping[str, float],
    label: str | None,
) -> BaselineLift:
    """Compare the system's rank agreement against a naive baseline on one shared subset.

    The subset is exactly the evaluable records whose ``subject_id`` carries a baseline
    score. Both coefficients are measured over that subset so the lift is apples to apples.
    Baseline scores are checked for finiteness before use.
    """
    subset = [item for item in resolved if item.subject_id in baseline_scores]
    without_baseline = len(resolved) - len(subset)

    baseline_xs: list[float] = []
    for item in subset:
        score = baseline_scores[item.subject_id]
        _require_finite(score, f"baseline_scores[{item.subject_id!r}]")
        baseline_xs.append(score)
    outcomes = [item.outcome_value for item in subset]
    system_xs = [item.predicted_score for item in subset]

    baseline_ra = _kendall_tau_b(baseline_xs, outcomes).tau_b
    system_ra = _kendall_tau_b(system_xs, outcomes).tau_b
    lift = (
        None
        if baseline_ra is None or system_ra is None
        else round(system_ra - baseline_ra, _METRIC_DECIMALS)
    )

    return BaselineLift(
        label=label,
        compared=len(subset),
        without_baseline=without_baseline,
        baseline_rank_agreement=baseline_ra,
        system_rank_agreement=system_ra,
        lift=lift,
    )


# ======================================================================================
# Per-subgroup breakdown
# ======================================================================================


def _subgroup_evaluations(
    resolved: Sequence[_Resolved],
    nominal_level: float | None,
    min_subgroup_size: int,
) -> tuple[SubgroupEvaluation, ...]:
    """Per-subgroup rank agreement and coverage, in sorted subgroup-name order.

    Every subgroup that has at least one evaluable record is emitted; one below
    ``min_subgroup_size`` is flagged ``sufficient_sample=False`` rather than dropped.
    """
    grouped: dict[str, list[_Resolved]] = {}
    for item in resolved:
        grouped.setdefault(item.subgroup, []).append(item)

    evaluations: list[SubgroupEvaluation] = []
    for name in sorted(grouped):
        members = grouped[name]
        evaluations.append(
            SubgroupEvaluation(
                subgroup=name,
                sample_size=len(members),
                sufficient_sample=len(members) >= min_subgroup_size,
                rank_agreement=_rank_agreement(members),
                coverage=_band_coverage(members, nominal_level),
            )
        )
    return tuple(evaluations)


# ======================================================================================
# Public entry point
# ======================================================================================


def evaluate_predictions(
    records: Sequence[EvalRecord],
    *,
    nominal_band_level: float | None = None,
    baseline_scores: Mapping[str, float] | None = None,
    baseline_label: str | None = None,
    min_subgroup_size: int = DEFAULT_MIN_SUBGROUP_SIZE,
    calibration_bins: int = DEFAULT_CALIBRATION_BINS,
) -> EvaluationReport:
    """Evaluate past predictions against realized outcomes, deterministically.

    ``records`` are past predictions, each optionally paired with a realized outcome. Only
    records with a realized outcome (both outcome fields present) are evaluable; the pending
    remainder is counted but sits out every metric. The returned :class:`EvaluationReport`
    bundles overall and per-subgroup rank agreement (Kendall tau-b of predicted score versus
    outcome value), band coverage against ``nominal_band_level``, the reused subgroup
    calibration report over ``(subgroup, predicted_confidence, outcome_success)``, and, when
    ``baseline_scores`` is supplied, the lift of the system's ranking over that naive
    baseline.

    ``nominal_band_level`` (in ``[0, 1]``) is the level the predicted bands were meant to
    hit; it is used only to compute the coverage gap and never changes which outcomes are
    counted as covered. ``baseline_scores`` maps ``subject_id`` to a naive ranking signal;
    records absent from it sit out the baseline comparison and are counted. ``baseline_label``
    is provenance for the baseline signal. ``min_subgroup_size`` sets the honesty threshold
    below which a subgroup is flagged insufficient (never dropped). ``calibration_bins`` is
    forwarded to the reused calibration report.

    Deterministic and pure: every metric is a fixed computation over the provided inputs and
    is permutation-invariant, so identical inputs always produce identical reports. Empty
    input yields an honest empty report (all counts zero, every metric ``None`` or empty).
    Non-finite numbers, out-of-range scores or confidences, an inverted band, or a half-
    populated outcome are rejected with a ``ValueError`` at the boundary.
    """
    if nominal_band_level is not None:
        _require_in_range(nominal_band_level, 0.0, 1.0, "nominal_band_level")

    resolved = _resolve(records)

    calibration = subgroup_calibration_report(
        [
            CalibrationEntry(
                subgroup=item.subgroup,
                predicted_confidence=item.predicted_confidence,
                actual_outcome=item.outcome_success,
            )
            for item in resolved
        ],
        bin_count=calibration_bins,
        min_subgroup_size=min_subgroup_size,
    )

    baseline_lift = (
        None
        if baseline_scores is None
        else _baseline_lift(resolved, baseline_scores, baseline_label)
    )

    return EvaluationReport(
        total=len(records),
        evaluable=len(resolved),
        excluded=len(records) - len(resolved),
        overall_rank_agreement=_rank_agreement(resolved),
        overall_coverage=_band_coverage(resolved, nominal_band_level),
        calibration=calibration,
        baseline_lift=baseline_lift,
        subgroups=_subgroup_evaluations(resolved, nominal_band_level, min_subgroup_size),
        min_subgroup_size=min_subgroup_size,
    )


__all__ = [
    "EVALUATION_POLICY_VERSION",
    "BandCoverage",
    "BaselineLift",
    "EvalRecord",
    "EvaluationReport",
    "RankAgreement",
    "SubgroupEvaluation",
    "evaluate_predictions",
]
