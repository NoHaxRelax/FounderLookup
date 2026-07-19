"""Provider-neutral, page-addressable PDF extraction contracts."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from typing import Annotated, Final, Literal, Protocol, Self, runtime_checkable

from pydantic import Field, StringConstraints, model_validator

from founderlookup.domain.common import (
    DomainModel,
    KnowledgeValue,
    NonNegativeInt,
    StableId,
    UTCDateTime,
    VersionId,
)
from founderlookup.domain.evidence import DataClassification, Sha256Hex

PDF_EXTRACTION_SCHEMA_VERSION: Final = "pdf-extraction.v0"

PdfMarkdown = Annotated[str, StringConstraints(strict=True, max_length=1_000_000)]
Confidence01 = Annotated[float, Field(strict=True, ge=0.0, le=1.0)]


class PdfExtractionError(RuntimeError):
    """Safe base error for extractor failures; source bytes never enter messages."""

    code = "pdf_extraction_failed"
    safe_message = "The pitch deck could not be extracted safely."

    def __init__(self) -> None:
        super().__init__(self.safe_message)


class PdfExtractionBlockedError(PdfExtractionError):
    """Policy or configuration prevented an external extraction attempt."""

    code = "pdf_extraction_blocked"
    safe_message = "The pitch deck extraction is blocked by the active data policy."


class MissingFakeExtractionError(PdfExtractionError):
    """The deterministic fake was not seeded for the requested content hash."""

    code = "fake_extraction_not_seeded"
    safe_message = "No deterministic extraction result is configured for this document."


class PdfPageConfidence(DomainModel):
    """Page-level confidence without inventing unavailable provider values."""

    average: KnowledgeValue[Confidence01]
    minimum: KnowledgeValue[Confidence01]


class ExtractedPdfPage(DomainModel):
    """One exact, zero-based page locator and its extracted Markdown."""

    page_index: NonNegativeInt
    locator: str
    markdown: PdfMarkdown
    confidence: PdfPageConfidence

    @model_validator(mode="after")
    def locator_matches_page(self) -> Self:
        if self.locator != f"page:{self.page_index}":
            raise ValueError("page locator must match page_index")
        return self


class PdfExtractionUsage(DomainModel):
    """Provider-neutral usage values; unavailable values stay explicitly Unknown."""

    pages_processed: KnowledgeValue[int]
    document_size_bytes: KnowledgeValue[int]


class PdfExtractionRequest(DomainModel):
    """Immutable bytes read from the private artifact store for extraction."""

    source_artifact_id: StableId
    input_sha256: Sha256Hex
    content: bytes
    media_type: Literal["application/pdf"] = "application/pdf"
    classification: DataClassification
    requested_at: UTCDateTime

    @model_validator(mode="after")
    def content_matches_hash(self) -> Self:
        if sha256(self.content).hexdigest() != self.input_sha256:
            raise ValueError("PDF content does not match input_sha256")
        return self


class PdfExtractionResult(DomainModel):
    """Accepted extractor output linked to the immutable source bytes."""

    schema_version: Literal["pdf-extraction.v0"] = PDF_EXTRACTION_SCHEMA_VERSION
    extraction_id: StableId
    source_artifact_id: StableId
    input_sha256: Sha256Hex
    extractor_version: VersionId
    model_version: KnowledgeValue[VersionId]
    extracted_at: UTCDateTime
    pages: Annotated[tuple[ExtractedPdfPage, ...], Field(min_length=1)]
    usage: PdfExtractionUsage

    @model_validator(mode="after")
    def pages_are_ordered_and_unique(self) -> Self:
        indexes = tuple(page.page_index for page in self.pages)
        if indexes != tuple(sorted(indexes)):
            raise ValueError("extracted pages must be ordered by page_index")
        if len(indexes) != len(set(indexes)):
            raise ValueError("extracted page indexes must be unique")
        return self


@runtime_checkable
class PdfExtractor(Protocol):
    """True external seam for page-addressable PDF extraction."""

    async def extract(self, request: PdfExtractionRequest) -> PdfExtractionResult:
        """Return validated pages without persisting or mutating source bytes."""
        ...


class FakePdfExtractor:
    """Deterministically replay extraction results by immutable input hash."""

    def __init__(self, responses: Mapping[str, PdfExtractionResult]) -> None:
        self._responses = dict(responses)
        self._requests: list[PdfExtractionRequest] = []

    @property
    def requests(self) -> tuple[PdfExtractionRequest, ...]:
        return tuple(self._requests)

    async def extract(self, request: PdfExtractionRequest) -> PdfExtractionResult:
        self._requests.append(request)
        try:
            result = self._responses[request.input_sha256]
        except KeyError as error:
            raise MissingFakeExtractionError from error
        if (
            result.source_artifact_id != request.source_artifact_id
            or result.input_sha256 != request.input_sha256
        ):
            raise PdfExtractionError
        return result
