"""Focused tests for safe minimum Application and follow-up intake."""

import asyncio
from datetime import UTC, datetime
from hashlib import sha256

import pytest

from founderlookup.application.ports import ApplicationIntakePort, IntakeSubmission
from founderlookup.domain.common import (
    EntityKind,
    KnowledgeState,
    KnowledgeValue,
    StableId,
    SubjectRef,
    UTCDateTime,
    VersionId,
)
from founderlookup.domain.evidence import (
    DataClassification,
    SourceArtifactKind,
    SourceCategory,
    SourceLocator,
    SourceLocatorKind,
)
from founderlookup.ingestion.extraction import (
    ExtractedPdfPage,
    FakePdfExtractor,
    PdfExtractionResult,
    PdfExtractionUsage,
    PdfPageConfidence,
)
from founderlookup.ingestion.intake import (
    ApplicationIntakeRecord,
    ApplicationIntakeService,
    ApplicationReservation,
    ConsentRequiredError,
    DeckExtractionFailedError,
    DeckTooLargeError,
    ExtractionAttemptStatus,
    FocusedArtifactConsent,
    FocusedArtifactKind,
    FocusedArtifactLocator,
    FocusedArtifactRecord,
    FocusedArtifactReservation,
    FocusedArtifactSpeaker,
    FocusedArtifactSubmission,
    IdempotencyConflictError,
    InvalidCompanyNameError,
    InvalidFocusedArtifactError,
    InvalidPdfSignatureError,
    PdfExtractionAttempt,
    SpeakerRole,
    UnsupportedDeckMediaTypeError,
)

FIXED_TIME = datetime(2026, 7, 18, 15, 30, tzinfo=UTC)
PDF_BYTES = b"%PDF-1.7\nfictional private deck\n%%EOF\n"


class _Ids:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def __call__(self, prefix: str) -> str:
        next_value = self._counts.get(prefix, 0) + 1
        self._counts[prefix] = next_value
        return f"{prefix}:{next_value}"


class _ArtifactStore:
    def __init__(self) -> None:
        self.content: dict[str, bytes] = {}
        self.put_calls: list[str] = []
        self.read_calls: list[tuple[str, str]] = []

    def put(
        self,
        artifact_id: str,
        content: bytes,
        *,
        expected_sha256: str,
    ) -> object:
        assert sha256(content).hexdigest() == expected_sha256
        existing = self.content.get(artifact_id)
        if existing is not None and existing != content:
            raise RuntimeError("immutable test artifact conflict")
        self.content[artifact_id] = content
        self.put_calls.append(artifact_id)
        return object()

    def read(
        self,
        artifact_id: str,
        *,
        principal_id: str,
        expected_sha256: str,
    ) -> bytes:
        content = self.content[artifact_id]
        assert sha256(content).hexdigest() == expected_sha256
        self.read_calls.append((artifact_id, principal_id))
        return content


