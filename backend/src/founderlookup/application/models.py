"""Immutable command results and read models exposed by application services."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, computed_field, model_validator

from founderlookup.domain.assessment import (
    AssessmentEnvelope,
    Decision,
    InvestmentMemo,
    Recommendation,
    RuleOutcome,
)
from founderlookup.domain.common import (
    DomainModel,
    KnowledgeState,
    KnowledgeValue,
    NonBlankStr,
    NonNegativeInt,
    PositiveInt,
    ScalarValue,
    StableId,
    UTCDateTime,
    VersionId,
)
from founderlookup.domain.evidence import Claim, Evidence
from founderlookup.domain.lifecycles import (
    ApplicationStatus,
    OpportunityOrigin,
    OutboundCandidateStatus,
    ScreeningCaseStatus,
)
from founderlookup.domain.query import (
    CriterionStrength,
    OpportunityQueryPlan,
    QueryCriterionField,
    QueryOperator,
    UnknownValuePolicy,
)
from founderlookup.domain.runs import PipelineRun


class ThesisCriterionMode(StrEnum):
    HARD_CONSTRAINT = "hard_constraint"
    SCORED_PREFERENCE = "scored_preference"
    NO_PREFERENCE = "no_preference"


class ThesisCriterion(DomainModel):
    mode: ThesisCriterionMode
    operator: QueryOperator | None = None
    values: tuple[ScalarValue, ...] = ()
    unknown_policy: UnknownValuePolicy

    @computed_field  # type: ignore[prop-decorator]
    @property
    def configured_outcome(self) -> RuleOutcome | None:
        """No Preference is inspectably Not Evaluated, never inferred missingness."""

        if self.mode is ThesisCriterionMode.NO_PREFERENCE:
            return RuleOutcome.NOT_EVALUATED
        return None

    @model_validator(mode="after")
    def validate_values(self) -> Self:
        if self.mode is ThesisCriterionMode.NO_PREFERENCE:
            if self.operator is not None or self.values:
                raise ValueError("no_preference cannot carry an operator or values")
            return self
        if self.operator is None:
            raise ValueError("binding or scored criteria require an operator")
        operand_count = len(self.values)
        if self.operator in {QueryOperator.IS_KNOWN, QueryOperator.IS_UNKNOWN}:
            if operand_count:
                raise ValueError("knowledge-state operators do not accept values")
        elif self.operator is QueryOperator.BETWEEN:
            if operand_count != 2:
                raise ValueError("between requires exactly two values")
            if not all(
                isinstance(value, int | float) and not isinstance(value, bool)
                for value in self.values
            ):
                raise ValueError("between values must be numeric")
        elif self.operator in {QueryOperator.ANY_OF, QueryOperator.ALL_OF}:
            if operand_count < 1:
                raise ValueError("set operators require at least one value")
        elif operand_count != 1:
            raise ValueError("operator requires exactly one value")
        return self


class InvestmentThesisRevision(DomainModel):
    thesis_id: StableId
    thesis_version_id: StableId
    revision_number: PositiveInt
    created_at: UTCDateTime
    created_by: StableId
    sector: ThesisCriterion
    stage: ThesisCriterion
    geography: ThesisCriterion
    check_size: ThesisCriterion
    ownership_target: ThesisCriterion
    risk_appetite: ThesisCriterion


class ThesisDraft(DomainModel):
    sector: ThesisCriterion
    stage: ThesisCriterion
    geography: ThesisCriterion
    check_size: ThesisCriterion
    ownership_target: ThesisCriterion
    risk_appetite: ThesisCriterion


class ApplicationReceipt(DomainModel):
    application_id: StableId
    company_id: StableId
    run_id: StableId
    source_artifact_id: StableId
    status: ApplicationStatus
    received_at: UTCDateTime
    founder_status_capability: NonBlankStr
    replayed: bool = False


class FounderFacingStage(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    NEEDS_INFORMATION = "needs_information"
    UNDER_REVIEW = "under_review"
    COMPLETE = "complete"


class TargetState(StrEnum):
    ON_TRACK = "on_track"
    APPROACHING = "approaching"
    MISSED = "missed"
    COMPLETE = "complete"


class FounderStatusView(DomainModel):
    application_id: StableId
    received_at: UTCDateTime
    stage: FounderFacingStage
    last_updated_at: UTCDateTime
    target_state: TargetState
    information_requests: tuple[NonBlankStr, ...] = ()
    outcome: NonBlankStr | None = None
    next_action: NonBlankStr | None = None
    outcome_at: UTCDateTime | None = None


class OutboundCandidateView(DomainModel):
    outbound_candidate_id: StableId
    company_id: StableId
    company_name: NonBlankStr
    founder_id: KnowledgeValue[StableId]
    status: OutboundCandidateStatus
    discovered_at: UTCDateTime
    source_artifact_ids: tuple[StableId, ...] = ()
    preliminary_assessment: AssessmentEnvelope | None = None
    application_id: StableId | None = None
    outreach_draft: NonBlankStr | None = None
    updated_at: UTCDateTime


class OutreachMethod(StrEnum):
    EMAIL = "email"
    LINKEDIN = "linkedin"
    INTRODUCTION = "introduction"
    OTHER = "other"


class OutreachRecord(DomainModel):
    outreach_id: StableId
    outbound_candidate_id: StableId
    method: OutreachMethod
    status: NonBlankStr
    actor_id: StableId
    occurred_at: UTCDateTime


class RunAccepted(DomainModel):
    run_id: StableId
    status_url: NonBlankStr
    run: PipelineRun


class OpportunityTiming(DomainModel):
    started_at: UTCDateTime
    last_updated_at: UTCDateTime
    decision_readiness_target_at: UTCDateTime
    elapsed_seconds: Annotated[int, Field(strict=True, ge=0)]
    target_state: TargetState


class OpportunityDetail(DomainModel):
    opportunity_id: StableId
    origin: OpportunityOrigin
    application_id: StableId
    outbound_candidate_id: StableId | None = None
    founder_id: KnowledgeValue[StableId]
    company_id: StableId
    screening_case_id: StableId
    screening_status: ScreeningCaseStatus
    latest_assessment: AssessmentEnvelope | None = None
    assessment_history: tuple[AssessmentEnvelope, ...] = ()
    claims: tuple[Claim, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    latest_memo: InvestmentMemo | None = None
    memo_revisions: tuple[InvestmentMemo, ...] = ()
    latest_recommendation: Recommendation | None = None
    human_decisions: tuple[Decision, ...] = ()
    related_run_ids: tuple[StableId, ...] = ()
    timing: OpportunityTiming


class OpportunitySummary(DomainModel):
    opportunity_id: StableId
    origin: OpportunityOrigin
    company_id: StableId
    screening_case_id: StableId
    screening_status: ScreeningCaseStatus
    recommendation: NonBlankStr | None = None
    updated_at: UTCDateTime


class OpportunityCollection(DomainModel):
    items: tuple[OpportunitySummary, ...]
    limit: PositiveInt
    truncated: bool
    applied_filters: tuple[NonBlankStr, ...] = ()
    ordering: NonBlankStr = "updated_at_desc,opportunity_id_asc"


class CandidateCollection(DomainModel):
    items: tuple[OutboundCandidateView, ...]
    limit: PositiveInt
    truncated: bool
    applied_filters: tuple[NonBlankStr, ...] = ()
    ordering: NonBlankStr = "discovered_at_asc,outbound_candidate_id_asc"


class CriterionMatchOutcome(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


class QueryCriterionResult(DomainModel):
    criterion_id: StableId
    field: QueryCriterionField
    strength: CriterionStrength
    outcome: CriterionMatchOutcome
    rationale: NonBlankStr
    knowledge_state: KnowledgeState
    unknown_policy: UnknownValuePolicy


class QueryResultItem(DomainModel):
    opportunity_id: StableId
    criteria: tuple[QueryCriterionResult, ...]
    matched_preferences: NonNegativeInt
    evaluated_preferences: NonNegativeInt


class QueryResult(DomainModel):
    plan: OpportunityQueryPlan
    results: tuple[QueryResultItem, ...]
    eligible_count: NonNegativeInt
    truncated: bool
    ordering: NonBlankStr
    sourcing_run_id: StableId | None = None


class PrivateArtifactDescriptor(DomainModel):
    artifact_id: StableId
    content_sha256: NonBlankStr
    media_type: NonBlankStr
    display_name: NonBlankStr


class StatusCapabilityRecord(DomainModel):
    application_id: StableId
    digest: NonBlankStr
    revoked: bool
    expires_at: UTCDateTime | None = None


class ServiceVersions(DomainModel):
    thesis_version: VersionId
    deterministic_rules: VersionId = "fake-rules.v0"
    founder_score: VersionId = "fake-founder-score.v0"
    axis_rubric: VersionId = "fake-axis-rubric.v0"
    readiness_policy: VersionId = "fake-readiness.v0"
    memo: VersionId = "fake-memo.v0"
    recommendation: VersionId = "fake-recommendation.v0"
