"""Typed, editable translation of one compound sourcing request."""

from __future__ import annotations

from enum import StrEnum
from typing import Final, Literal, Self

from pydantic import model_validator

from founderlookup.domain.common import (
    DomainModel,
    NonBlankStr,
    NonNegativeInt,
    PositiveInt,
    ScalarValue,
    StableId,
    UTCDateTime,
    VersionId,
)
from founderlookup.domain.discovery import BoundedRetrievalRequest

OPPORTUNITY_QUERY_PLAN_SCHEMA_VERSION: Final = "opportunity-query-plan.v0"


class QueryPlanningMode(StrEnum):
    DETERMINISTIC = "deterministic"
    MODEL_ASSISTED = "model_assisted"
    AGENT_ASSISTED = "agent_assisted"


class QueryPlanState(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    REJECTED = "rejected"


class QueryCriterionField(StrEnum):
    TECHNICAL_FOUNDER = "technical_founder"
    GEOGRAPHY = "geography"
    SECTOR = "sector"
    STAGE = "stage"
    CHECK_SIZE = "check_size"
    OWNERSHIP_TARGET = "ownership_target"
    RISK_APPETITE = "risk_appetite"
    ENTERPRISE_TRACTION = "enterprise_traction"
    PRIOR_VC_BACKING = "prior_vc_backing"
    ACCELERATOR = "accelerator"
    SOURCE_CATEGORY = "source_category"
    ORIGIN = "origin"
    WORKFLOW_STATE = "workflow_state"
    RECOMMENDATION = "recommendation"
    FOUNDER_AXIS = "founder_axis"
    MARKET_AXIS = "market_axis"
    IDEA_VS_MARKET_AXIS = "idea_vs_market_axis"
    TREND = "trend"
    CONTRADICTION_STATE = "contradiction_state"
    EVIDENCE_COVERAGE = "evidence_coverage"
    KNOWLEDGE_STATE = "knowledge_state"


class QueryOperator(StrEnum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    ANY_OF = "any_of"
    ALL_OF = "all_of"
    CONTAINS = "contains"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    BETWEEN = "between"
    IS_KNOWN = "is_known"
    IS_UNKNOWN = "is_unknown"


class CriterionStrength(StrEnum):
    HARD_CONSTRAINT = "hard_constraint"
    SCORED_PREFERENCE = "scored_preference"


class UnknownValuePolicy(StrEnum):
    PRESERVE_AS_UNKNOWN = "preserve_as_unknown"
    NEEDS_INFORMATION = "needs_information"
    MANUAL_REVIEW = "manual_review"


class QueryCriterion(DomainModel):
    criterion_id: StableId
    field: QueryCriterionField
    operator: QueryOperator
    operands: tuple[ScalarValue, ...] = ()
    strength: CriterionStrength
    unknown_policy: UnknownValuePolicy
    source_text: NonBlankStr

    @model_validator(mode="after")
    def validate_operand_arity(self) -> Self:
        operand_count = len(self.operands)
        if self.operator in {QueryOperator.IS_KNOWN, QueryOperator.IS_UNKNOWN}:
            if operand_count:
                raise ValueError("knowledge-state operators do not accept operands")
        elif self.operator is QueryOperator.BETWEEN:
            if operand_count != 2:
                raise ValueError("between requires exactly two operands")
            if not all(
                isinstance(value, int | float) and not isinstance(value, bool)
                for value in self.operands
            ):
                raise ValueError("between operands must be numeric")
        elif self.operator in {QueryOperator.ANY_OF, QueryOperator.ALL_OF}:
            if operand_count < 1:
                raise ValueError("set operators require at least one operand")
        elif operand_count != 1:
            raise ValueError("operator requires exactly one operand")
        return self


class UnresolvedQueryPhrase(DomainModel):
    text: NonBlankStr
    start_offset: NonNegativeInt
    end_offset: PositiveInt
    reason: NonBlankStr

    @model_validator(mode="after")
    def validate_offsets(self) -> Self:
        if self.end_offset <= self.start_offset:
            raise ValueError("end_offset must be greater than start_offset")
        return self


class SemanticRerankPlan(DomainModel):
    """Optional labeled ranking only; deterministic hard results retain authority."""

    query: NonBlankStr
    method_version: VersionId
    max_results: PositiveInt


class OpportunityQueryPlan(DomainModel):
    schema_version: Literal["opportunity-query-plan.v0"] = OPPORTUNITY_QUERY_PLAN_SCHEMA_VERSION
    query_plan_id: StableId
    query_plan_version_id: StableId
    supersedes_query_plan_version_id: StableId | None = None
    raw_query: NonBlankStr
    planning_mode: QueryPlanningMode
    planner_version: VersionId
    state: QueryPlanState
    criteria: tuple[QueryCriterion, ...] = ()
    retrieval_requests: tuple[BoundedRetrievalRequest, ...] = ()
    unresolved_phrases: tuple[UnresolvedQueryPhrase, ...] = ()
    semantic_rerank: SemanticRerankPlan | None = None
    max_results: PositiveInt
    created_at: UTCDateTime

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        criterion_ids = tuple(item.criterion_id for item in self.criteria)
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("criterion identifiers must be unique")
        retrieval_ids = tuple(item.retrieval_request_id for item in self.retrieval_requests)
        if len(retrieval_ids) != len(set(retrieval_ids)):
            raise ValueError("retrieval request identifiers must be unique")
        if self.state is QueryPlanState.VALIDATED and not (
            self.criteria or self.retrieval_requests
        ):
            raise ValueError("validated plan requires criteria or retrieval requests")
        if self.state is QueryPlanState.REJECTED and not self.unresolved_phrases:
            raise ValueError("rejected plan requires an unresolved phrase")
        if self.supersedes_query_plan_version_id == self.query_plan_version_id:
            raise ValueError("query plan version cannot supersede itself")
        return self
