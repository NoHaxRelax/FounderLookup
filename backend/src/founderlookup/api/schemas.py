"""Client-friendly HTTP commands converted into strict frozen domain contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from founderlookup.application.models import (
    OutreachMethod,
    ThesisCriterion,
    ThesisCriterionMode,
    ThesisDraft,
)
from founderlookup.application.sourcing import BoundedSourcingCommand
from founderlookup.domain.assessment import HumanDecisionDisposition
from founderlookup.domain.common import NonBlankStr, ScalarValue, StableId, VersionId
from founderlookup.domain.evidence import SourceCategory
from founderlookup.domain.query import (
    CriterionStrength,
    OpportunityQueryPlan,
    QueryCriterionField,
    QueryOperator,
    QueryPlanningMode,
    QueryPlanState,
    UnknownValuePolicy,
)
from founderlookup.screening.query_planner import (
    ControlledVocabularyEntry,
    QueryPlannerRequest,
)


class RequestModel(BaseModel):
    """JSON clients send strings/lists; strict domain conversion happens explicitly."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ThesisCriterionRequest(RequestModel):
    mode: ThesisCriterionMode
    operator: QueryOperator | None = None
    values: tuple[ScalarValue, ...] = ()
    unknown_policy: UnknownValuePolicy

    @model_validator(mode="after")
    def validate_domain_shape(self) -> Self:
        self.to_domain()
        return self

    def to_domain(self) -> ThesisCriterion:
        return ThesisCriterion.model_validate_json(self.model_dump_json())


class ThesisDraftRequest(RequestModel):
    sector: ThesisCriterionRequest
    stage: ThesisCriterionRequest
    geography: ThesisCriterionRequest
    check_size: ThesisCriterionRequest
    ownership_target: ThesisCriterionRequest
    risk_appetite: ThesisCriterionRequest

    def to_domain(self) -> ThesisDraft:
        return ThesisDraft.model_validate_json(self.model_dump_json())


class OutreachCommand(RequestModel):
    method: OutreachMethod
    status: NonBlankStr


class ActivationCommand(RequestModel):
    outreach_draft: NonBlankStr | None = None


class SourcingRunCommand(RequestModel):
    """Provider-neutral public discovery command with explicit hard budgets."""

    query: str = Field(min_length=1, max_length=400)
    source_categories: tuple[SourceCategory, ...] = (SourceCategory.OTHER,)
    allowed_domains: tuple[str, ...] = ()
    excluded_domains: tuple[str, ...] = ()
    max_results: int = Field(default=10, gt=0, le=20)
    max_pages: int = Field(default=5, gt=0, le=20)
    max_bytes: int = Field(default=500_000, gt=0, le=5_000_000)
    timeout_seconds: int = Field(default=20, gt=0, le=60)

    @model_validator(mode="after")
    def validate_domain_shape(self) -> Self:
        self.to_domain()
        return self

    def to_domain(self) -> BoundedSourcingCommand:
        return BoundedSourcingCommand.model_validate_json(self.model_dump_json())


class ControlledVocabularyRequest(RequestModel):
    phrase: str = Field(min_length=1, max_length=100)
    field: QueryCriterionField
    canonical_values: tuple[NonBlankStr, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_domain_shape(self) -> Self:
        self.to_domain()
        return self

    def to_domain(self) -> ControlledVocabularyEntry:
        return ControlledVocabularyEntry.model_validate_json(self.model_dump_json())


class QueryPlannerCommand(RequestModel):
    """One compound investor query plus explicit provider-neutral retrieval budgets."""

    raw_query: str = Field(min_length=1, max_length=400)
    max_results: int = Field(default=25, ge=1, le=100)
    retrieval_max_results: int = Field(default=10, ge=1, le=20)
    retrieval_max_pages: int = Field(default=2, ge=1, le=3)
    retrieval_timeout_seconds: int = Field(default=10, ge=1, le=30)
    controlled_vocabulary: tuple[ControlledVocabularyRequest, ...] = Field(
        default=(),
        max_length=50,
    )

    @model_validator(mode="after")
    def validate_domain_shape(self) -> Self:
        self.to_domain()
        return self

    def to_domain(self) -> QueryPlannerRequest:
        return QueryPlannerRequest.model_validate_json(self.model_dump_json())


class DecisionCommand(RequestModel):
    assessment_id: StableId
    memo_id: StableId
    recommendation_id: StableId
    disposition: HumanDecisionDisposition
    rationale: NonBlankStr


class QueryCriterionRequest(RequestModel):
    criterion_id: StableId
    field: QueryCriterionField
    operator: QueryOperator
    operands: tuple[ScalarValue, ...] = ()
    strength: CriterionStrength
    unknown_policy: UnknownValuePolicy
    source_text: NonBlankStr


class RetrievalRequest(RequestModel):
    retrieval_request_id: StableId
    query: NonBlankStr
    source_categories: tuple[SourceCategory, ...] = Field(min_length=1)
    allowed_domains: tuple[NonBlankStr, ...] = ()
    excluded_domains: tuple[NonBlankStr, ...] = ()
    published_after: datetime | None = None
    published_before: datetime | None = None
    max_results: int = Field(gt=0)
    max_pages: int = Field(gt=0)
    timeout_seconds: int = Field(gt=0)


class UnresolvedPhraseRequest(RequestModel):
    text: NonBlankStr
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    reason: NonBlankStr


class SemanticRerankRequest(RequestModel):
    query: NonBlankStr
    method_version: VersionId
    max_results: int = Field(gt=0)


class QueryPlanRequest(RequestModel):
    schema_version: str = "opportunity-query-plan.v0"
    query_plan_id: StableId
    query_plan_version_id: StableId
    supersedes_query_plan_version_id: StableId | None = None
    raw_query: NonBlankStr
    planning_mode: QueryPlanningMode
    planner_version: VersionId
    state: QueryPlanState
    criteria: tuple[QueryCriterionRequest, ...] = ()
    retrieval_requests: tuple[RetrievalRequest, ...] = ()
    unresolved_phrases: tuple[UnresolvedPhraseRequest, ...] = ()
    semantic_rerank: SemanticRerankRequest | None = None
    max_results: int = Field(gt=0, le=100)
    created_at: datetime

    @model_validator(mode="after")
    def safe_shape(self) -> Self:
        self.to_domain()
        return self

    def to_domain(self) -> OpportunityQueryPlan:
        return OpportunityQueryPlan.model_validate_json(self.model_dump_json())


class QueryCommand(RequestModel):
    plan: QueryPlanRequest


class CapabilityRevokedResponse(RequestModel):
    revoked: bool = True
