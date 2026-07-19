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