class _Repository:
    def __init__(self) -> None:
        self.applications: dict[str, ApplicationIntakeRecord] = {}
        self.applications_by_key: dict[str, ApplicationIntakeRecord] = {}
        self.focused: dict[str, FocusedArtifactRecord] = {}
        self.focused_by_key: dict[str, FocusedArtifactRecord] = {}
        self.application_reservations = 0
        self.focused_reservations = 0

    def reserve_application(self, record: ApplicationIntakeRecord) -> ApplicationReservation:
        self.application_reservations += 1
        existing = self.applications_by_key.get(record.idempotency_key_sha256)
        if existing is not None:
            if existing.request_fingerprint != record.request_fingerprint:
                raise IdempotencyConflictError
            return ApplicationReservation(record=existing, created=False)
        self.applications[record.application_id] = record
        self.applications_by_key[record.idempotency_key_sha256] = record
        return ApplicationReservation(record=record, created=True)

    def get_application(self, application_id: str) -> ApplicationIntakeRecord | None:
        return self.applications.get(application_id)

    def _replace_application(self, record: ApplicationIntakeRecord) -> ApplicationIntakeRecord:
        self.applications[record.application_id] = record
        self.applications_by_key[record.idempotency_key_sha256] = record
        return record

    def mark_application_artifact_stored(
        self,
        application_id: str,
        content_sha256: str,
    ) -> ApplicationIntakeRecord:
        record = self.applications[application_id]
        assert record.source_artifact.content_sha256 == content_sha256
        return self._replace_application(record.model_copy(update={"artifact_stored": True}))

    def record_application_extraction(
        self,
        application_id: str,
        extraction: PdfExtractionResult,
    ) -> ApplicationIntakeRecord:
        record = self.applications[application_id]
        attempt = PdfExtractionAttempt(
            status=ExtractionAttemptStatus.SUCCEEDED,
            safe_code="extraction_succeeded",
            attempted_at=extraction.extracted_at,
        )
        return self._replace_application(
            record.model_copy(
                update={
                    "extraction": KnowledgeValue[PdfExtractionResult].known(extraction),
                    "extraction_attempts": (*record.extraction_attempts, attempt),
                }
            )
        )

    def record_application_extraction_failure(
        self,
        application_id: str,
        *,
        status: ExtractionAttemptStatus,
        safe_code: str,
        attempted_at: datetime,
    ) -> ApplicationIntakeRecord:
        assert attempted_at == FIXED_TIME
        record = self.applications[application_id]
        attempt = PdfExtractionAttempt(
            status=status,
            safe_code=safe_code,
            attempted_at=attempted_at,
        )
        return self._replace_application(
            record.model_copy(
                update={
                    "extraction": KnowledgeValue[PdfExtractionResult].unknown(safe_code),
                    "extraction_attempts": (*record.extraction_attempts, attempt),
                }
            )
        )

    def reserve_focused_artifact(
        self,
        record: FocusedArtifactRecord,
    ) -> FocusedArtifactReservation:
        self.focused_reservations += 1
        existing = self.focused_by_key.get(record.idempotency_key_sha256)
        if existing is not None:
            if existing.request_fingerprint != record.request_fingerprint:
                raise IdempotencyConflictError
            return FocusedArtifactReservation(record=existing, created=False)
        self.focused[record.import_id] = record
        self.focused_by_key[record.idempotency_key_sha256] = record
        return FocusedArtifactReservation(record=record, created=True)

    def mark_focused_artifact_stored(
        self,
        import_id: str,
        content_sha256: str,
    ) -> FocusedArtifactRecord:
        record = self.focused[import_id]
        assert record.source_artifact.content_sha256 == content_sha256
        updated = record.model_copy(update={"artifact_stored": True})
        self.focused[import_id] = updated
        self.focused_by_key[updated.idempotency_key_sha256] = updated
        return updated


def _clock() -> datetime:
    return FIXED_TIME


def _submission(
    *,
    company_name: str = "  Acme   AI  ",
    display_name: str = r"C:\fakepath\deck.pdf",
    media_type: str = "APPLICATION/PDF",
    content: bytes = PDF_BYTES,
    key: str = "request-001",
) -> IntakeSubmission:
    return IntakeSubmission(
        company_name=company_name,
        display_name=display_name,
        media_type=media_type,
        deck_content=content,
        idempotency_key=key,
    )


def _extraction(source_artifact_id: str, content_sha256: str) -> PdfExtractionResult:
    confidence = PdfPageConfidence(
        average=KnowledgeValue[float].known(0.94),
        minimum=KnowledgeValue[float].known(0.86),
    )
    return PdfExtractionResult(
        extraction_id="extraction:1",
        source_artifact_id=source_artifact_id,
        input_sha256=content_sha256,
        extractor_version="deterministic-pdf.v0",
        model_version=KnowledgeValue[VersionId].unknown("deterministic fake uses no model"),
        extracted_at=FIXED_TIME,
        pages=(
            ExtractedPdfPage(
                page_index=0,
                locator="page:0",
                markdown="# Acme AI",
                confidence=confidence,
            ),
            ExtractedPdfPage(
                page_index=1,
                locator="page:1",
                markdown="Enterprise traction",
                confidence=confidence,
            ),
        ),
        usage=PdfExtractionUsage(
            pages_processed=KnowledgeValue[int].known(2),
            document_size_bytes=KnowledgeValue[int].known(len(PDF_BYTES)),
        ),
    )


def _service(
    repository: _Repository,
    store: _ArtifactStore,
    extractor: FakePdfExtractor,
    *,
    max_pdf_bytes: int = 1_000,
) -> ApplicationIntakeService:
    return ApplicationIntakeService(
        repository=repository,
        artifact_store=store,
        extractor=extractor,
        clock=_clock,
        id_factory=_Ids(),
        max_pdf_bytes=max_pdf_bytes,
    )


