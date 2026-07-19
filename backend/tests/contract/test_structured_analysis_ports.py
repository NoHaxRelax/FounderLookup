"""Contract tests for the framework-neutral structured analysis seams."""

import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

import founderlookup.screening as screening
from founderlookup.domain.assessment import (
    REQUIRED_MEMO_SECTIONS,
    Contradiction,
    ContradictionStatus,
    InvestmentMemo,
    MemoSection,
    MemoSectionKind,
)
from founderlookup.domain.common import EntityKind, KnowledgeState, KnowledgeValue, SubjectRef
from founderlookup.domain.evidence import (
    Claim,
    ClaimOrigin,
    ClaimStatus,
    Evidence,
    EvidenceStance,
    SourceLocator,
    SourceLocatorKind,
)
from founderlookup.domain.scoring import ClaimTrustScore, TrustScoreState
from founderlookup.screening.analysis import (
    AdversarialFinding,
    AdversarialFindingKind,
    AdversarialValidation,
    AnalysisGap,
    FounderDossierAnalysis,
    FounderFinding,
    FounderFindingKind,
    FounderPresentationDimension,
    FounderPresentationFinding,
    IdeaFinding,
    IdeaFindingKind,
    IdeaNoveltyQualityAnalysis,
    MarketAnalysis,
    MarketFinding,
    MarketFindingKind,
    MemoSectionCitation,
    MemoSynthesis,
)
from founderlookup.screening.fakes import (
    FakeAdversarialValidationAdapter,
    FakeFounderDossierAdapter,
    FakeIdeaNoveltyQualityAdapter,
    FakeMarketAnalysisAdapter,
    FakeMemoSynthesisAdapter,
    InvalidFakeAnalysisError,
)
from founderlookup.screening.ports import (
    AdversarialValidationPort,
    AnalysisRequest,
    FounderDossierPort,
    IdeaNoveltyQualityPort,
    MarketAnalysisPort,
    MemoSynthesisPort,
)

NOW = datetime(2026, 7, 19, 11, tzinfo=UTC)
SUBJECT = SubjectRef(kind=EntityKind.OPPORTUNITY, subject_id="opportunity:1")


def _supported_claim() -> Claim:
    return Claim(
        claim_id="claim:market-demand",
        claim_version_id="claim-version:market-demand:1",
        subject=SUBJECT,
        predicate="market_demand",
        statement="Regulated operators are actively buying audit automation.",
        status=ClaimStatus.SUPPORTED,
        origin=ClaimOrigin.MANUAL_ANALYSIS,
        as_of=NOW,
        created_at=NOW,
        supporting_evidence_ids=("evidence:market-demand",),
        trust=ClaimTrustScore(
            state=TrustScoreState.UNSCORED,
            trust_policy_version="trust-policy.v0",
            reason="Calibration is pending.",
        ),
    )


def _supporting_evidence() -> Evidence:
    return Evidence(
        evidence_id="evidence:market-demand",
        claim_id="claim:market-demand",
        source_artifact_id="artifact:market-report",
        stance=EvidenceStance.SUPPORTS,
        locator=SourceLocator(
            kind=SourceLocatorKind.URL_EXCERPT,
            locator="https://example.test/market#demand",
            excerpt="Three regulated operators reported active procurement.",
        ),
        collected_at=NOW,
        source_event_time=KnowledgeValue[datetime].known(NOW),
    )


def _other_supported_claim() -> Claim:
    return Claim(
        claim_id="claim:other",
        claim_version_id="claim-version:other:1",
        subject=SUBJECT,
        predicate="other_fact",
        statement="A separate source-backed fact is present.",
        status=ClaimStatus.SUPPORTED,
        origin=ClaimOrigin.MANUAL_ANALYSIS,
        as_of=NOW,
        created_at=NOW,
        supporting_evidence_ids=("evidence:other",),
        trust=ClaimTrustScore(
            state=TrustScoreState.UNSCORED,
            trust_policy_version="trust-policy.v0",
            reason="Calibration is pending.",
        ),
    )


def _other_supporting_evidence() -> Evidence:
    return _supporting_evidence().model_copy(
        update={
            "evidence_id": "evidence:other",
            "claim_id": "claim:other",
        }
    )


def _unsupported_claim() -> Claim:
    return Claim(
        claim_id="claim:unsupported-tam",
        claim_version_id="claim-version:unsupported-tam:1",
        subject=SUBJECT,
        predicate="total_addressable_market",
        statement="The total addressable market exceeds ten billion dollars.",
        status=ClaimStatus.UNSUPPORTED,
        origin=ClaimOrigin.MODEL_ASSISTED,
        as_of=NOW,
        created_at=NOW,
        trust=ClaimTrustScore(
            state=TrustScoreState.UNSUPPORTED,
            trust_policy_version="trust-policy.v0",
            reason="No valid source locator supports this estimate.",
        ),
    )


