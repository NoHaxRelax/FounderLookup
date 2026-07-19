"""Provider-neutral orchestration for bounded, multi-adapter outbound sourcing."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Literal, Protocol, TypedDict, cast, runtime_checkable
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from langgraph.graph import END, START, StateGraph
from pydantic import Field, StringConstraints, model_validator

from founderlookup.application.models import (
    OutboundCandidateView,
    PublicContactRouteKind,
    PublicContactRouteView,
    RunAccepted,
    SourcingLoopAuditStatus,
    SourcingLoopAuditView,
)
from founderlookup.application.screening_bridge import (
    DeterministicScreeningBridge,
    ScreeningSignalBundle,
)
from founderlookup.application.service import FakeVCBrainService
from founderlookup.domain.common import (
    DomainModel,
    EntityKind,
    KnowledgeValue,
    SubjectRef,
    UTCDateTime,
)
from founderlookup.domain.discovery import (
    AcquisitionRequest,
    AcquisitionResult,
    AcquisitionStatus,
    BoundedRetrievalRequest,
    CollectionFailure,
    DiscoveryLead,
    DiscoveryRequest,
    DiscoveryResult,
)
from founderlookup.domain.evidence import (
    ArtifactAvailability,
    DataClassification,
    SourceArtifact,
    SourceArtifactKind,
    SourceCategory,
)
from founderlookup.domain.lifecycles import PipelineRunStatus, PipelineStageStatus
from founderlookup.domain.runs import PipelineFailure, PipelineRun, PipelineStage
from founderlookup.domain.scoring import CoverageLevel, CoverageSummary
from founderlookup.infrastructure.artifacts import PrivateArtifactStore
from founderlookup.infrastructure.persistence import NewRecord, RecordCategory, SQLiteMemory
from founderlookup.ingestion.extraction import (
    PdfExtractionError,
    PdfExtractionRequest,
    PdfExtractionResult,
    PdfExtractor,
)
from founderlookup.ingestion.hackathons import (
    HACKATHON_PROJECTION_VERSION,
    HackathonLinkKind,
    HackathonShowcaseProjection,
    PublicHackathonDeckRelationship,
    PublicHackathonLink,
    link_public_hackathon_deck,
    project_hackathon_showcase,
)
from founderlookup.ingestion.openai_structured import (
    OPENAI_STRUCTURED_ADAPTER_ID,
    PublicContactEvidenceProjection,
    PublicContactProjection,
    PublicPageStructuredExtractorPort,
    PublicPageStructuredRequest,
    StructuredExtractionStatus,
    project_public_contact_evidence,
)
from founderlookup.ingestion.policy import (
    PublicSourceCollectionPolicy,
    PublicSourcePolicyRecord,
    project_public_source_policy,
)
from founderlookup.ingestion.ports import AcquisitionPort, DiscoveryPort
from founderlookup.ingestion.projections import (
    PublicSourceEvidenceProjection,
    project_public_source_evidence,
)

_QUERY = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=400, strip_whitespace=True),
]
_STAGE_DISCOVER = "discover_original_sources"
_STAGE_ACQUIRE = "acquire_original_content"
_STAGE_CANONICALIZE = "canonicalize_candidates"
_STAGE_KEYS = (_STAGE_DISCOVER, _STAGE_ACQUIRE, _STAGE_CANONICALIZE)
_STABLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_FAILURES = 64
_PROHIBITED_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")
_OUTBOUND_LOOP_VERSION = "outbound-convergence-loop.v0"
_PUBLIC_DECK_OCR_VERSION = "public-deck-ocr.v0"
_GOOGLE_SLIDES_DECK = re.compile(
    r"^/presentation/d/(?P<document_id>[A-Za-z0-9_-]{10,256})/"
    r"(?:edit|view|present|export/pdf)/?$"
)
_PUBLIC_PDF_MAX_BYTES = 10 * 1024 * 1024
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class BoundedSourcingCommand(DomainModel):
    """One provider-neutral, investor-authored sourcing request."""

    query: _QUERY
    source_categories: Annotated[tuple[SourceCategory, ...], Field(min_length=1)]
    allowed_domains: tuple[str, ...] = ()
    excluded_domains: tuple[str, ...] = ()
    max_results: Annotated[int, Field(strict=True, gt=0, le=20)] = 10
    max_pages: Annotated[int, Field(strict=True, gt=0, le=20)] = 5
    max_bytes: Annotated[int, Field(strict=True, gt=0, le=5_000_000)] = 500_000
    timeout_seconds: Annotated[int, Field(strict=True, gt=0, le=60)] = 20

    @model_validator(mode="after")
    def validate_page_budget(self) -> BoundedSourcingCommand:
        if self.max_pages > self.max_results:
            raise ValueError("max_pages cannot exceed max_results")
        if len(self.source_categories) != len(set(self.source_categories)):
            raise ValueError("source categories must be unique")
        return self


class SourcingUnavailableError(RuntimeError):
    """Live sourcing is disabled or lacks approved server-side configuration."""


@runtime_checkable
class SourcingCoordinatorPort(Protocol):
    """Queue quickly, then execute through FastAPI's background runner."""

    def enqueue(self, command: BoundedSourcingCommand) -> RunAccepted: ...

    async def execute(self, run_id: str, command: BoundedSourcingCommand) -> None: ...


class UnavailableSourcingCoordinator:
    """Explicit fail-closed runtime boundary; never falls back to claimed live work."""

    def enqueue(self, _command: BoundedSourcingCommand) -> RunAccepted:
        raise SourcingUnavailableError

    async def execute(self, _run_id: str, _command: BoundedSourcingCommand) -> None:
        raise SourcingUnavailableError


@dataclass(frozen=True, slots=True)
class SourceAdapterBinding:
    """Composition record for one generic or category-specific public source adapter.

    ``source_categories=None`` denotes the one selected generic provider. Category-specific
    bindings are authoritative for their own public records and win URL deduplication over a
    generic discovery lead. This is routing metadata, not a domain or Trust score.
    """

    adapter_id: str
    discovery: DiscoveryPort
    acquisition: AcquisitionPort
    source_categories: tuple[SourceCategory, ...] | None
    authoritative: bool
    artifact_kind: SourceArtifactKind
    allowed_media_types: tuple[str, ...]
    policy: PublicSourceCollectionPolicy

    def __post_init__(self) -> None:
        if _STABLE_ID.fullmatch(self.adapter_id) is None:
            raise ValueError("adapter_id must be a stable opaque identifier")
        if not isinstance(self.discovery, DiscoveryPort):
            raise TypeError("discovery must conform to DiscoveryPort")
        if not isinstance(self.acquisition, AcquisitionPort):
            raise TypeError("acquisition must conform to AcquisitionPort")
        if self.source_categories is not None:
            if not self.source_categories:
                raise ValueError("source-specific adapter categories cannot be empty")
            if len(self.source_categories) != len(set(self.source_categories)):
                raise ValueError("source-specific adapter categories must be unique")
        elif self.authoritative:
            raise ValueError("a generic discovery provider cannot be authoritative evidence")
        normalized_media = tuple(
            item.split(";", 1)[0].strip().casefold() for item in self.allowed_media_types
        )
        if not normalized_media or any(not item or "/" not in item for item in normalized_media):
            raise ValueError("adapter media types must be non-empty MIME types")
        if len(normalized_media) != len(set(normalized_media)):
            raise ValueError("adapter media types must be unique")
        object.__setattr__(self, "allowed_media_types", normalized_media)

    @property
    def is_generic(self) -> bool:
        return self.source_categories is None

    def categories_for(
        self,
        requested: tuple[SourceCategory, ...],
    ) -> tuple[SourceCategory, ...]:
        if self.source_categories is None:
            return requested
        supported = set(self.source_categories)
        return tuple(category for category in requested if category in supported)


class OutboundSearchStopReason(StrEnum):
    SUFFICIENT_EVIDENCE = "sufficient_evidence"
    NO_NEW_EVIDENCE = "no_new_evidence"
    ROUND_BUDGET_EXHAUSTED = "round_budget_exhausted"
    CALL_BUDGET_EXHAUSTED = "call_budget_exhausted"
    PAGE_BUDGET_EXHAUSTED = "page_budget_exhausted"
    PARTIAL_FAILURE = "partial_failure"


class OutboundSearchRoundAudit(DomainModel):
    round_index: Annotated[int, Field(strict=True, ge=0, le=3)]
    query: _QUERY
    discovery_call_count: Annotated[int, Field(strict=True, ge=0, le=32)]
    discovered_lead_count: Annotated[int, Field(strict=True, ge=0, le=20)]
    acquired_page_count: Annotated[int, Field(strict=True, ge=0, le=20)]
    new_evidence_count: Annotated[int, Field(strict=True, ge=0)]
    evidence_gaps: tuple[str, ...]
    partial_failure: bool


class OutboundSearchLoopAudit(DomainModel):
    record_type: Literal["outbound_search_loop"] = "outbound_search_loop"
    loop_version: Literal["outbound-convergence-loop.v0"] = "outbound-convergence-loop.v0"
    record_id: Annotated[
        str,
        StringConstraints(strict=True, min_length=1, max_length=128),
    ]
    run_id: Annotated[
        str,
        StringConstraints(strict=True, min_length=1, max_length=128),
    ]
    rounds: Annotated[tuple[OutboundSearchRoundAudit, ...], Field(min_length=1, max_length=4)]
    stop_reason: OutboundSearchStopReason
    maximum_follow_up_rounds: Annotated[int, Field(strict=True, ge=0, le=3)]
    maximum_discovery_calls: Annotated[int, Field(strict=True, ge=1, le=32)]
    candidate_activation: Literal["human_controlled"] = "human_controlled"
    outreach_action: Literal["none"] = "none"


class PublicDeckOcrRecord(DomainModel):
    """Immutable public-deck OCR result or explicit safe Unknown fallback."""

    record_type: Literal["public_deck_ocr"] = "public_deck_ocr"
    projection_version: Literal["public-deck-ocr.v0"] = "public-deck-ocr.v0"
    record_id: Annotated[
        str,
        StringConstraints(strict=True, min_length=1, max_length=128),
    ]
    record_version_id: Annotated[
        str,
        StringConstraints(strict=True, min_length=1, max_length=128),
    ]
    source_artifact_id: Annotated[
        str,
        StringConstraints(strict=True, min_length=1, max_length=128),
    ]
    state: Literal["known", "unknown"]
    extraction: PdfExtractionResult | None
    safe_code: (
        Annotated[
            str,
            StringConstraints(strict=True, min_length=1, max_length=128),
        ]
        | None
    )
    attempted_at: UTCDateTime

    @model_validator(mode="after")
    def preserve_known_unknown_shape(self) -> PublicDeckOcrRecord:
        if self.state == "known":
            if self.extraction is None or self.safe_code is not None:
                raise ValueError("known public-deck OCR requires only an extraction")
        elif self.extraction is not None or self.safe_code is None:
            raise ValueError("unknown public-deck OCR requires only a safe code")
        return self


@dataclass(frozen=True, slots=True)
class PublicDeckPdfTarget:
    """An explicit public deck URL and the HTTPS PDF URL safe to acquire."""

    source_url: str
    acquisition_url: str
    normalization: Literal["direct_pdf", "google_slides_export_pdf"]