def test_minimum_application_stores_original_before_page_extraction() -> None:
    digest = sha256(PDF_BYTES).hexdigest()
    repository = _Repository()
    store = _ArtifactStore()
    extractor = FakePdfExtractor({digest: _extraction("source-artifact:1", digest)})
    service = _service(repository, store, extractor)

    assert isinstance(service, ApplicationIntakePort)
    accepted = asyncio.run(service.submit(_submission()))
    record = repository.applications[accepted.application_id]

    assert accepted.replayed is False
    assert record.company_name == "Acme AI"
    assert record.source_artifact.display_name == "deck.pdf"
    assert record.source_artifact.classification is DataClassification.FOUNDER_PRIVATE
    assert record.source_artifact.content_sha256 == digest
    assert store.content[accepted.source_artifact_id] == PDF_BYTES
    assert not extractor.requests
    assert record.extraction.state is KnowledgeState.UNKNOWN
    assert record.optional_values.geography.state is KnowledgeState.UNKNOWN
    assert record.optional_values.founder_id.state is KnowledgeState.UNKNOWN

    extracted = asyncio.run(service.extract_deck(accepted.application_id))

    assert tuple(page.locator for page in extracted.pages) == ("page:0", "page:1")
    (extraction_request,) = extractor.requests
    assert extraction_request.classification is DataClassification.FOUNDER_PRIVATE
    assert store.read_calls == [(accepted.source_artifact_id, "system:pdf-extraction")]
    assert repository.applications[accepted.application_id].extraction.value == extracted


@pytest.mark.parametrize(
    ("submission", "max_pdf_bytes", "error_type"),
    [
        (_submission(company_name="Acme\x00AI"), 1_000, InvalidCompanyNameError),
        (_submission(media_type="text/plain"), 1_000, UnsupportedDeckMediaTypeError),
        (_submission(content=b""), 1_000, InvalidPdfSignatureError),
        (_submission(content=b"not a pdf"), 1_000, InvalidPdfSignatureError),
        (_submission(content=PDF_BYTES), 10, DeckTooLargeError),
    ],
)
def test_invalid_decks_fail_before_persistence(
    submission: IntakeSubmission,
    max_pdf_bytes: int,
    error_type: type[Exception],
) -> None:
    repository = _Repository()
    store = _ArtifactStore()
    service = _service(repository, store, FakePdfExtractor({}), max_pdf_bytes=max_pdf_bytes)

    with pytest.raises(error_type):
        asyncio.run(service.submit(submission))

    assert repository.application_reservations == 0
    assert store.content == {}


def test_idempotency_replays_normalized_request_and_conflicts_on_new_content() -> None:
    repository = _Repository()
    store = _ArtifactStore()
    service = _service(repository, store, FakePdfExtractor({}))

    first = asyncio.run(service.submit(_submission()))
    replay = asyncio.run(
        service.submit(
            _submission(
                company_name="acme ai",
                display_name="renamed-deck.pdf",
            )
        )
    )

    assert replay.replayed is True
    assert replay.application_id == first.application_id
    assert replay.run_id == first.run_id
    assert replay.source_artifact_id == first.source_artifact_id
    assert store.put_calls == [first.source_artifact_id]

    with pytest.raises(IdempotencyConflictError):
        asyncio.run(service.submit(_submission(content=b"%PDF-1.7\ndifferent deck\n%%EOF\n")))
    assert store.put_calls == [first.source_artifact_id]


def test_extraction_failure_keeps_original_and_explicit_unknown() -> None:
    repository = _Repository()
    store = _ArtifactStore()
    service = _service(repository, store, FakePdfExtractor({}))
    accepted = asyncio.run(service.submit(_submission()))

    with pytest.raises(DeckExtractionFailedError) as failure:
        asyncio.run(service.extract_deck(accepted.application_id))

    record = repository.applications[accepted.application_id]
    assert str(failure.value) == "The stored pitch deck could not be extracted safely."
    assert record.extraction.state is KnowledgeState.UNKNOWN
    assert record.extraction.reason == "fake_extraction_not_seeded"
    assert record.extraction_attempts[-1].status is ExtractionAttemptStatus.FAILED
    assert record.extraction_attempts[-1].safe_code == "fake_extraction_not_seeded"
    assert store.content[accepted.source_artifact_id] == PDF_BYTES