def _required_memo() -> InvestmentMemo:
    ordered_sections = tuple(sorted(REQUIRED_MEMO_SECTIONS, key=lambda item: item.value))
    return InvestmentMemo(
        memo_id="memo:1",
        memo_version_id="memo-version:1",
        opportunity_id=SUBJECT.subject_id,
        screening_case_id="screening-case:1",
        assessment_id="assessment:1",
        run_id="run:1",
        thesis_version="thesis.v1",
        evidence_as_of=NOW,
        generated_at=NOW,
        sections=tuple(
            MemoSection(
                kind=kind,
                content=KnowledgeValue[str].known(
                    f"Cited {kind.value.replace('_', ' ')} analysis."
                ),
                material_claim_ids=("claim:market-demand",),
            )
            for kind in ordered_sections
        ),
    )


def _complete_memo_citations() -> tuple[MemoSectionCitation, ...]:
    return tuple(
        MemoSectionCitation(
            section=kind,
            claim_ids=("claim:market-demand",),
            evidence_ids=("evidence:market-demand",),
        )
        for kind in sorted(REQUIRED_MEMO_SECTIONS, key=lambda item: item.value)
    )


def test_market_analysis_replays_a_cited_structured_result() -> None:
    request = AnalysisRequest(
        request_id="request:market:1",
        input_snapshot_id="snapshot:1",
        subject=SUBJECT,
        claims=(_supported_claim(),),
        evidence=(_supporting_evidence(),),
    )
    expected = MarketAnalysis(
        request_id=request.request_id,
        input_snapshot_id=request.input_snapshot_id,
        subject=request.subject,
        findings=(
            MarketFinding(
                finding_id="finding:market-direction",
                kind=MarketFindingKind.DIRECTION,
                conclusion=KnowledgeValue[str].known("Demand direction is positive."),
                confidence=KnowledgeValue[float].known(0.72),
                supporting_claim_ids=("claim:market-demand",),
                supporting_evidence_ids=("evidence:market-demand",),
                counter_claim_ids=(),
                counter_evidence_ids=(),
                gap_ids=(),
            ),
        ),
        gaps=(),
        generated_at=NOW,
    )
    adapter = FakeMarketAnalysisAdapter({request.request_id: expected})

    assert isinstance(adapter, MarketAnalysisPort)
    assert asyncio.run(adapter.analyze(request)) == expected
    assert adapter.requests == (request,)


def test_analysis_request_rejects_duplicate_claim_or_evidence_ids() -> None:
    with pytest.raises(ValidationError, match="Claim and Evidence identifiers must be unique"):
        AnalysisRequest(
            request_id="request:duplicate-input",
            input_snapshot_id="snapshot:1",
            subject=SUBJECT,
            claims=(_supported_claim(), _supported_claim()),
            evidence=(_supporting_evidence(),),
        )


def test_analysis_request_rejects_evidence_without_its_claim() -> None:
    with pytest.raises(ValidationError, match="Evidence must reference an input Claim"):
        AnalysisRequest(
            request_id="request:orphan-evidence",
            input_snapshot_id="snapshot:1",
            subject=SUBJECT,
            claims=(_supported_claim(),),
            evidence=(_other_supporting_evidence(),),
        )


def test_known_finding_requires_supporting_claim_and_evidence_citations() -> None:
    with pytest.raises(ValidationError, match="supporting Claim and Evidence citations"):
        MarketFinding(
            finding_id="finding:uncited",
            kind=MarketFindingKind.DIRECTION,
            conclusion=KnowledgeValue[str].known("Demand direction is positive."),
            confidence=KnowledgeValue[float].known(0.72),
            supporting_claim_ids=(),
            supporting_evidence_ids=(),
            counter_claim_ids=(),
            counter_evidence_ids=(),
            gap_ids=(),
        )


def test_unknown_finding_requires_an_explicit_gap() -> None:
    with pytest.raises(ValidationError, match="unknown finding requires an explicit gap"):
        MarketFinding(
            finding_id="finding:unknown-market-size",
            kind=MarketFindingKind.SIZING_ASSUMPTIONS,
            conclusion=KnowledgeValue[str].unknown(
                "No reliable bottom-up sizing evidence is present."
            ),
            confidence=KnowledgeValue[float].unknown(
                "Confidence is unavailable without a conclusion."
            ),
            supporting_claim_ids=(),
            supporting_evidence_ids=(),
            counter_claim_ids=(),
            counter_evidence_ids=(),
            gap_ids=(),
        )


def test_counter_claim_requires_counter_evidence_citation() -> None:
    with pytest.raises(ValidationError, match="counter Claim and Evidence citations"):
        MarketFinding(
            finding_id="finding:incomplete-counter-case",
            kind=MarketFindingKind.COMPETITORS,
            conclusion=KnowledgeValue[str].known("Competition is fragmented."),
            confidence=KnowledgeValue[float].known(0.61),
            supporting_claim_ids=("claim:market-demand",),
            supporting_evidence_ids=("evidence:market-demand",),
            counter_claim_ids=("claim:incumbent-concentration",),
            counter_evidence_ids=(),
            gap_ids=(),
        )


