"""Assessment-envelope, readiness, memo, and Decision invariants."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from founderlookup.domain import (
    AssessmentEnvelope,
    CoverageLevel,
    CoverageSummary,
    Decision,
    DecisionReadiness,
    DecisionReadinessStatus,
    FounderAxisAssessment,
    FounderAxisRating,
    FullAssessmentIdentity,
    IdeaVsMarketAxisAssessment,
    IdeaVsMarketAxisRating,
    IndependentAxes,
    InvestmentMemo,
    KnowledgeValue,
    MarketAxisAssessment,
    MarketAxisRating,
    MemoSection,
    MemoSectionKind,
    OpportunityOrigin,
    PreliminaryAssessmentIdentity,
    ReadinessBlocker,
    Trend,
    VersionManifest,
)

NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


def _coverage() -> CoverageSummary:
    return CoverageSummary(
        level=CoverageLevel.LOW,
        source_count=1,
        artifact_count=1,
        evidence_count=1,
        freshest_evidence_at=KnowledgeValue[datetime].known(NOW),
    )


def _axes() -> IndependentAxes:
    return IndependentAxes(
        founder=FounderAxisAssessment(
            assessment_id="axis:founder",
            assessment_version_id="axis-version:founder",
            rubric_version="axis-rubric.v0",
            trend=Trend.UNKNOWN,
            confidence=KnowledgeValue[float].unknown("insufficient comparable history"),
            coverage=_coverage(),
            rating=FounderAxisRating.UNKNOWN,
        ),
        market=MarketAxisAssessment(
            assessment_id="axis:market",
            assessment_version_id="axis-version:market",
            rubric_version="axis-rubric.v0",
            trend=Trend.UNKNOWN,
            confidence=KnowledgeValue[float].unknown("insufficient comparable history"),
            coverage=_coverage(),
            rating=MarketAxisRating.BEAR,
        ),
        idea_vs_market=IdeaVsMarketAxisAssessment(
            assessment_id="axis:idea-market",
            assessment_version_id="axis-version:idea-market",
            rubric_version="axis-rubric.v0",
            trend=Trend.UNKNOWN,
            confidence=KnowledgeValue[float].unknown("insufficient comparable history"),
            coverage=_coverage(),
            rating=IdeaVsMarketAxisRating.PIVOTABLE,
        ),
    )


def _memo_sections() -> tuple[MemoSection, ...]:
    return tuple(
        MemoSection(
            kind=kind,
            content=KnowledgeValue[str].unknown("Evidence not yet sufficient"),
        )
        for kind in (
            MemoSectionKind.COMPANY_SNAPSHOT,
            MemoSectionKind.INVESTMENT_HYPOTHESES,
            MemoSectionKind.SWOT,
            MemoSectionKind.PROBLEM_AND_PRODUCT,
            MemoSectionKind.TRACTION_AND_KPIS,
        )
    )


def test_preliminary_envelope_uses_common_axes_but_cannot_claim_readiness() -> None:
    identity = PreliminaryAssessmentIdentity(
        outbound_candidate_id="candidate:1",
        founder_id=KnowledgeValue[str].unknown("founder_identity_unresolved"),
        company_id=KnowledgeValue[str].known("company:1"),
    )
    envelope = AssessmentEnvelope(
        assessment_id="assessment:1",
        assessment_version_id="assessment-version:1",
        identity=identity,
        versions=VersionManifest(),
        input_snapshot_id="snapshot:1",
        input_snapshot_as_of=NOW,
        coverage=_coverage(),
        deterministic_results=(),
        founder_score=KnowledgeValue.unknown("founder_identity_unresolved"),
        axes=_axes(),
        claim_ids=(),
        evidence_ids=(),
        run_id="run:1",
        created_at=NOW,
    )
    assert envelope.identity.mode == "preliminary"
    assert envelope.axes.market.rating is MarketAxisRating.BEAR
    assert "aggregate" not in type(envelope.axes).model_fields

    full_readiness = DecisionReadiness(
        readiness_id="readiness:1",
        readiness_version_id="readiness-version:1",
        screening_case_id="screening-case:1",
        policy_version="readiness-policy.v0",
        evaluated_at=NOW,
        status=DecisionReadinessStatus.BLOCKED,
        checks=(),
        blockers=(
            ReadinessBlocker(
                blocker_id="blocker:1",
                check_key="founder_identity",
                reason="Founder is unresolved",
            ),
        ),
    )
    with pytest.raises(ValidationError, match="preliminary assessment"):
        AssessmentEnvelope.model_validate(
            {
                **envelope.model_dump(),
                "decision_readiness": full_readiness,
            }
        )


def test_memo_requires_all_five_named_sections() -> None:
    sections = _memo_sections()
    valid = InvestmentMemo(
        memo_id="memo:1",
        memo_version_id="memo-version:1",
        opportunity_id="opportunity:1",
        screening_case_id="screening-case:1",
        assessment_id="assessment:1",
        run_id="run:1",
        thesis_version="thesis.v0",
        evidence_as_of=NOW,
        generated_at=NOW,
        sections=sections,
    )
    assert len(valid.sections) == 5

    with pytest.raises(ValidationError, match="all five required"):
        InvestmentMemo(
            memo_id="memo:2",
            memo_version_id="memo-version:2",
            opportunity_id="opportunity:1",
            screening_case_id="screening-case:1",
            assessment_id="assessment:1",
            run_id="run:1",
            thesis_version="thesis.v0",
            evidence_as_of=NOW,
            generated_at=NOW,
            sections=(
                *sections[:-1],
                MemoSection(
                    kind=MemoSectionKind.RISKS_AND_DILIGENCE,
                    content=KnowledgeValue[str].unknown("not evaluated"),
                ),
            ),
        )


def test_readiness_and_decision_names_keep_human_authority_explicit() -> None:
    with pytest.raises(ValidationError, match="requires at least one blocker"):
        DecisionReadiness(
            readiness_id="readiness:1",
            readiness_version_id="readiness-version:1",
            screening_case_id="screening-case:1",
            policy_version="readiness-policy.v0",
            evaluated_at=NOW,
            status=DecisionReadinessStatus.BLOCKED,
            checks=(),
        )

    assert Decision.model_json_schema()["title"] == "Decision"


def test_full_identity_allows_unresolved_founder_without_placeholder() -> None:
    identity = FullAssessmentIdentity(
        origin=OpportunityOrigin.INBOUND,
        application_id="application:1",
        opportunity_id="opportunity:1",
        screening_case_id="screening-case:1",
        company_id="company:1",
        founder_id=KnowledgeValue[str].unknown("founder_identity_unresolved"),
    )
    assert identity.founder_id.value is None
