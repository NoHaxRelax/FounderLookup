"""Provider-neutral bounded discovery and content-acquisition contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final, Literal, Self

from pydantic import Field, model_validator

from founderlookup.domain.common import (
    DomainModel,
    KnowledgeValue,
    LongText,
    NonBlankStr,
    NonNegativeInt,
    PositiveInt,
    StableId,
    UTCDateTime,
)
from founderlookup.domain.evidence import DataClassification, Sha256Hex, SourceCategory

DISCOVERY_SCHEMA_VERSION: Final = "discovery.v0"
ACQUISITION_SCHEMA_VERSION: Final = "acquisition.v0"


class BoundedRetrievalRequest(DomainModel):
    """Inspectable original-source query with explicit resource budgets."""

    retrieval_request_id: StableId
    query: NonBlankStr
    source_categories: Annotated[tuple[SourceCategory, ...], Field(min_length=1)]
    allowed_domains: tuple[NonBlankStr, ...] = ()
    excluded_domains: tuple[NonBlankStr, ...] = ()
    published_after: UTCDateTime | None = None
    published_before: UTCDateTime | None = None
    max_results: PositiveInt
    max_pages: PositiveInt
    timeout_seconds: PositiveInt

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if set(self.allowed_domains) & set(self.excluded_domains):
            raise ValueError("a domain cannot be both allowed and excluded")
        if (
            self.published_after is not None
            and self.published_before is not None
            and self.published_after > self.published_before
        ):
            raise ValueError("published_after cannot be later than published_before")
        return self


class DiscoveryRequest(DomainModel):
    schema_version: Literal["discovery.v0"] = DISCOVERY_SCHEMA_VERSION
    request_id: StableId
    query_plan_id: StableId
    requested_at: UTCDateTime
    retrieval_requests: Annotated[tuple[BoundedRetrievalRequest, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def reject_duplicate_retrieval_ids(self) -> Self:
        identifiers = tuple(item.retrieval_request_id for item in self.retrieval_requests)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("retrieval request identifiers must be unique")
        return self


class DiscoveryLead(DomainModel):
    """A provider result is a lead, never primary Evidence by itself."""

    lead_id: StableId
    retrieval_request_id: StableId
    original_url: NonBlankStr
    source_category: SourceCategory
    discovered_at: UTCDateTime
    rank: PositiveInt
    title: KnowledgeValue[str]
    provider_summary: KnowledgeValue[LongText]
    retrieval_relevance: KnowledgeValue[float]


class CollectionFailure(DomainModel):
    operation_id: StableId
    safe_code: NonBlankStr
    safe_message: NonBlankStr
    retryable: bool


class ProviderUsage(DomainModel):
    """Operational metadata; it has no score or trust semantics."""

    adapter_id: StableId
    operation_id: StableId
    request_count: NonNegativeInt
    result_count: NonNegativeInt
    elapsed_milliseconds: NonNegativeInt
    cost_amount: KnowledgeValue[float]
    cost_currency: KnowledgeValue[str]


class CollectionResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    FAILED = "failed"


class DiscoveryResult(DomainModel):
    schema_version: Literal["discovery.v0"] = DISCOVERY_SCHEMA_VERSION
    result_id: StableId
    request_id: StableId
    status: CollectionResultStatus
    completed_at: UTCDateTime
    leads: tuple[DiscoveryLead, ...] = ()
    failures: tuple[CollectionFailure, ...] = ()
    usage: ProviderUsage

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status is CollectionResultStatus.SUCCEEDED and self.failures:
            raise ValueError("succeeded discovery cannot carry failures")
        if self.status is CollectionResultStatus.PARTIALLY_SUCCEEDED and (
            not self.leads or not self.failures
        ):
            raise ValueError("partial discovery requires leads and failures")
        if self.status is CollectionResultStatus.FAILED and (self.leads or not self.failures):
            raise ValueError("failed discovery requires failures and no accepted leads")
        return self


class AcquisitionRequest(DomainModel):
    schema_version: Literal["acquisition.v0"] = ACQUISITION_SCHEMA_VERSION
    acquisition_request_id: StableId
    discovery_lead_id: StableId | None = None
    original_url: NonBlankStr
    requested_at: UTCDateTime
    classification: DataClassification
    allowed_media_types: Annotated[tuple[NonBlankStr, ...], Field(min_length=1)]
    max_bytes: PositiveInt
    timeout_seconds: PositiveInt


class AcquisitionStatus(StrEnum):
    ACQUIRED = "acquired"
    NOT_MODIFIED = "not_modified"
    BLOCKED = "blocked"
    FAILED = "failed"


class AcquisitionResult(DomainModel):
    """Acquired original bytes plus source metadata, independent of any vendor type."""

    schema_version: Literal["acquisition.v0"] = ACQUISITION_SCHEMA_VERSION
    result_id: StableId
    acquisition_request_id: StableId
    original_url: NonBlankStr
    status: AcquisitionStatus
    completed_at: UTCDateTime
    content: bytes | None = None
    media_type: NonBlankStr | None = None
    content_sha256: Sha256Hex | None = None
    source_event_time: KnowledgeValue[UTCDateTime]
    failure: CollectionFailure | None = None

    @model_validator(mode="after")
    def validate_status_payload(self) -> Self:
        content_fields = (self.content, self.media_type, self.content_sha256)
        if self.status is AcquisitionStatus.ACQUIRED:
            if any(value is None for value in content_fields):
                raise ValueError("acquired content requires bytes, media type, and hash")
            if self.failure is not None:
                raise ValueError("acquired content cannot carry a failure")
        else:
            if any(value is not None for value in content_fields):
                raise ValueError("non-acquired results cannot carry content fields")
            if self.status in {AcquisitionStatus.BLOCKED, AcquisitionStatus.FAILED}:
                if self.failure is None:
                    raise ValueError("blocked or failed acquisition requires failure")
            elif self.failure is not None:
                raise ValueError("not-modified acquisition cannot carry failure")
        return self