@dataclass(frozen=True, slots=True)
class PublicPdfAcquisitionPolicy:
    """Server-owned ceilings and host allowlist for direct public PDF bytes."""

    allowed_domains: tuple[str, ...]
    excluded_domains: tuple[str, ...] = ()
    max_bytes: int = _PUBLIC_PDF_MAX_BYTES
    timeout_seconds: float = 20.0
    max_redirects: int = 5

    def __post_init__(self) -> None:
        allowed = tuple(_normalize_policy_domain(item) for item in self.allowed_domains)
        excluded = tuple(_normalize_policy_domain(item) for item in self.excluded_domains)
        if not allowed:
            raise ValueError("public PDF acquisition requires an explicit domain allowlist")
        if set(allowed) & set(excluded):
            raise ValueError("a public PDF domain cannot be both allowed and excluded")
        if not 0 < self.max_bytes <= _PUBLIC_PDF_MAX_BYTES:
            raise ValueError("public PDF byte ceiling must be between one byte and 10 MiB")
        if not 0 < self.timeout_seconds <= 60:
            raise ValueError("public PDF timeout must be between zero and 60 seconds")
        if not 0 <= self.max_redirects <= 5:
            raise ValueError("public PDF redirects must be between zero and five")
        object.__setattr__(self, "allowed_domains", tuple(dict.fromkeys(allowed)))
        object.__setattr__(self, "excluded_domains", tuple(dict.fromkeys(excluded)))