def test_finding_cannot_use_the_same_citation_on_both_sides() -> None:
    with pytest.raises(ValidationError, match="both support and counter"):
        MarketFinding(
            finding_id="finding:overlapping-citations",
            kind=MarketFindingKind.COMPETITORS,
            conclusion=KnowledgeValue[str].known("Competition is fragmented."),
            confidence=KnowledgeValue[float].known(0.61),
            supporting_claim_ids=("claim:market-demand",),
            supporting_evidence_ids=("evidence:market-demand",),
            counter_claim_ids=("claim:market-demand",),
            counter_evidence_ids=("evidence:market-demand",),
            gap_ids=(),
        )


def test_fake_market_analysis_rejects_an_unsupported_claim_as_fact() -> None:
    request = AnalysisRequest(
        request_id="request:market:unsupported",
        input_snapshot_id="snapshot:1",
        subject=SUBJECT,
        claims=(_supported_claim(), _unsupported_claim()),
        evidence=(_supporting_evidence(),),
    )
    response = MarketAnalysis(
        request_id=request.request_id,
        input_snapshot_id=request.input_snapshot_id,
        subject=request.subject,
        findings=(
            MarketFinding(
                finding_id="finding:unsupported-tam",
                kind=MarketFindingKind.SIZING_ASSUMPTIONS,
                conclusion=KnowledgeValue[str].known("The TAM exceeds ten billion dollars."),
                confidence=KnowledgeValue[float].known(0.8),
                supporting_claim_ids=("claim:unsupported-tam",),
                supporting_evidence_ids=("evidence:market-demand",),
                counter_claim_ids=(),
                counter_evidence_ids=(),
                gap_ids=(),
            ),
        ),
        gaps=(),
        generated_at=NOW,
    )

    with pytest.raises(InvalidFakeAnalysisError, match="unsupported Claim"):
        asyncio.run(FakeMarketAnalysisAdapter({request.request_id: response}).analyze(request))


def test_fake_market_analysis_fails_closed_on_request_mismatch() -> None:
    request = AnalysisRequest(
        request_id="request:market:mismatch",
        input_snapshot_id="snapshot:1",
        subject=SUBJECT,
        claims=(_supported_claim(),),
        evidence=(_supporting_evidence(),),
    )
    response = MarketAnalysis(
        request_id=request.request_id,
        input_snapshot_id="snapshot:other",
        subject=request.subject,
        findings=(
            MarketFinding(
                finding_id="finding:market-mismatch",
                kind=MarketFindingKind.DIRECTION,
                conclusion=KnowledgeValue[str].known("Demand direction is positive."),
                confidence=KnowledgeValue[float].known(0.72),
                supporting_claim_ids=("claim:market-demand",),
                supporting_evidence_ids=("evidence:market-demand",),
                counter_claim_ids=(),
                counter_evidence_ids=(),
                gap_ids=(),
            ),
        ),
        gaps=(),
        generated_at=NOW,
    )

    with pytest.raises(InvalidFakeAnalysisError, match="does not match request"):
        asyncio.run(FakeMarketAnalysisAdapter({request.request_id: response}).analyze(request))


def test_fake_market_analysis_rejects_citation_outside_the_snapshot() -> None:
    request = AnalysisRequest(
        request_id="request:market:invented-citation",
        input_snapshot_id="snapshot:1",
        subject=SUBJECT,
        claims=(_supported_claim(),),
        evidence=(_supporting_evidence(),),
    )
    response = MarketAnalysis(
        request_id=request.request_id,
        input_snapshot_id=request.input_snapshot_id,
        subject=request.subject,
        findings=(
            MarketFinding(
                finding_id="finding:invented-citation",
                kind=MarketFindingKind.DIRECTION,
                conclusion=KnowledgeValue[str].known("Demand direction is positive."),
                confidence=KnowledgeValue[float].known(0.72),
                supporting_claim_ids=("claim:market-demand",),
                supporting_evidence_ids=("evidence:not-in-snapshot",),
                counter_claim_ids=(),
                counter_evidence_ids=(),
                gap_ids=(),
            ),
        ),
        gaps=(),
        generated_at=NOW,
    )

    with pytest.raises(InvalidFakeAnalysisError, match="not present in the request"):
        asyncio.run(FakeMarketAnalysisAdapter({request.request_id: response}).analyze(request))


