"""Deterministic tests for the framework-neutral inbound analysis seam (task 3.7).

The suite pins the honesty guarantees the five analyses must make testable:

- unsupported claims are represented as ``ClaimStatus.UNSUPPORTED`` and surfaced by
  adversarial validation instead of passing as fact;
- conflicting claims produce a :class:`Contradiction` over claims marked
  ``ClaimStatus.CONTRADICTED``;
- the founder dossier read never lets presentation, polish, reach, or pedigree drive
  founder QUALITY (an all-polish, no-substance founder cannot earn a strong founder read);
- every material claim a memo section cites carries supporting evidence, and a memo whose
  material claim lacks evidence is rejected.

Every fixture is built with the real rubric functions (``score_claim_trust``,
``assess_*_axis``, ``builder_signal_read``/``fundability_read``) so the fakes stay honest
and every carried domain object passes its frozen validators. The fakes are pure seeded
replay: identical requests yield identical results with no model, framework, or network.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from founderlookup.domain.assessment import (
    Contradiction,
    ContradictionStatus,
    FounderAxisAssessment,
    FounderAxisRating,
    InvestmentMemo,
    MemoSection,
    MemoSectionKind,
    Recommendation,
    RecommendationAction,
    RecommendationReason,
    Trend,
)
from founderlookup.domain.common import (
    EntityKind,
    KnowledgeValue,
    ScalarValue,
    SubjectRef,
)
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
    TrustFactorKind,
    TrustFactorSignal,
)
from founderlookup.screening.inbound_analysis import (
    AdversarialValidationPort,
    AdversarialValidationResult,
    AnalysisRequest,
    AnalysisResultError,
    AnalysisResultHeader,
    FakeAdversarialValidationAdapter,
    FakeFounderDossierAnalysisAdapter,
    FakeIdeaNoveltyAnalysisAdapter,
    FakeMarketAnalysisAdapter,
    FakeMemoSynthesisAdapter,
    FounderDossierAnalysisPort,
    FounderDossierAnalysisResult,
    IdeaNoveltyAnalysisPort,
    IdeaNoveltyAnalysisResult,
    InvalidFakeAnalysisError,
    MarketAnalysisPort,
    MarketAnalysisResult,
    MemoSynthesisPort,
    MemoSynthesisResult,
    MissingFakeAnalysisError,
)
from founderlookup.screening.axes import (
    AxisSignal,
    SignalReading,
    assess_founder_axis,
    assess_idea_vs_market_axis,
    assess_market_axis,
)
from founderlookup.screening.founder_reads import (
    EvidenceGrade,
    FounderRead,
    GapLabel,
    GradedObservation,
    ReadKind,
    builder_fundability_gap,
    builder_signal_read,
    fundability_read,
)
from founderlookup.screening.rubrics import (
    ContributionTier,
    FounderBand,
    TrustFactorInput,
    score_claim_trust,
)

NOW = datetime(2026, 7, 19, 12, tzinfo=UTC)

COMPANY = SubjectRef(kind=EntityKind.COMPANY, subject_id="company-1")
FOUNDER = SubjectRef(kind=EntityKind.FOUNDER, subject_id="founder-1")
CANDIDATE = SubjectRef(kind=EntityKind.OUTBOUND_CANDIDATE, subject_id="candidate-1")


# --------------------------------------------------------------------------------------
# Request / header / coverage builders
# --------------------------------------------------------------------------------------


def _request(
    request_id: str,
    *,
    snapshot: str = "snapshot-1",
    subject: SubjectRef = CANDIDATE,
    mode: AssessmentMode = AssessmentMode.PRELIMINARY,
) -> AnalysisRequest:
    return AnalysisRequest(
        request_id=request_id,
        input_snapshot_id=snapshot,
        subject=subject,
        mode=mode,
    )


def _header(request: AnalysisRequest) -> AnalysisResultHeader:
    return AnalysisResultHeader(
        request_id=request.request_id,
        input_snapshot_id=request.input_snapshot_id,
        subject=request.subject,
        mode=request.mode,
    )


def _coverage(level: CoverageLevel = CoverageLevel.MEDIUM) -> CoverageSummary:
    return CoverageSummary(
        level=level,
        source_count=3,
        artifact_count=3,
        evidence_count=3,
        freshest_evidence_at=KnowledgeValue[datetime].known(NOW),
    )


# --------------------------------------------------------------------------------------
# Trust / claim / evidence builders (all pass the frozen validators)
# --------------------------------------------------------------------------------------


def _six_unknown_factors() -> list[TrustFactorInput]:
    return [
        TrustFactorInput(kind, KnowledgeValue[TrustFactorSignal].unknown("not assessed"), "n/a")
        for kind in TrustFactorKind
    ]


def _scored_trust(evidence_ids: tuple[str, ...]) -> ClaimTrustScore:
    factors = [
        TrustFactorInput(
            TrustFactorKind.PROVENANCE,
            KnowledgeValue[TrustFactorSignal].known(TrustFactorSignal.STRENGTHENS),
            "First-party source.",
            evidence_ids,
        ),
        TrustFactorInput(
            TrustFactorKind.INDEPENDENCE,
            KnowledgeValue[TrustFactorSignal].known(TrustFactorSignal.NEUTRAL),
            "n/a",
        ),
        TrustFactorInput(
            TrustFactorKind.RECENCY,
            KnowledgeValue[TrustFactorSignal].known(TrustFactorSignal.NEUTRAL),
            "n/a",
        ),
        TrustFactorInput(
            TrustFactorKind.EXTRACTION_CERTAINTY,
            KnowledgeValue[TrustFactorSignal].known(TrustFactorSignal.NEUTRAL),
            "n/a",
        ),
        TrustFactorInput(
            TrustFactorKind.CORROBORATION,
            KnowledgeValue[TrustFactorSignal].known(TrustFactorSignal.STRENGTHENS),
            "Independently corroborated.",
        ),
        TrustFactorInput(
            TrustFactorKind.CONTRADICTION,
            KnowledgeValue[TrustFactorSignal].known(TrustFactorSignal.NEUTRAL),
            "n/a",
        ),
    ]
    return score_claim_trust(factors, has_supporting_evidence=True)


def _unsupported_trust() -> ClaimTrustScore:
    return score_claim_trust(_six_unknown_factors(), has_supporting_evidence=False)


def _evidence(evidence_id: str, claim_id: str, stance: EvidenceStance) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        claim_id=claim_id,
        source_artifact_id="artifact-1",
        stance=stance,
        locator=SourceLocator(kind=SourceLocatorKind.URL_EXCERPT, locator="https://example.test/x"),
        collected_at=NOW,
        source_event_time=KnowledgeValue[datetime].known(NOW),
    )


def _claim(
    claim_id: str,
    status: ClaimStatus,
    *,
    subject: SubjectRef,
    supporting: tuple[str, ...] = (),
    counter: tuple[str, ...] = (),
    trust: ClaimTrustScore | None = None,
) -> Claim:
    if trust is None:
        trust = _unsupported_trust() if status is ClaimStatus.UNSUPPORTED else _scored_trust(
            supporting
        )
    return Claim(
        claim_id=claim_id,
        claim_version_id=f"{claim_id}.v1",
        subject=subject,
        predicate="analysis_finding",
        statement="A structured analytical finding about the subject.",
        status=status,
        origin=ClaimOrigin.MODEL_ASSISTED,
        as_of=NOW,
        created_at=NOW,
        supporting_evidence_ids=supporting,
        counter_evidence_ids=counter,
        trust=trust,
    )


def _supported(claim_id: str, evidence_id: str, subject: SubjectRef) -> tuple[Claim, Evidence]:
    evidence = _evidence(evidence_id, claim_id, EvidenceStance.SUPPORTS)
    claim = _claim(claim_id, ClaimStatus.SUPPORTED, subject=subject, supporting=(evidence_id,))
    return claim, evidence


def _contradicted(
    claim_id: str, sup_id: str, cnt_id: str, subject: SubjectRef
) -> tuple[Claim, Evidence, Evidence]:
    support = _evidence(sup_id, claim_id, EvidenceStance.SUPPORTS)
    counter = _evidence(cnt_id, claim_id, EvidenceStance.REFUTES)
    claim = _claim(
        claim_id,
        ClaimStatus.CONTRADICTED,
        subject=subject,
        supporting=(sup_id,),
        counter=(cnt_id,),
    )
    return claim, support, counter


def _pos_signal(key: str, claim_id: str) -> AxisSignal:
    return AxisSignal(
        key=key,
        reading=KnowledgeValue[SignalReading].known(SignalReading.STRONG_POSITIVE),
        claim_ids=(claim_id,),
    )


# ======================================================================================
# 1. Market analysis
# ======================================================================================


def _market_result(request: AnalysisRequest) -> MarketAnalysisResult:
    c1, e1 = _supported("mkt-claim-1", "mkt-ev-1", COMPANY)
    c2, e2 = _supported("mkt-claim-2", "mkt-ev-2", COMPANY)
    market_read = assess_market_axis(
        [_pos_signal("tam", "mkt-claim-1"), _pos_signal("growth", "mkt-claim-2")],
        coverage=_coverage(),
        assessment_id="axis-market-1",
        assessment_version_id="axis-market-1.v1",
    )
    return MarketAnalysisResult(
        header=_header(request),
        claims=(c1, c2),
        evidence=(e1, e2),
        market_read=market_read,
    )


def test_market_fake_replays_deterministically_and_satisfies_the_port() -> None:
    request = _request("market-1")
    expected = _market_result(request)
    adapter = FakeMarketAnalysisAdapter({request.request_id: expected})

    assert isinstance(adapter, MarketAnalysisPort)
    first = asyncio.run(adapter.analyze_market(request))
    second = asyncio.run(adapter.analyze_market(request))

    assert first is expected
    assert first is second
    assert adapter.requests == (request, request)
    # Headline confidence/open questions read straight off the carried axis (single source).
    assert first.confidence == first.market_read.confidence
    assert first.open_questions == first.market_read.open_questions


def test_market_fake_raises_for_an_unseeded_request() -> None:
    adapter = FakeMarketAnalysisAdapter({})
    with pytest.raises(MissingFakeAnalysisError, match="missing-market"):
        asyncio.run(adapter.analyze_market(_request("missing-market")))


def test_market_fake_rejects_a_seed_that_does_not_describe_the_request() -> None:
    seed_request = _request("market-x", snapshot="snapshot-A")
    other_request = _request("market-x", snapshot="snapshot-B")
    adapter = FakeMarketAnalysisAdapter({seed_request.request_id: _market_result(seed_request)})
    with pytest.raises(InvalidFakeAnalysisError, match="market-x"):
        asyncio.run(adapter.analyze_market(other_request))


def test_market_result_rejects_an_axis_routing_an_uncarried_claim() -> None:
    request = _request("market-bad")
    c1, e1 = _supported("mkt-claim-1", "mkt-ev-1", COMPANY)
    stray_read = assess_market_axis(
        [_pos_signal("tam", "mkt-claim-1"), _pos_signal("growth", "not-carried")],
        coverage=_coverage(),
        assessment_id="axis-market-2",
        assessment_version_id="axis-market-2.v1",
    )
    with pytest.raises(AnalysisResultError, match="not carried"):
        MarketAnalysisResult(
            header=_header(request), claims=(c1,), evidence=(e1,), market_read=stray_read
        )


def test_result_rejects_mis_stanced_citation() -> None:
    # A claim that cites CONTEXT-stance evidence as "supporting" is incoherent and rejected.
    request = _request("market-stance")
    ctx = _evidence("ctx-ev", "mkt-claim-1", EvidenceStance.CONTEXT)
    claim = _claim(
        "mkt-claim-1", ClaimStatus.SUPPORTED, subject=COMPANY, supporting=("ctx-ev",)
    )
    read = assess_market_axis(
        [],
        coverage=_coverage(CoverageLevel.LOW),
        assessment_id="axis-market-3",
        assessment_version_id="axis-market-3.v1",
    )
    with pytest.raises(AnalysisResultError, match="expected supports"):
        MarketAnalysisResult(
            header=_header(request), claims=(claim,), evidence=(ctx,), market_read=read
        )


# ======================================================================================
# 2. Idea novelty / quality
# ======================================================================================


def _idea_result(request: AnalysisRequest) -> IdeaNoveltyAnalysisResult:
    c1, e1 = _supported("idea-claim-1", "idea-ev-1", COMPANY)
    c2, e2 = _supported("idea-claim-2", "idea-ev-2", COMPANY)
    idea_read = assess_idea_vs_market_axis(
        [_pos_signal("fit", "idea-claim-1"), _pos_signal("wedge", "idea-claim-2")],
        coverage=_coverage(),
        assessment_id="axis-idea-1",
        assessment_version_id="axis-idea-1.v1",
    )
    return IdeaNoveltyAnalysisResult(
        header=_header(request),
        claims=(c1, c2),
        evidence=(e1, e2),
        idea_read=idea_read,
    )


def test_idea_novelty_fake_replays_and_satisfies_the_port() -> None:
    request = _request("idea-1")
    expected = _idea_result(request)
    adapter = FakeIdeaNoveltyAnalysisAdapter({request.request_id: expected})

    assert isinstance(adapter, IdeaNoveltyAnalysisPort)
    result = asyncio.run(adapter.analyze_idea_novelty(request))
    assert result is expected
    assert result.confidence == result.idea_read.confidence


# ======================================================================================
# 3. Founder dossier (presentation-proxy prohibition)
# ======================================================================================


def _substance_observations() -> list[GradedObservation]:
    return [
        GradedObservation(
            "shipped_adopted_work",
            ContributionTier.FULL,
            EvidenceGrade.OUTCOME_BACKED,
            KnowledgeValue[ScalarValue].known("Shipped a product adopted by 10k users."),
            "shipped",
            ("f-ev-1",),
        ),
        GradedObservation(
            "corroborated_domain_experience",
            ContributionTier.FULL,
            EvidenceGrade.OUTCOME_BACKED,
            KnowledgeValue[ScalarValue].known("Eight years of registry-verified domain work."),
            "domain",
            ("f-ev-2",),
        ),
        GradedObservation(
            "sustained_follow_through",
            ContributionTier.FULL,
            EvidenceGrade.OUTCOME_BACKED,
            KnowledgeValue[ScalarValue].known("Five years maintaining a live open-source project."),
            "follow-through",
        ),
    ]


def _polish_observations() -> list[GradedObservation]:
    return [
        GradedObservation(
            "institutional_pedigree",
            ContributionTier.FULL,
            EvidenceGrade.OUTCOME_BACKED,
            KnowledgeValue[ScalarValue].known("Ex-BigCo, elite university."),
            "pedigree",
        ),
        GradedObservation(
            "presentation_polish",
            ContributionTier.FULL,
            EvidenceGrade.OUTCOME_BACKED,
            KnowledgeValue[ScalarValue].known("Extremely slick pitch deck."),
            "polish",
        ),
        GradedObservation(
            "follower_reach",
            ContributionTier.FULL,
            EvidenceGrade.OUTCOME_BACKED,
            KnowledgeValue[ScalarValue].known("Two hundred thousand followers."),
            "reach",
        ),
        GradedObservation(
            "team_size",
            ContributionTier.FULL,
            EvidenceGrade.OUTCOME_BACKED,
            KnowledgeValue[ScalarValue].known("A team of twenty."),
            "team",
        ),
    ]


def test_all_polish_no_substance_never_reaches_a_strong_builder_read() -> None:
    # The bias prohibition at the rubric level: vanity signals carry zero builder weight,
    # so an all-polish founder sits at the baseline, never the STRONG band.
    builder = builder_signal_read(_polish_observations())
    fundability = fundability_read(_polish_observations())
    gap = builder_fundability_gap(builder, fundability)

    assert builder.band is not FounderBand.STRONG
    assert builder.band is FounderBand.BASELINE
    assert fundability.band is FounderBand.STRONG
    assert gap.label is GapLabel.SUBSTANCE_LIGHT_BUT_FUNDABLE


def test_founder_dossier_rejects_a_strong_read_without_strong_substance() -> None:
    # Even if a model tried to emit a STRONG founder read backed only by polish, the result
    # validator refuses it: a strong founder read requires strong builder substance.
    request = _request("founder-guard", subject=FOUNDER)
    builder = builder_signal_read(_polish_observations())
    fundability = fundability_read(_polish_observations())
    gap = builder_fundability_gap(builder, fundability)
    strong_read = FounderAxisAssessment(
        assessment_id="axis-f-guard",
        assessment_version_id="axis-f-guard.v1",
        rubric_version="axis-rubric.v0",
        rating=FounderAxisRating.STRONG,
        trend=Trend.UNKNOWN,
        confidence=KnowledgeValue[float].known(0.9),
        coverage=_coverage(),
    )
    with pytest.raises(AnalysisResultError, match="strong builder substance"):
        FounderDossierAnalysisResult(
            header=_header(request),
            claims=(),
            evidence=(),
            founder_read=strong_read,
            builder_read=builder,
            fundability_read=fundability,
            gap=gap,
        )


def _founder_result(request: AnalysisRequest) -> FounderDossierAnalysisResult:
    c1, e1 = _supported("f-claim-1", "f-ev-a", FOUNDER)
    c2, e2 = _supported("f-claim-2", "f-ev-b", FOUNDER)
    founder_read = assess_founder_axis(
        [_pos_signal("shipped", "f-claim-1"), _pos_signal("domain", "f-claim-2")],
        coverage=_coverage(),
        assessment_id="axis-founder-1",
        assessment_version_id="axis-founder-1.v1",
    )
    builder = builder_signal_read(_substance_observations())
    fundability = fundability_read(_substance_observations())
    gap = builder_fundability_gap(builder, fundability)
    return FounderDossierAnalysisResult(
        header=_header(request),
        claims=(c1, c2),
        evidence=(e1, e2),
        founder_read=founder_read,
        builder_read=builder,
        fundability_read=fundability,
        gap=gap,
    )


def test_founder_dossier_accepts_a_strong_read_backed_by_real_substance() -> None:
    request = _request("founder-1", subject=FOUNDER)
    result = _founder_result(request)
    adapter = FakeFounderDossierAnalysisAdapter({request.request_id: result})

    assert isinstance(adapter, FounderDossierAnalysisPort)
    replayed = asyncio.run(adapter.analyze_founder_dossier(request))
    assert replayed is result
    assert result.founder_read.rating is FounderAxisRating.STRONG
    assert result.builder_read.band is FounderBand.STRONG
    # Costly substance outruns conventional fundability: the underrated-builder gap.
    assert result.gap.label is GapLabel.UNDER_NETWORKED_STRONG_BUILDER


def test_founder_dossier_rejects_transposed_reads() -> None:
    request = _request("founder-swap", subject=FOUNDER)
    builder = builder_signal_read(_substance_observations())
    fundability = fundability_read(_substance_observations())
    gap = builder_fundability_gap(builder, fundability)
    weak_read = FounderAxisAssessment(
        assessment_id="axis-f-swap",
        assessment_version_id="axis-f-swap.v1",
        rubric_version="axis-rubric.v0",
        rating=FounderAxisRating.MIXED,
        trend=Trend.UNKNOWN,
        confidence=KnowledgeValue[float].known(0.5),
        coverage=_coverage(),
    )
    with pytest.raises(AnalysisResultError, match="builder-signal read"):
        FounderDossierAnalysisResult(
            header=_header(request),
            claims=(),
            evidence=(),
            founder_read=weak_read,
            builder_read=fundability,  # wrong kind in the builder slot
            fundability_read=builder,
            gap=gap,
        )


def test_a_forged_builder_band_cannot_manufacture_a_strong_founder_read() -> None:
    # FounderRead.band is a plain settable field a live model controls. Forging band=STRONG
    # onto a baseline-substance score must not slip a polish-only founder past the guard: the
    # result derives the band from the score and rejects a read that lies about its own band.
    request = _request("founder-forged", subject=FOUNDER)
    honest_builder = builder_signal_read(_polish_observations())
    assert honest_builder.score == 50.0
    assert honest_builder.band is FounderBand.BASELINE
    forged_builder = FounderRead(
        kind=ReadKind.BUILDER_SIGNAL,
        score=honest_builder.score,  # 50.0, baseline substance
        baseline=honest_builder.baseline,
        band=FounderBand.STRONG,  # the lie: classify_founder_band(50.0) is BASELINE
        factors=honest_builder.factors,
        observed_factor_count=honest_builder.observed_factor_count,
        counted_factor_count=honest_builder.counted_factor_count,
        policy_version=honest_builder.policy_version,
    )
    fundability = fundability_read(_polish_observations())
    gap = builder_fundability_gap(forged_builder, fundability)
    strong_read = FounderAxisAssessment(
        assessment_id="axis-f-forged",
        assessment_version_id="axis-f-forged.v1",
        rubric_version="axis-rubric.v0",
        rating=FounderAxisRating.STRONG,
        trend=Trend.UNKNOWN,
        confidence=KnowledgeValue[float].known(0.9),
        coverage=_coverage(),
    )
    with pytest.raises(AnalysisResultError, match="does not match its score"):
        FounderDossierAnalysisResult(
            header=_header(request),
            claims=(),
            evidence=(),
            founder_read=strong_read,
            builder_read=forged_builder,
            fundability_read=fundability,
            gap=gap,
        )


# ======================================================================================
# 4. Adversarial validation (contradictions + unsupported + corroboration)
# ======================================================================================


def _adversarial_result(request: AnalysisRequest) -> AdversarialValidationResult:
    a, a_sup, a_cnt = _contradicted("adv-claim-a", "adv-ev-a1", "adv-ev-a2", COMPANY)
    b, b_sup, b_cnt = _contradicted("adv-claim-b", "adv-ev-b1", "adv-ev-b2", COMPANY)
    unsupported = _claim("adv-claim-c", ClaimStatus.UNSUPPORTED, subject=COMPANY)
    supported, s_ev = _supported("adv-claim-d", "adv-ev-d", COMPANY)
    contradiction = Contradiction(
        contradiction_id="contra-1",
        contradiction_version_id="contra-1.v1",
        claim_ids=("adv-claim-a", "adv-claim-b"),
        evidence_ids=("adv-ev-a1", "adv-ev-a2", "adv-ev-b1", "adv-ev-b2"),
        status=ContradictionStatus.UNRESOLVED,
        blocking=True,
        summary="Revenue is asserted at two conflicting figures across sources.",
        detected_at=NOW,
    )
    return AdversarialValidationResult(
        header=_header(request),
        claims=(a, b, unsupported, supported),
        evidence=(a_sup, a_cnt, b_sup, b_cnt, s_ev),
        contradictions=(contradiction,),
        confidence=KnowledgeValue[float].known(0.6),
        open_questions=("Which revenue figure is authoritative?",),
    )


def test_adversarial_surfaces_contradictions_unsupported_and_corroboration() -> None:
    request = _request("adv-1")
    result = _adversarial_result(request)
    adapter = FakeAdversarialValidationAdapter({request.request_id: result})

    assert isinstance(adapter, AdversarialValidationPort)
    replayed = asyncio.run(adapter.validate(request))
    assert replayed is result

    # Contradictions are detected and the conflicting claims are marked CONTRADICTED.
    assert len(result.contradictions) == 1
    assert {c.claim_id for c in result.contradicted_claims} == {"adv-claim-a", "adv-claim-b"}
    # Unsupported claims are surfaced rather than passed as fact.
    assert tuple(c.claim_id for c in result.unsupported_claims) == ("adv-claim-c",)
    assert result.unsupported_claims[0].trust.state.value == "unsupported"
    # External corroboration is exposed (the SUPPORTS-stance evidence).
    assert {e.evidence_id for e in result.corroborating_evidence} == {
        "adv-ev-a1",
        "adv-ev-b1",
        "adv-ev-d",
    }
    assert tuple(c.claim_id for c in result.corroborated_claims) == ("adv-claim-d",)


def test_adversarial_surfaces_an_evidence_free_asserted_claim_as_unsupported() -> None:
    # An evidence-free claim must not hide behind a soft status. A claim with no supporting
    # evidence is surfaced by unsupported_claims whether it is marked UNSUPPORTED or merely
    # ASSERTED_UNVERIFIED, so nothing evidence-free can pass as fact by dodging the label.
    request = _request("adv-asserted")
    asserted = _claim("adv-claim-asserted", ClaimStatus.ASSERTED_UNVERIFIED, subject=COMPANY)
    supported, s_ev = _supported("adv-claim-supported", "adv-ev-s", COMPANY)
    result = AdversarialValidationResult(
        header=_header(request),
        claims=(asserted, supported),
        evidence=(s_ev,),
        contradictions=(),
        confidence=KnowledgeValue[float].known(0.5),
    )
    surfaced = {c.claim_id for c in result.unsupported_claims}
    assert "adv-claim-asserted" in surfaced  # evidence-free, must be surfaced
    assert "adv-claim-supported" not in surfaced  # carries evidence, stays off the list
    assert tuple(c.claim_id for c in result.corroborated_claims) == ("adv-claim-supported",)


def test_adversarial_rejects_a_contradiction_over_a_non_contradicted_claim() -> None:
    request = _request("adv-bad")
    a, a_sup, a_cnt = _contradicted("adv-claim-a", "adv-ev-a1", "adv-ev-a2", COMPANY)
    standing, s_ev = _supported("adv-claim-d", "adv-ev-d", COMPANY)
    bad_contradiction = Contradiction(
        contradiction_id="contra-bad",
        contradiction_version_id="contra-bad.v1",
        claim_ids=("adv-claim-a", "adv-claim-d"),  # adv-claim-d is only SUPPORTED
        evidence_ids=("adv-ev-a1", "adv-ev-a2"),
        status=ContradictionStatus.UNRESOLVED,
        blocking=False,
        summary="Improperly claims a standing supported claim is in conflict.",
        detected_at=NOW,
    )
    with pytest.raises(AnalysisResultError, match="must be marked CONTRADICTED"):
        AdversarialValidationResult(
            header=_header(request),
            claims=(a, standing),
            evidence=(a_sup, a_cnt, s_ev),
            contradictions=(bad_contradiction,),
            confidence=KnowledgeValue[float].known(0.5),
        )


# ======================================================================================
# 5. Memo synthesis (citation completeness)
# ======================================================================================


def _memo_sections(
    *, snapshot_claims: tuple[str, ...], traction_claims: tuple[str, ...]
) -> tuple[MemoSection, ...]:
    prose = KnowledgeValue[str].known("Section prose grounded in the cited material claims.")
    return (
        MemoSection(
            kind=MemoSectionKind.COMPANY_SNAPSHOT,
            content=prose,
            material_claim_ids=snapshot_claims,
        ),
        MemoSection(kind=MemoSectionKind.INVESTMENT_HYPOTHESES, content=prose),
        MemoSection(kind=MemoSectionKind.SWOT, content=prose),
        MemoSection(kind=MemoSectionKind.PROBLEM_AND_PRODUCT, content=prose),
        MemoSection(
            kind=MemoSectionKind.TRACTION_AND_KPIS,
            content=prose,
            material_claim_ids=traction_claims,
        ),
    )


def _memo(sections: tuple[MemoSection, ...]) -> InvestmentMemo:
    return InvestmentMemo(
        memo_id="memo-1",
        memo_version_id="memo-1.v1",
        opportunity_id="opportunity-1",
        screening_case_id="case-1",
        assessment_id="assessment-1",
        run_id="run-1",
        thesis_version="thesis.v0",
        evidence_as_of=NOW,
        generated_at=NOW,
        sections=sections,
    )


def _recommendation(claim_ids: tuple[str, ...]) -> Recommendation:
    return Recommendation(
        recommendation_id="rec-1",
        recommendation_version_id="rec-1.v1",
        subject=COMPANY,
        assessment_id="assessment-1",
        policy_version="rec.v0",
        action=RecommendationAction.ADVANCE,
        reasons=(
            RecommendationReason(summary="Cited material supports advancing.", claim_ids=claim_ids),
        ),
        next_actions=("Schedule a partner review.",),
        created_at=NOW,
    )


def _memo_result(request: AnalysisRequest) -> MemoSynthesisResult:
    m1, e1 = _supported("memo-claim-1", "memo-ev-1", COMPANY)
    m2, e2 = _supported("memo-claim-2", "memo-ev-2", COMPANY)
    sections = _memo_sections(
        snapshot_claims=("memo-claim-1",), traction_claims=("memo-claim-2",)
    )
    return MemoSynthesisResult(
        header=_header(request),
        memo=_memo(sections),
        recommendation=_recommendation(("memo-claim-1",)),
        material_claims=(m1, m2),
        evidence=(e1, e2),
        confidence=KnowledgeValue[float].known(0.7),
        open_questions=("Confirm the retention cohort figures.",),
    )


def test_memo_fake_replays_a_fully_cited_memo() -> None:
    request = _request("memo-1", mode=AssessmentMode.FULL)
    result = _memo_result(request)
    adapter = FakeMemoSynthesisAdapter({request.request_id: result})

    assert isinstance(adapter, MemoSynthesisPort)
    replayed = asyncio.run(adapter.synthesize_memo(request))
    assert replayed is result
    assert len(result.memo.sections) >= 5


def test_memo_rejects_a_material_claim_that_lacks_supporting_evidence() -> None:
    request = _request("memo-bad", mode=AssessmentMode.FULL)
    unsupported = _claim("memo-claim-x", ClaimStatus.UNSUPPORTED, subject=COMPANY)
    sections = _memo_sections(snapshot_claims=("memo-claim-x",), traction_claims=())
    with pytest.raises(AnalysisResultError, match="lacks supporting evidence"):
        MemoSynthesisResult(
            header=_header(request),
            memo=_memo(sections),
            recommendation=_recommendation(()),
            material_claims=(unsupported,),
            evidence=(),
            confidence=KnowledgeValue[float].known(0.4),
        )


def test_memo_rejects_a_section_citing_an_uncarried_claim() -> None:
    request = _request("memo-dangling", mode=AssessmentMode.FULL)
    m1, e1 = _supported("memo-claim-1", "memo-ev-1", COMPANY)
    sections = _memo_sections(snapshot_claims=("ghost-claim",), traction_claims=())
    with pytest.raises(AnalysisResultError, match="not carried as a material claim"):
        MemoSynthesisResult(
            header=_header(request),
            memo=_memo(sections),
            recommendation=_recommendation(()),
            material_claims=(m1,),
            evidence=(e1,),
            confidence=KnowledgeValue[float].known(0.4),
        )


def test_memo_rejects_a_recommendation_only_material_claim_without_evidence() -> None:
    # Citation completeness is uniform: a material claim that no section cites but the
    # recommendation leans on must still carry supporting evidence, so an unsupported claim
    # cannot ride into the memo through the recommendation alone.
    request = _request("memo-rec-unsupported", mode=AssessmentMode.FULL)
    unsupported = _claim("memo-claim-x", ClaimStatus.UNSUPPORTED, subject=COMPANY)
    sections = _memo_sections(snapshot_claims=(), traction_claims=())
    with pytest.raises(AnalysisResultError, match="lacks supporting evidence"):
        MemoSynthesisResult(
            header=_header(request),
            memo=_memo(sections),
            recommendation=_recommendation(("memo-claim-x",)),
            material_claims=(unsupported,),
            evidence=(),
            confidence=KnowledgeValue[float].known(0.4),
        )


def test_memo_rejects_a_recommendation_citing_a_non_material_claim() -> None:
    request = _request("memo-rec", mode=AssessmentMode.FULL)
    m1, e1 = _supported("memo-claim-1", "memo-ev-1", COMPANY)
    sections = _memo_sections(snapshot_claims=("memo-claim-1",), traction_claims=())
    with pytest.raises(AnalysisResultError, match="not a material claim"):
        MemoSynthesisResult(
            header=_header(request),
            memo=_memo(sections),
            recommendation=_recommendation(("outside-claim",)),
            material_claims=(m1,),
            evidence=(e1,),
            confidence=KnowledgeValue[float].known(0.4),
        )


def test_memo_rejects_a_carried_material_claim_that_no_section_cites() -> None:
    # Citation completeness runs both ways: a carried material claim that no section or
    # recommendation cites is an orphan and is rejected, so the memo and its material set
    # always describe the same evidence.
    request = _request("memo-orphan", mode=AssessmentMode.FULL)
    m1, e1 = _supported("memo-claim-1", "memo-ev-1", COMPANY)
    m2, e2 = _supported("memo-claim-2", "memo-ev-2", COMPANY)
    sections = _memo_sections(snapshot_claims=("memo-claim-1",), traction_claims=())
    with pytest.raises(AnalysisResultError, match="cited by no section"):
        MemoSynthesisResult(
            header=_header(request),
            memo=_memo(sections),
            recommendation=_recommendation(()),
            material_claims=(m1, m2),  # memo-claim-2 is carried but never cited
            evidence=(e1, e2),
            confidence=KnowledgeValue[float].known(0.4),
        )


# ======================================================================================
# Further citation-integrity and contradiction-integrity guards
# ======================================================================================


def test_result_rejects_evidence_attached_to_an_unknown_claim() -> None:
    # Every carried piece of evidence must attach to a carried claim; a dangling evidence
    # record whose claim is absent is rejected.
    request = _request("market-orphan-ev")
    c1, e1 = _supported("mkt-claim-1", "mkt-ev-1", COMPANY)
    orphan = _evidence("mkt-ev-orphan", "not-a-carried-claim", EvidenceStance.CONTEXT)
    read = assess_market_axis(
        [_pos_signal("tam", "mkt-claim-1")],
        coverage=_coverage(CoverageLevel.LOW),
        assessment_id="axis-market-orphan",
        assessment_version_id="axis-market-orphan.v1",
    )
    with pytest.raises(AnalysisResultError, match="unknown claim"):
        MarketAnalysisResult(
            header=_header(request), claims=(c1,), evidence=(e1, orphan), market_read=read
        )


def test_adversarial_rejects_a_contradiction_citing_uncarried_evidence() -> None:
    # A contradiction must be backed by carried evidence; citing an evidence id the result
    # never carries is rejected, so a contradiction can never rest on phantom evidence.
    request = _request("adv-ev")
    a, a_sup, a_cnt = _contradicted("adv-claim-a", "adv-ev-a1", "adv-ev-a2", COMPANY)
    b, b_sup, b_cnt = _contradicted("adv-claim-b", "adv-ev-b1", "adv-ev-b2", COMPANY)
    contradiction = Contradiction(
        contradiction_id="contra-ev",
        contradiction_version_id="contra-ev.v1",
        claim_ids=("adv-claim-a", "adv-claim-b"),
        evidence_ids=("adv-ev-a1", "adv-ev-ghost"),  # second id is not carried
        status=ContradictionStatus.UNRESOLVED,
        blocking=True,
        summary="Cites evidence the result never carries.",
        detected_at=NOW,
    )
    with pytest.raises(AnalysisResultError, match="does not carry"):
        AdversarialValidationResult(
            header=_header(request),
            claims=(a, b),
            evidence=(a_sup, a_cnt, b_sup, b_cnt),
            contradictions=(contradiction,),
            confidence=KnowledgeValue[float].known(0.5),
        )


def test_adversarial_rejects_a_contradiction_naming_an_unreviewed_claim() -> None:
    # A contradiction may only name claims the result actually reviewed; naming a claim the
    # result never carries is rejected.
    request = _request("adv-ghost")
    a, a_sup, a_cnt = _contradicted("adv-claim-a", "adv-ev-a1", "adv-ev-a2", COMPANY)
    b, b_sup, b_cnt = _contradicted("adv-claim-b", "adv-ev-b1", "adv-ev-b2", COMPANY)
    contradiction = Contradiction(
        contradiction_id="contra-ghost",
        contradiction_version_id="contra-ghost.v1",
        claim_ids=("adv-claim-a", "ghost-claim"),  # ghost-claim was never reviewed
        evidence_ids=("adv-ev-a1", "adv-ev-a2"),
        status=ContradictionStatus.UNRESOLVED,
        blocking=True,
        summary="Names a claim the result never reviewed.",
        detected_at=NOW,
    )
    with pytest.raises(AnalysisResultError, match="unknown"):
        AdversarialValidationResult(
            header=_header(request),
            claims=(a, b),
            evidence=(a_sup, a_cnt, b_sup, b_cnt),
            contradictions=(contradiction,),
            confidence=KnowledgeValue[float].known(0.5),
        )


# ======================================================================================
# End to end: the whole inbound pipeline runs deterministically with no model
# ======================================================================================


def test_inbound_pipeline_runs_end_to_end_without_a_model_and_is_deterministic() -> None:
    market_req = _request("pipe-market")
    idea_req = _request("pipe-idea")
    founder_req = _request("pipe-founder", subject=FOUNDER)
    adversarial_req = _request("pipe-adv")
    memo_req = _request("pipe-memo", mode=AssessmentMode.FULL)

    market = FakeMarketAnalysisAdapter({market_req.request_id: _market_result(market_req)})
    idea = FakeIdeaNoveltyAnalysisAdapter({idea_req.request_id: _idea_result(idea_req)})
    founder = FakeFounderDossierAnalysisAdapter(
        {founder_req.request_id: _founder_result(founder_req)}
    )
    adversarial = FakeAdversarialValidationAdapter(
        {adversarial_req.request_id: _adversarial_result(adversarial_req)}
    )
    memo = FakeMemoSynthesisAdapter({memo_req.request_id: _memo_result(memo_req)})

    async def run_pipeline() -> tuple[object, ...]:
        return (
            await market.analyze_market(market_req),
            await idea.analyze_idea_novelty(idea_req),
            await founder.analyze_founder_dossier(founder_req),
            await adversarial.validate(adversarial_req),
            await memo.synthesize_memo(memo_req),
        )

    # No model, no framework, no network: two runs of the whole pipeline are identical.
    assert asyncio.run(run_pipeline()) == asyncio.run(run_pipeline())