class BoundedPublicPdfAcquisition:
    """Acquire only allowlisted HTTPS PDF bytes, following at most five safe redirects."""

    adapter_id = "bounded-public-pdf-v0"

    def __init__(
        self,
        *,
        policy: PublicPdfAcquisitionPolicy,
        now: Callable[[], UTCDateTime],
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._policy = policy
        self._now = now
        self._client = client

    @asynccontextmanager
    async def _client_scope(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._client is not None:
            yield self._client
            return
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
            yield client

    async def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        operation_id = f"{self.adapter_id}:acquire:{request.acquisition_request_id}"
        if request.classification is not DataClassification.PUBLIC:
            return self._failure(
                request,
                operation_id,
                "classification_blocked",
                "Public PDF acquisition accepts public content only",
                retryable=False,
            )
        if "application/pdf" not in {
            item.split(";", 1)[0].strip().casefold() for item in request.allowed_media_types
        }:
            return self._failure(
                request,
                operation_id,
                "media_type_blocked",
                "Public PDF acquisition requires an application/pdf policy",
                retryable=False,
            )
        try:
            target = resolve_public_deck_pdf_url(request.original_url)
            current_url, host = _normalize_public_url(target.acquisition_url)
        except ValueError:
            return self._failure(
                request,
                operation_id,
                "unsafe_public_pdf_url",
                "The public PDF URL was rejected by source policy",
                retryable=False,
            )
        if not self._domain_allowed(host):
            return self._failure(
                request,
                operation_id,
                "domain_policy_rejected",
                "The public PDF URL is outside the explicit domain allowlist",
                retryable=False,
            )

        maximum = min(request.max_bytes, self._policy.max_bytes)
        timeout = min(float(request.timeout_seconds), self._policy.timeout_seconds)
        try:
            async with self._client_scope() as client:
                for redirect_count in range(self._policy.max_redirects + 1):
                    async with client.stream(
                        "GET",
                        current_url,
                        headers={"accept": "application/pdf"},
                        timeout=timeout,
                        follow_redirects=False,
                    ) as response:
                        if response.status_code in _REDIRECT_STATUSES:
                            location = response.headers.get("location")
                            if location is None or redirect_count >= self._policy.max_redirects:
                                return self._failure(
                                    request,
                                    operation_id,
                                    "public_pdf_redirect_rejected",
                                    "The public PDF redirect chain was rejected",
                                    retryable=False,
                                )
                            redirected = urljoin(current_url, location)
                            try:
                                current_url, host = _normalize_public_url(redirected)
                            except ValueError:
                                return self._failure(
                                    request,
                                    operation_id,
                                    "public_pdf_redirect_rejected",
                                    "The public PDF redirect chain was rejected",
                                    retryable=False,
                                )
                            if urlsplit(current_url).scheme != "https" or not self._domain_allowed(
                                host
                            ):
                                return self._failure(
                                    request,
                                    operation_id,
                                    "public_pdf_redirect_rejected",
                                    "The public PDF redirect chain was rejected",
                                    retryable=False,
                                )
                            continue
                        if response.status_code != 200:
                            return self._failure(
                                request,
                                operation_id,
                                "public_pdf_unavailable",
                                "The public PDF could not be acquired",
                                retryable=response.status_code >= 500,
                            )
                        declared = response.headers.get("content-length")
                        if declared is not None:
                            try:
                                if int(declared) > maximum:
                                    return self._failure(
                                        request,
                                        operation_id,
                                        "content_budget_exceeded",
                                        "The public PDF exceeded the byte budget",
                                        retryable=False,
                                    )
                            except ValueError:
                                return self._failure(
                                    request,
                                    operation_id,
                                    "invalid_public_pdf_response",
                                    "The public PDF response metadata was invalid",
                                    retryable=True,
                                )
                        media_type = (
                            response.headers.get("content-type", "")
                            .split(";", 1)[0]
                            .strip()
                            .casefold()
                        )
                        if media_type != "application/pdf":
                            return self._failure(
                                request,
                                operation_id,
                                "public_deck_not_pdf",
                                "The linked public deck did not return application/pdf",
                                retryable=False,
                            )
                        chunks: list[bytes] = []
                        size = 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > maximum:
                                return self._failure(
                                    request,
                                    operation_id,
                                    "content_budget_exceeded",
                                    "The public PDF exceeded the byte budget",
                                    retryable=False,
                                )
                            chunks.append(chunk)
                        content = b"".join(chunks)
                        if not content.startswith(b"%PDF-"):
                            return self._failure(
                                request,
                                operation_id,
                                "public_deck_not_pdf",
                                "The linked public deck did not return PDF bytes",
                                retryable=False,
                            )
                        return AcquisitionResult(
                            result_id=(
                                f"{self.adapter_id}:acquisition:{request.acquisition_request_id}"
                            ),
                            acquisition_request_id=request.acquisition_request_id,
                            original_url=request.original_url,
                            status=AcquisitionStatus.ACQUIRED,
                            completed_at=self._now(),
                            content=content,
                            media_type="application/pdf",
                            content_sha256=sha256(content).hexdigest(),
                            source_event_time=KnowledgeValue[datetime].unknown(
                                "The public deck response established no source event time"
                            ),
                        )
        except (httpx.HTTPError, TimeoutError):
            return self._failure(
                request,
                operation_id,
                "public_pdf_transport_failed",
                "The public PDF transport failed safely",
                retryable=True,
            )
        return self._failure(  # pragma: no cover - loop always returns
            request,
            operation_id,
            "public_pdf_redirect_rejected",
            "The public PDF redirect chain was rejected",
            retryable=False,
        )

    def _domain_allowed(self, host: str) -> bool:
        if any(_domain_matches(host, item) for item in self._policy.excluded_domains):
            return False
        return any(_domain_matches(host, item) for item in self._policy.allowed_domains)

    def _failure(
        self,
        request: AcquisitionRequest,
        operation_id: str,
        safe_code: str,
        safe_message: str,
        *,
        retryable: bool,
    ) -> AcquisitionResult:
        return AcquisitionResult(
            result_id=f"{self.adapter_id}:acquisition:{request.acquisition_request_id}",
            acquisition_request_id=request.acquisition_request_id,
            original_url=request.original_url,
            status=AcquisitionStatus.BLOCKED,
            completed_at=self._now(),
            source_event_time=KnowledgeValue[datetime].unknown("No public PDF bytes were accepted"),
            failure=CollectionFailure(
                operation_id=operation_id,
                safe_code=safe_code,
                safe_message=safe_message,
                retryable=retryable,
            ),
        )


@dataclass(frozen=True, slots=True)
class _DiscoveryCall:
    binding: SourceAdapterBinding
    request: DiscoveryRequest


@dataclass(frozen=True, slots=True)
class _DiscoveryOutcome:
    call: _DiscoveryCall
    result: DiscoveryResult | None
    raised: bool = False


@dataclass(frozen=True, slots=True)
class _RoutedLead:
    binding: SourceAdapterBinding
    lead: DiscoveryLead
    normalized_url: str


@dataclass(frozen=True, slots=True)
class _AcquisitionCall:
    routed: _RoutedLead
    request: AcquisitionRequest
    cached_artifact: SourceArtifact | None
    artifact_kind: SourceArtifactKind
    display_name: str | None = None


@dataclass(frozen=True, slots=True)
class _AcquisitionOutcome:
    call: _AcquisitionCall
    result: AcquisitionResult | None
    raised: bool = False


@dataclass(frozen=True, slots=True)
class _AcceptedAcquisition:
    artifact: SourceArtifact
    source_identity_key: str
    content: bytes | None
    is_new_artifact: bool


@dataclass(slots=True)
class _CanonicalizedLead:
    routed: _RoutedLead
    accepted: _AcceptedAcquisition
    candidate: OutboundCandidateView
    showcase: HackathonShowcaseProjection | None = None
    public_contacts: PublicContactProjection | None = None
    changed: bool = False


@dataclass(frozen=True, slots=True)
class _RoundExecution:
    query: str
    round_index: int
    canonicalized: tuple[_CanonicalizedLead, ...]
    routed_lead_ids: tuple[str, ...]
    discovery_call_count: int
    acquired_page_count: int
    evidence_keys: frozenset[str]
    partial_failure: bool


class _OutboundLoopState(TypedDict):
    round_index: int
    current_query: str
    queries: tuple[str, ...]
    rounds: tuple[OutboundSearchRoundAudit, ...]
    canonicalized: tuple[_CanonicalizedLead, ...]
    routed_lead_ids: tuple[str, ...]
    discovery_calls_used: int
    pages_used: int
    evidence_keys: frozenset[str]
    previous_evidence_keys: frozenset[str]
    evidence_gaps: tuple[str, ...]
    last_round: _RoundExecution | None
    stop_reason: OutboundSearchStopReason | None


class MultiAdapterSourcingCoordinator:
    """Fan out bounded discovery, prefer authoritative records, then canonicalize safely."""

    def __init__(
        self,
        *,
        adapters: tuple[SourceAdapterBinding, ...],
        service: FakeVCBrainService,
        memory: SQLiteMemory,
        artifact_store: PrivateArtifactStore,
        screening_bridge: DeterministicScreeningBridge,
        now: Callable[[], UTCDateTime],
        id_factory: Callable[[str], str],
        max_results: int,
        max_pages: int,
        max_bytes: int,
        timeout_seconds: float,
        cache_ttl_seconds: int = 900,
        structured_page_extractor: PublicPageStructuredExtractorPort | None = None,
        public_pdf_acquisition: AcquisitionPort | None = None,
        public_pdf_extractor: PdfExtractor | None = None,
        public_pdf_max_bytes: int | None = None,
        max_follow_up_rounds: int = 0,
        max_discovery_calls: int = 12,
    ) -> None:
        if not adapters:
            raise ValueError("at least one approved sourcing adapter is required")
        if min(max_results, max_pages, max_bytes, timeout_seconds) <= 0:
            raise ValueError("sourcing coordinator budgets must be positive")
        if max_results > 20 or max_pages > 20 or max_pages > max_results:
            raise ValueError("sourcing coordinator result/page budgets exceed the MVP ceiling")
        if cache_ttl_seconds < 0:
            raise ValueError("sourcing cache TTL cannot be negative")
        if public_pdf_max_bytes is not None and not (
            0 < public_pdf_max_bytes <= _PUBLIC_PDF_MAX_BYTES
        ):
            raise ValueError("public PDF byte ceiling must be between one byte and 10 MiB")
        if not 0 <= max_follow_up_rounds <= 3:
            raise ValueError("sourcing follow-up rounds must be between zero and three")
        if not 1 <= max_discovery_calls <= 32:
            raise ValueError("sourcing discovery calls must be between one and 32")
        adapter_ids = tuple(binding.adapter_id for binding in adapters)
        if len(adapter_ids) != len(set(adapter_ids)):
            raise ValueError("sourcing adapter identifiers must be unique")
        if sum(binding.is_generic for binding in adapters) > 1:
            raise ValueError("P0 permits exactly zero or one generic source provider")
        self._adapters = adapters
        self._service = service
        self._memory = memory
        self._artifact_store = artifact_store
        self._screening_bridge = screening_bridge
        self._now = now
        self._id_factory = id_factory
        self._max_results = max_results
        self._max_pages = max_pages
        self._max_bytes = max_bytes
        self._timeout_seconds = timeout_seconds
        self._cache_ttl = timedelta(seconds=cache_ttl_seconds)
        self._structured_page_extractor = structured_page_extractor
        self._public_pdf_acquisition = public_pdf_acquisition
        self._public_pdf_extractor = public_pdf_extractor
        self._public_pdf_max_bytes = public_pdf_max_bytes or max_bytes
        self._max_follow_up_rounds = max_follow_up_rounds
        self._max_discovery_calls = max_discovery_calls

    def _id(self, prefix: str) -> str:
        value = self._id_factory(prefix)
        if _STABLE_ID.fullmatch(value) is None:
            raise ValueError("id_factory returned an invalid stable identifier")
        return value

    def enqueue(self, command: BoundedSourcingCommand) -> RunAccepted:
        snapshot_digest = sha256(command.model_dump_json().encode("utf-8")).hexdigest()[:32]
        accepted = self._service.queue_sourcing_run(
            input_snapshot_id=f"sourcing-query:{snapshot_digest}",
            stage_keys=_STAGE_KEYS,
        )
        self._append_run_snapshot(accepted.run)
        return accepted

    async def execute(self, run_id: str, command: BoundedSourcingCommand) -> None:
        queued = self._service.get_run(run_id)
        started_at = self._now()
        running = queued.model_copy(
            update={
                "status": PipelineRunStatus.RUNNING,
                "started_at": started_at,
                "stages": (
                    PipelineStage(
                        stage_key=_STAGE_DISCOVER,
                        status=PipelineStageStatus.RUNNING,
                        queued_at=queued.stages[0].queued_at,
                        started_at=started_at,
                    ),
                    *queued.stages[1:],
                ),
            }
        )
        self._service.publish_sourcing_run(running)
        self._append_run_snapshot(running)

        discovery_failures: list[PipelineFailure] = []
        acquisition_failures: list[PipelineFailure] = []
        canonical_failures: list[PipelineFailure] = []
        accepted_artifact_ids: list[str] = []
        accepted_candidate_ids: list[str] = []
        accepted_projection_ids: list[str] = []
        accepted_assessment_ids: list[str] = []

        loop_state = await self._run_outbound_loop(
            run_id=run_id,
            queued=queued,
            command=command,
            started_at=started_at,
            discovery_failures=discovery_failures,
            acquisition_failures=acquisition_failures,
            canonical_failures=canonical_failures,
            accepted_artifact_ids=accepted_artifact_ids,
            accepted_candidate_ids=accepted_candidate_ids,
            accepted_projection_ids=accepted_projection_ids,
        )
        canonicalized = list(loop_state["canonicalized"])
        routed_lead_ids = loop_state["routed_lead_ids"]

        by_candidate: dict[str, _CanonicalizedLead] = {}
        for item in canonicalized:
            current = by_candidate.get(item.candidate.outbound_candidate_id)
            if current is None:
                by_candidate[item.candidate.outbound_candidate_id] = item
            else:
                current.changed = current.changed or item.changed
                current.candidate = item.candidate
        for item in by_candidate.values():
            try:
                coverage = self._coverage(item.candidate)
                self._screening_bridge.register(
                    item.candidate.outbound_candidate_id,
                    ScreeningSignalBundle(coverage=coverage),
                )
                if item.changed or item.candidate.preliminary_assessment is None:
                    assessment_run = self._service.start_preliminary_assessment(
                        item.candidate.outbound_candidate_id
                    )
                    accepted_assessment_ids.extend(assessment_run.run.accepted_output_ids)
            except Exception:
                self._add_failure(
                    canonical_failures,
                    _STAGE_CANONICALIZE,
                    "candidate_assessment_failed",
                    "A source-backed candidate could not be assessed safely",
                    retryable=True,
                )

        stop_reason = loop_state["stop_reason"]
        if stop_reason is None:
            raise RuntimeError("completed outbound loop is missing its stop reason")
        loop_audit = SourcingLoopAuditView(
            status=(
                SourcingLoopAuditStatus.COMPLETED
                if stop_reason is OutboundSearchStopReason.SUFFICIENT_EVIDENCE
                else SourcingLoopAuditStatus.STOPPED
            ),
            rounds_completed=len(loop_state["rounds"]),
            round_limit=self._max_follow_up_rounds + 1,
            stop_reason=stop_reason.value,
            run_id=run_id,
        )
        for candidate_id in dict.fromkeys(accepted_candidate_ids):
            try:
                self._service.publish_candidate_sourcing_audit(candidate_id, loop_audit)
            except Exception:
                self._add_failure(
                    canonical_failures,
                    _STAGE_CANONICALIZE,
                    "sourcing_audit_projection_failed",
                    "The bounded sourcing audit could not be projected safely",
                    retryable=True,
                )

        completed_at = self._now()
        failures = tuple((*discovery_failures, *acquisition_failures, *canonical_failures))
        accepted_output_ids = tuple(
            dict.fromkeys(
                (
                    *accepted_artifact_ids,
                    *accepted_candidate_ids,
                    *accepted_projection_ids,
                    *accepted_assessment_ids,
                )
            )
        )
        final_status = (
            PipelineRunStatus.PARTIALLY_SUCCEEDED
            if failures and accepted_output_ids
            else PipelineRunStatus.FAILED
            if failures
            else PipelineRunStatus.SUCCEEDED
        )
        terminal = PipelineRun(
            run_id=queued.run_id,
            kind=queued.kind,
            status=final_status,
            versions=queued.versions,
            input_snapshot_id=queued.input_snapshot_id,
            input_snapshot_as_of=queued.input_snapshot_as_of,
            queued_at=queued.queued_at,
            started_at=started_at,
            completed_at=completed_at,
            stages=(
                self._terminal_stage(
                    queued.stages[0],
                    started_at=started_at,
                    completed_at=completed_at,
                    accepted_output_ids=tuple(dict.fromkeys(routed_lead_ids)),
                    failures=tuple(discovery_failures),
                    skip=False,
                ),
                self._terminal_stage(
                    queued.stages[1],
                    started_at=started_at,
                    completed_at=completed_at,
                    accepted_output_ids=tuple(dict.fromkeys(accepted_artifact_ids)),
                    failures=tuple(acquisition_failures),
                    skip=not routed_lead_ids,
                ),
                self._terminal_stage(
                    queued.stages[2],
                    started_at=started_at,
                    completed_at=completed_at,
                    accepted_output_ids=tuple(
                        dict.fromkeys(
                            (
                                *accepted_candidate_ids,
                                *accepted_projection_ids,
                                *accepted_assessment_ids,
                            )
                        )
                    ),
                    failures=tuple(canonical_failures),
                    skip=not accepted_artifact_ids and not canonical_failures,
                ),
            ),
            accepted_output_ids=accepted_output_ids,
            failures=failures,
        )
        self._service.publish_sourcing_run(terminal)
        self._append_run_snapshot(terminal)

    async def _run_outbound_loop(
        self,
        *,
        run_id: str,
        queued: PipelineRun,
        command: BoundedSourcingCommand,
        started_at: UTCDateTime,
        discovery_failures: list[PipelineFailure],
        acquisition_failures: list[PipelineFailure],
        canonical_failures: list[PipelineFailure],
        accepted_artifact_ids: list[str],
        accepted_candidate_ids: list[str],
        accepted_projection_ids: list[str],
    ) -> _OutboundLoopState:
        """Run the thin LangGraph control loop around framework-neutral sourcing operations."""

        async def plan(state: _OutboundLoopState) -> dict[str, object]:
            query = (
                command.query
                if state["round_index"] == 0
                else _follow_up_query(
                    command.query,
                    state["evidence_gaps"],
                    state["queries"],
                )
            )
            return {"current_query": query}

        async def retrieve_structure(state: _OutboundLoopState) -> dict[str, object]:
            remaining_pages = min(command.max_pages, self._max_pages) - state["pages_used"]
            remaining_calls = self._max_discovery_calls - state["discovery_calls_used"]
            round_result = await self._run_sourcing_round(
                run_id=run_id,
                queued=queued,
                command=command,
                query=state["current_query"],
                round_index=state["round_index"],
                requested_at=started_at,
                remaining_pages=max(0, remaining_pages),
                remaining_discovery_calls=max(0, remaining_calls),
                discovery_failures=discovery_failures,
                acquisition_failures=acquisition_failures,
                canonical_failures=canonical_failures,
                accepted_artifact_ids=accepted_artifact_ids,
                accepted_candidate_ids=accepted_candidate_ids,
                accepted_projection_ids=accepted_projection_ids,
            )
            return {
                "queries": (*state["queries"], state["current_query"]),
                "canonicalized": (*state["canonicalized"], *round_result.canonicalized),
                "routed_lead_ids": (
                    *state["routed_lead_ids"],
                    *round_result.routed_lead_ids,
                ),
                "discovery_calls_used": (
                    state["discovery_calls_used"] + round_result.discovery_call_count
                ),
                "pages_used": state["pages_used"] + round_result.acquired_page_count,
                "previous_evidence_keys": state["evidence_keys"],
                "evidence_keys": state["evidence_keys"] | round_result.evidence_keys,
                "last_round": round_result,
            }

        def assess_gaps(state: _OutboundLoopState) -> dict[str, object]:
            round_result = state["last_round"]
            if round_result is None:
                raise RuntimeError("outbound loop cannot assess a missing retrieval round")
            new_evidence_count = len(state["evidence_keys"] - state["previous_evidence_keys"])
            gaps = self._evidence_gaps(command, state["canonicalized"])
            page_budget = min(command.max_pages, self._max_pages)
            if round_result.partial_failure:
                stop_reason = OutboundSearchStopReason.PARTIAL_FAILURE
            elif not gaps and new_evidence_count > 0:
                stop_reason = OutboundSearchStopReason.SUFFICIENT_EVIDENCE
            elif new_evidence_count == 0:
                stop_reason = OutboundSearchStopReason.NO_NEW_EVIDENCE
            elif state["discovery_calls_used"] >= self._max_discovery_calls:
                stop_reason = OutboundSearchStopReason.CALL_BUDGET_EXHAUSTED
            elif state["pages_used"] >= page_budget:
                stop_reason = OutboundSearchStopReason.PAGE_BUDGET_EXHAUSTED
            elif state["round_index"] >= self._max_follow_up_rounds:
                stop_reason = OutboundSearchStopReason.ROUND_BUDGET_EXHAUSTED
            else:
                stop_reason = None
            audit = OutboundSearchRoundAudit(
                round_index=round_result.round_index,
                query=round_result.query,
                discovery_call_count=round_result.discovery_call_count,
                discovered_lead_count=len(round_result.routed_lead_ids),
                acquired_page_count=round_result.acquired_page_count,
                new_evidence_count=new_evidence_count,
                evidence_gaps=gaps,
                partial_failure=round_result.partial_failure,
            )
            return {
                "rounds": (*state["rounds"], audit),
                "evidence_gaps": gaps,
                "stop_reason": stop_reason,
                "round_index": (
                    state["round_index"] if stop_reason is not None else state["round_index"] + 1
                ),
            }

        def route(state: _OutboundLoopState) -> Literal["plan", "finalize"]:
            return "finalize" if state["stop_reason"] is not None else "plan"

        def finalize(state: _OutboundLoopState) -> dict[str, object]:
            if state["stop_reason"] is None:
                raise RuntimeError("outbound loop finalized without a deterministic stop reason")
            return {"stop_reason": state["stop_reason"]}

        builder = StateGraph(_OutboundLoopState)
        builder.add_node("plan", plan)
        builder.add_node("retrieve_structure", retrieve_structure)
        builder.add_node("assess_gaps", assess_gaps)
        builder.add_node("finalize", finalize)
        builder.add_edge(START, "plan")
        builder.add_edge("plan", "retrieve_structure")
        builder.add_edge("retrieve_structure", "assess_gaps")
        builder.add_conditional_edges(
            "assess_gaps",
            route,
            {"plan": "plan", "finalize": "finalize"},
        )
        builder.add_edge("finalize", END)
        graph = builder.compile(name="founderlookup-outbound-convergence")
        initial: _OutboundLoopState = {
            "round_index": 0,
            "current_query": command.query,
            "queries": (),
            "rounds": (),
            "canonicalized": (),
            "routed_lead_ids": (),
            "discovery_calls_used": 0,
            "pages_used": 0,
            "evidence_keys": frozenset(),
            "previous_evidence_keys": frozenset(),
            "evidence_gaps": (),
            "last_round": None,
            "stop_reason": None,
        }
        result = await graph.ainvoke(initial, {"recursion_limit": 24})
        final_state = cast(_OutboundLoopState, result)
        self._persist_outbound_loop_audit(run_id, final_state)
        return final_state

    async def _run_sourcing_round(
        self,
        *,
        run_id: str,
        queued: PipelineRun,
        command: BoundedSourcingCommand,
        query: str,
        round_index: int,
        requested_at: UTCDateTime,
        remaining_pages: int,
        remaining_discovery_calls: int,
        discovery_failures: list[PipelineFailure],
        acquisition_failures: list[PipelineFailure],
        canonical_failures: list[PipelineFailure],
        accepted_artifact_ids: list[str],
        accepted_candidate_ids: list[str],
        accepted_projection_ids: list[str],
    ) -> _RoundExecution:
        failure_count = (
            len(discovery_failures) + len(acquisition_failures) + len(canonical_failures)
        )
        discovery_calls = self._discovery_calls(
            queued,
            command,
            requested_at,
            query=query,
            maximum_calls=remaining_discovery_calls,
        )
        outcomes = await asyncio.gather(*(self._discover(call) for call in discovery_calls))
        routed_leads = self._accept_discovery_outcomes(
            run_id=run_id,
            command=command,
            outcomes=outcomes,
            failures=discovery_failures,
        )

        rounds_remaining = self._max_follow_up_rounds - round_index + 1
        if SourceCategory.HACKATHON in command.source_categories and self._max_follow_up_rounds > 0:
            round_page_cap = min(
                remaining_pages,
                max(1, (remaining_pages + rounds_remaining - 1) // rounds_remaining),
            )
        else:
            round_page_cap = remaining_pages
        reserve_for_deck = int(
            SourceCategory.HACKATHON in command.source_categories and round_page_cap > 1
        )
        primary_limit = max(0, round_page_cap - reserve_for_deck)
        primary_routed = routed_leads[:primary_limit]
        primary_calls = tuple(
            self._acquisition_call(
                routed,
                command=command,
                artifact_kind=routed.binding.artifact_kind,
            )
            for routed in primary_routed
        )
        primary_outcomes = await asyncio.gather(*(self._acquire(call) for call in primary_calls))

        canonicalized: list[_CanonicalizedLead] = []
        round_artifact_ids: list[str] = []
        for outcome in primary_outcomes:
            accepted = self._accept_acquisition_outcome(
                run_id=run_id,
                outcome=outcome,
                failures=acquisition_failures,
            )
            if accepted is None:
                continue
            round_artifact_ids.append(accepted.artifact.source_artifact_id)
            accepted_artifact_ids.append(accepted.artifact.source_artifact_id)
            showcase: HackathonShowcaseProjection | None = None
            public_contacts: PublicContactProjection | None = None
            if accepted.artifact.source_category is SourceCategory.HACKATHON:
                try:
                    showcase, public_contacts = await self._project_showcase(
                        run_id=run_id,
                        accepted=accepted,
                        failures=canonical_failures,
                    )
                except Exception:
                    self._add_failure(
                        canonical_failures,
                        _STAGE_CANONICALIZE,
                        "hackathon_projection_failed",
                        "A public showcase could not be projected safely",
                        retryable=False,
                    )
            try:
                candidate = self._service.seed_outbound_candidate(
                    company_name=_candidate_name(outcome.call.routed.lead, showcase),
                    source_artifact_ids=(accepted.artifact.source_artifact_id,),
                    source_identity_key=accepted.source_identity_key,
                )
            except Exception:
                self._add_failure(
                    canonical_failures,
                    _STAGE_CANONICALIZE,
                    "candidate_canonicalization_failed",
                    "An acquired source could not be mapped to a candidate safely",
                    retryable=True,
                )
                continue
            accepted_candidate_ids.append(candidate.outbound_candidate_id)
            accepted_projection_ids.extend(
                self._persist_primary_projections(
                    accepted=accepted,
                    candidate=candidate,
                    showcase=showcase,
                    public_contacts=public_contacts,
                    failures=canonical_failures,
                )
            )
            canonicalized.append(
                _CanonicalizedLead(
                    routed=outcome.call.routed,
                    accepted=accepted,
                    candidate=candidate,
                    showcase=showcase,
                    public_contacts=public_contacts,
                    changed=(accepted.is_new_artifact or candidate.preliminary_assessment is None),
                )
            )

        deck_follow_ups = self._deck_follow_ups(canonicalized)
        remaining_round_pages = max(0, round_page_cap - len(primary_calls))
        selected_decks = deck_follow_ups[:remaining_round_pages]
        if len(deck_follow_ups) > remaining_round_pages and round_page_cap >= remaining_pages:
            self._add_failure(
                acquisition_failures,
                _STAGE_ACQUIRE,
                "follow_up_page_budget_exceeded",
                "Explicit public deck links remain uncollected because the page budget was reached",
                retryable=False,
            )
        deck_work: list[
            tuple[
                _CanonicalizedLead,
                PublicHackathonLink,
                PublicDeckPdfTarget,
                _AcquisitionCall,
            ]
        ] = []
        for canonical, source_link in selected_decks:
            try:
                target = resolve_public_deck_pdf_url(source_link.url)
                acquisition_link = source_link.model_copy(update={"url": target.acquisition_url})
                call = self._deck_acquisition_call(
                    (canonical, acquisition_link),
                    command=command,
                )
            except ValueError:
                self._add_failure(
                    acquisition_failures,
                    _STAGE_ACQUIRE,
                    "unsupported_public_deck_url",
                    "A linked deck was not an HTTPS PDF or supported public Slides URL",
                    retryable=False,
                )
                continue
            self._append_collection_telemetry(
                run_id=run_id,
                record_id=self._id("public-deck-url-resolution"),
                recorded_at=self._now(),
                payload={
                    "adapter_id": "public-deck-url-policy-v0",
                    "operation": "resolve_public_deck_pdf_url",
                    "status": "accepted",
                    "source_url": target.source_url,
                    "acquisition_url": target.acquisition_url,
                    "normalization": target.normalization,
                },
            )
            deck_work.append((canonical, source_link, target, call))
        deck_calls = tuple(item[3] for item in deck_work)
        deck_outcomes = await asyncio.gather(*(self._acquire(call) for call in deck_calls))
        for (canonical, source_link, target, _call), outcome in zip(
            deck_work, deck_outcomes, strict=True
        ):
            accepted = self._accept_acquisition_outcome(
                run_id=run_id,
                outcome=outcome,
                failures=acquisition_failures,
            )
            if accepted is None:
                continue
            round_artifact_ids.append(accepted.artifact.source_artifact_id)
            accepted_artifact_ids.append(accepted.artifact.source_artifact_id)
            try:
                candidate = self._service.seed_outbound_candidate(
                    company_name=canonical.candidate.company_name,
                    source_artifact_ids=(accepted.artifact.source_artifact_id,),
                    source_identity_key=canonical.accepted.source_identity_key,
                )
                assert canonical.showcase is not None
                relationship = link_public_hackathon_deck(
                    projection=canonical.showcase,
                    link=source_link,
                    deck_source_artifact=accepted.artifact,
                    candidate_id=candidate.outbound_candidate_id,
                    acquisition_url=target.acquisition_url,
                    url_normalization=target.normalization,
                )
                self._persist_hackathon_deck_relationship(relationship)
            except Exception:
                self._add_failure(
                    canonical_failures,
                    _STAGE_CANONICALIZE,
                    "hackathon_deck_link_failed",
                    "A separately acquired public deck could not be linked safely",
                    retryable=True,
                )
                continue
            accepted_projection_ids.extend(
                await self._extract_public_deck(
                    run_id=run_id,
                    accepted=accepted,
                    failures=canonical_failures,
                )
            )
            canonical.candidate = candidate
            canonical.changed = canonical.changed or accepted.is_new_artifact
            accepted_candidate_ids.append(candidate.outbound_candidate_id)
            accepted_projection_ids.append(relationship.relationship_id)

        current_failure_count = (
            len(discovery_failures) + len(acquisition_failures) + len(canonical_failures)
        )
        return _RoundExecution(
            query=query,
            round_index=round_index,
            canonicalized=tuple(canonicalized),
            routed_lead_ids=tuple(item.lead.lead_id for item in routed_leads),
            discovery_call_count=len(discovery_calls),
            acquired_page_count=len(primary_calls) + len(deck_calls),
            evidence_keys=_round_evidence_keys(canonicalized, round_artifact_ids),
            partial_failure=current_failure_count > failure_count,
        )

    async def _extract_public_deck(
        self,
        *,
        run_id: str,
        accepted: _AcceptedAcquisition,
        failures: list[PipelineFailure],
    ) -> tuple[str, ...]:
        """Send only newly acquired, signature-checked public PDFs through OCR."""

        if (
            self._public_pdf_extractor is None
            or accepted.content is None
            or not accepted.is_new_artifact
        ):
            return ()
        artifact = accepted.artifact
        media_type = artifact.media_type.split(";", 1)[0].strip().casefold()
        if media_type != "application/pdf" or not accepted.content.startswith(b"%PDF-"):
            safe_code = "public_deck_not_pdf"
            self._add_failure(
                failures,
                _STAGE_CANONICALIZE,
                safe_code,
                "The explicitly linked public deck was retained but was not sent to OCR",
                retryable=False,
            )
            self._persist_public_deck_ocr(
                artifact=artifact,
                attempted_at=self._now(),
                extraction=None,
                safe_code=safe_code,
            )
            self._append_public_deck_ocr_telemetry(
                run_id=run_id,
                artifact=artifact,
                status="unknown",
                safe_code=safe_code,
            )
            return ()

        attempted_at = self._now()
        try:
            extraction = await self._public_pdf_extractor.extract(
                PdfExtractionRequest(
                    source_artifact_id=artifact.source_artifact_id,
                    input_sha256=artifact.content_sha256,
                    content=accepted.content,
                    classification=DataClassification.PUBLIC,
                    requested_at=attempted_at,
                )
            )
            if (
                extraction.source_artifact_id != artifact.source_artifact_id
                or extraction.input_sha256 != artifact.content_sha256
            ):
                raise PdfExtractionError
        except Exception as error:
            safe_code = (
                error.code if isinstance(error, PdfExtractionError) else "public_deck_ocr_failed"
            )
            self._add_failure(
                failures,
                _STAGE_CANONICALIZE,
                safe_code,
                "Public deck OCR did not produce an accepted extraction",
                retryable=True,
            )
            self._persist_public_deck_ocr(
                artifact=artifact,
                attempted_at=attempted_at,
                extraction=None,
                safe_code=safe_code,
            )
            self._append_public_deck_ocr_telemetry(
                run_id=run_id,
                artifact=artifact,
                status="unknown",
                safe_code=safe_code,
            )
            return ()

        self._persist_public_deck_ocr(
            artifact=artifact,
            attempted_at=attempted_at,
            extraction=extraction,
            safe_code=None,
        )
        self._append_public_deck_ocr_telemetry(
            run_id=run_id,
            artifact=artifact,
            status="known",
            safe_code=None,
            extraction=extraction,
        )
        return (extraction.extraction_id,)

    def _evidence_gaps(
        self,
        command: BoundedSourcingCommand,
        canonicalized: tuple[_CanonicalizedLead, ...],
    ) -> tuple[str, ...]:
        categories = {item.accepted.artifact.source_category for item in canonicalized}
        gaps = [
            f"source_category:{category.value}"
            for category in command.source_categories
            if category not in categories
        ]
        if SourceCategory.HACKATHON not in command.source_categories:
            return tuple(gaps)
        showcases = tuple(item.showcase for item in canonicalized if item.showcase is not None)
        if not showcases:
            gaps.append("hackathon_showcase")
            return tuple(dict.fromkeys(gaps))
        if not any(item.event_name.value is not None for item in showcases):
            gaps.append("event")
        if not any(item.project_name.value is not None for item in showcases):
            gaps.append("project")
        if not any(item.participants for item in showcases):
            gaps.append("participants")
        link_kinds = {link.kind for item in showcases for link in item.links}
        if HackathonLinkKind.REPOSITORY not in link_kinds:
            gaps.append("repository")
        if HackathonLinkKind.DEMO not in link_kinds:
            gaps.append("demo")
        if HackathonLinkKind.PITCH_DECK not in link_kinds:
            gaps.append("pitch_deck")
        if self._structured_page_extractor is not None and not any(
            item.public_contacts is not None and item.public_contacts.routes
            for item in canonicalized
        ):
            gaps.append("public_contact")
        return tuple(dict.fromkeys(gaps))

    def _persist_outbound_loop_audit(
        self,
        run_id: str,
        state: _OutboundLoopState,
    ) -> None:
        stop_reason = state["stop_reason"]
        if stop_reason is None or not state["rounds"]:
            raise RuntimeError("completed outbound loop is missing its audit stop state")
        audit = OutboundSearchLoopAudit(
            record_id=self._id("outbound-search-loop"),
            run_id=run_id,
            rounds=state["rounds"],
            stop_reason=stop_reason,
            maximum_follow_up_rounds=self._max_follow_up_rounds,
            maximum_discovery_calls=self._max_discovery_calls,
        )
        self._append_collection_telemetry(
            run_id=run_id,
            record_id=audit.record_id,
            recorded_at=self._now(),
            payload=audit.model_dump(mode="json"),
        )

    def _discovery_calls(
        self,
        queued: PipelineRun,
        command: BoundedSourcingCommand,
        requested_at: UTCDateTime,
        *,
        query: str,
        maximum_calls: int,
    ) -> tuple[_DiscoveryCall, ...]:
        if maximum_calls <= 0:
            return ()
        calls: list[_DiscoveryCall] = []
        for binding in self._adapters:
            categories = binding.categories_for(command.source_categories)
            if not categories:
                continue
            request = DiscoveryRequest(
                request_id=self._id("discovery-request"),
                query_plan_id=queued.input_snapshot_id,
                requested_at=requested_at,
                retrieval_requests=(
                    BoundedRetrievalRequest(
                        retrieval_request_id=self._id("retrieval"),
                        query=query,
                        source_categories=categories,
                        allowed_domains=command.allowed_domains,
                        excluded_domains=command.excluded_domains,
                        max_results=min(command.max_results, self._max_results),
                        max_pages=min(command.max_pages, self._max_pages),
                        timeout_seconds=int(
                            min(float(command.timeout_seconds), self._timeout_seconds)
                        ),
                    ),
                ),
            )
            calls.append(_DiscoveryCall(binding=binding, request=request))
            if len(calls) >= maximum_calls:
                break
        return tuple(calls)

    @staticmethod
    async def _discover(call: _DiscoveryCall) -> _DiscoveryOutcome:
        try:
            result = await call.binding.discovery.discover(call.request)
        except Exception:
            return _DiscoveryOutcome(call=call, result=None, raised=True)
        return _DiscoveryOutcome(call=call, result=result)

    def _accept_discovery_outcomes(
        self,
        *,
        run_id: str,
        command: BoundedSourcingCommand,
        outcomes: tuple[_DiscoveryOutcome, ...] | list[_DiscoveryOutcome],
        failures: list[PipelineFailure],
    ) -> tuple[_RoutedLead, ...]:
        routed: list[_RoutedLead] = []
        adapter_order = {binding.adapter_id: index for index, binding in enumerate(self._adapters)}
        for outcome in outcomes:
            if outcome.raised or outcome.result is None:
                self._add_failure(
                    failures,
                    _STAGE_DISCOVER,
                    "adapter_discovery_failed",
                    "An approved public-source adapter could not complete discovery safely",
                    retryable=True,
                )
                self._append_collection_telemetry(
                    run_id=run_id,
                    record_id=self._id("collection-failure"),
                    recorded_at=self._now(),
                    payload={
                        "adapter_id": outcome.call.binding.adapter_id,
                        "operation": "discover",
                        "status": "failed",
                        "safe_code": "adapter_discovery_failed",
                    },
                )
                continue
            result = outcome.result
            self._append_collection_telemetry(
                run_id=run_id,
                record_id=result.result_id,
                recorded_at=result.completed_at,
                payload=self._discovery_telemetry(result, outcome.call.binding.adapter_id),
            )
            if result.request_id != outcome.call.request.request_id:
                self._add_failure(
                    failures,
                    _STAGE_DISCOVER,
                    "adapter_contract_violation",
                    "A public-source adapter returned mismatched discovery provenance",
                    retryable=False,
                )
                continue
            for failure in result.failures[:_MAX_FAILURES]:
                self._add_collection_failure(failures, _STAGE_DISCOVER, failure)
            supported = outcome.call.binding.categories_for(command.source_categories)
            for lead in result.leads[: min(command.max_results, self._max_results)]:
                if lead.source_category not in supported:
                    self._add_failure(
                        failures,
                        _STAGE_DISCOVER,
                        "adapter_contract_violation",
                        "A public-source adapter returned an unsupported source category",
                        retryable=False,
                    )
                    continue
                try:
                    normalized_url, host = _normalize_public_url(lead.original_url)
                except ValueError:
                    self._add_failure(
                        failures,
                        _STAGE_DISCOVER,
                        "unsafe_original_url",
                        "A discovered source URL was rejected by public-source policy",
                        retryable=False,
                    )
                    continue
                if not _command_domain_allows(host, command):
                    self._add_failure(
                        failures,
                        _STAGE_DISCOVER,
                        "domain_policy_rejected",
                        "A discovered source URL fell outside the requested domain policy",
                        retryable=False,
                    )
                    continue
                routed.append(
                    _RoutedLead(
                        binding=outcome.call.binding,
                        lead=lead.model_copy(update={"original_url": normalized_url}),
                        normalized_url=normalized_url,
                    )
                )

        routed.sort(
            key=lambda item: (
                not item.binding.authoritative,
                adapter_order[item.binding.adapter_id],
                item.lead.rank,
                item.normalized_url,
            )
        )
        accepted: list[_RoutedLead] = []
        seen_urls: set[str] = set()
        for item in routed:
            key = _url_key(item.normalized_url)
            if key in seen_urls:
                continue
            seen_urls.add(key)
            accepted.append(item)
            if len(accepted) >= min(command.max_results, self._max_results):
                break
        return tuple(accepted)

    def _acquisition_call(
        self,
        routed: _RoutedLead,
        *,
        command: BoundedSourcingCommand,
        artifact_kind: SourceArtifactKind,
        display_name: str | None = None,
    ) -> _AcquisitionCall:
        requested_at = self._now()
        return _AcquisitionCall(
            routed=routed,
            request=AcquisitionRequest(
                acquisition_request_id=self._id("acquisition-request"),
                discovery_lead_id=routed.lead.lead_id,
                original_url=routed.normalized_url,
                requested_at=requested_at,
                classification=DataClassification.PUBLIC,
                allowed_media_types=routed.binding.allowed_media_types,
                max_bytes=min(command.max_bytes, self._max_bytes),
                timeout_seconds=int(min(float(command.timeout_seconds), self._timeout_seconds)),
            ),
            cached_artifact=self._cached_artifact(routed.normalized_url, requested_at),
            artifact_kind=artifact_kind,
            display_name=display_name,
        )

    @staticmethod
    async def _acquire(call: _AcquisitionCall) -> _AcquisitionOutcome:
        if call.cached_artifact is not None:
            return _AcquisitionOutcome(call=call, result=None)
        try:
            result = await call.routed.binding.acquisition.acquire(call.request)
        except Exception:
            return _AcquisitionOutcome(call=call, result=None, raised=True)
        return _AcquisitionOutcome(call=call, result=result)

    def _accept_acquisition_outcome(
        self,
        *,
        run_id: str,
        outcome: _AcquisitionOutcome,
        failures: list[PipelineFailure],
    ) -> _AcceptedAcquisition | None:
        call = outcome.call
        if call.cached_artifact is not None:
            artifact = call.cached_artifact
            self._register_artifact(artifact)
            self._persist_policy(artifact, call.routed.binding)
            self._append_collection_telemetry(
                run_id=run_id,
                record_id=self._id("collection-cache-hit"),
                recorded_at=self._now(),
                payload={
                    "adapter_id": call.routed.binding.adapter_id,
                    "operation": "acquire",
                    "status": "cache_hit",
                    "original_url": artifact.origin_locator,
                    "source_artifact_id": artifact.source_artifact_id,
                    "content_sha256": artifact.content_sha256,
                },
            )
            return _AcceptedAcquisition(
                artifact=artifact,
                source_identity_key=artifact.artifact_series_id,
                content=None,
                is_new_artifact=False,
            )
        if outcome.raised or outcome.result is None:
            self._add_failure(
                failures,
                _STAGE_ACQUIRE,
                "adapter_acquisition_failed",
                "An approved public-source adapter could not acquire content safely",
                retryable=True,
            )
            self._append_collection_telemetry(
                run_id=run_id,
                record_id=self._id("collection-failure"),
                recorded_at=self._now(),
                payload={
                    "adapter_id": call.routed.binding.adapter_id,
                    "operation": "acquire",
                    "status": "failed",
                    "safe_code": "adapter_acquisition_failed",
                    "original_url": call.request.original_url,
                },
            )
            return None

        result = outcome.result
        self._append_collection_telemetry(
            run_id=run_id,
            record_id=result.result_id,
            recorded_at=result.completed_at,
            payload=self._acquisition_telemetry(result, call.routed.binding.adapter_id),
        )
        if result.acquisition_request_id != call.request.acquisition_request_id or _url_key(
            result.original_url
        ) != _url_key(call.request.original_url):
            self._add_failure(
                failures,
                _STAGE_ACQUIRE,
                "provenance_mismatch",
                "Acquired content did not retain the requested exact original-source URL",
                retryable=False,
            )
            return None
        if result.status is AcquisitionStatus.NOT_MODIFIED:
            latest_artifact = self._latest_artifact(call.request.original_url)
            if latest_artifact is None:
                self._add_failure(
                    failures,
                    _STAGE_ACQUIRE,
                    "not_modified_without_cache",
                    "A source reported not modified without an accepted cached artifact",
                    retryable=True,
                )
                return None
            self._register_artifact(latest_artifact)
            self._persist_policy(latest_artifact, call.routed.binding)
            return _AcceptedAcquisition(
                artifact=latest_artifact,
                source_identity_key=latest_artifact.artifact_series_id,
                content=None,
                is_new_artifact=False,
            )
        if result.status is not AcquisitionStatus.ACQUIRED:
            assert result.failure is not None
            self._add_collection_failure(failures, _STAGE_ACQUIRE, result.failure)
            return None
        assert result.content is not None
        assert result.content_sha256 is not None
        assert result.media_type is not None
        media_type = result.media_type.split(";", 1)[0].strip().casefold()
        if media_type not in call.routed.binding.allowed_media_types:
            self._add_failure(
                failures,
                _STAGE_ACQUIRE,
                "media_type_blocked",
                "Acquired content fell outside the configured media policy",
                retryable=False,
            )
            return None
        if len(result.content) > call.request.max_bytes:
            self._add_failure(
                failures,
                _STAGE_ACQUIRE,
                "content_budget_exceeded",
                "Acquired content exceeded the configured byte budget",
                retryable=False,
            )
            return None
        if sha256(result.content).hexdigest() != result.content_sha256:
            self._add_failure(
                failures,
                _STAGE_ACQUIRE,
                "content_hash_mismatch",
                "Acquired content failed immutable hash verification",
                retryable=False,
            )
            return None
        try:
            artifact, is_new = self._persist_source_artifact(
                lead=call.routed.lead,
                content=result.content,
                media_type=result.media_type,
                content_sha256=result.content_sha256,
                retrieved_at=result.completed_at,
                source_event_time=result.source_event_time,
                collection_operation_id=call.request.acquisition_request_id,
                artifact_kind=call.artifact_kind,
                display_name=call.display_name,
            )
            self._persist_policy(artifact, call.routed.binding)
        except Exception:
            self._add_failure(
                failures,
                _STAGE_ACQUIRE,
                "artifact_persistence_failed",
                "An acquired public source could not be persisted safely",
                retryable=True,
            )
            return None
        return _AcceptedAcquisition(
            artifact=artifact,
            source_identity_key=artifact.artifact_series_id,
            content=result.content,
            is_new_artifact=is_new,
        )

    def _showcase_projection(
        self,
        accepted: _AcceptedAcquisition,
    ) -> HackathonShowcaseProjection | None:
        if accepted.content is not None:
            return project_hackathon_showcase(
                source_artifact=accepted.artifact,
                content=accepted.content,
            )
        for record in self._memory.list_records(RecordCategory.CANONICAL_ENTITY):
            if (
                record.payload.get("projection_version") == HACKATHON_PROJECTION_VERSION
                and record.payload.get("source_artifact_id") == accepted.artifact.source_artifact_id
            ):
                return HackathonShowcaseProjection.model_validate_json(
                    json.dumps(dict(record.payload))
                )
        return None

    async def _project_showcase(
        self,
        *,
        run_id: str,
        accepted: _AcceptedAcquisition,
        failures: list[PipelineFailure],
    ) -> tuple[HackathonShowcaseProjection | None, PublicContactProjection | None]:
        if accepted.content is not None and self._structured_page_extractor is not None:
            request = PublicPageStructuredRequest(
                request_id=self._id("structured-extraction"),
                source_artifact=accepted.artifact,
                content=accepted.content,
            )
            try:
                result = await self._structured_page_extractor.extract(request)
            except Exception:
                self._add_failure(
                    failures,
                    _STAGE_CANONICALIZE,
                    "structured_extraction_failed",
                    "Optional structured page extraction failed safely",
                    retryable=True,
                )
                self._append_collection_telemetry(
                    run_id=run_id,
                    record_id=self._id("structured-extraction-failure"),
                    recorded_at=self._now(),
                    payload={
                        "adapter_id": OPENAI_STRUCTURED_ADAPTER_ID,
                        "operation": "structured_page_extraction",
                        "status": "failed",
                        "safe_code": "structured_extraction_failed",
                        "source_artifact_id": accepted.artifact.source_artifact_id,
                    },
                )
            else:
                self._append_collection_telemetry(
                    run_id=run_id,
                    record_id=result.result_id,
                    recorded_at=result.completed_at,
                    payload={
                        "adapter_id": OPENAI_STRUCTURED_ADAPTER_ID,
                        "operation": "structured_page_extraction",
                        "status": result.status.value,
                        "source_artifact_id": accepted.artifact.source_artifact_id,
                        "requested_model": result.requested_model,
                        "model_version": result.model_version.model_dump(mode="json"),
                        "provider_response_id": result.provider_response_id.model_dump(mode="json"),
                        "usage": result.usage.model_dump(mode="json"),
                        "safe_code": (
                            result.failure.safe_code if result.failure is not None else None
                        ),
                    },
                )
                if result.request_id != request.request_id:
                    self._add_failure(
                        failures,
                        _STAGE_CANONICALIZE,
                        "structured_provenance_mismatch",
                        "Structured extraction returned mismatched request provenance",
                        retryable=False,
                    )
                elif result.status is StructuredExtractionStatus.SUCCEEDED:
                    assert result.projection is not None
                    assert result.contact_projection is not None
                    if (
                        result.projection.source_artifact_id != accepted.artifact.source_artifact_id
                        or result.contact_projection.source_artifact_id
                        != accepted.artifact.source_artifact_id
                    ):
                        self._add_failure(
                            failures,
                            _STAGE_CANONICALIZE,
                            "structured_provenance_mismatch",
                            "Structured projections referenced a different Source Artifact",
                            retryable=False,
                        )
                    else:
                        return result.projection, result.contact_projection
                else:
                    assert result.failure is not None
                    self._add_collection_failure(
                        failures,
                        _STAGE_CANONICALIZE,
                        result.failure,
                    )
        return self._showcase_projection(accepted), None

    def _persist_primary_projections(
        self,
        *,
        accepted: _AcceptedAcquisition,
        candidate: OutboundCandidateView,
        showcase: HackathonShowcaseProjection | None,
        public_contacts: PublicContactProjection | None,
        failures: list[PipelineFailure],
    ) -> tuple[str, ...]:
        output_ids: list[str] = []
        if showcase is not None:
            try:
                self._persist_hackathon_projection(showcase, candidate.outbound_candidate_id)
                output_ids.append(showcase.projection_id)
            except Exception:
                self._add_failure(
                    failures,
                    _STAGE_CANONICALIZE,
                    "hackathon_projection_persistence_failed",
                    "A public showcase relationship projection could not be persisted safely",
                    retryable=True,
                )
        if public_contacts is not None:
            try:
                self._persist_public_contact_projection(
                    public_contacts,
                    candidate.outbound_candidate_id,
                    accepted.artifact.retrieved_at,
                )
                output_ids.append(public_contacts.projection_id)
                contact_evidence = project_public_contact_evidence(
                    source_artifact=accepted.artifact,
                    contacts=public_contacts,
                    subject=SubjectRef(
                        kind=EntityKind.OUTBOUND_CANDIDATE,
                        subject_id=candidate.outbound_candidate_id,
                    ),
                )
                if contact_evidence is not None:
                    self._persist_source_evidence(
                        contact_evidence,
                        accepted.artifact.retrieved_at,
                    )
                    output_ids.append(contact_evidence.projection_id)
                self._service.publish_candidate_public_contacts(
                    candidate.outbound_candidate_id,
                    tuple(
                        PublicContactRouteView(
                            route_id=route.route_id,
                            kind=PublicContactRouteKind(route.kind.value),
                            label=route.label,
                            value=route.value,
                            href=route.href,
                            classification="public",
                            source_artifact_id=route.source_artifact_id,
                            source_name=route.source_name,
                            source_locator=route.source_locator,
                            collected_at=route.collected_at,
                        )
                        for route in public_contacts.routes
                    ),
                )
            except Exception:
                self._add_failure(
                    failures,
                    _STAGE_CANONICALIZE,
                    "public_contact_projection_failed",
                    "Validated public contact routes could not be projected safely",
                    retryable=True,
                )
        if accepted.content is None:
            return tuple(output_ids)
        try:
            evidence_projection = project_public_source_evidence(
                source_artifact=accepted.artifact,
                content=accepted.content,
                subject=SubjectRef(
                    kind=EntityKind.OUTBOUND_CANDIDATE,
                    subject_id=candidate.outbound_candidate_id,
                ),
                hackathon=showcase,
            )
            if evidence_projection is not None:
                self._persist_source_evidence(evidence_projection, accepted.artifact.retrieved_at)
                output_ids.append(evidence_projection.projection_id)
        except Exception:
            self._add_failure(
                failures,
                _STAGE_CANONICALIZE,
                "source_evidence_projection_failed",
                "Authoritative public source assertions could not be projected safely",
                retryable=True,
            )
        return tuple(output_ids)

    @staticmethod
    def _deck_follow_ups(
        canonicalized: list[_CanonicalizedLead],
    ) -> tuple[tuple[_CanonicalizedLead, PublicHackathonLink], ...]:
        selected: list[tuple[_CanonicalizedLead, PublicHackathonLink]] = []
        seen: set[tuple[str, str]] = set()
        for item in canonicalized:
            if item.showcase is None:
                continue
            for link in item.showcase.links:
                if link.kind is not HackathonLinkKind.PITCH_DECK:
                    continue
                key = (item.candidate.outbound_candidate_id, link.url)
                if key in seen:
                    continue
                seen.add(key)
                selected.append((item, link))
        return tuple(selected)

    def _deck_acquisition_call(
        self,
        follow_up: tuple[_CanonicalizedLead, PublicHackathonLink],
        *,
        command: BoundedSourcingCommand,
    ) -> _AcquisitionCall:
        canonical, link = follow_up
        binding = next(
            (candidate for candidate in self._adapters if candidate.is_generic),
            canonical.routed.binding,
        )
        if self._public_pdf_acquisition is not None:
            binding = replace(
                binding,
                adapter_id="bounded-public-pdf-v0",
                acquisition=self._public_pdf_acquisition,
                source_categories=(SourceCategory.HACKATHON,),
                authoritative=False,
                artifact_kind=SourceArtifactKind.DOCUMENT,
                allowed_media_types=("application/pdf",),
            )
        normalized_url, host = _normalize_public_url(link.url)
        if not _command_domain_allows(host, command):
            raise ValueError("public deck URL falls outside the command domain policy")
        lead_digest = sha256(
            f"{canonical.showcase.projection_id if canonical.showcase else ''}\0{link.url}".encode()
        ).hexdigest()[:24]
        lead = DiscoveryLead(
            lead_id=f"hackathon-deck-lead:{lead_digest}",
            retrieval_request_id=canonical.routed.lead.retrieval_request_id,
            original_url=normalized_url,
            source_category=SourceCategory.HACKATHON,
            discovered_at=self._now(),
            rank=1,
            title=KnowledgeValue[str].known(link.label),
            provider_summary=KnowledgeValue[str].unknown(
                "The exact deck URL comes from the acquired showcase, not a search snippet"
            ),
            retrieval_relevance=KnowledgeValue[float].unknown(
                "A linked public deck has no provider relevance score"
            ),
        )
        routed = _RoutedLead(binding=binding, lead=lead, normalized_url=normalized_url)
        call = self._acquisition_call(
            routed,
            command=command,
            artifact_kind=SourceArtifactKind.DOCUMENT,
            display_name=f"Public pitch deck: {link.label}"[:200],
        )
        if self._public_pdf_acquisition is None:
            return call
        return replace(
            call,
            request=call.request.model_copy(update={"max_bytes": self._public_pdf_max_bytes}),
        )

    def _coverage(self, candidate: OutboundCandidateView) -> CoverageSummary:
        artifact_records = []
        for artifact_id in candidate.source_artifact_ids:
            record = self._memory.latest(RecordCategory.SOURCE_ARTIFACT, artifact_id)
            if record is not None:
                artifact_records.append(record)
        series_ids = {str(record.payload["artifact_series_id"]) for record in artifact_records}
        categories = tuple(
            sorted({str(record.payload["source_category"]) for record in artifact_records})
        )
        claims = self._memory.list_records(
            RecordCategory.CLAIM,
            subject_id=candidate.outbound_candidate_id,
        )
        evidence_count = len(claims)
        source_count = len(series_ids)
        if len(categories) >= 3 and evidence_count >= 8:
            level = CoverageLevel.HIGH
        elif len(categories) >= 2 and evidence_count >= 4:
            level = CoverageLevel.MEDIUM
        else:
            level = CoverageLevel.LOW
        missing = ["founder_identity", "corroborated_traction"]
        if not evidence_count:
            missing.append("claim_level_evidence")
        if source_count < 2:
            missing.append("cross_source_corroboration")
        freshest = (
            KnowledgeValue[datetime].known(max(record.recorded_at for record in claims))
            if claims
            else KnowledgeValue[datetime].unknown(
                "Acquired content has no accepted structured Claim Evidence"
            )
        )
        return CoverageSummary(
            level=level,
            source_count=source_count,
            artifact_count=len(candidate.source_artifact_ids),
            evidence_count=evidence_count,
            source_categories=categories,
            missing_fields=tuple(missing),
            freshest_evidence_at=freshest,
        )

    def _cached_artifact(
        self,
        original_url: str,
        requested_at: UTCDateTime,
    ) -> SourceArtifact | None:
        if self._cache_ttl <= timedelta(0):
            return None
        artifact = self._latest_artifact(original_url)
        if artifact is None or artifact.availability is not ArtifactAvailability.AVAILABLE:
            return None
        if requested_at - artifact.retrieved_at > self._cache_ttl:
            return None
        return artifact

    def _latest_artifact(self, original_url: str) -> SourceArtifact | None:
        history = self._artifact_history(original_url)
        return max(history, key=lambda item: item.version_number, default=None)

    def _artifact_history(self, original_url: str) -> tuple[SourceArtifact, ...]:
        series_id = _artifact_series_id(original_url)
        return tuple(
            SourceArtifact.model_validate_json(json.dumps(dict(record.payload)))
            for record in self._memory.list_records(
                RecordCategory.SOURCE_ARTIFACT,
                subject_id=series_id,
            )
        )

    def _persist_source_artifact(
        self,
        *,
        lead: DiscoveryLead,
        content: bytes,
        media_type: str,
        content_sha256: str,
        retrieved_at: UTCDateTime,
        source_event_time: KnowledgeValue[UTCDateTime],
        collection_operation_id: str,
        artifact_kind: SourceArtifactKind,
        display_name: str | None,
    ) -> tuple[SourceArtifact, bool]:
        series_id = _artifact_series_id(lead.original_url)
        history = self._artifact_history(lead.original_url)
        existing = next(
            (artifact for artifact in history if artifact.content_sha256 == content_sha256),
            None,
        )
        if existing is not None:
            self._artifact_store.put(
                existing.source_artifact_id,
                content,
                expected_sha256=existing.content_sha256,
            )
            self._register_artifact(existing)
            return existing, False

        previous = max(history, key=lambda item: item.version_number, default=None)
        artifact = SourceArtifact(
            source_artifact_id=self._id("source-artifact"),
            artifact_series_id=series_id,
            artifact_version_id=self._id("source-version"),
            version_number=(1 if previous is None else previous.version_number + 1),
            previous_source_artifact_id=(None if previous is None else previous.source_artifact_id),
            kind=artifact_kind,
            source_category=lead.source_category,
            classification=DataClassification.PUBLIC,
            origin_locator=lead.original_url,
            display_name=(display_name or _lead_display_name(lead))[:200],
            media_type=media_type,
            content_sha256=content_sha256,
            retrieved_at=retrieved_at,
            source_event_time=source_event_time,
            collection_operation_id=collection_operation_id,
            availability=ArtifactAvailability.AVAILABLE,
        )
        self._artifact_store.put(
            artifact.source_artifact_id,
            content,
            expected_sha256=artifact.content_sha256,
        )
        self._memory.append_source_artifact(artifact)
        self._register_artifact(artifact)
        return artifact, True

    def _register_artifact(self, artifact: SourceArtifact) -> None:
        self._service.register_source_artifact(
            artifact_id=artifact.source_artifact_id,
            content_sha256=artifact.content_sha256,
            media_type=artifact.media_type,
            display_name=artifact.display_name,
        )

    def _persist_policy(
        self,
        artifact: SourceArtifact,
        binding: SourceAdapterBinding,
    ) -> PublicSourcePolicyRecord:
        record = project_public_source_policy(
            source_artifact=artifact,
            adapter_id=binding.adapter_id,
            policy=binding.policy,
        )
        self._memory.append_many_idempotent(
            (
                NewRecord(
                    category=RecordCategory.CANONICAL_ENTITY,
                    record_id=record.record_id,
                    version_id=record.record_version_id,
                    subject_id=artifact.source_artifact_id,
                    recorded_at=artifact.retrieved_at,
                    payload=record.model_dump(mode="json"),
                ),
            )
        )
        return record

    def _persist_hackathon_projection(
        self,
        projection: HackathonShowcaseProjection,
        candidate_id: str,
    ) -> None:
        artifact_record = self._memory.latest(
            RecordCategory.SOURCE_ARTIFACT,
            projection.source_artifact_id,
        )
        if artifact_record is None:
            raise ValueError("hackathon projection source artifact is not persisted")
        self._memory.append_many_idempotent(
            (
                NewRecord(
                    category=RecordCategory.CANONICAL_ENTITY,
                    record_id=projection.projection_id,
                    version_id=f"{projection.projection_id}:v0",
                    subject_id=candidate_id,
                    recorded_at=artifact_record.recorded_at,
                    payload=projection.model_dump(mode="json"),
                ),
            )
        )

    def _persist_public_contact_projection(
        self,
        projection: PublicContactProjection,
        candidate_id: str,
        recorded_at: UTCDateTime,
    ) -> None:
        self._memory.append_many_idempotent(
            (
                NewRecord(
                    category=RecordCategory.CANONICAL_ENTITY,
                    record_id=projection.projection_id,
                    version_id=projection.projection_version_id,
                    subject_id=candidate_id,
                    recorded_at=recorded_at,
                    payload=projection.model_dump(mode="json"),
                ),
            )
        )

    def _persist_source_evidence(
        self,
        projection: PublicSourceEvidenceProjection | PublicContactEvidenceProjection,
        recorded_at: UTCDateTime,
    ) -> None:
        records: list[NewRecord] = [
            NewRecord(
                category=RecordCategory.CANONICAL_ENTITY,
                record_id=projection.projection_id,
                version_id=projection.projection_version_id,
                subject_id=projection.subject.subject_id,
                recorded_at=recorded_at,
                payload=projection.model_dump(mode="json"),
            )
        ]
        records.extend(
            NewRecord(
                category=RecordCategory.OBSERVATION,
                record_id=item.observation_id,
                version_id=item.observation_version_id,
                subject_id=projection.subject.subject_id,
                recorded_at=item.retrieved_at,
                payload=item.model_dump(mode="json"),
            )
            for item in projection.observations
        )
        records.extend(
            NewRecord(
                category=RecordCategory.EVIDENCE,
                record_id=item.evidence_id,
                version_id=item.evidence_id,
                subject_id=item.claim_id,
                recorded_at=item.collected_at,
                payload=item.model_dump(mode="json"),
            )
            for item in projection.evidence
        )
        records.extend(
            NewRecord(
                category=RecordCategory.CLAIM,
                record_id=item.claim_id,
                version_id=item.claim_version_id,
                subject_id=projection.subject.subject_id,
                recorded_at=item.created_at,
                payload=item.model_dump(mode="json"),
            )
            for item in projection.claims
        )
        self._memory.append_many_idempotent(tuple(records))

    def _persist_hackathon_deck_relationship(
        self,
        relationship: PublicHackathonDeckRelationship,
    ) -> None:
        self._memory.append_many_idempotent(
            (
                NewRecord(
                    category=RecordCategory.CANONICAL_ENTITY,
                    record_id=relationship.relationship_id,
                    version_id=relationship.relationship_version_id,
                    subject_id=relationship.candidate_id,
                    recorded_at=relationship.acquired_at,
                    payload=relationship.model_dump(mode="json"),
                ),
            )
        )

    def _persist_public_deck_ocr(
        self,
        *,
        artifact: SourceArtifact,
        attempted_at: UTCDateTime,
        extraction: PdfExtractionResult | None,
        safe_code: str | None,
    ) -> PublicDeckOcrRecord:
        record_digest = sha256(
            (
                f"{artifact.source_artifact_id}\x1f{artifact.artifact_version_id}\x1f"
                f"{_PUBLIC_DECK_OCR_VERSION}"
            ).encode()
        ).hexdigest()[:32]
        state: Literal["known", "unknown"] = "known" if extraction is not None else "unknown"
        version_digest = sha256(
            (
                f"{record_digest}\x1f{state}\x1f{safe_code or ''}\x1f"
                f"{extraction.extraction_id if extraction is not None else ''}"
            ).encode()
        ).hexdigest()[:32]
        record = PublicDeckOcrRecord(
            record_id=f"public-deck-ocr:{record_digest}",
            record_version_id=f"public-deck-ocr-version:{version_digest}",
            source_artifact_id=artifact.source_artifact_id,
            state=state,
            extraction=extraction,
            safe_code=safe_code,
            attempted_at=attempted_at,
        )
        self._memory.append_many_idempotent(
            (
                NewRecord(
                    category=RecordCategory.CANONICAL_ENTITY,
                    record_id=record.record_id,
                    version_id=record.record_version_id,
                    subject_id=artifact.source_artifact_id,
                    recorded_at=attempted_at,
                    payload=record.model_dump(mode="json"),
                ),
            )
        )
        return record

    def _append_public_deck_ocr_telemetry(
        self,
        *,
        run_id: str,
        artifact: SourceArtifact,
        status: Literal["known", "unknown"],
        safe_code: str | None,
        extraction: PdfExtractionResult | None = None,
    ) -> None:
        self._append_collection_telemetry(
            run_id=run_id,
            record_id=self._id("public-deck-ocr-telemetry"),
            recorded_at=self._now(),
            payload={
                "adapter_id": "public-deck-ocr-v0",
                "operation": "extract_public_pdf",
                "status": status,
                "source_artifact_id": artifact.source_artifact_id,
                "content_sha256": artifact.content_sha256,
                "extraction_id": (extraction.extraction_id if extraction is not None else None),
                "extractor_version": (
                    extraction.extractor_version if extraction is not None else None
                ),
                "model_version": (
                    extraction.model_version.model_dump(mode="json")
                    if extraction is not None
                    else None
                ),
                "safe_code": safe_code,
            },
        )

    def _add_collection_failure(
        self,
        failures: list[PipelineFailure],
        stage_key: str,
        failure: CollectionFailure,
    ) -> None:
        self._add_failure(
            failures,
            stage_key,
            failure.safe_code,
            failure.safe_message,
            retryable=failure.retryable,
        )

    def _add_failure(
        self,
        failures: list[PipelineFailure],
        stage_key: str,
        safe_code: str,
        safe_message: str,
        *,
        retryable: bool,
    ) -> None:
        if len(failures) >= _MAX_FAILURES:
            return
        failures.append(
            PipelineFailure(
                failure_id=self._id("pipeline-failure"),
                stage_key=stage_key,
                safe_code=safe_code,
                safe_message=safe_message,
                retryable=retryable,
                occurred_at=self._now(),
            )
        )

    @staticmethod
    def _terminal_stage(
        queued: PipelineStage,
        *,
        started_at: UTCDateTime,
        completed_at: UTCDateTime,
        accepted_output_ids: tuple[str, ...],
        failures: tuple[PipelineFailure, ...],
        skip: bool,
    ) -> PipelineStage:
        if failures:
            status = PipelineStageStatus.FAILED
        elif skip:
            status = PipelineStageStatus.SKIPPED
        else:
            status = PipelineStageStatus.SUCCEEDED
        return PipelineStage(
            stage_key=queued.stage_key,
            status=status,
            queued_at=queued.queued_at,
            started_at=started_at,
            completed_at=completed_at,
            accepted_output_ids=accepted_output_ids,
            failure_ids=tuple(failure.failure_id for failure in failures),
        )

    def _append_run_snapshot(self, run: PipelineRun) -> None:
        self._memory.append_pipeline_run(
            run,
            snapshot_version_id=self._id("run-snapshot"),
            recorded_at=self._now(),
        )

    def _append_collection_telemetry(
        self,
        *,
        run_id: str,
        record_id: str,
        recorded_at: UTCDateTime,
        payload: dict[str, object],
    ) -> None:
        self._memory.append(
            NewRecord(
                category=RecordCategory.COLLECTION_TELEMETRY,
                record_id=record_id,
                version_id=self._id("collection-snapshot"),
                subject_id=run_id,
                recorded_at=recorded_at,
                payload=payload,
            )
        )

    def _discovery_telemetry(
        self,
        result: DiscoveryResult,
        adapter_id: str,
    ) -> dict[str, object]:
        return {
            "schema_version": result.schema_version,
            "adapter_id": adapter_id,
            "result_id": result.result_id,
            "request_id": result.request_id,
            "status": result.status.value,
            "completed_at": result.completed_at.isoformat(),
            "leads": [item.model_dump(mode="json") for item in result.leads[: self._max_results]],
            "failures": [item.model_dump(mode="json") for item in result.failures[:_MAX_FAILURES]],
            "usage": result.usage.model_dump(mode="json"),
        }

    @staticmethod
    def _acquisition_telemetry(
        result: AcquisitionResult,
        adapter_id: str,
    ) -> dict[str, object]:
        return {
            "schema_version": result.schema_version,
            "adapter_id": adapter_id,
            "result_id": result.result_id,
            "acquisition_request_id": result.acquisition_request_id,
            "original_url": result.original_url,
            "status": result.status.value,
            "completed_at": result.completed_at.isoformat(),
            "media_type": result.media_type,
            "content_sha256": result.content_sha256,
            "failure": (
                result.failure.model_dump(mode="json") if result.failure is not None else None
            ),
        }


class TavilySourcingCoordinator(MultiAdapterSourcingCoordinator):
    """Backward-compatible one-adapter composition for the selected P0 generic provider."""

    def __init__(
        self,
        *,
        discovery: DiscoveryPort,
        acquisition: AcquisitionPort,
        service: FakeVCBrainService,
        memory: SQLiteMemory,
        artifact_store: PrivateArtifactStore,
        screening_bridge: DeterministicScreeningBridge,
        now: Callable[[], UTCDateTime],
        id_factory: Callable[[str], str],
        max_results: int,
        max_pages: int,
        max_bytes: int,
        timeout_seconds: float,
        cache_ttl_seconds: int = 900,
    ) -> None:
        adapter_id = getattr(discovery, "adapter_id", "generic-web-v0")
        if not isinstance(adapter_id, str) or _STABLE_ID.fullmatch(adapter_id) is None:
            adapter_id = "generic-web-v0"
        binding = SourceAdapterBinding(
            adapter_id=adapter_id,
            discovery=discovery,
            acquisition=acquisition,
            source_categories=None,
            authoritative=False,
            artifact_kind=SourceArtifactKind.WEB_SNAPSHOT,
            allowed_media_types=("text/markdown", "text/plain"),
            policy=PublicSourceCollectionPolicy(
                collection_purpose="investor sourcing and evaluation",
                lawful_basis="legitimate interests with human review and removal",
                source_terms=KnowledgeValue[str].unknown(
                    "Original-site terms vary and require source-specific review"
                ),
                robots_policy=KnowledgeValue[str].unknown(
                    "The generic provider must honor the configured public-source policy"
                ),
            ),
        )
        super().__init__(
            adapters=(binding,),
            service=service,
            memory=memory,
            artifact_store=artifact_store,
            screening_bridge=screening_bridge,
            now=now,
            id_factory=id_factory,
            max_results=max_results,
            max_pages=max_pages,
            max_bytes=max_bytes,
            timeout_seconds=timeout_seconds,
            cache_ttl_seconds=cache_ttl_seconds,
        )


def _artifact_series_id(original_url: str) -> str:
    digest = sha256(original_url.encode("utf-8")).hexdigest()[:32]
    return f"web-source:{digest}"


def _candidate_name(
    lead: DiscoveryLead,
    showcase: HackathonShowcaseProjection | None,
) -> str:
    if showcase is not None and showcase.project_name.value is not None:
        return f"Showcase project: {showcase.project_name.value}"[:300]
    return _unresolved_candidate_name(lead.original_url)


def _lead_display_name(lead: DiscoveryLead) -> str:
    if lead.title.value is not None:
        return lead.title.value[:200]
    host = urlsplit(lead.original_url).hostname or "public source"
    return f"Public source from {host}"[:200]


def _round_evidence_keys(
    canonicalized: list[_CanonicalizedLead],
    artifact_ids: list[str],
) -> frozenset[str]:
    keys = {f"artifact:{artifact_id}" for artifact_id in artifact_ids}
    for item in canonicalized:
        showcase = item.showcase
        if showcase is not None:
            if showcase.event_name.value is not None:
                keys.add(f"event:{showcase.event_name.value.casefold()}")
            if showcase.project_name.value is not None:
                keys.add(f"project:{showcase.project_name.value.casefold()}")
            keys.update(
                f"participant:{participant.display_name.casefold()}"
                for participant in showcase.participants
            )
            keys.update(f"link:{link.kind.value}:{link.url}" for link in showcase.links)
        if item.public_contacts is not None:
            keys.update(
                f"contact:{route.kind.value}:{route.value}" for route in item.public_contacts.routes
            )
    return frozenset(keys)


def _follow_up_query(
    base_query: str,
    gaps: tuple[str, ...],
    previous_queries: tuple[str, ...],
) -> str:
    terms: list[str] = []
    mapping = {
        "hackathon_showcase": "official hackathon showcase",
        "event": "hackathon event",
        "project": "project showcase",
        "participants": "team makers participants",
        "repository": "public repository GitHub",
        "demo": "public product demo",
        "pitch_deck": "public pitch deck slides",
        "public_contact": "official website public contact",
    }
    for gap in gaps:
        term = (
            gap.removeprefix("source_category:").replace("_", " ")
            if gap.startswith("source_category:")
            else mapping.get(gap, gap.replace("_", " "))
        )
        if term not in terms:
            terms.append(term)
        if len(terms) >= 3:
            break
    suffix = " ".join(terms) or "additional original public evidence"
    round_marker = f" follow-up {len(previous_queries)}"
    available = max(1, 400 - len(suffix) - len(round_marker) - 2)
    query = f"{base_query[:available].rstrip()} {suffix}{round_marker}".strip()
    if query in previous_queries:
        query = f"{base_query[:360].rstrip()} additional public evidence {len(previous_queries)}"
    return query[:400].strip()


def _unresolved_candidate_name(original_url: str) -> str:
    host = urlsplit(original_url).hostname or "unknown public source"
    return f"Unresolved public lead from {host}"[:300]


def _normalize_public_url(value: str) -> tuple[str, str]:
    if len(value) > 2_048:
        raise ValueError("public source URL exceeds the configured limit")
    parsed = urlsplit(value)
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise ValueError("public source URL must use HTTP(S)")
    if parsed.username is not None or parsed.password is not None or not parsed.hostname:
        raise ValueError("credential-bearing or hostless URLs are not public sources")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("public source URL has an invalid port") from error
    if port not in {None, 80, 443}:
        raise ValueError("public source URL uses a prohibited port")
    host = parsed.hostname.casefold().rstrip(".")
    if host == "localhost" or host.endswith(_PROHIBITED_SUFFIXES):
        raise ValueError("local sources are prohibited")
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as error:
            raise ValueError("public source hostname is invalid") from error
    else:
        if not address.is_global:
            raise ValueError("private-network sources are prohibited")
    netloc = host
    if port is not None and not (
        (parsed.scheme.casefold() == "http" and port == 80)
        or (parsed.scheme.casefold() == "https" and port == 443)
    ):
        netloc = f"{host}:{port}"
    normalized = urlunsplit(
        (
            parsed.scheme.casefold(),
            netloc,
            parsed.path or "",
            parsed.query,
            "",
        )
    )
    return normalized, host


def resolve_public_deck_pdf_url(value: str) -> PublicDeckPdfTarget:
    """Allow a direct HTTPS PDF or derive the canonical Google Slides PDF export URL."""

    normalized, host = _normalize_public_url(value)
    parsed = urlsplit(normalized)
    if parsed.scheme != "https":
        raise ValueError("public deck acquisition requires HTTPS")
    if parsed.path.casefold().endswith(".pdf"):
        return PublicDeckPdfTarget(
            source_url=normalized,
            acquisition_url=normalized,
            normalization="direct_pdf",
        )
    if host != "docs.google.com":
        raise ValueError("public deck URL is not a direct PDF or Google Slides presentation")
    match = _GOOGLE_SLIDES_DECK.fullmatch(parsed.path)
    if match is None:
        raise ValueError("Google Slides URL does not identify a supported presentation")
    document_id = match.group("document_id")
    export_url = f"https://docs.google.com/presentation/d/{document_id}/export/pdf"
    return PublicDeckPdfTarget(
        source_url=normalized,
        acquisition_url=export_url,
        normalization="google_slides_export_pdf",
    )


def _url_key(value: str) -> str:
    normalized, _host = _normalize_public_url(value)
    return normalized.rstrip("/")


def _domain_matches(host: str, policy_domain: str) -> bool:
    return host == policy_domain or host.endswith(f".{policy_domain}")


def _command_domain_allows(host: str, command: BoundedSourcingCommand) -> bool:
    try:
        allowed = tuple(_normalize_policy_domain(value) for value in command.allowed_domains)
        excluded = tuple(_normalize_policy_domain(value) for value in command.excluded_domains)
    except ValueError:
        return False
    if any(_domain_matches(host, item) for item in excluded):
        return False
    return not allowed or any(_domain_matches(host, item) for item in allowed)


def _normalize_policy_domain(value: str) -> str:
    candidate = value.strip().casefold().rstrip(".")
    if (
        not candidate
        or "://" in candidate
        or "/" in candidate
        or "@" in candidate
        or ":" in candidate
    ):
        raise ValueError("domain policy entries must be bare hostnames")
    try:
        encoded = candidate.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ValueError("domain policy entry is invalid") from error
    if encoded == "localhost" or encoded.endswith(_PROHIBITED_SUFFIXES):
        raise ValueError("local policy domains are prohibited")
    return encoded


__all__ = [
    "BoundedSourcingCommand",
    "MultiAdapterSourcingCoordinator",
    "PublicSourceCollectionPolicy",
    "SourceAdapterBinding",
    "SourcingCoordinatorPort",
    "SourcingUnavailableError",
    "TavilySourcingCoordinator",
    "UnavailableSourcingCoordinator",
]
