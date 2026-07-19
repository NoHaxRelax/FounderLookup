"""Focused behavior tests for deterministic compound-query planning."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from founderlookup.domain.evidence import SourceCategory
from founderlookup.domain.query import (
    CriterionStrength,
    QueryCriterionField,
    QueryOperator,
    QueryPlanState,
    UnknownValuePolicy,
)
from founderlookup.screening.query_planner import (
    ControlledVocabularyEntry,
    DeterministicQueryPlanner,
    QueryPlannerRequest,
)

_NOW = datetime(2026, 7, 19, 12, tzinfo=UTC)
_COMPOUND_QUERY = (
    "technical founder, Berlin, AI infra, enterprise traction, no prior VC backing, "
    "top-tier accelerator"
)


def _planner() -> DeterministicQueryPlanner:
    return DeterministicQueryPlanner(clock=lambda: _NOW)


def test_compound_prd_query_produces_six_inspectable_criteria_in_one_call() -> None:
    plan = _planner().plan(QueryPlannerRequest(raw_query=_COMPOUND_QUERY))

    assert plan.state is QueryPlanState.VALIDATED
    assert tuple(item.field for item in plan.criteria) == (
        QueryCriterionField.TECHNICAL_FOUNDER,
        QueryCriterionField.GEOGRAPHY,
        QueryCriterionField.SECTOR,
        QueryCriterionField.ENTERPRISE_TRACTION,
        QueryCriterionField.PRIOR_VC_BACKING,
        QueryCriterionField.ACCELERATOR,
    )
    criteria = {item.field: item for item in plan.criteria}
    assert criteria[QueryCriterionField.TECHNICAL_FOUNDER].operands == (True,)
    assert criteria[QueryCriterionField.GEOGRAPHY].operands == ("Berlin",)
    assert criteria[QueryCriterionField.SECTOR].operands == ("ai_infrastructure",)
    assert criteria[QueryCriterionField.ENTERPRISE_TRACTION].operands == (True,)
    assert criteria[QueryCriterionField.PRIOR_VC_BACKING].operands == (False,)
    assert criteria[QueryCriterionField.PRIOR_VC_BACKING].operator is QueryOperator.EQUALS
    assert criteria[QueryCriterionField.ACCELERATOR].operator is QueryOperator.IS_KNOWN
    assert criteria[QueryCriterionField.ACCELERATOR].operands == ()

    assert criteria[QueryCriterionField.SECTOR].strength is CriterionStrength.HARD_CONSTRAINT
    assert criteria[QueryCriterionField.SECTOR].unknown_policy is UnknownValuePolicy.MANUAL_REVIEW
    assert criteria[QueryCriterionField.GEOGRAPHY].strength is CriterionStrength.SCORED_PREFERENCE
    assert (
        criteria[QueryCriterionField.GEOGRAPHY].unknown_policy
        is UnknownValuePolicy.PRESERVE_AS_UNKNOWN
    )

    assert len(plan.unresolved_phrases) == 1
    unresolved = plan.unresolved_phrases[0]
    assert unresolved.text == "top-tier"
    assert unresolved.start_offset == _COMPOUND_QUERY.index("top-tier")
    assert unresolved.end_offset == unresolved.start_offset + len("top-tier")


def test_retrieval_is_provider_neutral_bounded_and_does_not_infer_negative_from_silence() -> None:
    plan = _planner().plan(
        QueryPlannerRequest(
            raw_query=_COMPOUND_QUERY,
            retrieval_max_results=20,
            retrieval_max_pages=3,
            retrieval_timeout_seconds=30,
        )
    )

    assert len(plan.retrieval_requests) == 1
    retrieval = plan.retrieval_requests[0]
    assert len(retrieval.query) <= 400
    assert "top-tier" not in retrieval.query
    assert "prior venture capital backing" in retrieval.query
    assert retrieval.max_results == 20
    assert retrieval.max_pages == 3
    assert retrieval.timeout_seconds == 30
    assert retrieval.source_categories == (
        SourceCategory.DEVELOPER_ACTIVITY,
        SourceCategory.PRODUCT_LAUNCH,
        SourceCategory.COMPANY_UPDATE,
        SourceCategory.ACCELERATOR_COHORT,
    )


def test_subjective_accelerator_phrase_requires_a_supplied_controlled_vocabulary() -> None:
    plan = _planner().plan(
        QueryPlannerRequest(
            raw_query="Berlin AI infra, top-tier accelerator",
            controlled_vocabulary=(
                ControlledVocabularyEntry(
                    phrase="top-tier accelerator",
                    field=QueryCriterionField.ACCELERATOR,
                    canonical_values=("Y Combinator", "Entrepreneur First"),
                ),
            ),
        )
    )

    accelerator = next(
        item for item in plan.criteria if item.field is QueryCriterionField.ACCELERATOR
    )
    assert accelerator.operator is QueryOperator.ANY_OF
    assert accelerator.operands == ("Y Combinator", "Entrepreneur First")
    assert plan.unresolved_phrases == ()


def test_same_request_and_clock_produce_identical_plan_and_stable_order() -> None:
    request = QueryPlannerRequest(raw_query=_COMPOUND_QUERY)

    first = _planner().plan(request)
    second = _planner().plan(request)

    assert first == second
    assert first.query_plan_id == second.query_plan_id
    assert tuple(item.criterion_id for item in first.criteria) == tuple(
        item.criterion_id for item in second.criteria
    )


@pytest.mark.parametrize(
    "overrides",
    (
        {"raw_query": " "},
        {"raw_query": "x" * 401},
        {"raw_query": "Berlin\x00"},
        {"raw_query": "Berlin", "retrieval_max_results": 21},
        {"raw_query": "Berlin", "retrieval_max_pages": 4},
        {"raw_query": "Berlin", "retrieval_timeout_seconds": 31},
    ),
)
def test_input_and_provider_neutral_budgets_are_bounded(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        QueryPlannerRequest(**overrides)  # type: ignore[arg-type]


def test_sqlish_text_is_inert_unresolved_and_never_reaches_retrieval() -> None:
    raw_query = "technical founder in Berlin; DROP TABLE founders; --"

    plan = _planner().plan(QueryPlannerRequest(raw_query=raw_query))

    assert tuple(item.field for item in plan.criteria) == (
        QueryCriterionField.TECHNICAL_FOUNDER,
        QueryCriterionField.GEOGRAPHY,
    )
    assert any(item.text == "DROP TABLE founders" for item in plan.unresolved_phrases)
    retrieval_query = plan.retrieval_requests[0].query.casefold()
    assert "drop" not in retrieval_query
    assert "table" not in retrieval_query
    assert "--" not in retrieval_query


def test_unrecognized_prose_does_not_fabricate_a_criterion_or_retrieval_request() -> None:
    raw_query = "visionary founder with magnetic storytelling"

    plan = _planner().plan(QueryPlannerRequest(raw_query=raw_query))

    assert plan.state is QueryPlanState.REJECTED
    assert plan.criteria == ()
    assert plan.retrieval_requests == ()
    assert tuple(item.text for item in plan.unresolved_phrases) == (raw_query,)
