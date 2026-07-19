"""Deterministic fake-backed use cases for the MVP HTTP and UX contracts.

This service intentionally performs no live discovery or model calls. It exercises
the common domain contracts, preserves immutable accepted outputs, and provides a
replaceable orchestration boundary for later approved adapters.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from base64 import urlsafe_b64encode
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import RLock
from typing import Final

from founderlookup.application.deck_evidence import (
    DeckEvidenceProjection,
    project_deck_evidence,
)
from founderlookup.application.models import (
    ApplicationReceipt,
    CandidateCollection,
    CriterionMatchOutcome,
    FounderFacingStage,
    FounderStatusView,
    InvestmentThesisRevision,
    OpportunityCollection,
    OpportunityDetail,
    OpportunitySummary,
    OpportunityTiming,
    OutboundCandidateView,
    OutreachMethod,
    OutreachRecord,
    PrivateArtifactDescriptor,
    QueryCriterionResult,
    QueryResult,
    QueryResultItem,
    RunAccepted,
    StatusCapabilityRecord,
    TargetState,
    ThesisDraft,
)
from founderlookup.application.ports import AcceptedApplication, PrivateArtifactReadPort
from founderlookup.application.screening_bridge import (
    DeterministicScreeningBridge,
    DeterministicScreeningProjection,
)
from founderlookup.domain.assessment import (
    AssessmentEnvelope,
    Decision,
    DecisionReadiness,
    DeterministicRuleResult,
    DiligenceAction,
    DiligenceActionStatus,
    FounderAxisAssessment,
    FounderAxisRating,
    FullAssessmentIdentity,
    HumanDecisionDisposition,
    IdeaVsMarketAxisAssessment,
    IdeaVsMarketAxisRating,
    IndependentAxes,
    InvestmentMemo,
    MarketAxisAssessment,
    MarketAxisRating,
    MemoSection,
    MemoSectionKind,
    PreliminaryAssessmentIdentity,
    ReadinessBlocker,
    ReadinessCheck,
    ReadinessCheckStatus,
    Recommendation,
    RecommendationAction,
    RecommendationReason,
    RuleInput,
    RuleOutcome,
    RuleOverride,
    Trend,
)
from founderlookup.domain.common import (
    ComponentVersion,
    EntityKind,
    KnowledgeState,
    KnowledgeValue,
    ScalarValue,
    SubjectRef,
    VersionComponent,
    VersionManifest,
)
from founderlookup.domain.evidence import SourceArtifact, SourceCategory
from founderlookup.domain.lifecycles import (
    ApplicationStatus,
    DecisionReadinessStatus,
    OpportunityOrigin,
    OutboundCandidateStatus,
    PipelineRunStatus,
    PipelineStageStatus,
    ScreeningCaseStatus,
)
from founderlookup.domain.query import OpportunityQueryPlan, QueryCriterionField
from founderlookup.domain.runs import (
    PipelineFailure,
    PipelineRun,
    PipelineRunKind,
    PipelineStage,
)
from founderlookup.domain.scoring import CoverageLevel, CoverageSummary
from founderlookup.ingestion.extraction import PdfExtractionResult
from founderlookup.screening.query_executor import (
    DeterministicQueryExecutor,
    OpportunityQueryRecord,
    RuleOverrideEvent,
    RuleOverrideLedger,
    RuleOverrideLedgerPort,
)

_MAX_COLLECTION_LIMIT: Final = 100
_DEFAULT_MAX_FAKE_PDF_BYTES: Final = 10 * 1024 * 1024


class ApplicationServiceError(RuntimeError):
    """Base class for errors safe to translate at the HTTP boundary."""

    code = "application_service_error"


class ApplicationExtractionOutcome(StrEnum):
    """Provider-neutral terminal outcome for the Application extraction stage."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"


class NotFoundError(ApplicationServiceError, LookupError):
    code = "resource_not_found"


class ConflictError(ApplicationServiceError):
    code = "state_conflict"


class OutboundApplicationLinkUnavailableError(ConflictError):
    """Generic public failure for missing, ineligible, or already-used invitations."""

    code = "outbound_application_link_unavailable"


class CapabilityDeniedError(ApplicationServiceError, PermissionError):
    code = "founder_status_denied"


class RetryLimitError(ApplicationServiceError):
    code = "retry_limit_reached"


class ArtifactUnavailableError(ApplicationServiceError):
    code = "artifact_unavailable"


class InvalidApplicationDeckError(ApplicationServiceError, ValueError):
    code = "invalid_application_deck"


class ApplicationDeckTooLargeError(ApplicationServiceError, ValueError):
    code = "application_deck_too_large"


@dataclass(slots=True)
class _ApplicationState:
    accepted: AcceptedApplication
    status: FounderStatusView
    capability: StatusCapabilityRecord
    artifact: PrivateArtifactDescriptor


@dataclass(slots=True)
class _OpportunityState:
    opportunity_id: str
    origin: OpportunityOrigin
    application_id: str
    company_id: str
    screening_case_id: str
    founder_id: KnowledgeValue[str]
    started_at: datetime
    updated_at: datetime
    outbound_candidate_id: str | None = None
    screening_status: ScreeningCaseStatus = ScreeningCaseStatus.FIRST_PASS
    assessments: list[AssessmentEnvelope] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    run_ids: list[str] = field(default_factory=list)
    deck_evidence_projection: DeckEvidenceProjection | None = None