def test_fake_market_analysis_rejects_claim_outside_the_snapshot() -> None:
    request = AnalysisRequest(
        request_id="request:market:invented-claim",
        input_snapshot_id="snapshot:1",
        subject=SUBJECT,
        claims=(_supported_claim(),),
        evidence=(_supporting_evidence(),),
    )
    response = MarketAnalysis(
        request_id=request.request_id,
        input_snapshot_id=request.input_snapshot_id,
        subject=request.subject,
        findings=(
            MarketFinding(
                finding_id="finding:invented-claim",
                kind=MarketFindingKind.DIRECTION,
                conclusion=KnowledgeValue[str].known("Demand direction is positive."),
                confidence=KnowledgeValue[float].known(0.72),
                supporting_claim_ids=("claim:not-in-snapshot",),
                supporting_evidence_ids=("evidence:market-demand",),
                counter_claim_ids=(),
                counter_evidence_ids=(),
                gap_ids=(),
            ),
        ),
        gaps=(),
        generated_at=NOW,
    )

    with pytest.raises(InvalidFakeAnalysisError, match=r"Claim identifier.*not present"):
        asyncio.run(FakeMarketAnalysisAdapter({request.request_id: response}).analyze(request))


def test_fake_market_analysis_rejects_evidence_linked_to_another_claim() -> None:
    request = AnalysisRequest(
        request_id="request:market:mislinked-evidence",
        input_snapshot_id="snapshot:1",
        subject=SUBJECT,
        claims=(_supported_claim(), _other_supported_claim()),
        evidence=(_supporting_evidence(), _other_supporting_evidence()),
    )
    response = MarketAnalysis(
        request_id=request.request_id,
        input_snapshot_id=request.input_snapshot_id,
        subject=request.subject,
        findings=(
            MarketFinding(
                finding_id="finding:mislinked-evidence",
                kind=MarketFindingKind.DIRECTION,
                conclusion=KnowledgeValue[str].known("Demand direction is positive."),
                confidence=KnowledgeValue[float].known(0.72),
                supporting_claim_ids=("claim:market-demand",),
                supporting_evidence_ids=("evidence:other",),
                counter_claim_ids=(),
                counter_evidence_ids=(),
                gap_ids=(),
            ),
        ),
        gaps=(),
        generated_at=NOW,
    )

    with pytest.raises(InvalidFakeAnalysisError, match=r"Evidence.*does not belong"):
        asyncio.run(FakeMarketAnalysisAdapter({request.request_id: response}).analyze(request))


def test_supporting_citation_cannot_use_refuting_evidence() -> None:
    contradicted_claim = Claim(
        claim_id="claim:market-demand",
        claim_version_id="claim-version:market-demand:2",
        subject=SUBJECT,
        predicate="market_demand",
        statement="Regulated operators are actively buying audit automation.",
        status=ClaimStatus.CONTRADICTED,
        origin=ClaimOrigin.MANUAL_ANALYSIS,
        as_of=NOW,
        created_at=NOW,
        supporting_evidence_ids=("evidence:market-demand",),
        counter_evidence_ids=("evidence:market-refute",),
        trust=ClaimTrustScore(
            state=TrustScoreState.UNSCORED,
            trust_policy_version="trust-policy.v0",
            reason="Contradictory sources require review.",
        ),
    )
    refuting_evidence = _supporting_evidence().model_copy(
        update={
            "evidence_id": "evidence:market-refute",
            "stance": EvidenceStance.REFUTES,
        }
    )
    request = AnalysisRequest(
        request_id="request:market:wrong-stance",
        input_snapshot_id="snapshot:1",
        subject=SUBJECT,
        claims=(contradicted_claim,),
        evidence=(_supporting_evidence(), refuting_evidence),
    )
    response = MarketAnalysis(
        request_id=request.request_id,
        input_snapshot_id=request.input_snapshot_id,
        subject=request.subject,
        findings=(
            MarketFinding(
                finding_id="finding:wrong-evidence-stance",
                kind=MarketFindingKind.DIRECTION,
                conclusion=KnowledgeValue[str].known("Demand direction is positive."),
                confidence=KnowledgeValue[float].known(0.72),
                supporting_claim_ids=("claim:market-demand",),
                supporting_evidence_ids=("evidence:market-refute",),
                counter_claim_ids=(),
                counter_evidence_ids=(),
                gap_ids=(),
            ),
        ),
        gaps=(),
        generated_at=NOW,
    )

    with pytest.raises(InvalidFakeAnalysisError, match="supporting Evidence stance"):
        asyncio.run(FakeMarketAnalysisAdapter({request.request_id: response}).analyze(request))


