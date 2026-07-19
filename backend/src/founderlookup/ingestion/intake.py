"""Safe, idempotent minimum-Application intake and focused artifact import."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Final, Literal, Protocol, Self, runtime_checkable

from pydantic import Field, model_validator

from founderlookup.application.ports import AcceptedApplication, IntakeSubmission
from founderlookup.domain.common import (
    DomainModel,
    KnowledgeValue,
    NonBlankStr,
    PositiveInt,
    StableId,
    SubjectRef,
    UTCDateTime,
)
from founderlookup.domain.evidence import (
    DataClassification,
    Sha256Hex,
    SourceArtifact,
    SourceArtifactKind,
    SourceCategory,
    SourceLocator,
    SourceLocatorKind,
)
from founderlookup.ingestion.extraction import (
    PdfExtractionBlockedError,
    PdfExtractionError,
    PdfExtractionRequest,
    PdfExtractionResult,
    PdfExtractor,
)

APPLICATION_INTAKE_SCHEMA_VERSION: Final = "application-intake.v0"
FOCUSED_ARTIFACT_SCHEMA_VERSION: Final = "focused-artifact.v0"
_PDF_MEDIA_TYPE: Final = "application/pdf"
_PDF_MAGIC: Final = b"%PDF-"
_IDEMPOTENCY_KEY_MAX_LENGTH: Final = 255
_COMPANY_NAME_MAX_LENGTH: Final = 300
_DISPLAY_NAME_MAX_LENGTH: Final = 255


class IntakeServiceError(RuntimeError):
    """Safe base exception whose message contains no private request material."""

    code = "intake_failed"
    safe_message = "The submission could not be accepted safely."

    def __init__(self) -> None:
        super().__init__(self.safe_message)


class InvalidCompanyNameError(IntakeServiceError, ValueError):
    code = "invalid_company_name"
    safe_message = "Company name must contain a valid non-blank value."


class InvalidDisplayNameError(IntakeServiceError, ValueError):
    code = "invalid_display_name"
    safe_message = "The uploaded file must have a valid display name."


class InvalidIdempotencyKeyError(IntakeServiceError, ValueError):
    code = "invalid_idempotency_key"
    safe_message = "A valid idempotency key is required."


class UnsupportedDeckMediaTypeError(IntakeServiceError, ValueError):
    code = "unsupported_deck_media_type"
    safe_message = "The pitch deck must use the application/pdf media type."


class DeckTooLargeError(IntakeServiceError, ValueError):
    code = "deck_too_large"
    safe_message = "The pitch deck exceeds the configured size limit."


class InvalidPdfSignatureError(IntakeServiceError, ValueError):
    code = "invalid_pdf_signature"
    safe_message = "The uploaded pitch deck does not have a valid PDF signature."


class IdempotencyConflictError(IntakeServiceError):
    code = "idempotency_conflict"
    safe_message = "The idempotency key was already used for different content."


class ArtifactStorageError(IntakeServiceError):
    code = "artifact_storage_failed"
    safe_message = "The private source artifact could not be stored safely."


class IntakePersistenceError(IntakeServiceError):
    code = "intake_persistence_failed"
    safe_message = "The intake state could not be persisted safely."


class ApplicationNotFoundError(IntakeServiceError):
    code = "application_not_found"
    safe_message = "The requested Application is not available."


class DeckExtractionFailedError(IntakeServiceError):
    code = "deck_extraction_failed"
    safe_message = "The stored pitch deck could not be extracted safely."


class ConsentRequiredError(IntakeServiceError):
    code = "artifact_consent_required"
    safe_message = "Confirmed consent is required for this focused artifact."


class InvalidFocusedArtifactError(IntakeServiceError, ValueError):
    code = "invalid_focused_artifact"
    safe_message = "The focused artifact is invalid or exceeds its configured limit."


def _unknown_text() -> KnowledgeValue[str]:
    return KnowledgeValue[str].unknown("not_provided_at_intake")


def _unknown_id() -> KnowledgeValue[StableId]:
    return KnowledgeValue[StableId].unknown("not_established_at_intake")


def _pending_extraction() -> KnowledgeValue[PdfExtractionResult]:
    return KnowledgeValue[PdfExtractionResult].unknown("deck_extraction_pending")


class ExtractionAttemptStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"


class PdfExtractionAttempt(DomainModel):
    """Safe attempt metadata; provider bodies and private content are never retained."""

    status: ExtractionAttemptStatus
    safe_code: NonBlankStr
    attempted_at: UTCDateTime


class ApplicationOptionalValues(DomainModel):
    """Optional intake values remain explicit Unknowns, never empty defaults."""

    founder_id: KnowledgeValue[StableId] = Field(default_factory=_unknown_id)
    founder_name: KnowledgeValue[str] = Field(default_factory=_unknown_text)
    founder_email: KnowledgeValue[str] = Field(default_factory=_unknown_text)
    sector: KnowledgeValue[str] = Field(default_factory=_unknown_text)
    stage: KnowledgeValue[str] = Field(default_factory=_unknown_text)
    geography: KnowledgeValue[str] = Field(default_factory=_unknown_text)
    traction: KnowledgeValue[str] = Field(default_factory=_unknown_text)
    financing: KnowledgeValue[str] = Field(default_factory=_unknown_text)


class ApplicationIntakeRecord(DomainModel):
    """Repository-owned intake state; every revision remains immutable."""

    schema_version: Literal["application-intake.v0"] = APPLICATION_INTAKE_SCHEMA_VERSION
    application_id: StableId
    company_id: StableId
    run_id: StableId
    company_name: NonBlankStr
    idempotency_key_sha256: Sha256Hex
    request_fingerprint: Sha256Hex
    source_artifact: SourceArtifact
    deck_size_bytes: PositiveInt
    received_at: UTCDateTime
    artifact_stored: bool = False
    extraction: KnowledgeValue[PdfExtractionResult] = Field(default_factory=_pending_extraction)
    extraction_attempts: tuple[PdfExtractionAttempt, ...] = ()
    optional_values: ApplicationOptionalValues = Field(default_factory=ApplicationOptionalValues)


class ApplicationReservation(DomainModel):
    """Result of one atomic key reservation at the persistence boundary."""

    record: ApplicationIntakeRecord
    created: bool


class FocusedArtifactKind(StrEnum):
    INTERVIEW = "interview"
    FOLLOW_UP = "follow_up"


class SpeakerRole(StrEnum):
    FOUNDER = "founder"
    COFOUNDER = "cofounder"
    TEAM_MEMBER = "team_member"
    INVESTOR = "investor"
    OTHER = "other"


class FocusedArtifactSpeaker(DomainModel):
    display_name: NonBlankStr
    role: SpeakerRole
    subject_id: KnowledgeValue[StableId]


class FocusedArtifactConsent(DomainModel):
    confirmed: bool
    confirmed_at: UTCDateTime | None = None
    reference: NonBlankStr | None = None

    @model_validator(mode="after")
    def confirmed_consent_has_provenance(self) -> Self:
        if self.confirmed and (self.confirmed_at is None or self.reference is None):
            raise ValueError("confirmed consent requires time and reference")
        return self


class FocusedArtifactLocator(DomainModel):
    locator: SourceLocator
    affected_claim_ids: Annotated[tuple[StableId, ...], Field(min_length=1)]


class FocusedArtifactSubmission(DomainModel):
    idempotency_key: NonBlankStr
    subject: SubjectRef
    kind: FocusedArtifactKind
    display_name: NonBlankStr
    media_type: NonBlankStr
    content: bytes
    speaker: FocusedArtifactSpeaker
    consent: FocusedArtifactConsent
    classification: DataClassification
    source_event_time: KnowledgeValue[UTCDateTime]
    locators: Annotated[tuple[FocusedArtifactLocator, ...], Field(min_length=1)]


class FocusedArtifactRecord(DomainModel):
    schema_version: Literal["focused-artifact.v0"] = FOCUSED_ARTIFACT_SCHEMA_VERSION
    import_id: StableId
    idempotency_key_sha256: Sha256Hex
    request_fingerprint: Sha256Hex
    subject: SubjectRef
    kind: FocusedArtifactKind
    source_artifact: SourceArtifact
    content_size_bytes: PositiveInt
    speaker: FocusedArtifactSpeaker
    consent: FocusedArtifactConsent
    locators: Annotated[tuple[FocusedArtifactLocator, ...], Field(min_length=1)]
    received_at: UTCDateTime
    artifact_stored: bool = False


class FocusedArtifactReservation(DomainModel):
    record: FocusedArtifactRecord
    created: bool


@runtime_checkable
class IntakeRepository(Protocol):
    """Atomic idempotency and immutable-state seam implemented by infrastructure."""

    def reserve_application(self, record: ApplicationIntakeRecord) -> ApplicationReservation:
        """Atomically create, replay, or raise IdempotencyConflictError."""
        ...

    def get_application(self, application_id: str) -> ApplicationIntakeRecord | None: ...

    def mark_application_artifact_stored(
        self,
        application_id: str,
        content_sha256: str,
    ) -> ApplicationIntakeRecord: ...

    def record_application_extraction(
        self,
        application_id: str,
        extraction: PdfExtractionResult,
    ) -> ApplicationIntakeRecord: ...

    def record_application_extraction_failure(
        self,
        application_id: str,
        *,
        status: ExtractionAttemptStatus,
        safe_code: str,
        attempted_at: datetime,
    ) -> ApplicationIntakeRecord: ...

    def reserve_focused_artifact(
        self,
        record: FocusedArtifactRecord,
    ) -> FocusedArtifactReservation:
        """Atomically create, replay, or raise IdempotencyConflictError."""
        ...

    def mark_focused_artifact_stored(
        self,
        import_id: str,
        content_sha256: str,
    ) -> FocusedArtifactRecord: ...


@runtime_checkable
class PrivateArtifactStorePort(Protocol):
    """Private bytes are addressed only by server-generated opaque identifiers."""

    def put(
        self,
        artifact_id: str,
        content: bytes,
        *,
        expected_sha256: str,
    ) -> object: ...

    def read(
        self,
        artifact_id: str,
        *,
        principal_id: str,
        expected_sha256: str,
    ) -> bytes: ...


class IntakeClock(Protocol):
    def __call__(self) -> datetime: ...


class IntakeIdFactory(Protocol):
    def __call__(self, prefix: str) -> str: ...


def normalize_company_name(value: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFC", value).split())
    if not normalized or len(normalized) > _COMPANY_NAME_MAX_LENGTH:
        raise InvalidCompanyNameError
    if any(unicodedata.category(character).startswith("C") for character in normalized):
        raise InvalidCompanyNameError
    return normalized


def normalize_display_name(value: str) -> str:
    leaf = re.split(r"[\\/]", unicodedata.normalize("NFC", value))[-1]
    printable = "".join(
        character for character in leaf if not unicodedata.category(character).startswith("C")
    )
    normalized = " ".join(printable.split())
    if normalized in {"", ".", ".."} or len(normalized) > _DISPLAY_NAME_MAX_LENGTH:
        raise InvalidDisplayNameError
    return normalized


def _normalize_idempotency_key(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized or len(normalized) > _IDEMPOTENCY_KEY_MAX_LENGTH:
        raise InvalidIdempotencyKeyError
    if any(unicodedata.category(character).startswith("C") for character in normalized):
        raise InvalidIdempotencyKeyError
    return normalized


def _normalize_media_type(value: str, *, pdf_only: bool) -> str:
    normalized = value.strip().lower()
    if pdf_only and normalized != _PDF_MEDIA_TYPE:
        raise UnsupportedDeckMediaTypeError
    if not normalized or "/" not in normalized or len(normalized) > 127:
        raise InvalidFocusedArtifactError
    return normalized


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _fingerprint(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return _sha256(encoded)


class ApplicationIntakeService:
    """Accept minimum Applications and extract only privately stored deck bytes."""

    def __init__(
        self,
        *,
        repository: IntakeRepository,
        artifact_store: PrivateArtifactStorePort,
        extractor: PdfExtractor,
        clock: IntakeClock,
        id_factory: IntakeIdFactory,
        max_pdf_bytes: int,
        extraction_principal_id: str = "system:pdf-extraction",
        max_focused_artifact_bytes: int = 1_000_000,
    ) -> None:
        if max_pdf_bytes <= 0 or max_focused_artifact_bytes <= 0:
            raise ValueError("artifact size limits must be positive")
        self._repository = repository
        self._artifact_store = artifact_store
        self._extractor = extractor
        self._clock = clock
        self._id_factory = id_factory
        self._max_pdf_bytes = max_pdf_bytes
        self._max_focused_artifact_bytes = max_focused_artifact_bytes
        self._extraction_principal_id = extraction_principal_id

    async def submit(self, submission: IntakeSubmission) -> AcceptedApplication:
        """Validate before persistence and atomically reserve or replay one Application."""

        company_name = normalize_company_name(submission.company_name)
        display_name = normalize_display_name(submission.display_name)
        media_type = _normalize_media_type(submission.media_type, pdf_only=True)
        idempotency_key = _normalize_idempotency_key(submission.idempotency_key)
        content = submission.deck_content
        if len(content) > self._max_pdf_bytes:
            raise DeckTooLargeError
        if not content.startswith(_PDF_MAGIC):
            raise InvalidPdfSignatureError

        content_sha256 = _sha256(content)
        request_fingerprint = _fingerprint(
            {
                "schema": APPLICATION_INTAKE_SCHEMA_VERSION,
                "company_name": company_name.casefold(),
                "media_type": media_type,
                "content_sha256": content_sha256,
            }
        )
        received_at = self._clock()
        source_artifact_id = self._id_factory("source-artifact")
        candidate = ApplicationIntakeRecord(
            application_id=self._id_factory("application"),
            company_id=self._id_factory("company"),
            run_id=self._id_factory("run"),
            company_name=company_name,
            idempotency_key_sha256=_sha256(idempotency_key.encode()),
            request_fingerprint=request_fingerprint,
            source_artifact=SourceArtifact(
                source_artifact_id=source_artifact_id,
                artifact_series_id=self._id_factory("artifact-series"),
                artifact_version_id=self._id_factory("artifact-version"),
                version_number=1,
                kind=SourceArtifactKind.DOCUMENT,
                source_category=SourceCategory.APPLICATION_DECK,
                classification=DataClassification.FOUNDER_PRIVATE,
                origin_locator=f"private-artifact:{source_artifact_id}",
                display_name=display_name,
                media_type=media_type,
                content_sha256=content_sha256,
                retrieved_at=received_at,
                source_event_time=KnowledgeValue[UTCDateTime].unknown(
                    "uploaded deck has no separate source event time"
                ),
            ),
            deck_size_bytes=len(content),
            received_at=received_at,
        )
        try:
            reservation = self._repository.reserve_application(candidate)
        except IdempotencyConflictError:
            raise
        except Exception as error:
            raise IntakePersistenceError from error
        record = reservation.record
        if record.request_fingerprint != request_fingerprint:
            raise IdempotencyConflictError

        if not record.artifact_stored:
            try:
                self._artifact_store.put(
                    record.source_artifact.source_artifact_id,
                    content,
                    expected_sha256=record.source_artifact.content_sha256,
                )
            except Exception as error:
                raise ArtifactStorageError from error
            try:
                record = self._repository.mark_application_artifact_stored(
                    record.application_id,
                    record.source_artifact.content_sha256,
                )
            except Exception as error:
                raise IntakePersistenceError from error

        return AcceptedApplication(
            application_id=record.application_id,
            company_id=record.company_id,
            run_id=record.run_id,
            source_artifact_id=record.source_artifact.source_artifact_id,
            source_artifact_sha256=record.source_artifact.content_sha256,
            received_at=record.received_at,
            replayed=not reservation.created,
        )

    async def extract_deck(self, application_id: str) -> PdfExtractionResult:
        """Extract only after the original private artifact has been stored."""

        try:
            record = self._repository.get_application(application_id)
        except Exception as error:
            raise IntakePersistenceError from error
        if record is None:
            raise ApplicationNotFoundError
        if record.extraction.value is not None:
            return record.extraction.value
        if not record.artifact_stored:
            raise ArtifactStorageError

        artifact = record.source_artifact
        try:
            content = self._artifact_store.read(
                artifact.source_artifact_id,
                principal_id=self._extraction_principal_id,
                expected_sha256=artifact.content_sha256,
            )
            if (
                len(content) != record.deck_size_bytes
                or _sha256(content) != artifact.content_sha256
            ):
                raise ArtifactStorageError
        except IntakeServiceError:
            raise
        except Exception as error:
            raise ArtifactStorageError from error

        attempted_at = self._clock()
        try:
            extraction = await self._extractor.extract(
                PdfExtractionRequest(
                    source_artifact_id=artifact.source_artifact_id,
                    input_sha256=artifact.content_sha256,
                    content=content,
                    classification=artifact.classification,
                    requested_at=attempted_at,
                )
            )
            if (
                extraction.source_artifact_id != artifact.source_artifact_id
                or extraction.input_sha256 != artifact.content_sha256
            ):
                raise PdfExtractionError
        except Exception as error:
            attempt_status = (
                ExtractionAttemptStatus.BLOCKED
                if isinstance(error, PdfExtractionBlockedError)
                else ExtractionAttemptStatus.FAILED
            )
            safe_code = (
                error.code if isinstance(error, PdfExtractionError) else "deck_extraction_failed"
            )
            try:
                self._repository.record_application_extraction_failure(
                    application_id,
                    status=attempt_status,
                    safe_code=safe_code,
                    attempted_at=attempted_at,
                )
            except Exception as persistence_error:
                raise IntakePersistenceError from persistence_error
            raise DeckExtractionFailedError from error

        try:
            self._repository.record_application_extraction(application_id, extraction)
        except Exception as error:
            raise IntakePersistenceError from error
        return extraction

    async def import_focused_artifact(
        self,
        submission: FocusedArtifactSubmission,
    ) -> FocusedArtifactRecord:
        """Import one consented interview or follow-up with exact source locators."""

        if not submission.consent.confirmed:
            raise ConsentRequiredError
        if not submission.content or len(submission.content) > self._max_focused_artifact_bytes:
            raise InvalidFocusedArtifactError

        idempotency_key = _normalize_idempotency_key(submission.idempotency_key)
        display_name = normalize_display_name(submission.display_name)
        media_type = _normalize_media_type(submission.media_type, pdf_only=False)
        speaker = submission.speaker.model_copy(
            update={"display_name": normalize_company_name(submission.speaker.display_name)}
        )
        permitted_locator_kinds = (
            {SourceLocatorKind.INTERVIEW_SEGMENT}
            if submission.kind is FocusedArtifactKind.INTERVIEW
            else {SourceLocatorKind.INTERVIEW_SEGMENT, SourceLocatorKind.SOURCE_RECORD}
        )
        locator_keys = tuple(
            (item.locator.kind, item.locator.locator) for item in submission.locators
        )
        if any(
            item.locator.kind not in permitted_locator_kinds for item in submission.locators
        ) or len(locator_keys) != len(set(locator_keys)):
            raise InvalidFocusedArtifactError

        content_sha256 = _sha256(submission.content)
        request_fingerprint = _fingerprint(
            {
                "schema": FOCUSED_ARTIFACT_SCHEMA_VERSION,
                "subject": submission.subject.model_dump(mode="json"),
                "kind": submission.kind.value,
                "display_name": display_name,
                "media_type": media_type,
                "content_sha256": content_sha256,
                "speaker": speaker.model_dump(mode="json"),
                "consent": submission.consent.model_dump(mode="json"),
                "classification": submission.classification.value,
                "source_event_time": submission.source_event_time.model_dump(mode="json"),
                "locators": [item.model_dump(mode="json") for item in submission.locators],
            }
        )
        received_at = self._clock()
        source_artifact_id = self._id_factory("source-artifact")
        source_category = (
            SourceCategory.INTERVIEW
            if submission.kind is FocusedArtifactKind.INTERVIEW
            else SourceCategory.FOLLOW_UP
        )
        artifact_kind = (
            SourceArtifactKind.INTERVIEW_TRANSCRIPT
            if submission.kind is FocusedArtifactKind.INTERVIEW
            else SourceArtifactKind.STRUCTURED_IMPORT
        )
        candidate = FocusedArtifactRecord(
            import_id=self._id_factory("focused-import"),
            idempotency_key_sha256=_sha256(idempotency_key.encode()),
            request_fingerprint=request_fingerprint,
            subject=submission.subject,
            kind=submission.kind,
            source_artifact=SourceArtifact(
                source_artifact_id=source_artifact_id,
                artifact_series_id=self._id_factory("artifact-series"),
                artifact_version_id=self._id_factory("artifact-version"),
                version_number=1,
                kind=artifact_kind,
                source_category=source_category,
                classification=submission.classification,
                origin_locator=f"private-artifact:{source_artifact_id}",
                display_name=display_name,
                media_type=media_type,
                content_sha256=content_sha256,
                retrieved_at=received_at,
                source_event_time=submission.source_event_time,
            ),
            content_size_bytes=len(submission.content),
            speaker=speaker,
            consent=submission.consent,
            locators=submission.locators,
            received_at=received_at,
        )
        try:
            reservation = self._repository.reserve_focused_artifact(candidate)
        except IdempotencyConflictError:
            raise
        except Exception as error:
            raise IntakePersistenceError from error
        record = reservation.record
        if record.request_fingerprint != request_fingerprint:
            raise IdempotencyConflictError
        if not record.artifact_stored:
            try:
                self._artifact_store.put(
                    record.source_artifact.source_artifact_id,
                    submission.content,
                    expected_sha256=record.source_artifact.content_sha256,
                )
            except Exception as error:
                raise ArtifactStorageError from error
            try:
                record = self._repository.mark_focused_artifact_stored(
                    record.import_id,
                    record.source_artifact.content_sha256,
                )
            except Exception as error:
                raise IntakePersistenceError from error
        return record
