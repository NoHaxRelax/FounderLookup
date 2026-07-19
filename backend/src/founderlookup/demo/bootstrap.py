"""Deterministic, network-free bootstrap data for local UX evaluation.

This module deliberately uses only public methods on the fake-backed application service,
public domain/projector contracts, and the deterministic bridge's signal-registration hook.
It never writes private artifact bytes, activates a candidate, sends outreach, records a
Decision, or invokes an OCR, source, network, or model adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Final
from weakref import WeakKeyDictionary

from founderlookup.application.deck_evidence import (
    DeckEvidenceProjection,
    SupportedMemoSectionInput,
    project_deck_evidence,
)
from founderlookup.application.models import (
    InvestmentThesisRevision,
    OpportunityDetail,
    OutboundCandidateView,
    ThesisCriterion,
    ThesisCriterionMode,
    ThesisDraft,
)
from founderlookup.application.ports import AcceptedApplication
from founderlookup.application.screening_bridge import (
    ConfidenceInputs,
    DeterministicScreeningBridge,
    FounderSignalObservation,
    ScreeningSignalBundle,
)
from founderlookup.application.service import (
    ApplicationExtractionOutcome,
    FakeVCBrainService,
)
from founderlookup.domain.assessment import MemoSectionKind
from founderlookup.domain.common import KnowledgeValue, ScalarValue
from founderlookup.domain.evidence import (
    ArtifactAvailability,
    Claim,
    DataClassification,
    SourceArtifact,
    SourceArtifactKind,
    SourceCategory,
)
from founderlookup.domain.query import QueryOperator, UnknownValuePolicy
from founderlookup.domain.scoring import CoverageLevel, CoverageSummary
from founderlookup.ingestion.extraction import (
    ExtractedPdfPage,
    PdfExtractionResult,
    PdfExtractionUsage,
    PdfPageConfidence,
)
from founderlookup.screening.axes import AxisSignal, SignalReading
from founderlookup.screening.founder_reads import EvidenceGrade
from founderlookup.screening.rubrics import ContributionTier

_DEMO_ACTOR_ID: Final = "system:fictional-demo-bootstrap"
_DEMO_COMPANY_NAME: Final = "Jade Meridian Systems — Fictional Demo"
_DEMO_FOUNDER_ID: Final = "demo:founder:fictional-001"
_DEMO_SOURCE_ARTIFACT_IDS: Final = (
    "demo:source-artifact:fictional-hackathon-001",
    "demo:source-artifact:fictional-research-001",
)
_DEMO_PROTOTYPE_EVIDENCE_ID: Final = "demo:evidence:fictional-prototype-001"
_DEMO_RESEARCH_EVIDENCE_ID: Final = "demo:evidence:fictional-research-001"
_DEMO_ADOPTION_EVIDENCE_ID: Final = "demo:evidence:fictional-adoption-001"
_DEMO_OUTBOUND_SOURCE_KEY: Final = "demo:outbound:fictional-jade-meridian-v1"

_DEMO_INBOUND_APPLICATION_ID: Final = "demo:application:fictional-inbound-001"
_DEMO_INBOUND_COMPANY_ID: Final = "demo:company:fictional-inbound-001"
_DEMO_INBOUND_RUN_ID: Final = "demo:run:fictional-inbound-ingestion-001"
_DEMO_INBOUND_SOURCE_ARTIFACT_ID: Final = "demo:source-artifact:fictional-inbound-deck-001"
_DEMO_INBOUND_ARTIFACT_SERIES_ID: Final = "demo:artifact-series:fictional-inbound-deck-001"
_DEMO_INBOUND_ARTIFACT_VERSION_ID: Final = "demo:artifact-version:fictional-inbound-deck-001"
_DEMO_INBOUND_EXTRACTION_ID: Final = "demo:extraction:fictional-inbound-deck-001"
_DEMO_INBOUND_COMPANY_NAME: Final = "Verdant Relay Labs — Fictional Inbound Demo"
_DEMO_INBOUND_CONTENT_SHA256: Final = "d" * 64
_DEMO_INBOUND_DISPLAY_NAME: Final = "fictional-verdant-relay-deck.pdf"


@dataclass(frozen=True, slots=True)
class DemoBootstrapResult:
    """Stable handles returned after one service instance has been bootstrapped."""

    thesis_version_id: str
    outbound_candidate_id: str
    preliminary_run_id: str
    inbound_application_id: str
    inbound_opportunity_id: str
    inbound_ingestion_run_id: str
    inbound_screening_run_id: str
    inbound_source_artifact_id: str


_bootstrap_lock = RLock()
_bootstrap_results: WeakKeyDictionary[FakeVCBrainService, DemoBootstrapResult] = WeakKeyDictionary()


def _criterion(
    mode: ThesisCriterionMode,
    *,
    operator: QueryOperator | None = None,
    values: tuple[str | int | float | bool, ...] = (),
    unknown_policy: UnknownValuePolicy = UnknownValuePolicy.MANUAL_REVIEW,
) -> ThesisCriterion:
    return ThesisCriterion(
        mode=mode,
        operator=operator,
        values=values,
        unknown_policy=unknown_policy,
    )


def _fictional_thesis() -> ThesisDraft:
    return ThesisDraft(
        sector=_criterion(
            ThesisCriterionMode.SCORED_PREFERENCE,
            operator=QueryOperator.CONTAINS,
            values=("AI infrastructure",),
        ),
        stage=_criterion(
            ThesisCriterionMode.HARD_CONSTRAINT,
            operator=QueryOperator.ANY_OF,
            values=("pre_seed", "seed"),
            unknown_policy=UnknownValuePolicy.NEEDS_INFORMATION,
        ),
        geography=_criterion(
            ThesisCriterionMode.NO_PREFERENCE,
            unknown_policy=UnknownValuePolicy.PRESERVE_AS_UNKNOWN,
        ),
        check_size=_criterion(
            ThesisCriterionMode.HARD_CONSTRAINT,
            operator=QueryOperator.BETWEEN,
            values=(50_000, 250_000),
        ),
        ownership_target=_criterion(
            ThesisCriterionMode.NO_PREFERENCE,
            unknown_policy=UnknownValuePolicy.PRESERVE_AS_UNKNOWN,
        ),
        risk_appetite=_criterion(
            ThesisCriterionMode.SCORED_PREFERENCE,
            operator=QueryOperator.EQUALS,
            values=("high",),
        ),
    )


def _axis_signal(
    key: str,
    reading: SignalReading,
    *,
    rationale: str,
    claim_id: str,
) -> AxisSignal:
    return AxisSignal(
        key=key,
        reading=KnowledgeValue[SignalReading].known(reading),
        rationale=rationale,
        claim_ids=(claim_id,),
    )


def _fictional_signal_bundle(observed_at: datetime) -> ScreeningSignalBundle:
    """Return explicit fake signals; identifiers never resolve to private bytes."""

    coverage = CoverageSummary(
        level=CoverageLevel.MEDIUM,
        source_count=2,
        artifact_count=2,
        evidence_count=3,
        source_categories=("fictional_hackathon", "fictional_research"),
        missing_fields=("corroborated_enterprise_traction",),
        freshest_evidence_at=KnowledgeValue[datetime].known(observed_at),
    )
    return ScreeningSignalBundle(
        coverage=coverage,
        founder_signals=(
            FounderSignalObservation(
                factor_key="shipped_adopted_work",
                tier=ContributionTier.FULL,
                grade=EvidenceGrade.OUTCOME_BACKED,
                observed_value=KnowledgeValue[ScalarValue].known(
                    "fictional prototype adopted by two simulated teams"
                ),
                rationale=(
                    "Fictional demo signal from a simulated public hackathon outcome; "
                    "it is not a claim about a real person or company."
                ),
                evidence_ids=(_DEMO_ADOPTION_EVIDENCE_ID,),
            ),
            FounderSignalObservation(
                factor_key="work_product_quality",
                tier=ContributionTier.PARTIAL,
                grade=EvidenceGrade.CORROBORATED,
                observed_value=KnowledgeValue[ScalarValue].known(
                    "fictional source-reviewed infrastructure prototype"
                ),
                rationale=(
                    "Fictional demo signal corroborated only inside the deterministic fixture."
                ),
                evidence_ids=(_DEMO_PROTOTYPE_EVIDENCE_ID,),
            ),
            FounderSignalObservation(
                factor_key="public_writing_depth",
                tier=ContributionTier.PARTIAL,
                grade=EvidenceGrade.SELF_ASSERTED,
                observed_value=KnowledgeValue[ScalarValue].known(
                    "fictional technical note on reliable agent infrastructure"
                ),
                rationale="Fictional self-asserted writing signal retained at the weaker grade.",
                evidence_ids=(_DEMO_RESEARCH_EVIDENCE_ID,),
            ),
        ),
        founder_axis_signals=(
            _axis_signal(
                "fictional_shipped_work",
                SignalReading.MODERATE_POSITIVE,
                rationale="The fictional fixture includes source-backed shipped work.",
                claim_id="demo:claim:fictional-shipped-work-001",
            ),
            _axis_signal(
                "fictional_work_quality",
                SignalReading.SLIGHT_POSITIVE,
                rationale="The fictional fixture includes a reviewed prototype signal.",
                claim_id="demo:claim:fictional-work-quality-001",
            ),
        ),
        market_axis_signals=(
            _axis_signal(
                "fictional_market_need",
                SignalReading.SLIGHT_POSITIVE,
                rationale="A simulated source suggests demand; live validation remains missing.",
                claim_id="demo:claim:fictional-market-need-001",
            ),
            _axis_signal(
                "fictional_market_validation",
                SignalReading.NEUTRAL,
                rationale="The fixture deliberately withholds a directional market conclusion.",
                claim_id="demo:claim:fictional-market-validation-001",
            ),
        ),
        idea_vs_market_axis_signals=(
            _axis_signal(
                "fictional_problem_fit",
                SignalReading.MODERATE_POSITIVE,
                rationale="The simulated prototype addresses the fixture's stated problem.",
                claim_id="demo:claim:fictional-problem-fit-001",
            ),
            _axis_signal(
                "fictional_adoption_fit",
                SignalReading.SLIGHT_POSITIVE,
                rationale="Simulated adoption supports only a preliminary positive read.",
                claim_id="demo:claim:fictional-adoption-fit-001",
            ),
        ),
        confidence_inputs=ConfidenceInputs(
            reasoned_samples=(68.0, 70.0, 69.0),
            coverage_level=0.65,
            snap_score=66.0,
        ),
    )


def _fictional_inbound_source_artifact(received_at: datetime) -> SourceArtifact:
    """Return metadata for a fictional deck whose private bytes intentionally do not exist."""

    return SourceArtifact(
        source_artifact_id=_DEMO_INBOUND_SOURCE_ARTIFACT_ID,
        artifact_series_id=_DEMO_INBOUND_ARTIFACT_SERIES_ID,
        artifact_version_id=_DEMO_INBOUND_ARTIFACT_VERSION_ID,
        version_number=1,
        kind=SourceArtifactKind.DOCUMENT,
        source_category=SourceCategory.APPLICATION_DECK,
        classification=DataClassification.FOUNDER_PRIVATE,
        origin_locator=f"private-artifact:{_DEMO_INBOUND_SOURCE_ARTIFACT_ID}",
        display_name=_DEMO_INBOUND_DISPLAY_NAME,
        media_type="application/pdf",
        content_sha256=_DEMO_INBOUND_CONTENT_SHA256,
        retrieved_at=received_at,
        source_event_time=KnowledgeValue[datetime].unknown(
            "The fictional deck does not disclose one effective date."
        ),
        availability=ArtifactAvailability.ACCESS_RESTRICTED,
    )


def _unknown_demo_page_confidence() -> PdfPageConfidence:
    reason = "The deterministic page fixture does not report OCR confidence."
    return PdfPageConfidence(
        average=KnowledgeValue[float].unknown(reason),
        minimum=KnowledgeValue[float].unknown(reason),
    )


def _fictional_inbound_extraction(received_at: datetime) -> PdfExtractionResult:
    """Return deterministic page Markdown without reading or synthesizing PDF bytes."""

    page_markdown = (
        (
            f"Company: {_DEMO_INBOUND_COMPANY_NAME}\n"
            "Problem: Regulated teams cannot inspect autonomous workflow changes quickly.\n"
            "Product: A fictional policy simulator for reviewable automation changes."
        ),
        (
            "Market: Crowded automation market with uncertain enterprise budget ownership.\n"
            "Traction/KPIs: Three fictional design partners; paid conversion is not established."
        ),
        "Funding: CHF 150,000 founder-funded runway.",
        "Funding: CHF 450,000 external seed financing.",
    )
    return PdfExtractionResult(
        extraction_id=_DEMO_INBOUND_EXTRACTION_ID,
        source_artifact_id=_DEMO_INBOUND_SOURCE_ARTIFACT_ID,
        input_sha256=_DEMO_INBOUND_CONTENT_SHA256,
        extractor_version="deterministic-demo-page-fixture.v1",
        model_version=KnowledgeValue[str].unknown(
            "No OCR or model is invoked for fictional demo seeding."
        ),
        extracted_at=received_at,
        pages=tuple(
            ExtractedPdfPage(
                page_index=index,
                locator=f"page:{index}",
                markdown=markdown,
                confidence=_unknown_demo_page_confidence(),
            )
            for index, markdown in enumerate(page_markdown)
        ),
        usage=PdfExtractionUsage(
            pages_processed=KnowledgeValue[int].known(len(page_markdown)),
            document_size_bytes=KnowledgeValue[int].unknown(
                "The metadata-only demo seed stores no private artifact bytes."
            ),
        ),
    )


def _claim_for_predicate(projection: DeckEvidenceProjection, predicate: str) -> Claim:
    try:
        return next(claim for claim in projection.claims if claim.predicate == predicate)
    except StopIteration as error:  # pragma: no cover - fixed fixture invariant
        raise RuntimeError(f"fictional demo projection is missing {predicate}") from error


def _claims_for_predicate(
    projection: DeckEvidenceProjection,
    predicate: str,
) -> tuple[Claim, ...]:
    claims = tuple(claim for claim in projection.claims if claim.predicate == predicate)
    if not claims:  # pragma: no cover - fixed fixture invariant
        raise RuntimeError(f"fictional demo projection is missing {predicate}")
    return claims


def _claim_evidence_ids(claims: tuple[Claim, ...]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            evidence_id for claim in claims for evidence_id in claim.supporting_evidence_ids
        )
    )


def _supported_demo_memo_sections(
    preview: DeckEvidenceProjection,
) -> tuple[SupportedMemoSectionInput, ...]:
    product = _claim_for_predicate(preview, "opportunity.product_assertion")
    market = _claim_for_predicate(preview, "opportunity.market_assertion")
    traction = _claim_for_predicate(preview, "opportunity.traction_kpis_assertion")
    funding = _claims_for_predicate(preview, "company.funding_assertion")
    hypothesis_claims = (product, market, traction)
    swot_claims = (product, market, *funding)
    return (
        SupportedMemoSectionInput(
            kind=MemoSectionKind.INVESTMENT_HYPOTHESES,
            content=(
                "Fixture hypothesis: reviewable automation changes may shorten regulated "
                "deployment cycles, but paid conversion and enterprise budget ownership "
                "still require validation."
            ),
            material_claim_ids=tuple(claim.claim_id for claim in hypothesis_claims),
            evidence_ids=_claim_evidence_ids(hypothesis_claims),
        ),
        SupportedMemoSectionInput(
            kind=MemoSectionKind.SWOT,
            content=(
                "Fixture SWOT: the concrete policy-simulation product is a strength; the "
                "crowded market and competing financing assertions are material weaknesses "
                "that require human diligence."
            ),
            material_claim_ids=tuple(claim.claim_id for claim in swot_claims),
            evidence_ids=_claim_evidence_ids(swot_claims),
        ),
    )


def _fictional_inbound_signal_bundle(
    projection: DeckEvidenceProjection,
) -> ScreeningSignalBundle:
    market = _claim_for_predicate(projection, "opportunity.market_assertion")
    traction = _claim_for_predicate(projection, "opportunity.traction_kpis_assertion")
    product = _claim_for_predicate(projection, "opportunity.product_assertion")
    coverage = CoverageSummary(
        level=CoverageLevel.MEDIUM,
        source_count=1,
        artifact_count=1,
        evidence_count=len(projection.evidence),
        source_categories=(SourceCategory.APPLICATION_DECK.value,),
        missing_fields=(
            "canonical_founder_identity",
            "externally_corroborated_paid_conversion",
        ),
        conflicted_fields=("company.funding_assertion",),
        freshest_evidence_at=KnowledgeValue[datetime].known(projection.projected_at),
    )
    return ScreeningSignalBundle(
        coverage=coverage,
        market_axis_signals=(
            _axis_signal(
                "fictional_crowded_market",
                SignalReading.MODERATE_NEGATIVE,
                rationale="The fictional deck describes a crowded market and unclear buyer.",
                claim_id=market.claim_id,
            ),
            _axis_signal(
                "fictional_unproven_conversion",
                SignalReading.SLIGHT_NEGATIVE,
                rationale="The fictional deck does not establish paid conversion.",
                claim_id=traction.claim_id,
            ),
        ),
        idea_vs_market_axis_signals=(
            _axis_signal(
                "fictional_specific_product",
                SignalReading.MODERATE_POSITIVE,
                rationale="The fictional product assertion addresses a specific workflow.",
                claim_id=product.claim_id,
            ),
            _axis_signal(
                "fictional_buyer_validation_gap",
                SignalReading.MODERATE_NEGATIVE,
                rationale="The fictional traction assertion does not validate a paying buyer.",
                claim_id=traction.claim_id,
            ),
        ),
        confidence_inputs=ConfidenceInputs(
            reasoned_samples=(51.0, 57.0, 54.0),
            coverage_level=0.55,
            snap_score=62.0,
        ),
    )


def _ensure_demo_thesis(service: FakeVCBrainService) -> InvestmentThesisRevision:
    draft = _fictional_thesis()
    for revision in service.thesis_history():
        if revision.created_by != _DEMO_ACTOR_ID:
            continue
        if all(
            getattr(revision, field_name) == getattr(draft, field_name)
            for field_name in (
                "sector",
                "stage",
                "geography",
                "check_size",
                "ownership_target",
                "risk_appetite",
            )
        ):
            return revision
    return service.create_thesis(draft, actor_id=_DEMO_ACTOR_ID)


def _candidate_by_id(
    service: FakeVCBrainService,
    candidate_id: str,
) -> OutboundCandidateView:
    try:
        return next(
            candidate
            for candidate in service.list_candidates(limit=100).items
            if candidate.outbound_candidate_id == candidate_id
        )
    except StopIteration as error:  # pragma: no cover - service registration invariant
        raise RuntimeError("fictional demo candidate was not registered") from error


def _opportunity_for_application(
    service: FakeVCBrainService,
    application_id: str,
) -> OpportunityDetail:
    matches = tuple(
        detail
        for summary in service.list_opportunities(limit=100).items
        if (detail := service.get_opportunity(summary.opportunity_id)).application_id
        == application_id
    )
    if len(matches) != 1:  # pragma: no cover - service registration invariant
        raise RuntimeError("fictional demo Application must resolve to exactly one Opportunity")
    return matches[0]


def seed_local_demo(
    service: FakeVCBrainService,
    *,
    screening_bridge: DeterministicScreeningBridge | None = None,
) -> DemoBootstrapResult:
    """Seed one assessed outbound candidate and one screened inbound Opportunity.

    The module-level weak registry makes repeated or concurrent calls idempotent without
    mutating the service with bootstrap-only flags or reaching into its private collections.
    Stable fixture keys and public read models also let a partially completed call resume.
    """

    with _bootstrap_lock:
        existing = _bootstrap_results.get(service)
        if existing is not None:
            return existing

        thesis = _ensure_demo_thesis(service)
        candidate = service.seed_outbound_candidate(
            company_name=_DEMO_COMPANY_NAME,
            founder_id=_DEMO_FOUNDER_ID,
            source_artifact_ids=_DEMO_SOURCE_ARTIFACT_IDS,
            source_identity_key=_DEMO_OUTBOUND_SOURCE_KEY,
        )
        if screening_bridge is not None:
            screening_bridge.register(
                candidate.outbound_candidate_id,
                _fictional_signal_bundle(candidate.discovered_at),
            )
        if candidate.preliminary_assessment is None:
            preliminary_run_id = service.start_preliminary_assessment(
                candidate.outbound_candidate_id
            ).run_id
            candidate = _candidate_by_id(service, candidate.outbound_candidate_id)
        else:
            preliminary_run_id = candidate.preliminary_assessment.run_id

        accepted_application = AcceptedApplication(
            application_id=_DEMO_INBOUND_APPLICATION_ID,
            company_id=_DEMO_INBOUND_COMPANY_ID,
            run_id=_DEMO_INBOUND_RUN_ID,
            source_artifact_id=_DEMO_INBOUND_SOURCE_ARTIFACT_ID,
            source_artifact_sha256=_DEMO_INBOUND_CONTENT_SHA256,
            received_at=candidate.discovered_at,
        )
        service.register_application(
            accepted_application,
            display_name=_DEMO_INBOUND_DISPLAY_NAME,
            media_type="application/pdf",
        )
        opportunity = _opportunity_for_application(service, _DEMO_INBOUND_APPLICATION_ID)
        source_artifact = _fictional_inbound_source_artifact(accepted_application.received_at)
        extraction = _fictional_inbound_extraction(accepted_application.received_at)
        preview = service.project_application_deck(
            _DEMO_INBOUND_APPLICATION_ID,
            extraction=extraction,
            source_artifact=source_artifact,
        )
        projection = project_deck_evidence(
            extraction=extraction,
            source_artifact=source_artifact,
            application_id=_DEMO_INBOUND_APPLICATION_ID,
            company_id=_DEMO_INBOUND_COMPANY_ID,
            opportunity_id=opportunity.opportunity_id,
            supported_memo_sections=_supported_demo_memo_sections(preview),
        )
        service.register_deck_evidence_projection(projection)
        service.record_application_extraction_outcome(
            _DEMO_INBOUND_APPLICATION_ID,
            outcome=ApplicationExtractionOutcome.SUCCEEDED,
            accepted_output_id=projection.projection_id,
            additional_output_ids=tuple(
                dict.fromkeys(
                    (
                        *(claim.claim_id for claim in projection.claims),
                        *(evidence.evidence_id for evidence in projection.evidence),
                    )
                )
            ),
        )

        if screening_bridge is not None:
            screening_bridge.register(
                opportunity.opportunity_id,
                _fictional_inbound_signal_bundle(projection),
            )
        opportunity = service.get_opportunity(
            opportunity.opportunity_id,
            include_claims=True,
            include_evidence=True,
        )
        if opportunity.latest_assessment is None:
            screening_run_id = service.start_screening(opportunity.opportunity_id).run_id
        else:
            screening_run_id = opportunity.latest_assessment.run_id

        result = DemoBootstrapResult(
            thesis_version_id=thesis.thesis_version_id,
            outbound_candidate_id=candidate.outbound_candidate_id,
            preliminary_run_id=preliminary_run_id,
            inbound_application_id=_DEMO_INBOUND_APPLICATION_ID,
            inbound_opportunity_id=opportunity.opportunity_id,
            inbound_ingestion_run_id=_DEMO_INBOUND_RUN_ID,
            inbound_screening_run_id=screening_run_id,
            inbound_source_artifact_id=_DEMO_INBOUND_SOURCE_ARTIFACT_ID,
        )
        _bootstrap_results[service] = result
        return result


__all__ = ["DemoBootstrapResult", "seed_local_demo"]