def test_analysis_requires_every_unknown_gap_reference_to_be_declared() -> None:
    with pytest.raises(ValidationError, match="undeclared gap"):
        MarketAnalysis(
            request_id="request:market:gap",
            input_snapshot_id="snapshot:1",
            subject=SUBJECT,
            findings=(
                MarketFinding(
                    finding_id="finding:unknown-sizing",
                    kind=MarketFindingKind.SIZING_ASSUMPTIONS,
                    conclusion=KnowledgeValue[str].unknown("Bottom-up sizing is not established."),
                    confidence=KnowledgeValue[float].unknown(
                        "Confidence is unavailable without a conclusion."
                    ),
                    supporting_claim_ids=(),
                    supporting_evidence_ids=(),
                    counter_claim_ids=(),
                    counter_evidence_ids=(),
                    gap_ids=("gap:market-sizing",),
                ),
            ),
            gaps=(
                AnalysisGap(
                    gap_id="gap:different",
                    topic="market sizing",
                    state=KnowledgeState.UNKNOWN,
                    reason="A bottom-up sizing source is absent.",
                    requested_evidence="Customer count and contract value assumptions.",
                ),
            ),
            generated_at=NOW,
        )


def test_idea_novelty_and_quality_replay_as_separate_cited_findings() -> None:
    request = AnalysisRequest(
        request_id="request:idea:1",
        input_snapshot_id="snapshot:1",
        subject=SUBJECT,
        claims=(_supported_claim(),),
        evidence=(_supporting_evidence(),),
    )
    expected = IdeaNoveltyQualityAnalysis(
        request_id=request.request_id,
        input_snapshot_id=request.input_snapshot_id,
        subject=request.subject,
        findings=(
            IdeaFinding(
                finding_id="finding:idea-novelty",
                kind=IdeaFindingKind.NOVELTY,
                conclusion=KnowledgeValue[str].known(
                    "The workflow applies a differentiated evidence-first approach."
                ),
                confidence=KnowledgeValue[float].known(0.64),
                supporting_claim_ids=("claim:market-demand",),
                supporting_evidence_ids=("evidence:market-demand",),
                counter_claim_ids=(),
                counter_evidence_ids=(),
                gap_ids=(),
            ),
            IdeaFinding(
                finding_id="finding:idea-quality",
                kind=IdeaFindingKind.QUALITY,
                conclusion=KnowledgeValue[str].known(
                    "The workflow applies a differentiated evidence-first approach."
                ),
                confidence=KnowledgeValue[float].known(0.64),
                supporting_claim_ids=("claim:market-demand",),
                supporting_evidence_ids=("evidence:market-demand",),
                counter_claim_ids=(),
                counter_evidence_ids=(),
                gap_ids=(),
            ),
        ),
        gaps=(),
        generated_at=NOW,
    )
    adapter = FakeIdeaNoveltyQualityAdapter({request.request_id: expected})

    assert isinstance(adapter, IdeaNoveltyQualityPort)
    assert asyncio.run(adapter.analyze(request)) == expected


def test_idea_analysis_requires_both_novelty_and_quality() -> None:
    with pytest.raises(ValidationError, match="novelty and quality"):
        IdeaNoveltyQualityAnalysis(
            request_id="request:idea:incomplete",
            input_snapshot_id="snapshot:1",
            subject=SUBJECT,
            findings=(
                IdeaFinding(
                    finding_id="finding:novelty-only",
                    kind=IdeaFindingKind.NOVELTY,
                    conclusion=KnowledgeValue[str].known("The approach is differentiated."),
                    confidence=KnowledgeValue[float].known(0.64),
                    supporting_claim_ids=("claim:market-demand",),
                    supporting_evidence_ids=("evidence:market-demand",),
                    counter_claim_ids=(),
                    counter_evidence_ids=(),
                    gap_ids=(),
                ),
            ),
            gaps=(),
            generated_at=NOW,
        )


def test_founder_dossier_keeps_builder_and_presentation_evidence_structured() -> None:
    request = AnalysisRequest(
        request_id="request:founder:1",
        input_snapshot_id="snapshot:1",
        subject=SUBJECT,
        claims=(_supported_claim(),),
        evidence=(_supporting_evidence(),),
    )
    expected = FounderDossierAnalysis(
        request_id=request.request_id,
        input_snapshot_id=request.input_snapshot_id,
        subject=request.subject,
        findings=(
            FounderFinding(
                finding_id="finding:founder-skill",
                kind=FounderFindingKind.SKILLS,
                conclusion=KnowledgeValue[str].known(
                    "The source demonstrates a clear evidence-backed explanation."
                ),
                confidence=KnowledgeValue[float].known(0.66),
                supporting_claim_ids=("claim:market-demand",),
                supporting_evidence_ids=("evidence:market-demand",),
                counter_claim_ids=(),
                counter_evidence_ids=(),
                gap_ids=(),
            ),
            FounderPresentationFinding(
                finding_id="finding:claim-clarity",
                dimension=FounderPresentationDimension.CLAIM_CLARITY,
                conclusion=KnowledgeValue[str].known(
                    "The source demonstrates a clear evidence-backed explanation."
                ),
                confidence=KnowledgeValue[float].known(0.66),
                supporting_claim_ids=("claim:market-demand",),
                supporting_evidence_ids=("evidence:market-demand",),
                counter_claim_ids=(),
                counter_evidence_ids=(),
                gap_ids=(),
            ),
        ),
        gaps=(),
        generated_at=NOW,
    )
    adapter = FakeFounderDossierAdapter({request.request_id: expected})

    assert isinstance(adapter, FounderDossierPort)
    assert asyncio.run(adapter.analyze(request)) == expected


