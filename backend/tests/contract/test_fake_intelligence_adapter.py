"""Shared contract tests for framework-neutral structured intelligence."""

import asyncio
from datetime import UTC, datetime

import pytest

from founderlookup.domain.assessment import (
    AssessmentEnvelope,
    FounderAxisAssessment,
    FounderAxisRating,
    IdeaVsMarketAxisAssessment,
    IdeaVsMarketAxisRating,
    IndependentAxes,
    MarketAxisAssessment,
    MarketAxisRating,
    PreliminaryAssessmentIdentity,
    Trend,
)
from founderlookup.domain.common import (
    EntityKind,
    KnowledgeState,
    KnowledgeValue,
    SubjectRef,
    VersionManifest,
)
from founderlookup.domain.lifecycles import AssessmentMode
from founderlookup.domain.scoring import CoverageLevel, CoverageSummary
from founderlookup.screening.fakes import (
    FakeIntelligenceAdapter,
    MissingFakeAssessmentError,
)
from founderlookup.screening.ports import IntelligencePort, IntelligenceRequest

FIXED_TIME = datetime(2026, 7, 1, 13, tzinfo=UTC)


def _coverage() -> CoverageSummary:
    return CoverageSummary(
        level=CoverageLevel.LOW,
        source_count=1,
        artifact_count=1,
        evidence_count=0,
        source_categories=("hackathon",),
        missing_fields=("founder_identity", "enterprise_traction"),
        freshest_evidence_at=KnowledgeValue[datetime].known(FIXED_TIME),
    )


def _assessment() -> AssessmentEnvelope:
    coverage = _coverage()
    return AssessmentEnvelope(
        assessment_id="assessment-001",
        assessment_version_id="assessment-001.v1",
        identity=PreliminaryAssessmentIdentity(
            outbound_candidate_id="candidate-001",
            founder_id=KnowledgeValue[str].unknown("Founder identity is unresolved"),
            company_id=KnowledgeValue[str].known("company-001"),
        ),
        versions=VersionManifest(),
        input_snapshot_id="snapshot-001",
        input_snapshot_as_of=FIXED_TIME,
        coverage=coverage,
        deterministic_results=(),
        founder_score=KnowledgeValue.unknown(
            "Founder score is unknown until founder identity is resolved"
        ),
        axes=IndependentAxes(
            founder=FounderAxisAssessment(
                assessment_id="axis-founder-001",
                assessment_version_id="axis-founder-001.v1",
                rubric_version="axis-rubric.v0",
                rating=FounderAxisRating.UNKNOWN,
                trend=Trend.UNKNOWN,
                confidence=KnowledgeValue[float].unknown("Founder identity is unresolved"),
                coverage=coverage,
                open_questions=("Who are the individual founders?",),
            ),
            market=MarketAxisAssessment(
                assessment_id="axis-market-001",
                assessment_version_id="axis-market-001.v1",
                rubric_version="axis-rubric.v0",
                rating=MarketAxisRating.UNKNOWN,
                trend=Trend.UNKNOWN,
                confidence=KnowledgeValue[float].unknown(
                    "The fixed fixture does not establish market direction"
                ),
                coverage=coverage,
                open_questions=("What evidence establishes current market direction?",),
            ),
            idea_vs_market=IdeaVsMarketAxisAssessment(
                assessment_id="axis-idea-market-001",
                assessment_version_id="axis-idea-market-001.v1",
                rubric_version="axis-rubric.v0",
                rating=IdeaVsMarketAxisRating.UNKNOWN,
                trend=Trend.UNKNOWN,
                confidence=KnowledgeValue[float].unknown(
                    "The fixed fixture has insufficient market evidence"
                ),
                coverage=coverage,
                open_questions=("Which buyer validates the proposed problem?",),
            ),
        ),
        claim_ids=(),
        evidence_ids=(),
        run_id="run-intelligence-001",
        created_at=FIXED_TIME,
    )


def _request(request_id: str = "intelligence-request-001") -> IntelligenceRequest:
    return IntelligenceRequest(
        request_id=request_id,
        input_snapshot_id="snapshot-001",
        subject=SubjectRef(
            kind=EntityKind.OUTBOUND_CANDIDATE,
            subject_id="candidate-001",
        ),
        mode=AssessmentMode.PRELIMINARY,
    )


def test_fake_intelligence_replays_one_schema_valid_assessment() -> None:
    request = _request()
    expected = _assessment()
    adapter = FakeIntelligenceAdapter({request.request_id: expected})

    assert isinstance(adapter, IntelligencePort)
    first = asyncio.run(adapter.assess(request))
    second = asyncio.run(adapter.assess(request))

    assert first == expected
    assert second == expected
    assert adapter.requests == (request, request)
    assert AssessmentEnvelope.model_validate(first.model_dump(mode="python")) == first
    assert first.identity.mode == "preliminary"
    assert first.founder_score.state is KnowledgeState.UNKNOWN
    assert first.decision_readiness is None
    assert first.memo is None


def test_fake_intelligence_fails_explicitly_for_an_unseeded_request() -> None:
    adapter = FakeIntelligenceAdapter({})

    with pytest.raises(MissingFakeAssessmentError, match="missing-intelligence"):
        asyncio.run(adapter.assess(_request("missing-intelligence")))
