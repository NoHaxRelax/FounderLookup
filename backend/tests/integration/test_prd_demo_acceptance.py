"""One deterministic, network-free acceptance journey for the VC Brain PRD.

This is intentionally one scenario rather than a collection of narrow unit tests.  It is the
merge contract for the demo: a versioned thesis and compound query, minimum founder intake
through the OCR seam, multi-source outbound discovery, human activation, common Screening,
and a human Decision all run against the same application service.

Paid-provider smoke tests remain opt-in under ``tests/live``.  Every adapter below is an
explicit recorded fixture and therefore never claims that a live provider was contacted.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from itertools import count
from pathlib import Path

import pytest

from founderlookup.application.models import FounderFacingStage, OpportunityDetail, TargetState
from founderlookup.application.ports import AcceptedApplication, IntakeSubmission
from founderlookup.application.screening_bridge import DeterministicScreeningBridge
from founderlookup.application.service import (
    ApplicationExtractionOutcome,
    FakeVCBrainService,
)
from founderlookup.application.sourcing import (
    BoundedSourcingCommand,
    MultiAdapterSourcingCoordinator,
    OutboundSearchLoopAudit,
    OutboundSearchStopReason,
    SourceAdapterBinding,
)
from founderlookup.demo.bootstrap import seed_local_demo
from founderlookup.domain.assessment import (
    Decision,
    FounderAxisRating,
    FullAssessmentIdentity,
    HumanDecisionDisposition,
    IdeaVsMarketAxisRating,
    MarketAxisRating,
    MemoSectionKind,
    RecommendationAction,
)
from founderlookup.domain.common import KnowledgeState, KnowledgeValue
from founderlookup.domain.discovery import (
    AcquisitionRequest,
    AcquisitionResult,
    AcquisitionStatus,
    CollectionFailure,
    CollectionResultStatus,
    DiscoveryLead,
    DiscoveryRequest,
    DiscoveryResult,
    ProviderUsage,
)
from founderlookup.domain.evidence import (
    ArtifactAvailability,
    DataClassification,
    SourceArtifactKind,
    SourceCategory,
    SourceLocatorKind,
)
from founderlookup.domain.lifecycles import (
    DecisionReadinessStatus,
    OpportunityOrigin,
    OutboundCandidateStatus,
    PipelineRunStatus,
    ScreeningCaseStatus,
)
from founderlookup.domain.query import QueryCriterionField, QueryPlanState
from founderlookup.infrastructure.artifacts import PrivateArtifactStore
from founderlookup.infrastructure.intake_repository import SQLiteIntakeRepository
from founderlookup.infrastructure.persistence import RecordCategory, SQLiteMemory
from founderlookup.ingestion.extraction import (
    ExtractedPdfPage,
    PdfExtractionRequest,
    PdfExtractionResult,
    PdfExtractionUsage,
    PdfPageConfidence,
)
from founderlookup.ingestion.hackathons import (
    HackathonShowcaseProjection,
    IdentityReviewState,
    PublicHackathonDeckRelationship,
)
from founderlookup.ingestion.intake import ApplicationIntakeService, ExtractionAttemptStatus
from founderlookup.ingestion.openai_structured import (
    OpenAIPublicPageExtraction,
    OpenAIStructuredResult,
    OpenAIStructuredUsage,
    PublicPageStructuredRequest,
    StructuredExtractionStatus,
    project_validated_openai_extraction,
)
from founderlookup.ingestion.policy import PublicSourceCollectionPolicy
from founderlookup.screening.query_planner import (
    DeterministicQueryPlanner,
    QueryPlannerRequest,
)

NOW = datetime(2026, 7, 19, 12, tzinfo=UTC)
COMPOUND_QUERY = (
    "technical founder, Berlin, AI infra, enterprise traction, "
    "no prior VC backing, top-tier accelerator"
)
PRIVATE_PDF = b"%PDF-1.7\n% deterministic founder-private demo deck\n%%EOF"
PRD_MEMO_SECTIONS = (
    MemoSectionKind.COMPANY_SNAPSHOT,
    MemoSectionKind.INVESTMENT_HYPOTHESES,
    MemoSectionKind.SWOT,
    MemoSectionKind.PROBLEM_AND_PRODUCT,
    MemoSectionKind.TRACTION_AND_KPIS,
)


class _RecordedPdfExtractor:
    """Recorded OCR boundary: exact request capture, zero network/provider calls."""

    def __init__(self) -> None:
        self.requests: list[PdfExtractionRequest] = []

    async def extract(self, request: PdfExtractionRequest) -> PdfExtractionResult:
        self.requests.append(request)
        return PdfExtractionResult(
            extraction_id=f"fixture:pdf-extraction:{request.source_artifact_id}",
            source_artifact_id=request.source_artifact_id,
            input_sha256=request.input_sha256,
            extractor_version="recorded-ocr-fixture.v0",
            model_version=KnowledgeValue[str].unknown(
                "A deterministic fixture has no live provider model version."
            ),
            extracted_at=NOW,
            pages=(
                ExtractedPdfPage(
                    page_index=0,
                    locator="page:0",
                    markdown=(
                        "# Minimum Application\n"
                        "Problem: Reviewable AI infrastructure for regulated teams."
                    ),
                    confidence=PdfPageConfidence(
                        average=KnowledgeValue[float].unknown(
                            "The recorded fixture does not invent provider confidence."
                        ),
                        minimum=KnowledgeValue[float].unknown(
                            "The recorded fixture does not invent provider confidence."
                        ),
                    ),
                ),
            ),
            usage=PdfExtractionUsage(
                pages_processed=KnowledgeValue[int].known(1),
                document_size_bytes=KnowledgeValue[int].known(len(request.content)),
            ),
        )


class _RecordedPublicSource:
    """Deterministic discovery/acquisition replay with observable calls."""

    def __init__(
        self,
        *,
        adapter_id: str,
        category: SourceCategory,
        leads: tuple[tuple[str, str], ...],
        content: dict[str, tuple[bytes, str]],
        failure: CollectionFailure | None = None,
    ) -> None:
        self.adapter_id = adapter_id
        self.category = category
        self._leads = leads
        self._content = content
        self._failure = failure
        self.discovery_requests: list[DiscoveryRequest] = []
        self.acquisition_requests: list[AcquisitionRequest] = []

    async def discover(self, request: DiscoveryRequest) -> DiscoveryResult:
        self.discovery_requests.append(request)
        retrieval = request.retrieval_requests[0]
        leads = tuple(
            DiscoveryLead(
                lead_id=f"{self.adapter_id}:lead:{index}",
                retrieval_request_id=retrieval.retrieval_request_id,
                original_url=url,
                source_category=self.category,
                discovered_at=NOW,
                rank=index,
                title=KnowledgeValue[str].known(title),
                provider_summary=KnowledgeValue[str].unknown(
                    "Recorded retrieval metadata is not primary Evidence."
                ),
                retrieval_relevance=KnowledgeValue[float].unknown(
                    "The recorded source does not invent provider relevance."
                ),
            )
            for index, (url, title) in enumerate(self._leads, start=1)
        )
        failures = () if self._failure is None else (self._failure,)
        status = (
            CollectionResultStatus.PARTIALLY_SUCCEEDED
            if leads and failures
            else CollectionResultStatus.FAILED
            if failures
            else CollectionResultStatus.SUCCEEDED
        )
        return DiscoveryResult(
            result_id=f"{self.adapter_id}:result:{request.request_id}",
            request_id=request.request_id,
            status=status,
            completed_at=NOW,
            leads=leads,
            failures=failures,
            usage=ProviderUsage(
                adapter_id=self.adapter_id,
                operation_id=f"{self.adapter_id}:discover:{len(self.discovery_requests)}",
                request_count=1,
                result_count=len(leads),
                elapsed_milliseconds=0,
                cost_amount=KnowledgeValue[float].known(0.0),
                cost_currency=KnowledgeValue[str].known("USD"),
            ),
        )

    async def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        self.acquisition_requests.append(request)
        body, media_type = self._content[request.original_url]
        return AcquisitionResult(
            result_id=f"{self.adapter_id}:acquired:{request.acquisition_request_id}",
            acquisition_request_id=request.acquisition_request_id,
            original_url=request.original_url,
            status=AcquisitionStatus.ACQUIRED,
            completed_at=NOW,
            content=body,
            media_type=media_type,
            content_sha256=sha256(body).hexdigest(),
            source_event_time=KnowledgeValue[datetime].unknown(
                "The fixture source publishes no authoritative event time."
            ),
        )


class _RecordedStructuredExtractor:
    """Schema-valid structured projection of exact fixture lines, with no model call."""

    def __init__(self) -> None:
        self.requests: list[PublicPageStructuredRequest] = []

    async def extract(self, request: PublicPageStructuredRequest) -> OpenAIStructuredResult:
        self.requests.append(request)
        extraction = OpenAIPublicPageExtraction.model_validate(
            {
                "schema_version": "openai-public-page-extraction.v0",
                "event": {
                    "state": "known",
                    "value": "Alpine AI Hack 2026",
                    "gap_reason": None,
                    "evidence": {
                        "line_number": 2,
                        "excerpt": "Event: Alpine AI Hack 2026",
                    },
                },
                "project": {
                    "state": "known",
                    "value": "Signal Forge",
                    "gap_reason": None,
                    "evidence": {"line_number": 3, "excerpt": "Project: Signal Forge"},
                },
                "participants": (
                    {
                        "display_name": "Ada Demo",
                        "public_profile_url": "https://showcase.example.test/people/ada",
                        "evidence": {
                            "line_number": 4,
                            "excerpt": (
                                "Participants: [Ada Demo]"
                                "(https://showcase.example.test/people/ada), Bo Demo"
                            ),
                        },
                    },
                    {
                        "display_name": "Bo Demo",
                        "public_profile_url": None,
                        "evidence": {
                            "line_number": 4,
                            "excerpt": (
                                "Participants: [Ada Demo]"
                                "(https://showcase.example.test/people/ada), Bo Demo"
                            ),
                        },
                    },
                ),
                "participant_gap_reason": None,
                "links": (
                    {
                        "kind": "repository",
                        "label": "GitHub",
                        "url": "https://github.com/example/signal-forge",
                        "evidence": {
                            "line_number": 5,
                            "excerpt": (
                                "Repository: [GitHub](https://github.com/example/signal-forge)"
                            ),
                        },
                    },
                    {
                        "kind": "demo",
                        "label": "Try it",
                        "url": "https://signal-forge.example.test/",
                        "evidence": {
                            "line_number": 6,
                            "excerpt": "Demo: [Try it](https://signal-forge.example.test/)",
                        },
                    },
                    {
                        "kind": "pitch_deck",
                        "label": "Public slides",
                        "url": (
                            "https://docs.google.com/presentation/d/"
                            "1tsx9EmV3Mx0U4Hcv9Hhew0FdMP4ybgdOQPDxFbFZ_DA/"
                            "edit?usp=sharing"
                        ),
                        "evidence": {
                            "line_number": 7,
                            "excerpt": (
                                "Pitch deck: [Public slides]"
                                "(https://docs.google.com/presentation/d/"
                                "1tsx9EmV3Mx0U4Hcv9Hhew0FdMP4ybgdOQPDxFbFZ_DA/"
                                "edit?usp=sharing)"
                            ),
                        },
                    },
                ),
                "public_deck_gap_reason": None,
                "public_contacts": (
                    {
                        "kind": "website",
                        "label": "Official website",
                        "value": "https://signal-forge.example.test/",
                        "evidence": {
                            "line_number": 8,
                            "excerpt": (
                                "Website: [Official website](https://signal-forge.example.test/)"
                            ),
                        },
                    },
                    {
                        "kind": "public_email",
                        "label": "Contact",
                        "value": "hello@signal-forge.example",
                        "evidence": {
                            "line_number": 9,
                            "excerpt": "Contact: hello@signal-forge.example",
                        },
                    },
                ),
                "public_contact_gap_reason": None,
                "ambiguous_or_unsupported": (),
                "identity_verification": "not_performed",
            }
        )
        showcase, contacts = project_validated_openai_extraction(
            source_artifact=request.source_artifact,
            content=request.content,
            extraction=extraction,
            model_version="recorded-structured-fixture.v0",
        )
        return OpenAIStructuredResult(
            result_id=f"fixture:structured-result:{request.request_id}",
            request_id=request.request_id,
            status=StructuredExtractionStatus.SUCCEEDED,
            completed_at=NOW,
            requested_model="recorded-structured-fixture.v0",
            model_version=KnowledgeValue[str].known("recorded-structured-fixture.v0"),
            provider_response_id=KnowledgeValue[str].unknown(
                "A deterministic fixture has no provider response."
            ),
            projection=showcase,
            contact_projection=contacts,
            usage=OpenAIStructuredUsage(input_tokens=0, output_tokens=0, total_tokens=0),
            failure=None,
        )


def _source_binding(
    source: _RecordedPublicSource,
    *,
    kind: SourceArtifactKind,
    media_types: tuple[str, ...],
) -> SourceAdapterBinding:
    return SourceAdapterBinding(
        adapter_id=source.adapter_id,
        discovery=source,
        acquisition=source,
        source_categories=(source.category,),
        authoritative=True,
        artifact_kind=kind,
        allowed_media_types=media_types,
        policy=PublicSourceCollectionPolicy(
            collection_purpose="investor sourcing and evaluation",
            lawful_basis="legitimate interests with human review and removal",
            source_terms=KnowledgeValue[str].known("https://example.test/terms"),
            robots_policy=KnowledgeValue[str].known(
                "Recorded fixture representing an approved source interface."
            ),
        ),
    )


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
    assert len(matches) == 1
    return matches[0]


def _record_request_for_information(
    service: FakeVCBrainService,
    opportunity_id: str,
) -> Decision:
    detail = service.get_opportunity(opportunity_id)
    assessment = detail.latest_assessment
    memo = detail.latest_memo
    recommendation = detail.latest_recommendation
    assert assessment is not None
    assert memo is not None
    assert recommendation is not None
    return service.record_decision(
        opportunity_id,
        assessment_id=assessment.assessment_id,
        memo_id=memo.memo_id,
        recommendation_id=recommendation.recommendation_id,
        disposition=HumanDecisionDisposition.REQUEST_MORE_INFORMATION,
        rationale="Human reviewer requests the unresolved diligence evidence.",
        actor_id="investor:acceptance-reviewer",
    )


@pytest.mark.anyio
async def test_prd_demo_acceptance_journey(tmp_path: Path) -> None:
    """Exercise the complete deterministic demo contract against one service."""

    service_ids = count(1)
    intake_ids = count(1)
    sourcing_ids = count(1)
    bridge = DeterministicScreeningBridge()
    artifacts = PrivateArtifactStore(
        (tmp_path / "artifacts").absolute(),
        authorize_read=lambda _principal, _artifact: True,
    )
    service = FakeVCBrainService(
        clock=lambda: NOW,
        id_factory=lambda: f"acceptance:service:{next(service_ids):04d}",
        capability_pepper=b"deterministic-acceptance-capability-pepper",
        artifact_reader=artifacts,
        screening_bridge=bridge,
    )
    seeded = seed_local_demo(service, screening_bridge=bridge)

    # Versioned Thesis + the PRD's compound-query semantics remain inspectable.
    thesis = service.active_thesis()
    assert thesis.thesis_version_id == seeded.thesis_version_id
    assert service.thesis_history() == (thesis,)
    plan = DeterministicQueryPlanner(clock=lambda: NOW).plan(
        QueryPlannerRequest(raw_query=COMPOUND_QUERY)
    )
    assert plan.state is QueryPlanState.VALIDATED
    assert tuple(criterion.field for criterion in plan.criteria) == (
        QueryCriterionField.TECHNICAL_FOUNDER,
        QueryCriterionField.GEOGRAPHY,
        QueryCriterionField.SECTOR,
        QueryCriterionField.ENTERPRISE_TRACTION,
        QueryCriterionField.PRIOR_VC_BACKING,
        QueryCriterionField.ACCELERATOR,
    )
    assert [gap.text for gap in plan.unresolved_phrases] == ["top-tier"]
    assert plan.retrieval_requests[0].max_results == 10
    assert plan.retrieval_requests[0].max_pages == 2

    # Minimum public founder path: company + PDF enter private storage, then the actual
    # provider-neutral OCR port is invoked exactly once with founder-private bytes.
    pdf_extractor = _RecordedPdfExtractor()
    intake_repository = SQLiteIntakeRepository(tmp_path / "intake.sqlite3")
    intake = ApplicationIntakeService(
        repository=intake_repository,
        artifact_store=artifacts,
        extractor=pdf_extractor,
        clock=lambda: NOW,
        id_factory=lambda prefix: f"{prefix}:acceptance:{next(intake_ids):04d}",
        max_pdf_bytes=1_000_000,
    )
    submission = IntakeSubmission(
        company_name="Minimum Inbound Demo",
        display_name="../minimum-demo.pdf",
        media_type="application/pdf",
        deck_content=PRIVATE_PDF,
        idempotency_key="minimum-inbound-demo-v1",
    )
    accepted = await intake.submit(submission)
    replayed = await intake.submit(submission)
    assert replayed == accepted.model_copy(update={"replayed": True})
    receipt = service.register_application(
        accepted,
        display_name=submission.display_name,
        media_type=submission.media_type,
    )
    assert service.founder_status(receipt.founder_status_capability).stage is (
        FounderFacingStage.RECEIVED
    )
    extraction = await intake.extract_deck(accepted.application_id)
    service.record_application_extraction_outcome(
        accepted.application_id,
        outcome=ApplicationExtractionOutcome.SUCCEEDED,
        accepted_output_id=extraction.extraction_id,
    )
    stored_intake = intake_repository.get_application(accepted.application_id)
    assert stored_intake is not None
    assert stored_intake.source_artifact.classification is DataClassification.FOUNDER_PRIVATE
    assert stored_intake.source_artifact.display_name == "minimum-demo.pdf"
    assert tuple(attempt.status for attempt in stored_intake.extraction_attempts) == (
        ExtractionAttemptStatus.SUCCEEDED,
    )
    assert len(pdf_extractor.requests) == 1
    assert pdf_extractor.requests[0].classification is DataClassification.FOUNDER_PRIVATE
    assert pdf_extractor.requests[0].content == PRIVATE_PDF
    assert extraction.pages[0].locator == "page:0"
    assert (
        service.read_artifact(
            accepted.source_artifact_id,
            principal_id="investor:acceptance-reviewer",
        )[0]
        == PRIVATE_PDF
    )

    minimum = _opportunity_for_application(service, accepted.application_id)
    assert minimum.origin is OpportunityOrigin.INBOUND
    assert minimum.outbound_candidate_id is None
    service.start_screening(minimum.opportunity_id)
    minimum = service.get_opportunity(minimum.opportunity_id)
    assert minimum.latest_assessment is not None
    assert minimum.latest_assessment.identity.mode == "full"
    assert minimum.latest_memo is not None
    assert tuple(section.kind for section in minimum.latest_memo.sections) == PRD_MEMO_SECTIONS
    # No deck-to-Claim projection was accepted for this minimal fixture, so unsupported
    # memo facts remain explicit Unknowns instead of fluent invention.
    assert all(
        section.content.state is KnowledgeState.UNKNOWN
        and section.content.reason is not None
        and not section.material_claim_ids
        for section in minimum.latest_memo.sections
    )
    minimum_decision = _record_request_for_information(service, minimum.opportunity_id)
    assert minimum_decision.actor_id == "investor:acceptance-reviewer"
    assert minimum_decision.reviewed_recommendation_id == (
        minimum.latest_recommendation.recommendation_id
        if minimum.latest_recommendation is not None
        else None
    )
    founder_status = service.founder_status(receipt.founder_status_capability)
    assert founder_status.stage is FounderFacingStage.COMPLETE
    assert founder_status.target_state is TargetState.COMPLETE

    # A bounded outbound graph fans out across three successful signal categories while
    # retaining one explicit empty result and one safe partial provider failure.
    showcase_url = "https://showcase.example.test/projects/signal-forge"
    deck_url = (
        "https://docs.google.com/presentation/d/"
        "1tsx9EmV3Mx0U4Hcv9Hhew0FdMP4ybgdOQPDxFbFZ_DA/edit?usp=sharing"
    )
    deck_export_url = (
        "https://docs.google.com/presentation/d/"
        "1tsx9EmV3Mx0U4Hcv9Hhew0FdMP4ybgdOQPDxFbFZ_DA/export/pdf"
    )
    showcase = b"""# Signal Forge