def test_founder_presentation_rejects_charisma_and_other_proxies() -> None:
    with pytest.raises(ValidationError, match="prohibited presentation proxy"):
        FounderPresentationFinding(
            finding_id="finding:charisma",
            dimension="charisma",  # type: ignore[arg-type]
            conclusion=KnowledgeValue[str].known("The founder is charismatic."),
            confidence=KnowledgeValue[float].known(0.9),
            supporting_claim_ids=("claim:market-demand",),
            supporting_evidence_ids=("evidence:market-demand",),
            counter_claim_ids=(),
            counter_evidence_ids=(),
            gap_ids=(),
        )


def test_presentation_finding_cannot_replace_founder_builder_evidence() -> None:
    with pytest.raises(ValidationError, match="founder dossier requires builder evidence"):
        FounderDossierAnalysis(
            request_id="request:founder:presentation-only",
            input_snapshot_id="snapshot:1",
            subject=SUBJECT,
            findings=(
                FounderPresentationFinding(
                    finding_id="finding:clarity-only",
                    dimension=FounderPresentationDimension.CLAIM_CLARITY,
                    conclusion=KnowledgeValue[str].known("Claims are clearly stated."),
                    confidence=KnowledgeValue[float].known(0.66),
                    supporting_claim_ids=("claim:market-demand",),
                    supporting_evidence_ids=("evidence:market-demand",),
                    counter_claim_ids=(),
                    counter_evidence_ids=(),
                    gap_ids=(),
                ),
            ),
            gaps=(),
            generated_at=NOW,
        )


def test_founder_presentation_cannot_smuggle_appearance_into_conclusion() -> None:
    with pytest.raises(ValidationError, match="prohibited presentation proxy"):
        FounderPresentationFinding(
            finding_id="finding:appearance",
            dimension=FounderPresentationDimension.CLAIM_CLARITY,
            conclusion=KnowledgeValue[str].known(
                "The founder's appearance increases investor confidence."
            ),
            confidence=KnowledgeValue[float].known(0.9),
            supporting_claim_ids=("claim:market-demand",),
            supporting_evidence_ids=("evidence:market-demand",),
            counter_claim_ids=(),
            counter_evidence_ids=(),
            gap_ids=(),
        )


def test_adversarial_validation_replays_unsupported_claims_explicitly() -> None:
    request = AnalysisRequest(
        request_id="request:adversarial:1",
        input_snapshot_id="snapshot:1",
        subject=SUBJECT,
        claims=(_supported_claim(), _unsupported_claim()),
        evidence=(_supporting_evidence(),),
    )
    expected = AdversarialValidation(
        request_id=request.request_id,
        input_snapshot_id=request.input_snapshot_id,
        subject=request.subject,
        findings=(
            AdversarialFinding(
                finding_id="finding:adversarial-corroboration",
                kind=AdversarialFindingKind.CORROBORATION,
                conclusion=KnowledgeValue[str].known(
                    "The market-demand Claim has a precise source locator."
                ),
                confidence=KnowledgeValue[float].known(0.7),
                supporting_claim_ids=("claim:market-demand",),
                supporting_evidence_ids=("evidence:market-demand",),
                counter_claim_ids=(),
                counter_evidence_ids=(),
                gap_ids=(),
            ),
        ),
        gaps=(),
        contradictions=(),
        unsupported_claim_ids=("claim:unsupported-tam",),
        generated_at=NOW,
    )
    adapter = FakeAdversarialValidationAdapter({request.request_id: expected})

    assert isinstance(adapter, AdversarialValidationPort)
    assert asyncio.run(adapter.validate(request)) == expected


def test_adversarial_validation_must_enumerate_every_unsupported_claim() -> None:
    request = AnalysisRequest(
        request_id="request:adversarial:omission",
        input_snapshot_id="snapshot:1",
        subject=SUBJECT,
        claims=(_supported_claim(), _unsupported_claim()),
        evidence=(_supporting_evidence(),),
    )
    response = AdversarialValidation(
        request_id=request.request_id,
        input_snapshot_id=request.input_snapshot_id,
        subject=request.subject,
        findings=(
            AdversarialFinding(
                finding_id="finding:adversarial-omission",
                kind=AdversarialFindingKind.CORROBORATION,
                conclusion=KnowledgeValue[str].known("One Claim is source-backed."),
                confidence=KnowledgeValue[float].known(0.7),
                supporting_claim_ids=("claim:market-demand",),
                supporting_evidence_ids=("evidence:market-demand",),
                counter_claim_ids=(),
                counter_evidence_ids=(),
                gap_ids=(),
            ),
        ),
        gaps=(),
        contradictions=(),
        unsupported_claim_ids=(),
        generated_at=NOW,
    )

    with pytest.raises(InvalidFakeAnalysisError, match="unsupported Claim inventory"):
        asyncio.run(
            FakeAdversarialValidationAdapter({request.request_id: response}).validate(request)
        )


