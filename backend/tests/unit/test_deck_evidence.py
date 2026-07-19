"""Focused tests for deterministic OCR-page to Evidence/Claim projection."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from founderlookup.application.deck_evidence import (
    AcceptedClaimCorroboration,
    DeckEvidenceLimits,
    DeckEvidenceProjection,
    DeckField,
    SupportedMemoSectionInput,
    project_deck_evidence,
)
from founderlookup.domain.assessment import ContradictionStatus, MemoSection, MemoSectionKind
from founderlookup.domain.common import KnowledgeState, KnowledgeValue, VersionId
from founderlookup.domain.evidence import (
    ClaimOrigin,
    ClaimStatus,
    DataClassification,
    SourceArtifact,
    SourceArtifactKind,
    SourceCategory,
    SourceLocatorKind,
    VerificationState,
)
from founderlookup.domain.scoring import TrustFactorKind, TrustFactorSignal, TrustScoreState
from founderlookup.ingestion.extraction import (
    ExtractedPdfPage,
    PdfExtractionResult,
    PdfExtractionUsage,
    PdfPageConfidence,
)

NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
CONTENT_HASH = "a" * 64


def _confidence(value: float | None = 0.96) -> PdfPageConfidence:
    if value is None:
        return PdfPageConfidence(
            average=KnowledgeValue[float].unknown("provider did not return confidence"),
            minimum=KnowledgeValue[float].unknown("provider did not return confidence"),
        )
    return PdfPageConfidence(
        average=KnowledgeValue[float].known(value),
        minimum=KnowledgeValue[float].known(max(0.0, value - 0.02)),
    )


def _artifact(*, event_time_known: bool = False) -> SourceArtifact:
    event_time = (
        KnowledgeValue[datetime].known(NOW)
        if event_time_known
        else KnowledgeValue[datetime].unknown("deck effective date is not disclosed")
    )
    return SourceArtifact(
        source_artifact_id="source-artifact:deck:1",
        artifact_series_id="artifact-series:deck:1",
        artifact_version_id="artifact-version:deck:1",
        version_number=1,
        kind=SourceArtifactKind.DOCUMENT,
        source_category=SourceCategory.APPLICATION_DECK,
        classification=DataClassification.FOUNDER_PRIVATE,
        origin_locator="private-artifact:deck:1",
        display_name="fictional-deck.pdf",
        media_type="application/pdf",
        content_sha256=CONTENT_HASH,
        retrieved_at=NOW,
        source_event_time=event_time,
    )


def _extraction(
    *pages: str,
    confidences: tuple[float | None, ...] | None = None,
) -> PdfExtractionResult:
    confidence_values = confidences or tuple(0.96 for _ in pages)
    return PdfExtractionResult(
        extraction_id="pdf-extraction:deck:1",
        source_artifact_id="source-artifact:deck:1",
        input_sha256=CONTENT_HASH,
        extractor_version="deterministic-ocr-fixture.v1",
        model_version=KnowledgeValue[VersionId].known("mistral-ocr-4"),
        extracted_at=NOW,
        pages=tuple(
            ExtractedPdfPage(
                page_index=index,
                locator=f"page:{index}",
                markdown=markdown,
                confidence=_confidence(confidence_values[index]),
            )
            for index, markdown in enumerate(pages)
        ),
        usage=PdfExtractionUsage(
            pages_processed=KnowledgeValue[int].known(len(pages)),
            document_size_bytes=KnowledgeValue[int].known(1_024),
        ),
    )


def _project(
    *pages: str,
    confidences: tuple[float | None, ...] | None = None,
    corroboration: tuple[AcceptedClaimCorroboration, ...] = (),
    supported_memo_sections: tuple[SupportedMemoSectionInput, ...] = (),
    limits: DeckEvidenceLimits | None = None,
) -> DeckEvidenceProjection:
    return project_deck_evidence(
        extraction=_extraction(*pages, confidences=confidences),
        source_artifact=_artifact(),
        application_id="application:1",
        company_id="company:1",
        opportunity_id="opportunity:1",
        corroboration=corroboration,
        supported_memo_sections=supported_memo_sections,
        limits=limits,
    )


def _section(projection: DeckEvidenceProjection, kind: MemoSectionKind) -> MemoSection:
    return next(section for section in projection.memo_sections if section.kind is kind)


def test_each_assertion_retains_exact_page_locator_and_excerpt() -> None:
    projection = _project(
        "Company: Fictional Forge\n\nRevenue might be enormous someday.",
        "| Traction/KPIs | 12 paid pilots |",
    )

    assert len(projection.observations) == 2
    assert len(projection.evidence) == 2
    assert tuple(item.locator.locator for item in projection.evidence) == (
        "page:0#line:1",
        "page:1#line:1",
    )
    assert tuple(item.locator.excerpt for item in projection.evidence) == (
        "Company: Fictional Forge",
        "| Traction/KPIs | 12 paid pilots |",
    )
    assert all(item.locator.kind is SourceLocatorKind.DOCUMENT_PAGE for item in projection.evidence)
    assert all(item.source_artifact_id == "source-artifact:deck:1" for item in projection.evidence)
    assert "Revenue might be enormous" not in str(projection.model_dump(mode="json"))


def test_self_authored_deck_claim_is_asserted_unverified_with_all_trust_factors() -> None:
    projection = _project("**Company:** Fictional Forge")
    claim = projection.claims[0]

    assert claim.origin is ClaimOrigin.SOURCE_ASSERTION
    assert claim.status is ClaimStatus.ASSERTED_UNVERIFIED
    assert claim.statement == "Pitch deck asserts Company: Fictional Forge"
    assert claim.trust.state is TrustScoreState.SCORED
    factors = {factor.kind: factor for factor in claim.trust.factors}
    assert set(factors) == set(TrustFactorKind)
    assert factors[TrustFactorKind.INDEPENDENCE].signal.value is TrustFactorSignal.WEAKENS
    assert factors[TrustFactorKind.CORROBORATION].signal.value is TrustFactorSignal.NEUTRAL
    assert projection.observations[0].verification_state is VerificationState.SOURCE_ASSERTED


def test_absent_fields_remain_unknown_and_exactly_five_sections_are_present() -> None:
    projection = _project("Product: A fictional deployment guardrail")
    expected_order = (
        MemoSectionKind.COMPANY_SNAPSHOT,
        MemoSectionKind.INVESTMENT_HYPOTHESES,
        MemoSectionKind.SWOT,
        MemoSectionKind.PROBLEM_AND_PRODUCT,
        MemoSectionKind.TRACTION_AND_KPIS,
    )

    assert tuple(section.kind for section in projection.memo_sections) == expected_order
    assert (
        _section(projection, MemoSectionKind.COMPANY_SNAPSHOT).content.state
        is KnowledgeState.UNKNOWN
    )
    assert (
        _section(projection, MemoSectionKind.INVESTMENT_HYPOTHESES).content.state
        is KnowledgeState.UNKNOWN
    )
    assert _section(projection, MemoSectionKind.SWOT).content.state is KnowledgeState.UNKNOWN
    assert (
        _section(projection, MemoSectionKind.PROBLEM_AND_PRODUCT).content.state
        is KnowledgeState.KNOWN
    )
    assert (
        _section(projection, MemoSectionKind.TRACTION_AND_KPIS).content.state
        is KnowledgeState.UNKNOWN
    )
    assert {claim.predicate for claim in projection.claims} == {"opportunity.product_assertion"}


def test_materially_different_repeated_value_creates_blocking_conflict() -> None:
    projection = _project("Funding: $1M seed", "Funding: $2M seed")

    assert len(projection.claims) == 2
    assert all(claim.status is ClaimStatus.UNRESOLVED for claim in projection.claims)
    assert all(claim.trust.state is TrustScoreState.UNSCORED for claim in projection.claims)
    assert len(projection.contradictions) == 1
    contradiction = projection.contradictions[0]
    assert contradiction.status is ContradictionStatus.UNRESOLVED
    assert contradiction.blocking is True
    assert set(contradiction.claim_ids) == {claim.claim_id for claim in projection.claims}
    assert set(contradiction.evidence_ids) == {item.evidence_id for item in projection.evidence}
    assert (
        _section(projection, MemoSectionKind.COMPANY_SNAPSHOT).content.state
        is KnowledgeState.CONFLICTED
    )


def test_unlabelled_deck_prose_cannot_fabricate_claims_or_memo_content() -> None:
    projection = _project("Fictional Forge has a huge market, brilliant founders, and $9M ARR.")

    assert projection.observations == ()
    assert projection.evidence == ()
    assert projection.claims == ()
    assert projection.contradictions == ()
    assert all(
        section.content.state is KnowledgeState.UNKNOWN for section in projection.memo_sections
    )


def test_projection_ids_order_and_output_are_stable() -> None:
    pages = (
        "Product: Guardrail\nCompany: Fictional Forge",
        "Traction: 12 paid pilots\nCompany: fictional forge.",
    )

    first = _project(*pages)
    second = _project(*pages)

    assert first == second
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert len(first.claims) == 3
    assert len({claim.claim_id for claim in first.claims}) == 3
    company_claim = next(
        claim for claim in first.claims if claim.predicate == "company.name_assertion"
    )
    assert len(company_claim.supporting_evidence_ids) == 2
    assert first.contradictions == ()


def test_unknown_page_confidence_stays_unknown_without_becoming_negative() -> None:
    projection = _project("Company: Fictional Forge", confidences=(None,))
    claim = projection.claims[0]
    factor = next(
        item for item in claim.trust.factors if item.kind is TrustFactorKind.EXTRACTION_CERTAINTY
    )

    assert claim.trust.state is TrustScoreState.SCORED
    assert factor.signal.state is KnowledgeState.UNKNOWN
    assert "not treated as weakness" in factor.rationale


def test_only_explicit_independent_support_can_verify_and_fill_analysis_sections() -> None:
    external_evidence_id = "evidence:external:company"
    projection = _project(
        "Company: Fictional Forge",
        corroboration=(
            AcceptedClaimCorroboration(
                field=DeckField.COMPANY,
                asserted_value="Fictional Forge",
                evidence_ids=(external_evidence_id,),
            ),
        ),
        supported_memo_sections=(
            SupportedMemoSectionInput(
                kind=MemoSectionKind.INVESTMENT_HYPOTHESES,
                content=(
                    "Accepted hypothesis: test enterprise conversion through a paid pilot cohort."
                ),
                material_claim_ids=("claim:analysis:hypothesis:1",),
                evidence_ids=("evidence:analysis:hypothesis:1",),
            ),
        ),
    )
    claim = projection.claims[0]

    assert claim.status is ClaimStatus.SUPPORTED
    assert external_evidence_id in claim.supporting_evidence_ids
    assert projection.observations[0].verification_state is VerificationState.CORROBORATED
    assert (
        _section(projection, MemoSectionKind.INVESTMENT_HYPOTHESES).content.state
        is KnowledgeState.KNOWN
    )
    assert _section(projection, MemoSectionKind.SWOT).content.state is KnowledgeState.UNKNOWN


def test_projection_caps_pages_fields_and_never_emits_omitted_raw_text() -> None:
    projection = _project(
        "Company: Fictional Forge\nProblem: OMITTED_FIELD_SENTINEL",
        "Product: OMITTED_PAGE_SENTINEL",
        limits=DeckEvidenceLimits(max_pages=1, max_fields_per_page=1),
    )
    serialized = str(projection.model_dump(mode="json"))

    assert projection.pages_examined == 1
    assert projection.omitted_page_count == 1
    assert projection.omitted_field_count == 1
    assert projection.truncated is True
    assert "OMITTED_FIELD_SENTINEL" not in serialized
    assert "OMITTED_PAGE_SENTINEL" not in serialized


def test_explicit_analysis_input_must_obey_the_section_output_cap() -> None:
    supported = SupportedMemoSectionInput(
        kind=MemoSectionKind.SWOT,
        content="x" * 1_025,
        material_claim_ids=("claim:analysis:swot:1",),
        evidence_ids=("evidence:analysis:swot:1",),
    )

    with pytest.raises(ValueError, match="supported memo section exceeds 1024 characters"):
        _project(
            "Company: Fictional Forge",
            supported_memo_sections=(supported,),
            limits=DeckEvidenceLimits(max_section_chars=1_024),
        )
