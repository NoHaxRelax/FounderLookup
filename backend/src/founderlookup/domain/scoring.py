"""Versioned score containers without prescribing v0 rubrics or thresholds."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from founderlookup.domain.common import (
    DomainModel,
    KnowledgeValue,
    NonBlankStr,
    NonNegativeInt,
    ScalarValue,
    Score100,
    StableId,
    UTCDateTime,
    VersionId,
)

FOUNDER_SCORE_SCHEMA_VERSION = "founder-score.v0"
CLAIM_TRUST_SCHEMA_VERSION = "claim-trust.v0"


class CoverageLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CoverageSummary(DomainModel):
    """Source richness, kept separate from quality or eligibility."""

    level: CoverageLevel
    source_count: NonNegativeInt
    artifact_count: NonNegativeInt
    evidence_count: NonNegativeInt
    source_categories: tuple[NonBlankStr, ...] = ()
    missing_fields: tuple[NonBlankStr, ...] = ()
    conflicted_fields: tuple[NonBlankStr, ...] = ()
    freshest_evidence_at: KnowledgeValue[UTCDateTime]


class QualitativeUncertainty(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


class FounderScoreFactor(DomainModel):
    """One disclosed input/contribution; calculation policy is versioned elsewhere."""

    factor_key: NonBlankStr
    label: NonBlankStr
    observed_value: KnowledgeValue[ScalarValue]
    contribution: KnowledgeValue[float]
    evidence_ids: tuple[StableId, ...] = ()
    rationale: NonBlankStr


class FounderScoreSnapshot(DomainModel):
    """Immutable person-level score snapshot that survives across opportunities."""

    schema_version: Literal["founder-score.v0"] = FOUNDER_SCORE_SCHEMA_VERSION
    snapshot_id: StableId
    snapshot_version_id: StableId
    founder_id: StableId
    score_policy_version: VersionId
    as_of: UTCDateTime
    score: Score100
    factors: AnnotatedFactorTuple
    coverage: CoverageSummary
    uncertainty: QualitativeUncertainty
    provisional: bool

    @model_validator(mode="after")
    def reject_duplicate_factors(self) -> Self:
        keys = tuple(factor.factor_key for factor in self.factors)
        if len(keys) != len(set(keys)):
            raise ValueError("founder score factor keys must be unique")
        return self


AnnotatedFactorTuple = tuple[FounderScoreFactor, ...]


class TrustFactorKind(StrEnum):
    PROVENANCE = "provenance"
    INDEPENDENCE = "independence"
    RECENCY = "recency"
    EXTRACTION_CERTAINTY = "extraction_certainty"
    CORROBORATION = "corroboration"
    CONTRADICTION = "contradiction"


class TrustFactorSignal(StrEnum):
    STRENGTHENS = "strengthens"
    NEUTRAL = "neutral"
    WEAKENS = "weakens"


class ClaimTrustFactor(DomainModel):
    """Qualitative factor input; task 3.4 will define numeric weighting."""

    kind: TrustFactorKind
    signal: KnowledgeValue[TrustFactorSignal]
    evidence_ids: tuple[StableId, ...] = ()
    rationale: NonBlankStr


class TrustScoreState(StrEnum):
    SCORED = "scored"
    UNSCORED = "unscored"
    UNSUPPORTED = "unsupported"


class ClaimTrustScore(DomainModel):
    """Claim-level score state; never a company- or founder-wide score."""

    schema_version: Literal["claim-trust.v0"] = CLAIM_TRUST_SCHEMA_VERSION
    state: TrustScoreState
    trust_policy_version: VersionId
    score: Score100 | None = None
    factors: tuple[ClaimTrustFactor, ...] = ()
    reason: NonBlankStr | None = None

    @model_validator(mode="after")
    def validate_score_state(self) -> Self:
        kinds = tuple(factor.kind for factor in self.factors)
        if len(kinds) != len(set(kinds)):
            raise ValueError("claim trust factor kinds must be unique")

        if self.state is TrustScoreState.SCORED:
            if self.score is None:
                raise ValueError("scored claim trust requires score")
            if set(kinds) != set(TrustFactorKind):
                raise ValueError("scored claim trust must expose all six factor kinds")
            if self.reason is not None:
                raise ValueError("scored claim trust cannot carry an unscored reason")
        else:
            if self.score is not None:
                raise ValueError("unscored or unsupported claim trust cannot carry score")
            if self.reason is None:
                raise ValueError("unscored or unsupported claim trust requires reason")
        return self