Event: Alpine AI Hack 2026
Project: Signal Forge
Participants: [Ada Demo](https://showcase.example.test/people/ada), Bo Demo
Repository: [GitHub](https://github.com/example/signal-forge)
Demo: [Try it](https://signal-forge.example.test/)
Pitch deck: [Public slides](https://docs.google.com/presentation/d/1tsx9EmV3Mx0U4Hcv9Hhew0FdMP4ybgdOQPDxFbFZ_DA/edit?usp=sharing)
Website: [Official website](https://signal-forge.example.test/)
Contact: hello@signal-forge.example
"""
    hackathon = _RecordedPublicSource(
        adapter_id="fixture-hackathon-v0",
        category=SourceCategory.HACKATHON,
        leads=((showcase_url, "Signal Forge public showcase"),),
        content={
            showcase_url: (showcase, "text/markdown; charset=utf-8"),
            deck_export_url: (
                b"%PDF-1.7\n% Signal Forge public deck",
                "application/pdf",
            ),
        },
    )
    github_url = "https://github.com/ada-demo/signal-runtime"
    github_snapshot = json.dumps(
        {
            "schema_version": "github-developer-activity-snapshot.v0",
            "subject": {
                "kind": "user",
                "owner": "ada-demo",
                "repository": None,
                "original_url": github_url,
            },
            "records": {
                "profile": {"login": "ada-demo", "html_url": github_url},
                "repositories": [
                    {
                        "full_name": "ada-demo/signal-runtime",
                        "html_url": github_url,
                    }
                ],
                "public_events": [{"id": "fixture-event-1", "type": "PushEvent"}],
            },
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    developer = _RecordedPublicSource(
        adapter_id="fixture-developer-v0",
        category=SourceCategory.DEVELOPER_ACTIVITY,
        leads=((github_url, "Ada Demo public developer activity"),),
        content={github_url: (github_snapshot, "application/json")},
    )
    launch_url = "https://launch.example.test/products/reviewable-runtime"
    product = _RecordedPublicSource(
        adapter_id="fixture-product-launch-v0",
        category=SourceCategory.PRODUCT_LAUNCH,
        leads=((launch_url, "Reviewable Runtime product launch"),),
        content={
            launch_url: (
                b"# Reviewable Runtime\nPublic launch of an inspectable AI runtime.\n",
                "text/markdown",
            )
        },
    )
    empty = _RecordedPublicSource(
        adapter_id="fixture-empty-accelerator-v0",
        category=SourceCategory.ACCELERATOR_COHORT,
        leads=(),
        content={},
    )
    partial = _RecordedPublicSource(
        adapter_id="fixture-partial-research-v0",
        category=SourceCategory.RESEARCH,
        leads=(),
        content={},
        failure=CollectionFailure(
            operation_id="fixture-partial-research-v0:discover",
            safe_code="fixture_source_temporarily_unavailable",
            safe_message="One fixture source is temporarily unavailable.",
            retryable=True,
        ),
    )
    memory = SQLiteMemory(tmp_path / "memory.sqlite3")
    structured = _RecordedStructuredExtractor()
    coordinator = MultiAdapterSourcingCoordinator(
        adapters=(
            _source_binding(
                hackathon,
                kind=SourceArtifactKind.WEB_SNAPSHOT,
                media_types=("text/markdown", "application/pdf"),
            ),
            _source_binding(
                developer,
                kind=SourceArtifactKind.REPOSITORY_RECORD,
                media_types=("application/json",),
            ),
            _source_binding(
                product,
                kind=SourceArtifactKind.WEB_SNAPSHOT,
                media_types=("text/markdown",),
            ),
            _source_binding(
                empty,
                kind=SourceArtifactKind.SOURCE_API_RECORD,
                media_types=("application/json",),
            ),
            _source_binding(
                partial,
                kind=SourceArtifactKind.SOURCE_API_RECORD,
                media_types=("application/json",),
            ),
        ),
        service=service,
        memory=memory,
        artifact_store=artifacts,
        screening_bridge=bridge,
        now=lambda: NOW,
        id_factory=lambda prefix: f"{prefix}:acceptance:{next(sourcing_ids):04d}",
        max_results=10,
        max_pages=4,
        max_bytes=500_000,
        timeout_seconds=20,
        cache_ttl_seconds=900,
        structured_page_extractor=structured,
        public_pdf_acquisition=hackathon,
        public_pdf_extractor=pdf_extractor,
        max_follow_up_rounds=0,
        max_discovery_calls=8,
    )
    command = BoundedSourcingCommand(
        query=COMPOUND_QUERY,
        source_categories=(
            SourceCategory.HACKATHON,
            SourceCategory.DEVELOPER_ACTIVITY,
            SourceCategory.PRODUCT_LAUNCH,
            SourceCategory.ACCELERATOR_COHORT,
            SourceCategory.RESEARCH,
        ),
        max_results=10,
        max_pages=4,
        max_bytes=500_000,
        timeout_seconds=20,
    )
    first = coordinator.enqueue(command)
    await coordinator.execute(first.run.run_id, command)
    source_count_after_first = len(memory.list_records(RecordCategory.SOURCE_ARTIFACT))
    candidate_count_after_first = len(service.list_candidates(limit=100).items)
    second = coordinator.enqueue(command)
    await coordinator.execute(second.run.run_id, command)

    assert service.get_run(first.run.run_id).status is PipelineRunStatus.PARTIALLY_SUCCEEDED
    assert service.get_run(second.run.run_id).status is PipelineRunStatus.PARTIALLY_SUCCEEDED
    assert len(empty.discovery_requests) == 2
    assert empty.acquisition_requests == []
    assert len(partial.discovery_requests) == 2
    assert partial.acquisition_requests == []
    assert [request.original_url for request in hackathon.acquisition_requests] == [
        showcase_url,
        deck_export_url,
    ]
    assert len(developer.acquisition_requests) == 1
    assert len(product.acquisition_requests) == 1
    assert len(structured.requests) == 1
    assert len(pdf_extractor.requests) == 2
    outbound_pdf_request = pdf_extractor.requests[1]
    assert outbound_pdf_request.classification is DataClassification.PUBLIC
    assert outbound_pdf_request.content.startswith(b"%PDF-")
    public_deck_ocr = [
        record.payload
        for record in memory.list_records(RecordCategory.CANONICAL_ENTITY)
        if record.payload.get("record_type") == "public_deck_ocr"
    ]
    assert len(public_deck_ocr) == 1
    assert public_deck_ocr[0]["state"] == "known"
    assert len(memory.list_records(RecordCategory.SOURCE_ARTIFACT)) == source_count_after_first
    assert len(service.list_candidates(limit=100).items) == candidate_count_after_first
    assert {
        record.payload["source_category"]
        for record in memory.list_records(RecordCategory.SOURCE_ARTIFACT)
    } >= {
        SourceCategory.HACKATHON.value,
        SourceCategory.DEVELOPER_ACTIVITY.value,
        SourceCategory.PRODUCT_LAUNCH.value,
    }
    second_telemetry = memory.list_records(
        RecordCategory.COLLECTION_TELEMETRY,
        subject_id=second.run.run_id,
    )
    assert sum(record.payload.get("status") == "cache_hit" for record in second_telemetry) == 4
    loop_payload = next(
        record.payload
        for record in memory.list_records(
            RecordCategory.COLLECTION_TELEMETRY,
            subject_id=first.run.run_id,
        )
        if record.payload.get("record_type") == "outbound_search_loop"
    )
    loop_audit = OutboundSearchLoopAudit.model_validate_json(json.dumps(dict(loop_payload)))
    assert loop_audit.stop_reason is OutboundSearchStopReason.PARTIAL_FAILURE
    assert len(loop_audit.rounds) == 1
    assert loop_audit.rounds[0].acquired_page_count == 4
    assert loop_audit.candidate_activation == "human_controlled"
    assert loop_audit.outreach_action == "none"

    canonical = memory.list_records(RecordCategory.CANONICAL_ENTITY)
    projection = HackathonShowcaseProjection.model_validate_json(
        json.dumps(
            dict(
                next(
                    record.payload
                    for record in canonical
                    if record.payload.get("projection_version")
                    == "hackathon-showcase-projection.v0"
                )
            )
        )
    )
    assert projection.event_name.value == "Alpine AI Hack 2026"
    assert projection.project_name.value == "Signal Forge"
    assert [participant.display_name for participant in projection.participants] == [
        "Ada Demo",
        "Bo Demo",
    ]
    assert all(
        participant.identity_state is IdentityReviewState.NEEDS_REVIEW
        for participant in projection.participants
    )
    relationship = PublicHackathonDeckRelationship.model_validate_json(
        json.dumps(
            dict(
                next(
                    record.payload
                    for record in canonical
                    if record.payload.get("record_type") == "public_hackathon_deck_relationship"
                )
            )
        )
    )
    assert relationship.showcase_source_artifact_id != relationship.deck_source_artifact_id
    assert relationship.deck_original_url == deck_url
    assert relationship.deck_acquisition_url == deck_export_url
    assert relationship.deck_url_normalization == "google_slides_export_pdf"
    assert (
        next(link.url for link in projection.links if link.kind.value == "pitch_deck") == deck_url
    )
    assert outbound_pdf_request.source_artifact_id == relationship.deck_source_artifact_id
    hackathon_candidate = next(
        item
        for item in service.list_candidates(limit=100).items
        if item.company_name == "Showcase project: Signal Forge"
    )
    assert {route.value for route in hackathon_candidate.public_contact_routes} == {
        "https://signal-forge.example.test/",
        "hello@signal-forge.example",
    }, service.get_run(first.run.run_id).model_dump_json(indent=2)
    assert all(
        route.source_artifact_id == projection.source_artifact_id
        and route.source_locator in {"line:8", "line:9"}
        and route.classification == "public"
        for route in hackathon_candidate.public_contact_routes
    )

    # Cold-start evidence produces a Founder Score that is separate from all three axes.
    outbound_before = next(
        item
        for item in service.list_candidates(limit=100).items
        if item.outbound_candidate_id == seeded.outbound_candidate_id
    )
    preliminary = outbound_before.preliminary_assessment
    assert preliminary is not None
    assert preliminary.identity.mode == "preliminary"
    assert preliminary.founder_score.value is not None
    founder_score = preliminary.founder_score.value
    assert founder_score.score == 76.0
    assert any(factor.evidence_ids for factor in founder_score.factors)
    axis_assessment_ids = {
        preliminary.axes.founder.assessment_id,
        preliminary.axes.market.assessment_id,
        preliminary.axes.idea_vs_market.assessment_id,
    }
    assert len(axis_assessment_ids) == 3
    assert founder_score.snapshot_id not in axis_assessment_ids
    assert preliminary.axes.founder.rating is FounderAxisRating.STRONG
    assert preliminary.axes.market.rating is MarketAxisRating.NEUTRAL
    assert preliminary.axes.idea_vs_market.rating is IdeaVsMarketAxisRating.VIABLE

    # Human activation only creates an editable draft.  No outreach occurs before the same
    # canonical Company accepts an Application and enters the common full-Screening contract.
    draft = "Fixture draft for a human investor to edit; do not send automatically."
    activated = service.activate_candidate(
        seeded.outbound_candidate_id,
        outreach_draft=draft,
    )
    assert activated.status is OutboundCandidateStatus.ACTIVATED
    assert activated.outreach_draft == draft
    company_id = service.canonical_company_for_outbound_application(seeded.outbound_candidate_id)
    outbound_accepted = AcceptedApplication(
        application_id="acceptance:application:outbound",
        company_id=company_id,
        run_id="acceptance:run:outbound-intake",
        source_artifact_id="acceptance:artifact:outbound-deck",
        source_artifact_sha256="b" * 64,
        received_at=NOW,
    )
    outbound_receipt = service.register_application(
        outbound_accepted,
        display_name="outbound-deck.pdf",
        media_type="application/pdf",
        outbound_candidate_id=seeded.outbound_candidate_id,
    )
    service.record_application_extraction_outcome(
        outbound_accepted.application_id,
        outcome=ApplicationExtractionOutcome.SUCCEEDED,
        accepted_output_id="acceptance:extraction:outbound",
    )
    outbound_opportunity = _opportunity_for_application(service, outbound_accepted.application_id)
    assert outbound_opportunity.origin is OpportunityOrigin.OUTBOUND
    assert outbound_opportunity.company_id == outbound_before.company_id
    assert outbound_opportunity.founder_id == outbound_before.founder_id
    assert outbound_opportunity.related_run_ids[:2] == (
        preliminary.run_id,
        outbound_accepted.run_id,
    )
    service.start_screening(outbound_opportunity.opportunity_id)
    outbound_screened = service.get_opportunity(outbound_opportunity.opportunity_id)
    assert outbound_screened.latest_assessment is not None
    assert isinstance(outbound_screened.latest_assessment.identity, FullAssessmentIdentity)
    assert outbound_screened.latest_assessment.identity.origin is OpportunityOrigin.OUTBOUND
    assert outbound_screened.latest_assessment.identity.outbound_candidate_id == (
        seeded.outbound_candidate_id
    )
    assert (
        next(
            item
            for item in service.list_candidates(limit=100).items
            if item.outbound_candidate_id == seeded.outbound_candidate_id
        ).preliminary_assessment
        == preliminary
    )
    outbound_decision = _record_request_for_information(service, outbound_screened.opportunity_id)
    assert service.founder_status(outbound_receipt.founder_status_capability).stage is (
        FounderFacingStage.COMPLETE
    )

    # The rich inbound fixture pins citations, claim-level Trust, axis disagreement,
    # a blocking Contradiction, readiness, the five memo sections, and timing.
    rich = service.get_opportunity(
        seeded.inbound_opportunity_id,
        include_claims=True,
        include_evidence=True,
    )
    assessment = rich.latest_assessment
    assert rich.origin is OpportunityOrigin.INBOUND
    assert assessment is not None
    assert assessment.axes.founder.rating is FounderAxisRating.UNKNOWN
    assert assessment.axes.market.rating is MarketAxisRating.BEAR
    assert assessment.axes.idea_vs_market.rating is IdeaVsMarketAxisRating.PIVOTABLE
    assert assessment.contradictions
    assert all(contradiction.blocking for contradiction in assessment.contradictions)
    assert assessment.decision_readiness is not None
    assert assessment.decision_readiness.status is DecisionReadinessStatus.BLOCKED
    assert assessment.decision_readiness.blockers
    assert len(rich.claims) == len(rich.evidence) == 7
    assert all(claim.trust.state.value in {"scored", "unscored"} for claim in rich.claims)
    assert all(
        evidence.locator.kind is SourceLocatorKind.DOCUMENT_PAGE
        and evidence.source_artifact_id == seeded.inbound_source_artifact_id
        and evidence.availability is ArtifactAvailability.ACCESS_RESTRICTED
        for evidence in rich.evidence
    )
    memo = rich.latest_memo
    assert memo is not None
    assert tuple(section.kind for section in memo.sections) == PRD_MEMO_SECTIONS
    for section in memo.sections:
        if section.content.state is KnowledgeState.KNOWN:
            assert section.content.evidence_ids
        elif section.content.state is KnowledgeState.CONFLICTED:
            assert section.content.alternatives
            assert all(alternative.evidence_ids for alternative in section.content.alternatives)
    recommendation = rich.latest_recommendation
    assert recommendation is not None
    assert recommendation.action is RecommendationAction.NEEDS_INFORMATION
    assert rich.human_decisions == ()
    assert rich.timing.decision_readiness_target_at == rich.timing.started_at + timedelta(hours=24)
    assert rich.timing.target_state is TargetState.ON_TRACK
    rich_decision = _record_request_for_information(service, rich.opportunity_id)
    rich_after_decision = service.get_opportunity(rich.opportunity_id)
    assert rich_after_decision.screening_status is ScreeningCaseStatus.DECIDED
    assert rich_after_decision.human_decisions == (rich_decision,)
    assert rich_decision.reviewed_recommendation_id == recommendation.recommendation_id
    assert rich_decision.decision_id != recommendation.recommendation_id

    # Recommendations and Decisions are analytical records only.  The accepted workflow has
    # neither an autonomous outreach transition nor any fund-transfer capability/field.
    assert all(
        item.status is not OutboundCandidateStatus.CONTACTED
        for item in service.list_candidates(limit=100).items
    )
    assert loop_audit.outreach_action == "none"
    for decision in (minimum_decision, outbound_decision, rich_decision):
        serialized = decision.model_dump(mode="json")
        assert not any("fund" in field or "transfer" in field for field in serialized)

    # The query is executable against the same mixed inbound/outbound workspace and remains
    # criterion-by-criterion inspectable even when hard facts are Unknown.
    query_result = service.query_opportunities(plan)
    assert query_result.plan == plan
    assert query_result.sourcing_run_id is None
    assert all(len(item.criteria) == len(plan.criteria) for item in query_result.results)

    # Expiring the HTTP cache may legitimately reacquire an identical public deck, but
    # immutable artifact identity must prevent a second paid OCR call for the same bytes.
    uncached_replay = MultiAdapterSourcingCoordinator(
        adapters=(
            _source_binding(
                hackathon,
                kind=SourceArtifactKind.WEB_SNAPSHOT,
                media_types=("text/markdown", "application/pdf"),
            ),
            _source_binding(
                developer,
                kind=SourceArtifactKind.REPOSITORY_RECORD,
                media_types=("application/json",),
            ),
            _source_binding(
                product,
                kind=SourceArtifactKind.WEB_SNAPSHOT,
                media_types=("text/markdown",),
            ),
            _source_binding(
                empty,
                kind=SourceArtifactKind.SOURCE_API_RECORD,
                media_types=("application/json",),
            ),
            _source_binding(
                partial,
                kind=SourceArtifactKind.SOURCE_API_RECORD,
                media_types=("application/json",),
            ),
        ),
        service=service,
        memory=memory,
        artifact_store=artifacts,
        screening_bridge=bridge,
        now=lambda: NOW,
        id_factory=lambda prefix: f"{prefix}:acceptance:{next(sourcing_ids):04d}",
        max_results=10,
        max_pages=4,
        max_bytes=500_000,
        timeout_seconds=20,
        cache_ttl_seconds=0,
        structured_page_extractor=structured,
        public_pdf_acquisition=hackathon,
        public_pdf_extractor=pdf_extractor,
        max_follow_up_rounds=0,
        max_discovery_calls=8,
    )
    replay = uncached_replay.enqueue(command)
    await uncached_replay.execute(replay.run.run_id, command)
    assert len(pdf_extractor.requests) == 2
