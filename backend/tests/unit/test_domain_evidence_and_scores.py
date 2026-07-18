"""Evidence-graph and score-container contract tests."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from founderlookup.domain import (
    Claim,
    ClaimOrigin,
    ClaimStatus,
    ClaimTrustFactor,
    ClaimTrustScore,
    CoverageLevel,
    CoverageSummary,
    DataClassification,
    EntityKind,
    FounderScoreFactor,
    FounderScoreSnapshot,
    KnowledgeValue,
    QualitativeUncertainty,
    SourceArtifact,
    SourceArtifactKind,
    SourceCategory,
    SubjectRef,
    TrustFactorKind,
    TrustFactorSignal,
    TrustScoreState,
)

NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


def _coverage() -> CoverageSummary:
    return CoverageSummary(
        level=CoverageLevel.LOW,
        source_count=1,
        artifact_count=1,
        evidence_count=1,
        source_categories=(SourceCategory.APPLICATION_DECK.value,),
        missing_fields=("public_history",),
        freshest_evidence_at=KnowledgeValue[datetime].known(NOW),
    )


def _trust_factor(kind: TrustFactorKind) -> ClaimTrustFactor:
    return ClaimTrustFactor(
        kind=kind,
        signal=KnowledgeValue[TrustFactorSignal].known(TrustFactorSignal.NEUTRAL),
        rationale="Factor retained for the later calibrated policy",
    )


def test_scored_claim_trust_exposes_every_factor_without_a_rubric() -> None:
    trust = ClaimTrustScore(
        state=TrustScoreState.SCORED,
        trust_policy_version="trust-policy.v0",
        score=62.0,
        factors=tuple(_trust_factor(kind) for kind in TrustFactorKind),
    )
    assert trust.score == 62.0

    with pytest.raises(ValidationError, match="all six factor kinds"):
        ClaimTrustScore(
            state=TrustScoreState.SCORED,
            trust_policy_version="trust-policy.v0",
            score=62.0,
            factors=(_trust_factor(TrustFactorKind.PROVENANCE),),
        )


def test_founder_score_factor_alias_is_resolved_and_snapshot_is_person_level() -> None:
    factor = FounderScoreFactor(
        factor_key="technical_execution",
        label="Technical execution",
        observed_value=KnowledgeValue[str | int | float | bool].known("prototype shipped"),
        contribution=KnowledgeValue[float].known(8.0),
        evidence_ids=("evidence:prototype",),
        rationale="A source-backed work product is present",
    )
    snapshot = FounderScoreSnapshot(
        snapshot_id="founder-score:1",
        snapshot_version_id="founder-score-version:1",
        founder_id="founder:1",
        score_policy_version="founder-score-policy.v0",
        as_of=NOW,
        score=64.0,
        factors=(factor,),
        coverage=_coverage(),
        uncertainty=QualitativeUncertainty.HIGH,
        provisional=True,
    )
    assert snapshot.factors == (factor,)
    assert snapshot.founder_id == "founder:1"


def test_unsupported_claim_cannot_smuggle_supporting_evidence() -> None:
    trust = ClaimTrustScore(
        state=TrustScoreState.UNSUPPORTED,
        trust_policy_version="trust-policy.v0",
        reason="No valid source locator",
    )
    with pytest.raises(ValidationError, match="cannot carry supporting evidence"):
        Claim(
            claim_id="claim:1",
            claim_version_id="claim-version:1",
            subject=SubjectRef(kind=EntityKind.COMPANY, subject_id="company:1"),
            predicate="traction",
            statement="The company has enterprise traction",
            status=ClaimStatus.UNSUPPORTED,
            origin=ClaimOrigin.MODEL_ASSISTED,
            as_of=NOW,
            created_at=NOW,
            supporting_evidence_ids=("evidence:1",),
            trust=trust,
        )


def test_artifact_history_and_source_categories_are_explicit() -> None:
    assert SourceCategory.ACCELERATOR_COHORT.value == "accelerator_cohort"
    assert SourceCategory.COMPANY_UPDATE.value == "company_update"

    with pytest.raises(ValidationError, match="first artifact version"):
        SourceArtifact(
            source_artifact_id="artifact:1",
            artifact_series_id="artifact-series:1",
            artifact_version_id="artifact-version:1",
            version_number=1,
            previous_source_artifact_id="artifact:old",
            kind=SourceArtifactKind.DOCUMENT,
            source_category=SourceCategory.APPLICATION_DECK,
            classification=DataClassification.FOUNDER_PRIVATE,
            origin_locator="upload:deck",
            display_name="deck.pdf",
            media_type="application/pdf",
            content_sha256="a" * 64,
            retrieved_at=NOW,
            source_event_time=KnowledgeValue[datetime].unknown("not supplied"),
        )