def test_adversarial_validation_rejects_invented_contradiction_citations() -> None:
    request = AnalysisRequest(
        request_id="request:adversarial:invented-contradiction",
        input_snapshot_id="snapshot:1",
        subject=SUBJECT,
        claims=(_supported_claim(),),
        evidence=(_supporting_evidence(),),
    )
    response = AdversarialValidation(
        request_id=request.request_id,
        input_snapshot_id=request.input_snapshot_id,
        subject=request.subject,
        findings=(
            AdversarialFinding(
                finding_id="finding:adversarial-contradiction",
                kind=AdversarialFindingKind.WEAKNESS,
                conclusion=KnowledgeValue[str].known("A contradiction needs review."),
                confidence=KnowledgeValue[float].known(0.7),
                supporting_claim_ids=("claim:market-demand",),
                supporting_evidence_ids=("evidence:market-demand",),
                counter_claim_ids=(),
                counter_evidence_ids=(),
                gap_ids=(),
            ),
        ),
        gaps=(),
        contradictions=(
            Contradiction(
                contradiction_id="contradiction:invented",
                contradiction_version_id="contradiction-version:invented:1",
                claim_ids=("claim:not-in-snapshot:1", "claim:not-in-snapshot:2"),
                evidence_ids=(
                    "evidence:not-in-snapshot:1",
                    "evidence:not-in-snapshot:2",
                ),
                status=ContradictionStatus.UNRESOLVED,
                blocking=True,
                summary="Two invented values disagree.",
                detected_at=NOW,
            ),
        ),
        unsupported_claim_ids=(),
        generated_at=NOW,
    )

    with pytest.raises(InvalidFakeAnalysisError, match="Contradiction citations"):
        asyncio.run(
            FakeAdversarialValidationAdapter({request.request_id: response}).validate(request)
        )


def test_memo_synthesis_replays_all_five_sections_with_complete_citations() -> None:
    request = AnalysisRequest(
        request_id="request:memo:1",
        input_snapshot_id="snapshot:1",
        subject=SUBJECT,
        claims=(_supported_claim(),),
        evidence=(_supporting_evidence(),),
    )
    expected = MemoSynthesis(
        request_id=request.request_id,
        input_snapshot_id=request.input_snapshot_id,
        subject=request.subject,
        memo=_required_memo(),
        section_citations=_complete_memo_citations(),
        gaps=(),
        contradiction_ids=(),
    )
    adapter = FakeMemoSynthesisAdapter({request.request_id: expected})

    assert isinstance(adapter, MemoSynthesisPort)
    assert asyncio.run(adapter.synthesize(request)) == expected
    assert len(expected.memo.sections) == 5


def test_memo_citations_must_cover_each_section_exactly_once() -> None:
    citations = list(_complete_memo_citations())
    citations[-1] = citations[0]

    with pytest.raises(ValidationError, match="exactly mirror memo sections"):
        MemoSynthesis(
            request_id="request:memo:incomplete-citations",
            input_snapshot_id="snapshot:1",
            subject=SUBJECT,
            memo=_required_memo(),
            section_citations=tuple(citations),
            gaps=(),
            contradiction_ids=(),
        )


def test_memo_opportunity_must_match_the_requested_subject() -> None:
    with pytest.raises(ValidationError, match="memo Opportunity must match"):
        MemoSynthesis(
            request_id="request:memo:wrong-opportunity",
            input_snapshot_id="snapshot:1",
            subject=SUBJECT,
            memo=_required_memo().model_copy(update={"opportunity_id": "opportunity:different"}),
            section_citations=_complete_memo_citations(),
            gaps=(),
            contradiction_ids=(),
        )


def test_memo_material_claims_require_evidence_citations() -> None:
    citations = list(_complete_memo_citations())
    omitted = citations[0]
    citations[0] = MemoSectionCitation(
        section=omitted.section,
        claim_ids=(),
        evidence_ids=(),
    )

    with pytest.raises(ValidationError, match="material Claim citations"):
        MemoSynthesis(
            request_id="request:memo:uncited-material-claim",
            input_snapshot_id="snapshot:1",
            subject=SUBJECT,
            memo=_required_memo(),
            section_citations=tuple(citations),
            gaps=(),
            contradiction_ids=(),
        )


