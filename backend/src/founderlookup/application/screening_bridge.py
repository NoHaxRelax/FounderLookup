"""Honest deterministic bridge from explicit signals to screening contracts.

The application service owns workflow state while the Data/ML modules own rubric
semantics.  This module is the narrow seam between them: callers register an immutable,
subject-keyed signal bundle and the bridge converts only those explicit signals into the
frozen assessment containers.  Source-artifact presence is deliberately absent from the
interface, so a stored deck or URL can never become an inferred founder observation.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from threading import RLock

from founderlookup.domain.assessment import IndependentAxes
from founderlookup.domain.common import (
    KnowledgeState,
    KnowledgeValue,
    ScalarValue,
    StableId,
    VersionId,
)
from founderlookup.domain.scoring import CoverageSummary, FounderScoreSnapshot
from founderlookup.screening.axes import (
    AXIS_RUBRIC_VERSION,
    AxisSignal,
    TrendPoint,
    assemble_independent_axes,
    assess_founder_axis,
    assess_idea_vs_market_axis,
    assess_market_axis,
)
from founderlookup.screening.confidence import ConfidenceBand, estimate_confidence_band
from founderlookup.screening.founder_reads import (
    BuilderFundabilityGap,
    EvidenceGrade,
    FounderRead,
    GradedObservation,
    builder_fundability_gap,
    builder_signal_read,
    fundability_read,
)
from founderlookup.screening.rubrics import (
    FOUNDER_SCORE_POLICY_VERSION,
    ContributionTier,
    FounderFactorObservation,
    score_founder,
)


@dataclass(frozen=True, slots=True)
class FounderSignalObservation:
    """One source-backed founder signal shared by scoring and diagnostic reads."""

    factor_key: str
    tier: ContributionTier
    grade: EvidenceGrade
    observed_value: KnowledgeValue[ScalarValue]
    rationale: str
    evidence_ids: tuple[StableId, ...] = ()

    def __post_init__(self) -> None:
        if not self.factor_key.strip():
            raise ValueError("founder signal factor_key must be non-blank")
        if not self.rationale.strip():
            raise ValueError("founder signal rationale must be non-blank")
        if self.observed_value.state is KnowledgeState.KNOWN and not self.evidence_ids:
            raise ValueError("a known founder signal requires supporting Evidence identifiers")

    def founder_score_observation(self) -> FounderFactorObservation:
        """Adapt the shared signal to the versioned Founder Score rubric input."""

        return FounderFactorObservation(
            factor_key=self.factor_key,
            tier=self.tier,
            observed_value=self.observed_value,
            rationale=self.rationale,
            evidence_ids=self.evidence_ids,
        )

    def graded_observation(self) -> GradedObservation:
        """Adapt the shared signal to the builder/fundability read input."""

        return GradedObservation(
            factor_key=self.factor_key,
            tier=self.tier,
            grade=self.grade,
            observed_value=self.observed_value,
            rationale=self.rationale,
            evidence_ids=self.evidence_ids,
        )


@dataclass(frozen=True, slots=True)
class ConfidenceInputs:
    """Explicit reasoned samples for the framework-neutral confidence estimator."""

    reasoned_samples: tuple[float, ...]
    coverage_level: float
    snap_score: float | None = None


@dataclass(frozen=True, slots=True)
class ScreeningSignalBundle:
    """Immutable inputs for one candidate or Opportunity assessment.

    Coverage is supplied by the caller from canonical Evidence.  The bridge never derives
    it from a Source Artifact count.  A known founder signal must point to Evidence, and a
    known axis signal must point to at least one Claim; the explicit coverage must also
    report that Evidence exists.
    """

    coverage: CoverageSummary
    founder_signals: tuple[FounderSignalObservation, ...] = ()
    founder_axis_signals: tuple[AxisSignal, ...] = ()
    market_axis_signals: tuple[AxisSignal, ...] = ()
    idea_vs_market_axis_signals: tuple[AxisSignal, ...] = ()
    founder_trend_points: tuple[TrendPoint, ...] = ()
    market_trend_points: tuple[TrendPoint, ...] = ()
    idea_vs_market_trend_points: tuple[TrendPoint, ...] = ()
    confidence_inputs: ConfidenceInputs | None = None

    def __post_init__(self) -> None:
        axis_signals = (
            *self.founder_axis_signals,
            *self.market_axis_signals,
            *self.idea_vs_market_axis_signals,
        )
        known_founder_signal = any(
            signal.observed_value.state is KnowledgeState.KNOWN for signal in self.founder_signals
        )
        known_axis_signal = any(
            signal.reading.state is KnowledgeState.KNOWN for signal in axis_signals
        )
        for signal in axis_signals:
            if signal.reading.state is KnowledgeState.KNOWN and not signal.claim_ids:
                raise ValueError("a known axis signal requires supporting Claim identifiers")
        if (known_founder_signal or known_axis_signal) and self.coverage.evidence_count == 0:
            raise ValueError("known screening signals require explicit non-zero Evidence coverage")


@dataclass(frozen=True, slots=True)
class ScreeningDiagnostics:
    """Internal reads intentionally kept outside ``AssessmentEnvelope``."""

    builder_signal: FounderRead | None
    fundability: FounderRead | None
    builder_fundability_gap: BuilderFundabilityGap | None
    confidence: ConfidenceBand | None


@dataclass(frozen=True, slots=True)
class DeterministicScreeningProjection:
    """Frozen-contract fields produced by the real deterministic Data/ML rubrics."""

    coverage: CoverageSummary
    founder_score: KnowledgeValue[FounderScoreSnapshot]
    axes: IndependentAxes
    founder_score_version: VersionId = FOUNDER_SCORE_POLICY_VERSION
    axis_rubric_version: VersionId = AXIS_RUBRIC_VERSION


class DeterministicScreeningBridge:
    """Thread-safe registry and evaluator for explicit subject-keyed signal bundles."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._bundles: dict[StableId, ScreeningSignalBundle] = {}
        self._diagnostics: dict[StableId, ScreeningDiagnostics] = {}

    def register(self, subject_id: StableId, bundle: ScreeningSignalBundle) -> None:
        """Register or deliberately replace one subject's immutable input bundle."""

        if not subject_id.strip():
            raise ValueError("screening signal subject_id must be non-blank")
        with self._lock:
            self._bundles[subject_id] = bundle
            self._diagnostics.pop(subject_id, None)

    def diagnostics_for(self, subject_id: StableId) -> ScreeningDiagnostics | None:
        """Return the last immutable diagnostics computed for ``subject_id``."""

        with self._lock:
            return self._diagnostics.get(subject_id)

    def project(
        self,
        subject_id: StableId,
        *,
        founder_identity: KnowledgeValue[StableId],
        as_of: datetime,
        id_factory: Callable[[], StableId],
    ) -> DeterministicScreeningProjection | None:
        """Run real deterministic rubrics when a subject bundle is registered.

        ``as_of`` is checked by the downstream strict UTC Pydantic contract.
        """

        with self._lock:
            bundle = self._bundles.get(subject_id)
        if bundle is None:
            return None

        founder_id = (
            founder_identity.value if founder_identity.state is KnowledgeState.KNOWN else None
        )
        founder_signals = bundle.founder_axis_signals if founder_id is not None else ()
        axes = assemble_independent_axes(
            founder=assess_founder_axis(
                founder_signals,
                coverage=bundle.coverage,
                assessment_id=id_factory(),
                assessment_version_id=id_factory(),
                trend_points=(bundle.founder_trend_points if founder_id is not None else ()),
            ),
            market=assess_market_axis(
                bundle.market_axis_signals,
                coverage=bundle.coverage,
                assessment_id=id_factory(),
                assessment_version_id=id_factory(),
                trend_points=bundle.market_trend_points,
            ),
            idea_vs_market=assess_idea_vs_market_axis(
                bundle.idea_vs_market_axis_signals,
                coverage=bundle.coverage,
                assessment_id=id_factory(),
                assessment_version_id=id_factory(),
                trend_points=bundle.idea_vs_market_trend_points,
            ),
        )

        known_founder_signals = tuple(
            signal
            for signal in bundle.founder_signals
            if signal.observed_value.state is KnowledgeState.KNOWN
        )
        if founder_id is None:
            founder_score = KnowledgeValue[FounderScoreSnapshot].unknown(
                founder_identity.reason or "founder_identity_unresolved"
            )
        elif not known_founder_signals:
            founder_score = KnowledgeValue[FounderScoreSnapshot].unknown(
                "no_evidence_backed_founder_signals"
            )
        else:
            founder_score = KnowledgeValue[FounderScoreSnapshot].known(
                score_founder(
                    founder_id=founder_id,
                    snapshot_id=id_factory(),
                    snapshot_version_id=id_factory(),
                    as_of=as_of,
                    coverage=bundle.coverage,
                    observations=tuple(
                        signal.founder_score_observation() for signal in bundle.founder_signals
                    ),
                )
            )

        diagnostics = self._compute_diagnostics(
            bundle,
            founder_identity_resolved=founder_id is not None,
            has_known_founder_signals=bool(known_founder_signals),
        )
        with self._lock:
            if self._bundles.get(subject_id) is bundle:
                self._diagnostics[subject_id] = diagnostics

        return DeterministicScreeningProjection(
            coverage=bundle.coverage,
            founder_score=founder_score,
            axes=axes,
        )

    @staticmethod
    def _compute_diagnostics(
        bundle: ScreeningSignalBundle,
        *,
        founder_identity_resolved: bool,
        has_known_founder_signals: bool,
    ) -> ScreeningDiagnostics:
        graded: Sequence[GradedObservation] = tuple(
            signal.graded_observation() for signal in bundle.founder_signals
        )
        if founder_identity_resolved and has_known_founder_signals:
            builder = builder_signal_read(graded)
            fundability = fundability_read(graded)
            gap = builder_fundability_gap(builder, fundability)
        else:
            builder = None
            fundability = None
            gap = None
        confidence = (
            estimate_confidence_band(
                bundle.confidence_inputs.reasoned_samples,
                snap_score=bundle.confidence_inputs.snap_score,
                coverage_level=bundle.confidence_inputs.coverage_level,
            )
            if bundle.confidence_inputs is not None
            else None
        )
        return ScreeningDiagnostics(
            builder_signal=builder,
            fundability=fundability,
            builder_fundability_gap=gap,
            confidence=confidence,
        )


__all__ = [
    "ConfidenceInputs",
    "DeterministicScreeningBridge",
    "DeterministicScreeningProjection",
    "FounderSignalObservation",
    "ScreeningDiagnostics",
    "ScreeningSignalBundle",
]