class FakeVCBrainService:
    """Thread-safe deterministic in-memory use-case implementation for the MVP."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        capability_pepper: bytes | None = None,
        max_retry_attempts: int = 3,
        max_fake_pdf_bytes: int = _DEFAULT_MAX_FAKE_PDF_BYTES,
        artifact_reader: PrivateArtifactReadPort | None = None,
        query_executor: DeterministicQueryExecutor | None = None,
        rule_override_ledger: RuleOverrideLedgerPort | None = None,
        screening_bridge: DeterministicScreeningBridge | None = None,
    ) -> None:
        if max_retry_attempts < 1:
            raise ValueError("max_retry_attempts must be positive")
        if max_fake_pdf_bytes < 1:
            raise ValueError("max_fake_pdf_bytes must be positive")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: secrets.token_hex(16))
        self._capability_pepper = capability_pepper or secrets.token_bytes(32)
        self._max_retry_attempts = max_retry_attempts
        self._max_fake_pdf_bytes = max_fake_pdf_bytes
        self._artifact_reader = artifact_reader
        self._query_executor = query_executor or DeterministicQueryExecutor(
            maximum_results=_MAX_COLLECTION_LIMIT
        )
        self._rule_override_ledger = rule_override_ledger or RuleOverrideLedger()
        self._screening_bridge = screening_bridge or DeterministicScreeningBridge()
        self._lock = RLock()
        self._theses: list[InvestmentThesisRevision] = []
        self._applications: dict[str, _ApplicationState] = {}
        self._artifact_descriptors: dict[str, PrivateArtifactDescriptor] = {}
        self._idempotency: dict[str, tuple[str, AcceptedApplication]] = {}
        self._opportunities: dict[str, _OpportunityState] = {}
        self._application_opportunities: dict[str, str] = {}
        self._candidates: dict[str, OutboundCandidateView] = {}
        self._candidate_applications: dict[str, str] = {}
        self._application_candidates: dict[str, str] = {}
        self._candidate_by_source_key: dict[str, str] = {}
        self._outreach: dict[str, list[OutreachRecord]] = {}
        self._runs: dict[str, PipelineRun] = {}
        self._retry_by_parent: dict[str, str] = {}

    def _id(self) -> str:
        return self._id_factory()

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise RuntimeError("application clock must return UTC")
        return now

    def create_thesis(self, draft: ThesisDraft, *, actor_id: str) -> InvestmentThesisRevision:
        with self._lock:
            revision_number = len(self._theses) + 1
            thesis_id = self._theses[0].thesis_id if self._theses else self._id()
            revision = InvestmentThesisRevision(
                thesis_id=thesis_id,
                thesis_version_id=self._id(),
                revision_number=revision_number,
                created_at=self._now(),
                created_by=actor_id,
                sector=draft.sector,
                stage=draft.stage,
                geography=draft.geography,
                check_size=draft.check_size,
                ownership_target=draft.ownership_target,
                risk_appetite=draft.risk_appetite,
            )
            self._theses.append(revision)
            return revision

    def active_thesis(self) -> InvestmentThesisRevision:
        with self._lock:
            if not self._theses:
                raise NotFoundError("no investment thesis has been configured")
            return self._theses[-1]

    def thesis_history(self) -> tuple[InvestmentThesisRevision, ...]:
        with self._lock:
            return tuple(self._theses)

    def accept_application(
        self,
        *,
        company_name: str,
        display_name: str,
        media_type: str,
        deck_content: bytes,
        idempotency_key: str,
    ) -> ApplicationReceipt:
        """Fallback fake intake used when no provider-neutral intake service is injected."""

        normalized_name = " ".join(company_name.split())
        if not normalized_name:
            raise ValueError("company_name must be non-blank")
        normalized_media_type = media_type.strip().lower()
        if normalized_media_type != "application/pdf":
            raise InvalidApplicationDeckError(
                "the fake Application helper accepts application/pdf only"
            )
        if len(deck_content) > self._max_fake_pdf_bytes:
            raise ApplicationDeckTooLargeError("the fake Application deck exceeds its size limit")
        if not deck_content.startswith(b"%PDF-"):
            raise InvalidApplicationDeckError("the fake Application deck has an invalid signature")
        content_sha256 = hashlib.sha256(deck_content).hexdigest()
        fingerprint = hashlib.sha256(
            normalized_name.casefold().encode("utf-8")
            + b"\0"
            + normalized_media_type.encode("ascii")
            + b"\0"
            + deck_content
        ).hexdigest()
        with self._lock:
            previous = self._idempotency.get(idempotency_key)
            if previous is not None:
                previous_fingerprint, accepted = previous
                if not hmac.compare_digest(previous_fingerprint, fingerprint):
                    raise ConflictError("idempotency key was already used for different content")
                return self.register_application(
                    accepted.model_copy(update={"replayed": True}),
                    display_name=display_name,
                    media_type=normalized_media_type,
                )

            now = self._now()
            accepted = AcceptedApplication(
                application_id=self._id(),
                company_id=self._id(),
                run_id=self._id(),
                source_artifact_id=self._id(),
                source_artifact_sha256=content_sha256,
                received_at=now,
            )
            self._idempotency[idempotency_key] = (fingerprint, accepted)
            self._runs[accepted.run_id] = self._completed_run(
                run_id=accepted.run_id,
                kind=PipelineRunKind.INGESTION,
                input_snapshot_id=accepted.source_artifact_id,
                versions=VersionManifest(),
                stage_keys=("store_original", "fake_extract"),
                accepted_output_ids=(accepted.source_artifact_id,),
                at=now,
            )
            return self.register_application(
                accepted,
                display_name=display_name,
                media_type=normalized_media_type,
            )

    def register_application(
        self,
        accepted: AcceptedApplication,
        *,
        display_name: str,
        media_type: str,
        outbound_candidate_id: str | None = None,
    ) -> ApplicationReceipt:
        """Register an accepted provider-neutral intake result and issue status access."""

        with self._lock:
            candidate: OutboundCandidateView | None = None
            if outbound_candidate_id is not None:
                candidate = self._validated_outbound_application_candidate(outbound_candidate_id)
                if accepted.company_id != candidate.company_id:
                    raise OutboundApplicationLinkUnavailableError(
                        "outbound Application link is unavailable"
                    )
                candidate_application_id = self._candidate_applications.get(outbound_candidate_id)
                if (
                    candidate_application_id is not None
                    and candidate_application_id != accepted.application_id
                ):
                    raise OutboundApplicationLinkUnavailableError(
                        "outbound Application link is unavailable"
                    )
                application_candidate_id = self._application_candidates.get(accepted.application_id)
                if (
                    application_candidate_id is not None
                    and application_candidate_id != outbound_candidate_id
                ):
                    raise OutboundApplicationLinkUnavailableError(
                        "outbound Application link is unavailable"
                    )
            elif accepted.application_id in self._application_candidates:
                raise OutboundApplicationLinkUnavailableError(
                    "outbound Application link is unavailable"
                )

            existing = self._applications.get(accepted.application_id)
            if existing is None:
                token, capability = self._issue_capability(accepted.application_id)
                status = FounderStatusView(
                    application_id=accepted.application_id,
                    received_at=accepted.received_at,
                    stage=FounderFacingStage.RECEIVED,
                    last_updated_at=accepted.received_at,
                    target_state=TargetState.ON_TRACK,
                    next_action="We are processing the submitted deck.",
                )
                artifact = PrivateArtifactDescriptor(
                    artifact_id=accepted.source_artifact_id,
                    content_sha256=accepted.source_artifact_sha256,
                    media_type=media_type,
                    display_name=self._safe_display_name(display_name),
                )
                self._applications[accepted.application_id] = _ApplicationState(
                    accepted=accepted,
                    status=status,
                    capability=capability,
                    artifact=artifact,
                )
                self._artifact_descriptors[artifact.artifact_id] = artifact
                candidate_run_ids: tuple[str, ...] = ()
                if candidate is not None and candidate.preliminary_assessment is not None:
                    candidate_run_ids = (candidate.preliminary_assessment.run_id,)
                opportunity = _OpportunityState(
                    opportunity_id=self._id(),
                    origin=(
                        OpportunityOrigin.OUTBOUND
                        if candidate is not None
                        else OpportunityOrigin.INBOUND
                    ),
                    application_id=accepted.application_id,
                    company_id=accepted.company_id,
                    screening_case_id=self._id(),
                    founder_id=(
                        candidate.founder_id
                        if candidate is not None
                        else KnowledgeValue[str].unknown("founder_identity_unresolved")
                    ),
                    started_at=(
                        candidate.discovered_at if candidate is not None else accepted.received_at
                    ),
                    updated_at=accepted.received_at,
                    outbound_candidate_id=outbound_candidate_id,
                    run_ids=list(dict.fromkeys((*candidate_run_ids, accepted.run_id))),
                )
                self._opportunities[opportunity.opportunity_id] = opportunity
                self._application_opportunities[accepted.application_id] = (
                    opportunity.opportunity_id
                )
                if candidate is not None and outbound_candidate_id is not None:
                    self._candidate_applications[outbound_candidate_id] = accepted.application_id
                    self._application_candidates[accepted.application_id] = outbound_candidate_id
                    self._candidates[outbound_candidate_id] = candidate.model_copy(
                        update={
                            "status": OutboundCandidateStatus.APPLIED,
                            "application_id": accepted.application_id,
                            "updated_at": accepted.received_at,
                        }
                    )
            else:
                opportunity_id = self._application_opportunities.get(accepted.application_id)
                existing_opportunity = (
                    self._opportunities.get(opportunity_id) if opportunity_id is not None else None
                )
                expected_origin = (
                    OpportunityOrigin.OUTBOUND
                    if outbound_candidate_id is not None
                    else OpportunityOrigin.INBOUND
                )
                if (
                    existing_opportunity is None
                    or existing_opportunity.origin is not expected_origin
                    or existing_opportunity.outbound_candidate_id != outbound_candidate_id
                    or existing_opportunity.company_id != accepted.company_id
                ):
                    if outbound_candidate_id is not None:
                        raise OutboundApplicationLinkUnavailableError(
                            "outbound Application link is unavailable"
                        )
                    raise ConflictError("Application origin or canonical Company cannot change")
                # Recompute the bearer without retaining it. Keeping the original
                # digest record makes replay concurrency safe and, critically,
                # preserves an investor's prior revocation.
                token = self._capability_token(accepted.application_id)
                existing.accepted = accepted
                status = existing.status

            if accepted.run_id not in self._runs:
                self._runs[accepted.run_id] = PipelineRun(
                    run_id=accepted.run_id,
                    kind=PipelineRunKind.INGESTION,
                    status=PipelineRunStatus.QUEUED,
                    versions=VersionManifest(),
                    input_snapshot_id=accepted.source_artifact_id,
                    input_snapshot_as_of=accepted.received_at,
                    queued_at=accepted.received_at,
                    stages=(
                        PipelineStage(
                            stage_key="extract_deck",
                            status=PipelineStageStatus.QUEUED,
                            queued_at=accepted.received_at,
                        ),
                    ),
                    accepted_output_ids=(accepted.source_artifact_id,),
                )

            return ApplicationReceipt(
                application_id=accepted.application_id,
                company_id=accepted.company_id,
                run_id=accepted.run_id,
                source_artifact_id=accepted.source_artifact_id,
                status=ApplicationStatus.RECEIVED,
                received_at=accepted.received_at,
                founder_status_capability=token,
                replayed=accepted.replayed,
            )

    def canonical_company_for_outbound_application(self, candidate_id: str) -> str:
        """Resolve the canonical Company only after invitation-state validation."""

        with self._lock:
            return self._validated_outbound_application_candidate(candidate_id).company_id

    def _validated_outbound_application_candidate(
        self,
        candidate_id: str,
    ) -> OutboundCandidateView:
        candidate = self._candidates.get(candidate_id)
        if candidate is None:
            raise OutboundApplicationLinkUnavailableError(
                "outbound Application link is unavailable"
            )
        linked_application_id = self._candidate_applications.get(candidate_id)
        is_unused_invitation = (
            candidate.status
            in {
                OutboundCandidateStatus.ACTIVATED,
                OutboundCandidateStatus.CONTACTED,
            }
            and linked_application_id is None
            and candidate.application_id is None
        )
        is_replay = (
            candidate.status is OutboundCandidateStatus.APPLIED
            and linked_application_id is not None
            and candidate.application_id == linked_application_id
        )
        if not is_unused_invitation and not is_replay:
            raise OutboundApplicationLinkUnavailableError(
                "outbound Application link is unavailable"
            )
        return candidate

    def project_application_deck(
        self,
        application_id: str,
        *,
        extraction: PdfExtractionResult,
        source_artifact: SourceArtifact,
    ) -> DeckEvidenceProjection:
        """Build a bounded projection against immutable canonical Application identity."""

        with self._lock:
            application = self._applications.get(application_id)
            opportunity_id = self._application_opportunities.get(application_id)
            opportunity = (
                self._opportunities.get(opportunity_id) if opportunity_id is not None else None
            )
            if application is None or opportunity is None:
                raise NotFoundError("Application projection context was not found")
            if (
                application.accepted.company_id != opportunity.company_id
                or application.accepted.source_artifact_id
                != source_artifact.source_artifact_id
                or application.artifact.content_sha256 != source_artifact.content_sha256
            ):
                raise ConflictError("Application projection context does not match accepted intake")
            company_id = opportunity.company_id

        return project_deck_evidence(
            extraction=extraction,
            source_artifact=source_artifact,
            application_id=application_id,
            company_id=company_id,
            opportunity_id=opportunity.opportunity_id,
        )

    def register_deck_evidence_projection(
        self,
        projection: DeckEvidenceProjection,
    ) -> DeckEvidenceProjection:
        """Register one accepted immutable projection on the common Opportunity state."""

        with self._lock:
            application = self._applications.get(projection.application_id)
            opportunity = self._opportunities.get(projection.opportunity_id)
            if application is None or opportunity is None:
                raise NotFoundError("Application projection context was not found")
            if (
                opportunity.application_id != projection.application_id
                or opportunity.company_id != projection.company_id
                or application.accepted.source_artifact_id != projection.source_artifact_id
            ):
                raise ConflictError("deck Evidence projection does not match canonical identity")
            existing = opportunity.deck_evidence_projection
            if existing is not None:
                if existing == projection:
                    return existing
                raise ConflictError("accepted deck Evidence projection cannot be replaced")
            opportunity.deck_evidence_projection = projection
            opportunity.updated_at = max(opportunity.updated_at, projection.projected_at)
            return projection

    def record_application_extraction_outcome(
        self,
        application_id: str,
        *,
        outcome: ApplicationExtractionOutcome,
        accepted_output_id: str | None = None,
        additional_output_ids: tuple[str, ...] = (),
        safe_code: str | None = None,
    ) -> PipelineRun:
        """Publish one safe extraction-stage outcome through the observable run."""

        if outcome is ApplicationExtractionOutcome.SUCCEEDED:
            if accepted_output_id is None or not accepted_output_id.strip():
                raise ValueError("successful extraction requires an accepted output identifier")
            if safe_code is not None:
                raise ValueError("successful extraction cannot carry a failure code")
            if any(not item.strip() for item in additional_output_ids):
                raise ValueError("accepted output identifiers must be non-blank")
        else:
            if safe_code is None or not safe_code.strip():
                raise ValueError("failed or blocked extraction requires a safe failure code")
            if accepted_output_id is not None:
                raise ValueError("failed or blocked extraction cannot accept an output")
            if additional_output_ids:
                raise ValueError("failed or blocked extraction cannot accept additional outputs")

        with self._lock:
            try:
                application = self._applications[application_id]
                run = self._runs[application.accepted.run_id]
            except KeyError as error:
                raise NotFoundError("Application ingestion run was not found") from error
            if run.kind is not PipelineRunKind.INGESTION or len(run.stages) != 1:
                raise ConflictError("Application extraction run has an incompatible shape")
            stage = run.stages[0]
            if stage.stage_key != "extract_deck":
                raise ConflictError("Application extraction stage is unavailable")

            if outcome is ApplicationExtractionOutcome.SUCCEEDED:
                assert accepted_output_id is not None  # narrowed by validation above
                stage_output_ids = tuple(
                    dict.fromkeys((accepted_output_id, *additional_output_ids))
                )
                if run.status is PipelineRunStatus.SUCCEEDED:
                    if set(stage_output_ids).issubset(run.accepted_output_ids):
                        return run
                    raise ConflictError("accepted extraction output cannot be replaced")
                now = self._now()
                accepted_output_ids = tuple(
                    dict.fromkeys((*run.accepted_output_ids, *stage_output_ids))
                )
                updated = run.model_copy(
                    update={
                        "status": PipelineRunStatus.SUCCEEDED,
                        "started_at": run.started_at or now,
                        "completed_at": now,
                        "stages": (
                            PipelineStage(
                                stage_key=stage.stage_key,
                                status=PipelineStageStatus.SUCCEEDED,
                                queued_at=stage.queued_at,
                                started_at=stage.started_at or now,
                                completed_at=now,
                                accepted_output_ids=stage_output_ids,
                            ),
                        ),
                        "accepted_output_ids": accepted_output_ids,
                        "failures": (),
                    }
                )
            else:
                assert safe_code is not None  # narrowed by validation above
                if run.status is PipelineRunStatus.SUCCEEDED:
                    return run
                now = self._now()
                failure = PipelineFailure(
                    failure_id=self._id(),
                    stage_key=stage.stage_key,
                    safe_code=safe_code,
                    safe_message=(
                        "Deck extraction is blocked by the active configuration or data policy."
                        if outcome is ApplicationExtractionOutcome.BLOCKED
                        else "Deck extraction could not be completed safely."
                    ),
                    retryable=True,
                    occurred_at=now,
                )
                updated = run.model_copy(
                    update={
                        "status": (
                            PipelineRunStatus.PARTIALLY_SUCCEEDED
                            if run.accepted_output_ids
                            else PipelineRunStatus.FAILED
                        ),
                        "started_at": run.started_at or now,
                        "completed_at": now,
                        "stages": (
                            PipelineStage(
                                stage_key=stage.stage_key,
                                status=PipelineStageStatus.FAILED,
                                queued_at=stage.queued_at,
                                started_at=stage.started_at or now,
                                completed_at=now,
                                failure_ids=(failure.failure_id,),
                            ),
                        ),
                        "failures": (failure,),
                    }
                )
            self._runs[run.run_id] = updated
            return updated

    @staticmethod
    def _safe_display_name(display_name: str) -> str:
        normalized = display_name.replace("\\", "/").rsplit("/", 1)[-1].strip()
        return normalized[:200] or "deck.pdf"

    def _issue_capability(self, application_id: str) -> tuple[str, StatusCapabilityRecord]:
        token = self._capability_token(application_id)
        digest = self._capability_digest(token)
        return token, StatusCapabilityRecord(
            application_id=application_id,
            digest=digest,
            revoked=False,
        )

    def _capability_token(self, application_id: str) -> str:
        """Derive a stable bearer while retaining only its separately keyed digest."""

        token_bytes = hmac.new(
            self._capability_pepper,
            b"founderlookup:founder-status-capability:v1\0" + application_id.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return urlsafe_b64encode(token_bytes).rstrip(b"=").decode("ascii")

    def _capability_digest(self, token: str) -> str:
        return hmac.new(
            self._capability_pepper,
            token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @property
    def capability_digests(self) -> tuple[str, ...]:
        """Testing/telemetry projection that can never reveal bearer capabilities."""

        with self._lock:
            return tuple(state.capability.digest for state in self._applications.values())

    def founder_status(self, capability_token: str) -> FounderStatusView:
        presented = self._capability_digest(capability_token)
        now = self._now()
        with self._lock:
            matched: _ApplicationState | None = None
            for state in self._applications.values():
                if hmac.compare_digest(state.capability.digest, presented):
                    matched = state
            if (
                matched is None
                or matched.capability.revoked
                or (
                    matched.capability.expires_at is not None
                    and matched.capability.expires_at <= now
                )
            ):
                raise CapabilityDeniedError("founder status access denied")
            return self._refresh_founder_target(matched.status, now)

    def revoke_founder_status(self, application_id: str) -> None:
        with self._lock:
            state = self._applications.get(application_id)
            if state is None:
                raise NotFoundError("application was not found")
            state.capability = state.capability.model_copy(update={"revoked": True})

    @staticmethod
    def _refresh_founder_target(status: FounderStatusView, now: datetime) -> FounderStatusView:
        if status.outcome_at is not None:
            target = TargetState.COMPLETE
        else:
            elapsed = now - status.received_at
            if elapsed >= timedelta(hours=24):
                target = TargetState.MISSED
            elif elapsed >= timedelta(hours=20):
                target = TargetState.APPROACHING
            else:
                target = TargetState.ON_TRACK
        return status.model_copy(update={"target_state": target})

    def artifact_descriptor(self, artifact_id: str) -> PrivateArtifactDescriptor:
        with self._lock:
            descriptor = self._artifact_descriptors.get(artifact_id)
            if descriptor is not None:
                return descriptor
        raise NotFoundError("artifact was not found")

    def register_source_artifact(
        self,
        *,
        artifact_id: str,
        content_sha256: str,
        media_type: str,
        display_name: str,
    ) -> PrivateArtifactDescriptor:
        """Make already-stored immutable source bytes available to investors."""

        descriptor = PrivateArtifactDescriptor(
            artifact_id=artifact_id,
            content_sha256=content_sha256,
            media_type=media_type,
            display_name=self._safe_display_name(display_name),
        )
        with self._lock:
            existing = self._artifact_descriptors.get(artifact_id)
            if existing is not None and existing != descriptor:
                raise ConflictError("source artifact metadata cannot be replaced")
            self._artifact_descriptors[artifact_id] = descriptor
        return descriptor

    def read_artifact(
        self,
        artifact_id: str,
        *,
        principal_id: str,
    ) -> tuple[bytes, PrivateArtifactDescriptor]:
        descriptor = self.artifact_descriptor(artifact_id)
        if self._artifact_reader is None:
            raise ArtifactUnavailableError("artifact byte storage is not configured")
        try:
            content = self._artifact_reader.read(
                artifact_id,
                principal_id=principal_id,
                expected_sha256=descriptor.content_sha256,
            )
        except Exception as error:
            raise ArtifactUnavailableError("artifact bytes are unavailable") from error
        return content, descriptor

    def seed_outbound_candidate(
        self,
        *,
        company_name: str,
        founder_id: str | None = None,
        source_artifact_ids: tuple[str, ...] = (),
        source_identity_key: str | None = None,
    ) -> OutboundCandidateView:
        now = self._now()
        with self._lock:
            if source_identity_key is not None:
                existing_id = self._candidate_by_source_key.get(source_identity_key)
                if existing_id is not None:
                    existing = self._candidate(existing_id)
                    merged_artifacts = tuple(
                        dict.fromkeys((*existing.source_artifact_ids, *source_artifact_ids))
                    )
                    updated = existing.model_copy(
                        update={"source_artifact_ids": merged_artifacts, "updated_at": now}
                    )
                    self._candidates[existing_id] = updated
                    return updated
        candidate = OutboundCandidateView(
            outbound_candidate_id=self._id(),
            company_id=self._id(),
            company_name=company_name,
            founder_id=(
                KnowledgeValue[str].known(founder_id)
                if founder_id is not None
                else KnowledgeValue[str].unknown("founder_identity_unresolved")
            ),
            status=OutboundCandidateStatus.DISCOVERED,
            discovered_at=now,
            source_artifact_ids=source_artifact_ids,
            updated_at=now,
        )
        with self._lock:
            self._candidates[candidate.outbound_candidate_id] = candidate
            if source_identity_key is not None:
                self._candidate_by_source_key[source_identity_key] = candidate.outbound_candidate_id
        return candidate

    def list_candidates(
        self,
        *,
        limit: int = 50,
        status: OutboundCandidateStatus | None = None,
    ) -> CandidateCollection:
        bounded = self._bounded_limit(limit)
        with self._lock:
            ordered = sorted(
                (
                    item
                    for item in self._candidates.values()
                    if status is None or item.status is status
                ),
                key=lambda item: (item.discovered_at, item.outbound_candidate_id),
            )
        return CandidateCollection(
            items=tuple(ordered[:bounded]),
            limit=bounded,
            truncated=len(ordered) > bounded,
            applied_filters=((f"status={status.value}",) if status is not None else ()),
        )

    def start_preliminary_assessment(self, candidate_id: str) -> RunAccepted:
        with self._lock:
            candidate = self._candidate(candidate_id)
            thesis = self.active_thesis()
            now = self._now()
            run_id = self._id()
            assessment = self._preliminary_assessment(candidate, thesis, run_id=run_id, at=now)
            run = self._completed_run(
                run_id=run_id,
                kind=PipelineRunKind.INTELLIGENCE,
                input_snapshot_id=candidate.outbound_candidate_id,
                versions=assessment.versions,
                stage_keys=("canonical_snapshot", "fake_preliminary_assessment"),
                accepted_output_ids=(assessment.assessment_id,),
                at=now,
            )
            self._runs[run_id] = run
            next_status = (
                OutboundCandidateStatus.READY_FOR_ACTIVATION
                if assessment.recommendation is not None
                and assessment.recommendation.action is RecommendationAction.ACTIVATE
                else OutboundCandidateStatus.PRELIMINARY_ASSESSMENT
            )
            self._candidates[candidate_id] = candidate.model_copy(
                update={
                    "status": next_status,
                    "preliminary_assessment": assessment,
                    "updated_at": now,
                }
            )
            return self._accepted(run)

    def activate_candidate(
        self,
        candidate_id: str,
        *,
        outreach_draft: str | None = None,
    ) -> OutboundCandidateView:
        with self._lock:
            candidate = self._candidate(candidate_id)
            if candidate.status is not OutboundCandidateStatus.READY_FOR_ACTIVATION:
                raise ConflictError(
                    "candidate must complete preliminary assessment before activation"
                )
            updated = candidate.model_copy(
                update={
                    "status": OutboundCandidateStatus.ACTIVATED,
                    "outreach_draft": outreach_draft
                    or (
                        f"We would like to learn more about {candidate.company_name}. "
                        "Please review and personalize this draft before any outreach."
                    ),
                    "updated_at": self._now(),
                }
            )
            self._candidates[candidate_id] = updated
            return updated

    def record_outreach(
        self,
        candidate_id: str,
        *,
        method: OutreachMethod,
        status: str,
        actor_id: str,
    ) -> OutreachRecord:
        with self._lock:
            candidate = self._candidate(candidate_id)
            if candidate.status not in {
                OutboundCandidateStatus.ACTIVATED,
                OutboundCandidateStatus.CONTACTED,
            }:
                raise ConflictError("outreach requires an activated candidate")
            now = self._now()
            event = OutreachRecord(
                outreach_id=self._id(),
                outbound_candidate_id=candidate_id,
                method=method,
                status=status,
                actor_id=actor_id,
                occurred_at=now,
            )
            self._outreach.setdefault(candidate_id, []).append(event)
            self._candidates[candidate_id] = candidate.model_copy(
                update={"status": OutboundCandidateStatus.CONTACTED, "updated_at": now}
            )
            return event

    def start_sourcing(self) -> RunAccepted:
        thesis = self.active_thesis()
        now = self._now()
        run_id = self._id()
        run = self._completed_run(
            run_id=run_id,
            kind=PipelineRunKind.SOURCING,
            input_snapshot_id=thesis.thesis_version_id,
            versions=self._versions(thesis.thesis_version_id),
            stage_keys=("validated_query", "fake_discovery", "canonicalize_leads"),
            accepted_output_ids=(),
            at=now,
        )
        with self._lock:
            self._runs[run_id] = run
        return self._accepted(run)

    def queue_sourcing_run(
        self,
        *,
        input_snapshot_id: str,
        stage_keys: tuple[str, ...],
    ) -> RunAccepted:
        """Create an observable queued run for an injected live sourcing coordinator."""

        if not stage_keys or any(not key.strip() for key in stage_keys):
            raise ValueError("sourcing stage keys must be non-blank")
        if len(stage_keys) != len(set(stage_keys)):
            raise ValueError("sourcing stage keys must be unique")
        thesis = self.active_thesis()
        now = self._now()
        run = PipelineRun(
            run_id=self._id(),
            kind=PipelineRunKind.SOURCING,
            status=PipelineRunStatus.QUEUED,
            versions=self._versions(thesis.thesis_version_id),
            input_snapshot_id=input_snapshot_id,
            input_snapshot_as_of=now,
            queued_at=now,
            stages=tuple(
                PipelineStage(
                    stage_key=stage_key,
                    status=PipelineStageStatus.QUEUED,
                    queued_at=now,
                )
                for stage_key in stage_keys
            ),
        )
        with self._lock:
            self._runs[run.run_id] = run
        return self._accepted(run)

    def publish_sourcing_run(self, run: PipelineRun) -> PipelineRun:
        """Publish a validated queued/running/terminal snapshot for one sourcing run."""

        if run.kind is not PipelineRunKind.SOURCING:
            raise ValueError("only sourcing runs can use the live sourcing publisher")
        with self._lock:
            previous = self._runs.get(run.run_id)
            if previous is None:
                raise NotFoundError("sourcing run was not found")
            if (
                previous.kind is not run.kind
                or previous.input_snapshot_id != run.input_snapshot_id
                or previous.input_snapshot_as_of != run.input_snapshot_as_of
                or previous.queued_at != run.queued_at
                or tuple(stage.stage_key for stage in previous.stages)
                != tuple(stage.stage_key for stage in run.stages)
            ):
                raise ConflictError("sourcing run identity cannot be replaced")
            order = {
                PipelineRunStatus.QUEUED: 0,
                PipelineRunStatus.RUNNING: 1,
                PipelineRunStatus.SUCCEEDED: 2,
                PipelineRunStatus.PARTIALLY_SUCCEEDED: 2,
                PipelineRunStatus.FAILED: 2,
            }
            if order[run.status] < order[previous.status] or (
                order[previous.status] == 2 and run != previous
            ):
                raise ConflictError("sourcing run cannot move backward or replace a terminal state")
            self._runs[run.run_id] = run
            return run

    def start_screening(self, opportunity_id: str) -> RunAccepted:
        with self._lock:
            opportunity = self._opportunity(opportunity_id)
            thesis = self.active_thesis()
            now = self._now()
            run_id = self._id()
            assessment = self._full_assessment(opportunity, thesis, run_id=run_id, at=now)
            accepted_outputs = [assessment.assessment_id]
            if assessment.memo is not None:
                accepted_outputs.append(assessment.memo.memo_id)
            if assessment.recommendation is not None:
                accepted_outputs.append(assessment.recommendation.recommendation_id)
            run = self._completed_run(
                run_id=run_id,
                kind=PipelineRunKind.SCREENING,
                input_snapshot_id=assessment.input_snapshot_id,
                versions=assessment.versions,
                stage_keys=(
                    "canonical_snapshot",
                    "deterministic_rules",
                    "fake_intelligence",
                    "decision_readiness",
                    "memo_and_recommendation",
                ),
                accepted_output_ids=tuple(accepted_outputs),
                at=now,
            )
            self._runs[run_id] = run
            opportunity.assessments.append(assessment)
            opportunity.run_ids.append(run_id)
            opportunity.screening_status = ScreeningCaseStatus.BLOCKED
            opportunity.updated_at = now
            application = self._applications.get(opportunity.application_id)
            if application is not None:
                application.status = application.status.model_copy(
                    update={
                        "stage": FounderFacingStage.NEEDS_INFORMATION,
                        "last_updated_at": now,
                        "information_requests": ("Please identify the individual founders.",),
                        "next_action": "Provide the requested founder information.",
                    }
                )
            return self._accepted(run)

    def get_run(self, run_id: str) -> PipelineRun:
        with self._lock:
            try:
                return self._runs[run_id]
            except KeyError as error:
                raise NotFoundError("pipeline run was not found") from error

    def seed_failed_run(
        self,
        *,
        kind: PipelineRunKind = PipelineRunKind.SCREENING,
        input_snapshot_id: str | None = None,
        accepted_output_ids: tuple[str, ...] = (),
        attempt: int = 1,
    ) -> PipelineRun:
        """Seed an observable safe failure for deterministic retry tests and demos."""

        now = self._now()
        run_id = self._id()
        failure = PipelineFailure(
            failure_id=self._id(),
            stage_key="fake_intelligence",
            safe_code="fake_stage_failed",
            safe_message="The deterministic fake stage failed and may be retried.",
            retryable=True,
            occurred_at=now,
        )
        completed_stage = PipelineStage(
            stage_key="canonical_snapshot",
            status=PipelineStageStatus.SUCCEEDED,
            queued_at=now,
            started_at=now,
            completed_at=now,
            accepted_output_ids=accepted_output_ids,
        )
        failed_stage = PipelineStage(
            stage_key="fake_intelligence",
            status=PipelineStageStatus.FAILED,
            queued_at=now,
            started_at=now,
            completed_at=now,
            failure_ids=(failure.failure_id,),
        )
        status = (
            PipelineRunStatus.PARTIALLY_SUCCEEDED
            if accepted_output_ids
            else PipelineRunStatus.FAILED
        )
        run = PipelineRun(
            run_id=run_id,
            kind=kind,
            status=status,
            versions=VersionManifest(),
            input_snapshot_id=input_snapshot_id or self._id(),
            input_snapshot_as_of=now,
            queued_at=now,
            started_at=now,
            completed_at=now,
            stages=(completed_stage, failed_stage),
            accepted_output_ids=accepted_output_ids,
            failures=(failure,),
            attempt=attempt,
            retry_of_run_id=(self._id() if attempt > 1 else None),
        )
        with self._lock:
            self._runs[run_id] = run
        return run

    def retry_run(self, run_id: str) -> RunAccepted:
        with self._lock:
            original = self.get_run(run_id)
            prior_retry_id = self._retry_by_parent.get(run_id)
            if prior_retry_id is not None:
                return self._accepted(self._runs[prior_retry_id])
            if original.status not in {
                PipelineRunStatus.FAILED,
                PipelineRunStatus.PARTIALLY_SUCCEEDED,
            }:
                raise ConflictError("only failed or partially succeeded runs may be retried")
            if not original.failures or any(not failure.retryable for failure in original.failures):
                raise ConflictError("run contains a non-retryable failure")
            next_attempt = original.attempt + 1
            if next_attempt > self._max_retry_attempts:
                raise RetryLimitError("the bounded retry limit has been reached")
            now = self._now()
            retry_id = self._id()
            if not any(stage.status is PipelineStageStatus.FAILED for stage in original.stages):
                raise ConflictError("run has no failed stage to resume")
            resumed_stages: list[PipelineStage] = []
            recovered_output_ids: list[str] = []
            for stage in original.stages:
                if stage.status in {
                    PipelineStageStatus.SUCCEEDED,
                    PipelineStageStatus.SKIPPED,
                }:
                    resumed_stages.append(
                        PipelineStage(
                            stage_key=stage.stage_key,
                            status=PipelineStageStatus.SKIPPED,
                            queued_at=now,
                            started_at=now,
                            completed_at=now,
                            accepted_output_ids=stage.accepted_output_ids,
                        )
                    )
                    continue
                if stage.status is not PipelineStageStatus.FAILED:
                    raise ConflictError("terminal retry input contains a non-terminal stage")
                recovered_output_id = self._id()
                recovered_output_ids.append(recovered_output_id)
                resumed_stages.append(
                    PipelineStage(
                        stage_key=stage.stage_key,
                        status=PipelineStageStatus.SUCCEEDED,
                        queued_at=now,
                        started_at=now,
                        completed_at=now,
                        accepted_output_ids=(recovered_output_id,),
                    )
                )
            accepted_output_ids = tuple(
                dict.fromkeys((*original.accepted_output_ids, *recovered_output_ids))
            )
            if not recovered_output_ids or not accepted_output_ids:
                raise ConflictError("retry produced no required recovered output")
            retry = PipelineRun(
                run_id=retry_id,
                kind=original.kind,
                status=PipelineRunStatus.SUCCEEDED,
                versions=original.versions,
                input_snapshot_id=original.input_snapshot_id,
                input_snapshot_as_of=original.input_snapshot_as_of,
                queued_at=now,
                started_at=now,
                completed_at=now,
                stages=tuple(resumed_stages),
                accepted_output_ids=accepted_output_ids,
                retry_of_run_id=original.run_id,
                attempt=next_attempt,
            )
            self._runs[retry_id] = retry
            self._retry_by_parent[run_id] = retry_id
            return self._accepted(retry)

    def list_opportunities(
        self,
        *,
        limit: int = 50,
        origin: OpportunityOrigin | None = None,
        screening_status: ScreeningCaseStatus | None = None,
    ) -> OpportunityCollection:
        bounded = self._bounded_limit(limit)
        with self._lock:
            states = [
                item
                for item in self._opportunities.values()
                if (origin is None or item.origin is origin)
                and (screening_status is None or item.screening_status is screening_status)
            ]
            states.sort(key=lambda item: item.opportunity_id)
            states.sort(key=lambda item: item.updated_at, reverse=True)
            items = tuple(self._summary(item) for item in states[:bounded])
        filters: list[str] = []
        if origin is not None:
            filters.append(f"origin={origin.value}")
        if screening_status is not None:
            filters.append(f"screening_status={screening_status.value}")
        return OpportunityCollection(
            items=items,
            limit=bounded,
            truncated=len(states) > bounded,
            applied_filters=tuple(filters),
        )

    def get_opportunity(
        self,
        opportunity_id: str,
        *,
        include_claims: bool = False,
        include_evidence: bool = False,
    ) -> OpportunityDetail:
        with self._lock:
            return self._detail(
                self._opportunity(opportunity_id),
                include_claims=include_claims,
                include_evidence=include_evidence,
            )

    def query_opportunities(self, plan: OpportunityQueryPlan) -> QueryResult:
        with self._lock:
            states = tuple(self._opportunities.values())
            records = tuple(self._query_record(state) for state in states)
        executed = self._query_executor.execute(plan, records)
        items = tuple(
            QueryResultItem(
                opportunity_id=item.opportunity_id,
                criteria=tuple(
                    QueryCriterionResult(
                        criterion_id=criterion.criterion_id,
                        field=criterion.field,
                        strength=criterion.strength,
                        outcome=CriterionMatchOutcome(criterion.match.value),
                        rationale=criterion.reason,
                        knowledge_state=criterion.knowledge_state,
                        unknown_policy=criterion.unknown_policy,
                    )
                    for criterion in item.criteria
                ),
                matched_preferences=item.matched_preferences,
                evaluated_preferences=item.evaluated_preferences,
            )
            for item in executed.items
        )
        return QueryResult(
            plan=plan,
            results=items,
            eligible_count=executed.eligible_count,
            truncated=executed.truncated,
            ordering=executed.ordering,
        )

    def record_rule_override(
        self,
        opportunity_id: str,
        *,
        result_id: str,
        override: RuleOverride,
    ) -> RuleOverrideEvent:
        """Append an attributed override without mutating its deterministic result."""

        with self._lock:
            opportunity = self._opportunity(opportunity_id)
            result = next(
                (
                    candidate
                    for assessment in reversed(opportunity.assessments)
                    for candidate in assessment.deterministic_results
                    if candidate.result_id == result_id
                ),
                None,
            )
            if result is None:
                raise NotFoundError("deterministic rule result was not found")
            return self._rule_override_ledger.record(result, override)

    def rule_override_history(
        self,
        result_id: str | None = None,
    ) -> tuple[RuleOverrideEvent, ...]:
        with self._lock:
            return self._rule_override_ledger.history(result_id)

    def record_decision(
        self,
        opportunity_id: str,
        *,
        assessment_id: str,
        memo_id: str,
        recommendation_id: str,
        disposition: HumanDecisionDisposition,
        rationale: str,
        actor_id: str,
    ) -> Decision:
        with self._lock:
            opportunity = self._opportunity(opportunity_id)
            if not opportunity.assessments:
                raise ConflictError("opportunity has no accepted assessment")
            assessment = opportunity.assessments[-1]
            if (
                assessment.assessment_id != assessment_id
                or assessment.memo is None
                or assessment.memo.memo_id != memo_id
                or assessment.recommendation is None
                or assessment.recommendation.recommendation_id != recommendation_id
            ):
                raise ConflictError("decision references do not match the reviewed revisions")
            if (
                disposition is HumanDecisionDisposition.ADVANCE
                and assessment.decision_readiness is not None
                and assessment.decision_readiness.status is DecisionReadinessStatus.BLOCKED
            ):
                raise ConflictError(
                    "an Advance decision requires resolved blockers or accepted readiness risk"
                )
            now = self._now()
            decision = Decision(
                decision_id=self._id(),
                screening_case_id=opportunity.screening_case_id,
                opportunity_id=opportunity.opportunity_id,
                assessment_id=assessment_id,
                memo_id=memo_id,
                reviewed_recommendation_id=recommendation_id,
                disposition=disposition,
                actor_id=actor_id,
                rationale=rationale,
                decided_at=now,
            )
            opportunity.decisions.append(decision)
            opportunity.screening_status = ScreeningCaseStatus.DECIDED
            opportunity.updated_at = now
            application = self._applications.get(opportunity.application_id)
            if application is not None:
                application.status = application.status.model_copy(
                    update={
                        "stage": FounderFacingStage.COMPLETE,
                        "last_updated_at": now,
                        "target_state": TargetState.COMPLETE,
                        "information_requests": (),
                        "outcome": disposition.value,
                        "next_action": "The investor decision has been recorded.",
                        "outcome_at": now,
                    }
                )
            return decision

    def _candidate(self, candidate_id: str) -> OutboundCandidateView:
        try:
            return self._candidates[candidate_id]
        except KeyError as error:
            raise NotFoundError("outbound candidate was not found") from error

    def _opportunity(self, opportunity_id: str) -> _OpportunityState:
        try:
            return self._opportunities[opportunity_id]
        except KeyError as error:
            raise NotFoundError("opportunity was not found") from error

    @staticmethod
    def _bounded_limit(limit: int) -> int:
        if limit < 1 or limit > _MAX_COLLECTION_LIMIT:
            raise ValueError(f"limit must be between 1 and {_MAX_COLLECTION_LIMIT}")
        return limit

    @staticmethod
    def _accepted(run: PipelineRun) -> RunAccepted:
        return RunAccepted(
            run_id=run.run_id,
            status_url=f"/api/v1/runs/{run.run_id}",
            run=run,
        )

    @staticmethod
    def _completed_run(
        *,
        run_id: str,
        kind: PipelineRunKind,
        input_snapshot_id: str,
        versions: VersionManifest,
        stage_keys: tuple[str, ...],
        accepted_output_ids: tuple[str, ...],
        at: datetime,
    ) -> PipelineRun:
        stages = tuple(
            PipelineStage(
                stage_key=stage_key,
                status=PipelineStageStatus.SUCCEEDED,
                queued_at=at,
                started_at=at,
                completed_at=at,
                accepted_output_ids=(accepted_output_ids if index == len(stage_keys) - 1 else ()),
            )
            for index, stage_key in enumerate(stage_keys)
        )
        return PipelineRun(
            run_id=run_id,
            kind=kind,
            status=PipelineRunStatus.SUCCEEDED,
            versions=versions,
            input_snapshot_id=input_snapshot_id,
            input_snapshot_as_of=at,
            queued_at=at,
            started_at=at,
            completed_at=at,
            stages=stages,
            accepted_output_ids=accepted_output_ids,
        )

    @staticmethod
    def _versions(thesis_version: str) -> VersionManifest:
        return FakeVCBrainService._screening_versions(thesis_version)

    @staticmethod
    def _screening_versions(
        thesis_version: str,
        projection: DeterministicScreeningProjection | None = None,
        deck_projection: DeckEvidenceProjection | None = None,
    ) -> VersionManifest:
        founder_score_version = (
            projection.founder_score_version if projection is not None else "fake-founder-score.v0"
        )
        axis_rubric_version = (
            projection.axis_rubric_version if projection is not None else "fake-axis-rubric.v0"
        )
        components = [
                ComponentVersion(
                    component=VersionComponent.THESIS,
                    version_id=thesis_version,
                ),
                ComponentVersion(
                    component=VersionComponent.DETERMINISTIC_RULES,
                    version_id="fake-rules.v0",
                ),
                ComponentVersion(
                    component=VersionComponent.FOUNDER_SCORE,
                    version_id=founder_score_version,
                ),
                ComponentVersion(
                    component=VersionComponent.AXIS_RUBRIC,
                    version_id=axis_rubric_version,
                ),
                ComponentVersion(
                    component=VersionComponent.DECISION_READINESS_POLICY,
                    version_id="fake-readiness.v0",
                ),
                ComponentVersion(
                    component=VersionComponent.MEMO,
                    version_id="fake-memo.v0",
                ),
                ComponentVersion(
                    component=VersionComponent.RECOMMENDATION,
                    version_id="fake-recommendation.v0",
                ),
            ]
        if deck_projection is not None:
            components.append(
                ComponentVersion(
                    component=VersionComponent.TOOL,
                    version_id=deck_projection.projection_version,
                    name="deck-evidence-projection",
                )
            )
        return VersionManifest(components=tuple(components))

    @staticmethod
    def _coverage(
        at: datetime,
        source_artifact_ids: tuple[str, ...],
    ) -> CoverageSummary:
        artifact_count = len(set(source_artifact_ids))
        return CoverageSummary(
            level=CoverageLevel.LOW,
            source_count=artifact_count,
            artifact_count=artifact_count,
            evidence_count=0,
            missing_fields=(
                ("founder_identity", "corroborated_traction")
                if artifact_count
                else ("source_evidence", "founder_identity", "corroborated_traction")
            ),
            freshest_evidence_at=(
                KnowledgeValue[datetime].known(at)
                if artifact_count
                else KnowledgeValue[datetime].unknown("no source artifact is available")
            ),
        )

    @staticmethod
    def _coverage_with_deck_projection(
        base: CoverageSummary,
        projection: DeckEvidenceProjection | None,
    ) -> CoverageSummary:
        if projection is None:
            return base
        claim_by_id = {claim.claim_id: claim for claim in projection.claims}
        conflicted_predicates = tuple(
            dict.fromkeys(
                claim_by_id[claim_id].predicate
                for contradiction in projection.contradictions
                for claim_id in contradiction.claim_ids
                if claim_id in claim_by_id
            )
        )
        freshest = projection.projected_at
        if (
            base.freshest_evidence_at.state is KnowledgeState.KNOWN
            and base.freshest_evidence_at.value is not None
        ):
            freshest = max(freshest, base.freshest_evidence_at.value)
        return base.model_copy(
            update={
                "source_count": max(base.source_count, 1),
                "artifact_count": max(base.artifact_count, 1),
                "evidence_count": max(base.evidence_count, len(projection.evidence)),
                "source_categories": tuple(
                    dict.fromkeys((*base.source_categories, SourceCategory.APPLICATION_DECK.value))
                ),
                "conflicted_fields": tuple(
                    dict.fromkeys((*base.conflicted_fields, *conflicted_predicates))
                ),
                "freshest_evidence_at": KnowledgeValue[datetime].known(freshest),
            }
        )

    def _thesis_rule_results(
        self,
        thesis: InvestmentThesisRevision,
    ) -> tuple[DeterministicRuleResult, ...]:
        criteria = (
            ("sector", thesis.sector),
            ("stage", thesis.stage),
            ("geography", thesis.geography),
            ("check_size", thesis.check_size),
            ("ownership_target", thesis.ownership_target),
            ("risk_appetite", thesis.risk_appetite),
        )
        results: list[DeterministicRuleResult] = []
        for field_name, criterion in criteria:
            if criterion.configured_outcome is RuleOutcome.NOT_EVALUATED:
                outcome = RuleOutcome.NOT_EVALUATED
                reason = "Criterion is configured as No Preference and is not evaluated."
            else:
                outcome = RuleOutcome.INDETERMINATE
                operator = criterion.operator.value if criterion.operator is not None else "none"
                reason = (
                    f"Canonical {field_name} is Unknown; {criterion.mode.value} "
                    f"operator {operator} cannot be evaluated."
                )
            results.append(
                DeterministicRuleResult(
                    result_id=self._id(),
                    rule_id=f"thesis-rule-{field_name}",
                    rule_version="fake-rules.v0",
                    outcome=outcome,
                    inputs=(
                        RuleInput(
                            field=field_name,
                            value=KnowledgeValue[ScalarValue].unknown(
                                f"{field_name} is absent from the fake canonical snapshot"
                            ),
                        ),
                    ),
                    reason=reason,
                )
            )
        return tuple(results)

    def _axes(self, coverage: CoverageSummary) -> IndependentAxes:
        confidence = KnowledgeValue[float].unknown("fake output has sparse evidence coverage")
        return IndependentAxes(
            founder=FounderAxisAssessment(
                assessment_id=self._id(),
                assessment_version_id=self._id(),
                rubric_version="fake-axis-rubric.v0",
                trend=Trend.UNKNOWN,
                confidence=confidence,
                coverage=coverage,
                rating=FounderAxisRating.UNKNOWN,
                open_questions=("Who are the individual founders?",),
            ),
            market=MarketAxisAssessment(
                assessment_id=self._id(),
                assessment_version_id=self._id(),
                rubric_version="fake-axis-rubric.v0",
                trend=Trend.UNKNOWN,
                confidence=confidence,
                coverage=coverage,
                rating=MarketAxisRating.UNKNOWN,
                open_questions=("Which evidence establishes current market direction?",),
            ),
            idea_vs_market=IdeaVsMarketAxisAssessment(
                assessment_id=self._id(),
                assessment_version_id=self._id(),
                rubric_version="fake-axis-rubric.v0",
                trend=Trend.UNKNOWN,
                confidence=confidence,
                coverage=coverage,
                rating=IdeaVsMarketAxisRating.UNKNOWN,
                open_questions=("Which buyer validates the proposed problem?",),
            ),
        )

    def _preliminary_assessment(
        self,
        candidate: OutboundCandidateView,
        thesis: InvestmentThesisRevision,
        *,
        run_id: str,
        at: datetime,
    ) -> AssessmentEnvelope:
        assessment_id = self._id()
        projection = self._screening_bridge.project(
            candidate.outbound_candidate_id,
            founder_identity=candidate.founder_id,
            as_of=at,
            id_factory=self._id,
        )
        coverage = (
            projection.coverage
            if projection is not None
            else self._coverage(at, candidate.source_artifact_ids)
        )
        has_source_evidence = (
            coverage.evidence_count > 0 if projection is not None else coverage.artifact_count > 0
        )
        return AssessmentEnvelope(
            assessment_id=assessment_id,
            assessment_version_id=self._id(),
            identity=PreliminaryAssessmentIdentity(
                outbound_candidate_id=candidate.outbound_candidate_id,
                founder_id=candidate.founder_id,
                company_id=KnowledgeValue[str].known(candidate.company_id),
            ),
            versions=self._screening_versions(
                thesis.thesis_version_id,
                projection,
            ),
            input_snapshot_id=candidate.outbound_candidate_id,
            input_snapshot_as_of=at,
            coverage=coverage,
            deterministic_results=self._thesis_rule_results(thesis),
            founder_score=(
                projection.founder_score
                if projection is not None
                else KnowledgeValue.unknown(
                    "Founder Score remains unknown in the sparse deterministic fake"
                )
            ),
            axes=(projection.axes if projection is not None else self._axes(coverage)),
            claim_ids=(),
            evidence_ids=(),
            recommendation=Recommendation(
                recommendation_id=self._id(),
                recommendation_version_id=self._id(),
                subject=SubjectRef(
                    kind=EntityKind.OUTBOUND_CANDIDATE,
                    subject_id=candidate.outbound_candidate_id,
                ),
                assessment_id=assessment_id,
                policy_version="fake-recommendation.v0",
                action=(
                    RecommendationAction.ACTIVATE
                    if has_source_evidence
                    else RecommendationAction.NEEDS_INFORMATION
                ),
                reasons=(
                    RecommendationReason(
                        summary=(
                            "The source-backed deterministic fixture is ready for human "
                            "activation review."
                            if has_source_evidence
                            else "No source artifact supports activation; collect one approved "
                            "signal before activation review."
                        )
                    ),
                ),
                next_actions=(
                    (
                        "Review evidence and explicitly activate before outreach."
                        if has_source_evidence
                        else "Collect one approved source artifact and rerun preliminary review."
                    ),
                ),
                created_at=at,
            ),
            run_id=run_id,
            created_at=at,
        )

    def _full_assessment(
        self,
        opportunity: _OpportunityState,
        thesis: InvestmentThesisRevision,
        *,
        run_id: str,
        at: datetime,
    ) -> AssessmentEnvelope:
        assessment_id = self._id()
        application = self._applications.get(opportunity.application_id)
        deck_projection = opportunity.deck_evidence_projection
        candidate = (
            self._candidates.get(opportunity.outbound_candidate_id)
            if opportunity.outbound_candidate_id is not None
            else None
        )
        application_artifact_ids = (
            (application.artifact.artifact_id,) if application is not None else ()
        )
        source_artifact_ids = tuple(
            dict.fromkeys(
                (
                    *(candidate.source_artifact_ids if candidate is not None else ()),
                    *application_artifact_ids,
                )
            )
        )
        projection = self._screening_bridge.project(
            opportunity.opportunity_id,
            founder_identity=opportunity.founder_id,
            as_of=at,
            id_factory=self._id,
        )
        base_coverage = (
            projection.coverage
            if projection is not None
            else self._coverage(at, source_artifact_ids)
        )
        coverage = self._coverage_with_deck_projection(base_coverage, deck_projection)
        memo_sections = (
            deck_projection.memo_sections
            if deck_projection is not None
            else tuple(
                MemoSection(
                    kind=kind,
                    content=KnowledgeValue[str].unknown(
                        "No accepted deck Evidence projection supports this section."
                    ),
                )
                for kind in (
                    MemoSectionKind.COMPANY_SNAPSHOT,
                    MemoSectionKind.INVESTMENT_HYPOTHESES,
                    MemoSectionKind.SWOT,
                    MemoSectionKind.PROBLEM_AND_PRODUCT,
                    MemoSectionKind.TRACTION_AND_KPIS,
                )
            )
        )
        founder_identity_known = opportunity.founder_id.state is KnowledgeState.KNOWN
        analysis_sections_unknown = any(
            section.kind
            in {MemoSectionKind.INVESTMENT_HYPOTHESES, MemoSectionKind.SWOT}
            and section.content.state is not KnowledgeState.KNOWN
            for section in memo_sections
        )
        checks: list[ReadinessCheck] = []
        blockers: list[ReadinessBlocker] = []
        diligence_actions: list[DiligenceAction] = []
        if founder_identity_known:
            checks.append(
                ReadinessCheck(
                    check_key="founder_identity",
                    status=ReadinessCheckStatus.SATISFIED,
                    reason="A canonical Founder identity is linked to the Opportunity.",
                )
            )
        else:
            founder_blocker_id = self._id()
            checks.append(
                ReadinessCheck(
                    check_key="founder_identity",
                    status=ReadinessCheckStatus.BLOCKING,
                    reason="Founder identity remains unresolved.",
                )
            )
            blockers.append(
                ReadinessBlocker(
                    blocker_id=founder_blocker_id,
                    check_key="founder_identity",
                    reason="Founder identity must be resolved before decision readiness.",
                )
            )
            diligence_actions.append(
                DiligenceAction(
                    action_id=self._id(),
                    status=DiligenceActionStatus.OPEN,
                    description="Resolve the individual founder identities.",
                    requested_evidence=(
                        "Founder names, roles, and one verifiable profile or interview."
                    ),
                )
            )

        contradictions = (
            deck_projection.contradictions if deck_projection is not None else ()
        )
        if contradictions:
            checks.append(
                ReadinessCheck(
                    check_key="deck_contradictions",
                    status=ReadinessCheckStatus.BLOCKING,
                    reason="The deck contains unresolved competing labeled assertions.",
                    related_record_ids=tuple(
                        contradiction.contradiction_id for contradiction in contradictions
                    ),
                )
            )
            for contradiction in contradictions:
                contradiction_blocker_id = self._id()
                blockers.append(
                    ReadinessBlocker(
                        blocker_id=contradiction_blocker_id,
                        check_key="deck_contradictions",
                        reason=contradiction.summary,
                        related_record_ids=(contradiction.contradiction_id,),
                    )
                )
                diligence_actions.append(
                    DiligenceAction(
                        action_id=self._id(),
                        status=DiligenceActionStatus.OPEN,
                        description="Resolve a competing labeled pitch-deck assertion.",
                        resolves_claim_ids=contradiction.claim_ids,
                        resolves_contradiction_ids=(contradiction.contradiction_id,),
                        requested_evidence=(
                            "Provide one dated source or founder clarification that resolves "
                            "the competing values."
                        ),
                    )
                )
        else:
            checks.append(
                ReadinessCheck(
                    check_key="deck_contradictions",
                    status=ReadinessCheckStatus.SATISFIED,
                    reason="No competing labeled deck assertions were detected.",
                )
            )

        if analysis_sections_unknown:
            analysis_blocker_id = self._id()
            checks.append(
                ReadinessCheck(
                    check_key="supported_memo_analysis",
                    status=ReadinessCheckStatus.BLOCKING,
                    reason=(
                        "Investment Hypotheses and SWOT remain Unknown until supported "
                        "analysis is accepted."
                    ),
                )
            )
            blockers.append(
                ReadinessBlocker(
                    blocker_id=analysis_blocker_id,
                    check_key="supported_memo_analysis",
                    reason="Required analytical memo sections remain explicitly Unknown.",
                )
            )
            diligence_actions.append(
                DiligenceAction(
                    action_id=self._id(),
                    status=DiligenceActionStatus.OPEN,
                    description="Complete evidence-backed hypotheses and SWOT analysis.",
                    requested_evidence=(
                        "Accepted Claims and Evidence supporting investment hypotheses, "
                        "strengths, weaknesses, opportunities, and threats."
                    ),
                )
            )
        else:
            checks.append(
                ReadinessCheck(
                    check_key="supported_memo_analysis",
                    status=ReadinessCheckStatus.SATISFIED,
                    reason="Required analytical memo sections have accepted support.",
                )
            )

        readiness = DecisionReadiness(
            readiness_id=self._id(),
            readiness_version_id=self._id(),
            screening_case_id=opportunity.screening_case_id,
            policy_version="fake-readiness.v0",
            evaluated_at=at,
            status=(
                DecisionReadinessStatus.BLOCKED
                if blockers
                else DecisionReadinessStatus.READY
            ),
            checks=tuple(checks),
            blockers=tuple(blockers),
        )
        memo = InvestmentMemo(
            memo_id=self._id(),
            memo_version_id=self._id(),
            opportunity_id=opportunity.opportunity_id,
            screening_case_id=opportunity.screening_case_id,
            assessment_id=assessment_id,
            run_id=run_id,
            thesis_version=thesis.thesis_version_id,
            evidence_as_of=at,
            generated_at=at,
            sections=memo_sections,
        )
        if contradictions:
            recommendation_action = RecommendationAction.NEEDS_INFORMATION
            recommendation_summary = (
                "Unresolved competing deck assertions block decision readiness."
            )
            recommendation_claim_ids = tuple(
                dict.fromkeys(
                    claim_id
                    for contradiction in contradictions
                    for claim_id in contradiction.claim_ids
                )
            )
            next_action = "Resolve the cited competing values before investment review."
        elif not founder_identity_known:
            recommendation_action = RecommendationAction.NEEDS_INFORMATION
            recommendation_summary = "A material founder-identity gap blocks readiness."
            recommendation_claim_ids = ()
            next_action = "Request the individual founder names and roles."
        elif analysis_sections_unknown:
            recommendation_action = RecommendationAction.NEEDS_INFORMATION
            recommendation_summary = (
                "Required analytical memo sections remain explicitly Unknown."
            )
            recommendation_claim_ids = ()
            next_action = "Complete cited Investment Hypotheses and SWOT analysis."
        else:
            recommendation_action = RecommendationAction.MANUAL_REVIEW
            recommendation_summary = "The deterministic evidence package is ready for review."
            recommendation_claim_ids = (
                tuple(claim.claim_id for claim in deck_projection.claims)
                if deck_projection is not None
                else ()
            )
            next_action = "A human investor should review the cited evidence package."
        recommendation = Recommendation(
            recommendation_id=self._id(),
            recommendation_version_id=self._id(),
            subject=SubjectRef(
                kind=EntityKind.OPPORTUNITY,
                subject_id=opportunity.opportunity_id,
            ),
            assessment_id=assessment_id,
            policy_version="fake-recommendation.v0",
            action=recommendation_action,
            reasons=(
                RecommendationReason(
                    summary=recommendation_summary,
                    claim_ids=recommendation_claim_ids,
                ),
            ),
            next_actions=(next_action,),
            created_at=at,
        )
        return AssessmentEnvelope(
            assessment_id=assessment_id,
            assessment_version_id=self._id(),
            identity=FullAssessmentIdentity(
                origin=opportunity.origin,
                application_id=opportunity.application_id,
                outbound_candidate_id=opportunity.outbound_candidate_id,
                opportunity_id=opportunity.opportunity_id,
                screening_case_id=opportunity.screening_case_id,
                company_id=opportunity.company_id,
                founder_id=opportunity.founder_id,
            ),
            versions=self._screening_versions(
                thesis.thesis_version_id,
                projection,
                deck_projection,
            ),
            input_snapshot_id=(
                deck_projection.projection_id if deck_projection is not None else self._id()
            ),
            input_snapshot_as_of=at,
            coverage=coverage,
            deterministic_results=self._thesis_rule_results(thesis),
            founder_score=(
                projection.founder_score
                if projection is not None
                else KnowledgeValue.unknown("founder_identity_unresolved")
            ),
            axes=(projection.axes if projection is not None else self._axes(coverage)),
            claim_ids=(
                tuple(claim.claim_id for claim in deck_projection.claims)
                if deck_projection is not None
                else ()
            ),
            evidence_ids=(
                tuple(evidence.evidence_id for evidence in deck_projection.evidence)
                if deck_projection is not None
                else ()
            ),
            contradictions=contradictions,
            diligence_actions=tuple(diligence_actions),
            decision_readiness=readiness,
            memo=memo,
            recommendation=recommendation,
            run_id=run_id,
            created_at=at,
        )

    def _summary(self, state: _OpportunityState) -> OpportunitySummary:
        latest = state.assessments[-1] if state.assessments else None
        recommendation = (
            latest.recommendation.action.value
            if latest is not None and latest.recommendation is not None
            else None
        )
        return OpportunitySummary(
            opportunity_id=state.opportunity_id,
            origin=state.origin,
            company_id=state.company_id,
            screening_case_id=state.screening_case_id,
            screening_status=state.screening_status,
            recommendation=recommendation,
            updated_at=state.updated_at,
        )

    def _detail(
        self,
        state: _OpportunityState,
        *,
        include_claims: bool = False,
        include_evidence: bool = False,
    ) -> OpportunityDetail:
        latest = state.assessments[-1] if state.assessments else None
        now = self._now()
        target_at = state.started_at + timedelta(hours=24)
        if state.screening_status in {ScreeningCaseStatus.DECIDED, ScreeningCaseStatus.CLOSED}:
            target_state = TargetState.COMPLETE
        elif now >= target_at:
            target_state = TargetState.MISSED
        elif now >= target_at - timedelta(hours=4):
            target_state = TargetState.APPROACHING
        else:
            target_state = TargetState.ON_TRACK
        elapsed = max(0, int((now - state.started_at).total_seconds()))
        assessments = tuple(state.assessments)
        memos = tuple(item.memo for item in assessments if item.memo is not None)
        deck_projection = state.deck_evidence_projection
        return OpportunityDetail(
            opportunity_id=state.opportunity_id,
            origin=state.origin,
            application_id=state.application_id,
            outbound_candidate_id=state.outbound_candidate_id,
            founder_id=state.founder_id,
            company_id=state.company_id,
            screening_case_id=state.screening_case_id,
            screening_status=state.screening_status,
            latest_assessment=latest,
            assessment_history=assessments,
            claims=(
                deck_projection.claims
                if include_claims and deck_projection is not None
                else ()
            ),
            evidence=(
                deck_projection.evidence
                if include_evidence and deck_projection is not None
                else ()
            ),
            latest_memo=(latest.memo if latest is not None else None),
            memo_revisions=memos,
            latest_recommendation=(latest.recommendation if latest is not None else None),
            human_decisions=tuple(state.decisions),
            related_run_ids=tuple(state.run_ids),
            timing=OpportunityTiming(
                started_at=state.started_at,
                last_updated_at=state.updated_at,
                decision_readiness_target_at=target_at,
                elapsed_seconds=elapsed,
                target_state=target_state,
            ),
        )

    @staticmethod
    def _query_record(state: _OpportunityState) -> OpportunityQueryRecord:
        return OpportunityQueryRecord(
            opportunity_id=state.opportunity_id,
            values={
                QueryCriterionField.ORIGIN: KnowledgeValue.known(state.origin.value),
                QueryCriterionField.WORKFLOW_STATE: KnowledgeValue.known(
                    state.screening_status.value
                ),
            },
        )
