"""Opt-in GPT-5.6 Luna Structured Outputs acceptance for the screening reasoner.

Run explicitly with ``FOUNDERLOOKUP_RUN_LIVE_TESTS=1 uv run pytest
tests/live/test_live_openai_reasoner.py``. The prompt is synthetic and public; no founder,
investor, customer, or other private material is sent by this test.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict

from founderlookup.api.settings import APISettings
from founderlookup.domain.common import EntityKind, KnowledgeValue, SubjectRef
from founderlookup.domain.evidence import (
    Claim,
    ClaimOrigin,
    ClaimStatus,
    Evidence,
    EvidenceStance,
    SourceLocator,
    SourceLocatorKind,
)
from founderlookup.domain.lifecycles import AssessmentMode
from founderlookup.domain.scoring import (
    ClaimTrustScore,
    CoverageLevel,
    CoverageSummary,
    TrustScoreState,
)
from founderlookup.screening.inbound_analysis import AnalysisRequest
from founderlookup.screening.inbound_graph import InboundGraphLimits
from founderlookup.screening.inbound_runtime import RuntimeInboundIntelligence
from founderlookup.screening.live_analyses import InboundAnalysisSnapshot, MemoIdentity
from founderlookup.screening.openai_client import DEFAULT_MODEL, OpenAIReasoner

pytestmark = pytest.mark.live


class _FictionalPublicSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_name: str
    public_contact: str
    founder_identity_disclosed: bool


def _synthetic_inbound() -> tuple[AnalysisRequest, InboundAnalysisSnapshot]:
    now = datetime(2026, 7, 19, 12, tzinfo=UTC)
    subject = SubjectRef(kind=EntityKind.OPPORTUNITY, subject_id="opportunity:live-synthetic")
    claim = Claim(
        claim_id="claim:live-synthetic",
        claim_version_id="claim-version:live-synthetic:1",
        subject=subject,
        predicate="shipped_product",
        statement="The fictional team shipped an audit automation pilot.",
        status=ClaimStatus.SUPPORTED,
        origin=ClaimOrigin.SOURCE_ASSERTION,
        as_of=now,
        created_at=now,
        supporting_evidence_ids=("evidence:live-synthetic",),
        trust=ClaimTrustScore(
            state=TrustScoreState.UNSCORED,
            trust_policy_version="trust-policy.v0",
            reason="Synthetic live input is not calibrated.",
        ),
    )
    evidence = Evidence(
        evidence_id="evidence:live-synthetic",
        claim_id=claim.claim_id,
        source_artifact_id="artifact:live-synthetic",
        stance=EvidenceStance.SUPPORTS,
        locator=SourceLocator(
            kind=SourceLocatorKind.DOCUMENT_PAGE,
            locator="synthetic.pdf#page=2",
            excerpt="Pilot shipped to one fictional operator.",
        ),
        collected_at=now,
        source_event_time=KnowledgeValue[datetime].known(now),
    )
    snapshot = InboundAnalysisSnapshot(
        input_snapshot_id="snapshot:live-synthetic",
        claims=(claim,),
        evidence=(evidence,),
        coverage=CoverageSummary(
            level=CoverageLevel.LOW,
            source_count=1,
            artifact_count=1,
            evidence_count=1,
            source_categories=("founder_provided_deck",),
            missing_fields=("independent_corroboration",),
            freshest_evidence_at=KnowledgeValue[datetime].known(now),
        ),
        memo_identity=MemoIdentity(
            opportunity_id=subject.subject_id,
            screening_case_id="screening-case:live-synthetic",
            assessment_id="assessment:live-synthetic",
            run_id="run:live-synthetic",
            thesis_version="thesis.v1",
            evidence_as_of=now,
        ),
    )
    return AnalysisRequest(
        request_id="request:live-synthetic",
        input_snapshot_id=snapshot.input_snapshot_id,
        subject=subject,
        mode=AssessmentMode.FULL,
    ), snapshot


@pytest.mark.skipif(
    os.getenv("FOUNDERLOOKUP_RUN_LIVE_TESTS") != "1",
    reason="set FOUNDERLOOKUP_RUN_LIVE_TESTS=1 to call the real OpenAI API",
)
@pytest.mark.anyio
async def test_real_gpt_5_6_luna_parses_fictional_public_signals() -> None:
    configured_key = APISettings().openai_api_key
    if configured_key is None or not configured_key.get_secret_value().strip():
        pytest.skip("OPENAI_API_KEY is not configured")

    async with AsyncOpenAI(api_key=configured_key.get_secret_value()) as provider:
        reasoner = OpenAIReasoner(provider, model=DEFAULT_MODEL, effort="low")
        result = await reasoner.extract(
            schema=_FictionalPublicSignal,
            instructions=(
                "Extract only values explicitly stated in the fictional public source. "
                "Do not infer a founder identity."
            ),
            content=(
                "FICTIONAL PUBLIC SHOWCASE\n"
                "Project: Signal Forge\n"
                "Public contact: hello@signal-forge.example\n"
                "Founder identity: not disclosed\n"
            ),
        )

    assert result.project_name == "Signal Forge"
    assert result.public_contact == "hello@signal-forge.example"
    assert result.founder_identity_disclosed is False
    assert configured_key.get_secret_value() not in repr(result)


@pytest.mark.skipif(
    os.getenv("FOUNDERLOOKUP_RUN_LIVE_TESTS") != "1",
    reason="set FOUNDERLOOKUP_RUN_LIVE_TESTS=1 to call the real OpenAI API",
)
@pytest.mark.anyio
async def test_real_gpt_5_6_luna_runs_the_bounded_five_stage_inbound_graph() -> None:
    configured_key = APISettings().openai_api_key
    if configured_key is None or not configured_key.get_secret_value().strip():
        pytest.skip("OPENAI_API_KEY is not configured")

    async with AsyncOpenAI(api_key=configured_key.get_secret_value()) as provider:
        intelligence = RuntimeInboundIntelligence(
            OpenAIReasoner(provider, model=DEFAULT_MODEL, effort="low"),
            clock=lambda: datetime.now(UTC),
            max_input_bytes=100_000,
            limits=InboundGraphLimits(
                max_model_calls=5,
                stage_timeout_seconds=60,
                total_timeout_seconds=180,
            ),
        )
        request, snapshot = _synthetic_inbound()
        result = await intelligence.analyze(request, snapshot)

    assert [item.stage for item in result.audit] == [
        "market",
        "idea",
        "founder",
        "adversarial",
        "memo",
    ]
    assert len(result.memo.memo.sections) == 5
    assert result.memo.recommendation is not None
    assert configured_key.get_secret_value() not in repr(result)
