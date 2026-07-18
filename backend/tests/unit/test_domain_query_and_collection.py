"""Provider-neutral collection and query-plan invariants."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from founderlookup.domain import (
    AcquisitionRequest,
    AcquisitionResult,
    AcquisitionStatus,
    BoundedRetrievalRequest,
    CriterionStrength,
    DataClassification,
    KnowledgeValue,
    OpportunityQueryPlan,
    QueryCriterion,
    QueryCriterionField,
    QueryOperator,
    QueryPlanningMode,
    QueryPlanState,
    SourceCategory,
    UnknownValuePolicy,
)

NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


def _retrieval() -> BoundedRetrievalRequest:
    return BoundedRetrievalRequest(
        retrieval_request_id="retrieval:1",
        query="Berlin AI infrastructure technical founder",
        source_categories=(SourceCategory.DEVELOPER_ACTIVITY,),
        max_results=20,
        max_pages=5,
        timeout_seconds=15,
    )


def test_query_criterion_enforces_typed_operator_shape() -> None:
    criterion = QueryCriterion(
        criterion_id="criterion:geography",
        field=QueryCriterionField.GEOGRAPHY,
        operator=QueryOperator.EQUALS,
        operands=("Berlin",),
        strength=CriterionStrength.HARD_CONSTRAINT,
        unknown_policy=UnknownValuePolicy.MANUAL_REVIEW,
        source_text="in Berlin",
    )
    assert criterion.operands == ("Berlin",)

    with pytest.raises(ValidationError, match="exactly one operand"):
        QueryCriterion(
            criterion_id="criterion:bad",
            field=QueryCriterionField.GEOGRAPHY,
            operator=QueryOperator.EQUALS,
            operands=(),
            strength=CriterionStrength.HARD_CONSTRAINT,
            unknown_policy=UnknownValuePolicy.PRESERVE_AS_UNKNOWN,
            source_text="in Berlin",
        )


def test_validated_query_plan_requires_inspectable_work_and_forbids_sql() -> None:
    with pytest.raises(ValidationError, match="requires criteria or retrieval"):
        OpportunityQueryPlan(
            query_plan_id="query-plan:1",
            query_plan_version_id="query-plan-version:1",
            raw_query="find founders",
            planning_mode=QueryPlanningMode.DETERMINISTIC,
            planner_version="planner.v0",
            state=QueryPlanState.VALIDATED,
            max_results=25,
            created_at=NOW,
        )

    payload = {
        "query_plan_id": "query-plan:1",
        "query_plan_version_id": "query-plan-version:1",
        "raw_query": "find founders",
        "planning_mode": "deterministic",
        "planner_version": "planner.v0",
        "state": "validated",
        "retrieval_requests": [_retrieval().model_dump(mode="json")],
        "max_results": 25,
        "created_at": "2026-07-18T10:00:00Z",
        "generated_sql": "select * from founders",
    }
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        OpportunityQueryPlan.model_validate(payload)


def test_acquisition_contract_keys_results_by_request_and_fails_closed() -> None:
    request = AcquisitionRequest(
        acquisition_request_id="acquisition:1",
        discovery_lead_id="lead:1",
        original_url="https://example.test/founder",
        requested_at=NOW,
        classification=DataClassification.PUBLIC,
        allowed_media_types=("text/html",),
        max_bytes=1_000_000,
        timeout_seconds=15,
    )
    assert request.acquisition_request_id == "acquisition:1"

    with pytest.raises(ValidationError, match="requires bytes, media type, and hash"):
        AcquisitionResult(
            result_id="acquisition-result:1",
            acquisition_request_id=request.acquisition_request_id,
            original_url=request.original_url,
            status=AcquisitionStatus.ACQUIRED,
            completed_at=NOW,
            source_event_time=KnowledgeValue[datetime].unknown("not published"),
        )