def _focused_submission(
    *,
    kind: FocusedArtifactKind,
    locator_kind: SourceLocatorKind,
    key: str,
    consent_confirmed: bool = True,
) -> FocusedArtifactSubmission:
    return FocusedArtifactSubmission(
        idempotency_key=key,
        subject=SubjectRef(kind=EntityKind.APPLICATION, subject_id="application:known"),
        kind=kind,
        display_name=r"C:\fakepath\focused-answer.txt",
        media_type="text/plain",
        content=b"Founder: the technical prototype is available for review.",
        speaker=FocusedArtifactSpeaker(
            display_name="  Ada   Founder ",
            role=SpeakerRole.FOUNDER,
            subject_id=KnowledgeValue[StableId].unknown("identity review pending"),
        ),
        consent=FocusedArtifactConsent(
            confirmed=consent_confirmed,
            confirmed_at=FIXED_TIME if consent_confirmed else None,
            reference="consent:record-1" if consent_confirmed else None,
        ),
        classification=DataClassification.FOUNDER_PRIVATE,
        source_event_time=KnowledgeValue[UTCDateTime].known(FIXED_TIME),
        locators=(
            FocusedArtifactLocator(
                locator=SourceLocator(
                    kind=locator_kind,
                    locator="segment:00:00:10-00:00:24",
                ),
                affected_claim_ids=("claim:technical-prototype",),
            ),
        ),
    )


@pytest.mark.parametrize(
    ("kind", "locator_kind", "source_category", "artifact_kind"),
    [
        (
            FocusedArtifactKind.INTERVIEW,
            SourceLocatorKind.INTERVIEW_SEGMENT,
            SourceCategory.INTERVIEW,
            SourceArtifactKind.INTERVIEW_TRANSCRIPT,
        ),
        (
            FocusedArtifactKind.FOLLOW_UP,
            SourceLocatorKind.SOURCE_RECORD,
            SourceCategory.FOLLOW_UP,
            SourceArtifactKind.STRUCTURED_IMPORT,
        ),
    ],
)
def test_focused_artifacts_preserve_speaker_consent_classification_and_locator(
    kind: FocusedArtifactKind,
    locator_kind: SourceLocatorKind,
    source_category: SourceCategory,
    artifact_kind: SourceArtifactKind,
) -> None:
    repository = _Repository()
    store = _ArtifactStore()
    service = _service(repository, store, FakePdfExtractor({}))
    submission = _focused_submission(
        kind=kind,
        locator_kind=locator_kind,
        key=f"focused-{kind.value}",
    )

    record = asyncio.run(service.import_focused_artifact(submission))

    assert record.artifact_stored is True
    assert record.speaker.display_name == "Ada Founder"
    assert record.consent.confirmed is True
    assert record.source_artifact.classification is DataClassification.FOUNDER_PRIVATE
    assert record.source_artifact.source_category is source_category
    assert record.source_artifact.kind is artifact_kind
    assert record.locators[0].locator.locator == "segment:00:00:10-00:00:24"
    assert record.locators[0].affected_claim_ids == ("claim:technical-prototype",)
    assert store.content[record.source_artifact.source_artifact_id] == submission.content


def test_focused_import_fails_closed_without_consent_or_exact_locator() -> None:
    repository = _Repository()
    store = _ArtifactStore()
    service = _service(repository, store, FakePdfExtractor({}))

    with pytest.raises(ConsentRequiredError):
        asyncio.run(
            service.import_focused_artifact(
                _focused_submission(
                    kind=FocusedArtifactKind.INTERVIEW,
                    locator_kind=SourceLocatorKind.INTERVIEW_SEGMENT,
                    key="no-consent",
                    consent_confirmed=False,
                )
            )
        )
    with pytest.raises(InvalidFocusedArtifactError):
        asyncio.run(
            service.import_focused_artifact(
                _focused_submission(
                    kind=FocusedArtifactKind.INTERVIEW,
                    locator_kind=SourceLocatorKind.SOURCE_RECORD,
                    key="wrong-locator",
                )
            )
        )

    assert repository.focused_reservations == 0
    assert store.content == {}
