"""Immutable source, observation, evidence, and claim graph contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from founderlookup.domain.common import (
    DomainModel,
    KnowledgeValue,
    LongText,
    NonBlankStr,
    PositiveInt,
    ScalarValue,
    StableId,
    SubjectRef,
    UTCDateTime,
    VersionId,
)
from founderlookup.domain.scoring import ClaimTrustScore, TrustScoreState

EVIDENCE_GRAPH_SCHEMA_VERSION = "evidence-graph.v0"
Sha256Hex = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$"),
]


class SourceCategory(StrEnum):
    APPLICATION_DECK = "application_deck"
    DEVELOPER_ACTIVITY = "developer_activity"
    PRODUCT_LAUNCH = "product_launch"
    HACKATHON = "hackathon"
    RESEARCH = "research"
    PATENT = "patent"
    ACCELERATOR = "accelerator"
    PUBLIC_SOCIAL = "public_social"
    INTERVIEW = "interview"
    FOLLOW_UP = "follow_up"
    IMPORT = "import"
    OTHER = "other"


class SourceArtifactKind(StrEnum):
    DOCUMENT = "document"
    WEB_SNAPSHOT = "web_snapshot"
    SOURCE_API_RECORD = "source_api_record"
    REPOSITORY_RECORD = "repository_record"
    INTERVIEW_TRANSCRIPT = "interview_transcript"
    STRUCTURED_IMPORT = "structured_import"


class DataClassification(StrEnum):
    PUBLIC = "public"
    FOUNDER_PRIVATE = "founder_private"
    INVESTOR_INTERNAL = "investor_internal"
    RESTRICTED = "restricted"


class ArtifactAvailability(StrEnum):
    AVAILABLE = "available"
    SOURCE_UNAVAILABLE = "source_unavailable"
    CONTENT_REMOVED = "content_removed"
    ACCESS_RESTRICTED = "access_restricted"


class SourceLocatorKind(StrEnum):
    DOCUMENT_PAGE = "document_page"
    URL_EXCERPT = "url_excerpt"
    REPOSITORY_COMMIT = "repository_commit"
    PAPER_SECTION = "paper_section"
    PATENT_SECTION = "patent_section"
    INTERVIEW_SEGMENT = "interview_segment"
    SOURCE_RECORD = "source_record"


class SourceLocator(DomainModel):
    kind: SourceLocatorKind
    locator: NonBlankStr
    excerpt: LongText | None = None


class SourceArtifact(DomainModel):
    """One immutable acquired version in a source series."""

    schema_version: Literal["evidence-graph.v0"] = EVIDENCE_GRAPH_SCHEMA_VERSION
    source_artifact_id: StableId
    artifact_series_id: StableId
    artifact_version_id: StableId
    version_number: PositiveInt
    previous_source_artifact_id: StableId | None = None
    kind: SourceArtifactKind
    source_category: SourceCategory
    classification: DataClassification
    origin_locator: NonBlankStr
    display_name: NonBlankStr
    media_type: NonBlankStr
    content_sha256: Sha256Hex
    retrieved_at: UTCDateTime
    source_event_time: KnowledgeValue[UTCDateTime]
    collection_operation_id: StableId | None = None
    availability: ArtifactAvailability = ArtifactAvailability.AVAILABLE

    @model_validator(mode="after")
    def validate_version_link(self) -> Self:
        if self.previous_source_artifact_id == self.source_artifact_id:
            raise ValueError("artifact version cannot point to itself")
        if self.version_number == 1 and self.previous_source_artifact_id is not None:
            raise ValueError("first artifact version cannot have a predecessor")
        if self.version_number > 1 and self.previous_source_artifact_id is None:
            raise ValueError("later artifact versions require a predecessor")
        return self


class ExtractionMethod(StrEnum):
    MANUAL = "manual"
    DETERMINISTIC = "deterministic"
    MODEL_ASSISTED = "model_assisted"
    STRUCTURED_IMPORT = "structured_import"


class VerificationState(StrEnum):
    SOURCE_ASSERTED = "source_asserted"
    CORROBORATED = "corroborated"
    DISPUTED = "disputed"
    UNVERIFIED = "unverified"


class Observation(DomainModel):
    """What one source said, before cross-source conflict resolution."""

    schema_version: Literal["evidence-graph.v0"] = EVIDENCE_GRAPH_SCHEMA_VERSION
    observation_id: StableId
    observation_version_id: StableId
    source_artifact_id: StableId
    subject: SubjectRef
    predicate: NonBlankStr
    observed_value: KnowledgeValue[ScalarValue]
    locator: SourceLocator
    retrieved_at: UTCDateTime
    source_event_time: KnowledgeValue[UTCDateTime]
    extraction_method: ExtractionMethod
    extraction_version: VersionId
    verification_state: VerificationState


class EvidenceStance(StrEnum):
    SUPPORTS = "supports"
    REFUTES = "refutes"
    CONTEXT = "context"


class Evidence(DomainModel):
    """A precise source locator attached to one analytical Claim."""

    schema_version: Literal["evidence-graph.v0"] = EVIDENCE_GRAPH_SCHEMA_VERSION
    evidence_id: StableId
    claim_id: StableId
    source_artifact_id: StableId
    observation_id: StableId | None = None
    stance: EvidenceStance
    locator: SourceLocator
    collected_at: UTCDateTime
    source_event_time: KnowledgeValue[UTCDateTime]
    availability: ArtifactAvailability = ArtifactAvailability.AVAILABLE


class ClaimStatus(StrEnum):
    ASSERTED_UNVERIFIED = "asserted_unverified"
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    UNSUPPORTED = "unsupported"
    UNRESOLVED = "unresolved"


class ClaimOrigin(StrEnum):
    SOURCE_ASSERTION = "source_assertion"
    DETERMINISTIC_RULE = "deterministic_rule"
    MANUAL_ANALYSIS = "manual_analysis"
    MODEL_ASSISTED = "model_assisted"


class Claim(DomainModel):
    """Versioned investor-relevant assertion with claim-level trust."""

    schema_version: Literal["evidence-graph.v0"] = EVIDENCE_GRAPH_SCHEMA_VERSION
    claim_id: StableId
    claim_version_id: StableId
    subject: SubjectRef
    predicate: NonBlankStr
    statement: LongText
    status: ClaimStatus
    origin: ClaimOrigin
    as_of: UTCDateTime
    created_at: UTCDateTime
    supporting_evidence_ids: tuple[StableId, ...] = ()
    counter_evidence_ids: tuple[StableId, ...] = ()
    trust: ClaimTrustScore

    @model_validator(mode="after")
    def validate_evidence_shape(self) -> Self:
        supporting = set(self.supporting_evidence_ids)
        counter = set(self.counter_evidence_ids)
        if supporting & counter:
            raise ValueError("the same evidence cannot both support and refute a claim")
        if self.status is ClaimStatus.SUPPORTED and not supporting:
            raise ValueError("supported claims require supporting evidence")
        if self.status is ClaimStatus.CONTRADICTED and (not supporting or not counter):
            raise ValueError("contradicted claims require supporting and counter evidence")
        if self.status is ClaimStatus.UNSUPPORTED:
            if supporting:
                raise ValueError("unsupported claims cannot carry supporting evidence")
            if self.trust.state is not TrustScoreState.UNSUPPORTED:
                raise ValueError("unsupported claims require unsupported trust state")
        return self