def test_known_memo_section_cannot_bypass_claim_citations() -> None:
    memo = _required_memo()
    uncited_kind = MemoSectionKind.COMPANY_SNAPSHOT
    memo = memo.model_copy(
        update={
            "sections": tuple(
                section.model_copy(update={"material_claim_ids": ()})
                if section.kind is uncited_kind
                else section
                for section in memo.sections
            )
        }
    )
    citations = tuple(
        MemoSectionCitation(
            section=citation.section,
            claim_ids=() if citation.section is uncited_kind else citation.claim_ids,
            evidence_ids=() if citation.section is uncited_kind else citation.evidence_ids,
        )
        for citation in _complete_memo_citations()
    )

    with pytest.raises(ValidationError, match=r"known memo section requires.*citations"):
        MemoSynthesis(
            request_id="request:memo:known-uncited",
            input_snapshot_id="snapshot:1",
            subject=SUBJECT,
            memo=memo,
            section_citations=citations,
            gaps=(),
            contradiction_ids=(),
        )


def test_fake_memo_synthesis_rejects_unsupported_factual_claims() -> None:
    request = AnalysisRequest(
        request_id="request:memo:unsupported",
        input_snapshot_id="snapshot:1",
        subject=SUBJECT,
        claims=(_supported_claim(), _unsupported_claim()),
        evidence=(_supporting_evidence(),),
    )
    memo = _required_memo().model_copy(
        update={
            "sections": tuple(
                section.model_copy(update={"material_claim_ids": ("claim:unsupported-tam",)})
                for section in _required_memo().sections
            )
        }
    )
    response = MemoSynthesis(
        request_id=request.request_id,
        input_snapshot_id=request.input_snapshot_id,
        subject=request.subject,
        memo=memo,
        section_citations=tuple(
            MemoSectionCitation(
                section=section.kind,
                claim_ids=("claim:unsupported-tam",),
                evidence_ids=("evidence:market-demand",),
            )
            for section in memo.sections
        ),
        gaps=(),
        contradiction_ids=(),
    )

    with pytest.raises(InvalidFakeAnalysisError, match="unsupported Claim"):
        asyncio.run(FakeMemoSynthesisAdapter({request.request_id: response}).synthesize(request))


def test_fake_memo_synthesis_rejects_mislinked_evidence() -> None:
    request = AnalysisRequest(
        request_id="request:memo:mislinked-evidence",
        input_snapshot_id="snapshot:1",
        subject=SUBJECT,
        claims=(_supported_claim(), _other_supported_claim()),
        evidence=(_supporting_evidence(), _other_supporting_evidence()),
    )
    response = MemoSynthesis(
        request_id=request.request_id,
        input_snapshot_id=request.input_snapshot_id,
        subject=request.subject,
        memo=_required_memo(),
        section_citations=tuple(
            citation.model_copy(update={"evidence_ids": ("evidence:other",)})
            for citation in _complete_memo_citations()
        ),
        gaps=(),
        contradiction_ids=(),
    )

    with pytest.raises(InvalidFakeAnalysisError, match=r"Evidence.*does not belong"):
        asyncio.run(FakeMemoSynthesisAdapter({request.request_id: response}).synthesize(request))


def test_non_known_memo_section_requires_an_explicit_section_gap() -> None:
    memo = _required_memo()
    missing_kind = MemoSectionKind.TRACTION_AND_KPIS
    memo = memo.model_copy(
        update={
            "sections": tuple(
                section.model_copy(
                    update={
                        "content": KnowledgeValue[str].unknown(
                            "Reliable traction data is not present."
                        ),
                        "material_claim_ids": (),
                    }
                )
                if section.kind is missing_kind
                else section
                for section in memo.sections
            )
        }
    )
    citations = tuple(
        MemoSectionCitation(
            section=citation.section,
            claim_ids=() if citation.section is missing_kind else citation.claim_ids,
            evidence_ids=() if citation.section is missing_kind else citation.evidence_ids,
        )
        for citation in _complete_memo_citations()
    )

    with pytest.raises(ValidationError, match=r"non-known memo section requires.*gap"):
        MemoSynthesis(
            request_id="request:memo:unknown-without-gap",
            input_snapshot_id="snapshot:1",
            subject=SUBJECT,
            memo=memo,
            section_citations=citations,
            gaps=(),
            contradiction_ids=(),
        )


def test_structured_analysis_seams_are_public_package_contracts() -> None:
    expected_names = {
        "AdversarialValidationPort",
        "AnalysisRequest",
        "FakeAdversarialValidationAdapter",
        "FakeFounderDossierAdapter",
        "FakeIdeaNoveltyQualityAdapter",
        "FakeMarketAnalysisAdapter",
        "FakeMemoSynthesisAdapter",
        "FounderDossierPort",
        "IdeaNoveltyQualityPort",
        "MarketAnalysisPort",
        "MemoSynthesisPort",
    }

    assert expected_names.issubset(screening.__all__)
    assert all(hasattr(screening, name) for name in expected_names)
