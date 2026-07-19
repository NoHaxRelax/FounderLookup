"""Deterministic tests for the natural-language query planner baseline.

These exercise the honesty invariants that matter most for a v0 planner:
contract-valid output, honest unresolved offsets, no hallucinated criteria,
determinism, deliberate hard-vs-preference strength, explicit Unknown policy,
correct operand arity, and bounded retrieval requests.
"""

import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from founderlookup.domain.evidence import SourceCategory
from founderlookup.domain.query import (
    CriterionStrength,
    OpportunityQueryPlan,
    QueryCriterion,
    QueryCriterionField,
    QueryOperator,
    QueryPlanningMode,
    QueryPlanState,
    UnknownValuePolicy,
)
from founderlookup.ingestion.query_planner import (
    DETERMINISTIC_QUERY_PLANNER_VERSION,
    DeterministicQueryPlanner,
    QueryPlannerPort,
    QueryPlanRequest,
)

NOW = datetime(2026, 7, 19, 10, 0, tzinfo=UTC)


def _request(raw_query: str, **overrides: object) -> QueryPlanRequest:
    payload: dict[str, object] = {
        "raw_query": raw_query,
        "query_plan_id": "query-plan:1",
        "query_plan_version_id": "query-plan-version:1",
        "created_at": NOW,
        "max_results": 25,
    }
    payload.update(overrides)
    return QueryPlanRequest(**payload)  # type: ignore[arg-type]


def _plan(raw_query: str, **overrides: object) -> OpportunityQueryPlan:
    planner = DeterministicQueryPlanner()
    return asyncio.run(planner.plan(_request(raw_query, **overrides)))


def _fields(plan: OpportunityQueryPlan) -> set[QueryCriterionField]:
    return {criterion.field for criterion in plan.criteria}


def _criterion(plan: OpportunityQueryPlan, field: QueryCriterionField) -> QueryCriterion:
    return next(item for item in plan.criteria if item.field is field)


def test_baseline_satisfies_the_port_protocol() -> None:
    assert isinstance(DeterministicQueryPlanner(), QueryPlannerPort)


def test_plan_is_stamped_and_deterministic() -> None:
    query = "pre-seed AI/ML founders in Berlin, ideally YC-backed"
    first = _plan(query)
    second = _plan(query)

    assert first.planning_mode is QueryPlanningMode.DETERMINISTIC
    assert first.planner_version == DETERMINISTIC_QUERY_PLANNER_VERSION
    assert first.semantic_rerank is None
    # Byte-identical serialization proves there is no hidden nondeterminism.
    assert first.model_dump_json() == second.model_dump_json()


def test_recognized_cues_map_to_typed_criteria() -> None:
    plan = _plan(
        "We only fund pre-seed fintech companies in Europe with technical founders"
    )
    assert plan.state is QueryPlanState.VALIDATED
    assert _fields(plan) == {
        QueryCriterionField.STAGE,
        QueryCriterionField.SECTOR,
        QueryCriterionField.GEOGRAPHY,
        QueryCriterionField.TECHNICAL_FOUNDER,
    }
    stage = _criterion(plan, QueryCriterionField.STAGE)
    assert stage.operator is QueryOperator.EQUALS
    assert stage.operands == ("pre_seed",)
    assert stage.source_text == "pre-seed"


def test_every_unresolved_phrase_has_honest_offsets() -> None:
    queries = [
        "technical founders in Berlin working on quantum cryptography",
        "AI/ML teams with novel foundation models for exotic drug discovery",
        "prefer YC-backed founders, high-risk moonshot bets, growth-stage only",
        "raising $500k to $2m checks in the Bay Area for climate tech",
        "totally novel exotic widget synthesis pipeline",
    ]
    for query in queries:
        plan = _plan(query)
        for phrase in plan.unresolved_phrases:
            assert query[phrase.start_offset : phrase.end_offset] == phrase.text
            assert phrase.end_offset > phrase.start_offset


def test_unrecognized_text_is_never_turned_into_a_criterion() -> None:
    plan = _plan("totally novel exotic widget synthesis")
    assert plan.criteria == ()
    assert plan.retrieval_requests == ()
    assert plan.state is QueryPlanState.REJECTED
    texts = {phrase.text for phrase in plan.unresolved_phrases}
    assert "totally novel exotic widget synthesis" in texts


def test_unrecognized_span_survives_length_changing_lowercasing() -> None:
    # The dotted capital I lowercases to two code points; the planner must keep
    # offsets aligned so the reported span still slices back to its own text.
    plan = _plan("İstanbul robotics zeppelin foundries")
    assert QueryCriterionField.SECTOR in _fields(plan)  # robotics is recognized
    for phrase in plan.unresolved_phrases:
        assert plan.raw_query[phrase.start_offset : phrase.end_offset] == phrase.text


def test_hard_words_force_a_hard_constraint() -> None:
    plan = _plan("candidates must be based in Berlin")
    geography = _criterion(plan, QueryCriterionField.GEOGRAPHY)
    assert geography.strength is CriterionStrength.HARD_CONSTRAINT


def test_preference_words_force_a_scored_preference() -> None:
    # Geography defaults to a hard constraint, so an explicit "prefer" flip is
    # the deliberate signal being tested.
    plan = _plan("we would prefer founders in London")
    geography = _criterion(plan, QueryCriterionField.GEOGRAPHY)
    assert geography.strength is CriterionStrength.SCORED_PREFERENCE


def test_strength_default_differs_by_field() -> None:
    plan = _plan("seed sector fintech with enterprise traction")
    stage = _criterion(plan, QueryCriterionField.STAGE)
    traction = _criterion(plan, QueryCriterionField.ENTERPRISE_TRACTION)
    # No modifier present: stage defaults hard, traction defaults preference.
    assert stage.strength is CriterionStrength.HARD_CONSTRAINT
    assert traction.strength is CriterionStrength.SCORED_PREFERENCE


def test_every_criterion_carries_an_explicit_unknown_policy() -> None:
    plan = _plan(
        "pre-seed fintech founders in Berlin, YC-backed, with enterprise traction"
    )
    assert plan.criteria
    for criterion in plan.criteria:
        assert isinstance(criterion.unknown_policy, UnknownValuePolicy)
    policies = {criterion.unknown_policy for criterion in plan.criteria}
    # The lexicon deliberately uses more than one policy across fields.
    assert len(policies) >= 2


def test_generic_accelerator_uses_is_known_with_no_operands() -> None:
    plan = _plan("founders who went through an accelerator")
    accelerator = _criterion(plan, QueryCriterionField.ACCELERATOR)
    assert accelerator.operator is QueryOperator.IS_KNOWN
    assert accelerator.operands == ()


def test_named_accelerator_uses_equals_with_one_operand() -> None:
    plan = _plan("prefer Techstars alumni")
    accelerator = _criterion(plan, QueryCriterionField.ACCELERATOR)
    assert accelerator.operator is QueryOperator.EQUALS
    assert accelerator.operands == ("Techstars",)
    assert accelerator.unknown_policy is UnknownValuePolicy.MANUAL_REVIEW


def test_check_size_range_maps_to_between_two_numeric_operands() -> None:
    plan = _plan("funds writing $500k to $2m checks")
    check = _criterion(plan, QueryCriterionField.CHECK_SIZE)
    assert check.operator is QueryOperator.BETWEEN
    assert check.operands == (500_000.0, 2_000_000.0)


def test_qualified_single_check_maps_to_a_directional_comparison() -> None:
    up_to = _criterion(_plan("up to $2m per check"), QueryCriterionField.CHECK_SIZE)
    assert up_to.operator is QueryOperator.LESS_THAN_OR_EQUAL
    assert up_to.operands == (2_000_000.0,)

    at_least = _criterion(
        _plan("at least $1m per check"), QueryCriterionField.CHECK_SIZE
    )
    assert at_least.operator is QueryOperator.GREATER_THAN_OR_EQUAL


def test_ownership_target_maps_to_a_percentage_floor() -> None:
    plan = _plan("targeting at least 15% ownership")
    ownership = _criterion(plan, QueryCriterionField.OWNERSHIP_TARGET)
    assert ownership.operator is QueryOperator.GREATER_THAN_OR_EQUAL
    assert ownership.operands == (15.0,)


def test_bare_amount_without_a_qualifier_is_not_guessed() -> None:
    plan = _plan("we write $500k checks")
    # No direction is stated, so the planner refuses to invent a comparison.
    assert QueryCriterionField.CHECK_SIZE not in _fields(plan)
    assert any("500k" in phrase.text for phrase in plan.unresolved_phrases)


def test_source_cue_emits_a_bounded_retrieval_request() -> None:
    plan = _plan("technical founders active on GitHub", retrieval_max_pages=4)
    categories = {
        category
        for request in plan.retrieval_requests
        for category in request.source_categories
    }
    assert SourceCategory.DEVELOPER_ACTIVITY in categories
    request = plan.retrieval_requests[0]
    assert request.query == plan.raw_query
    assert request.max_pages == 4
    assert request.max_results == 25


def test_duplicate_source_categories_yield_one_retrieval_request() -> None:
    # Both the technical-founder cue and the GitHub cue imply developer activity.
    plan = _plan("technical founders on GitHub with open-source commits")
    dev_requests = [
        request
        for request in plan.retrieval_requests
        if SourceCategory.DEVELOPER_ACTIVITY in request.source_categories
    ]
    assert len(dev_requests) == 1
    identifiers = [request.retrieval_request_id for request in plan.retrieval_requests]
    assert len(identifiers) == len(set(identifiers))


def test_allowed_source_categories_bounds_retrieval_emission() -> None:
    plan = _plan(
        "founders with patents and hackathon wins",
        allowed_source_categories=(SourceCategory.PATENT,),
    )
    categories = {
        category
        for request in plan.retrieval_requests
        for category in request.source_categories
    }
    assert categories == {SourceCategory.PATENT}


def test_retrieval_only_query_is_still_validated() -> None:
    # A pure source cue produces a source_category criterion plus a retrieval,
    # so the plan is validated even though the caller asked only "where".
    plan = _plan("scan Product Hunt launches")
    assert plan.state is QueryPlanState.VALIDATED
    assert plan.retrieval_requests


def test_plan_with_no_signal_is_a_draft() -> None:
    plan = _plan("find founders")
    assert plan.criteria == ()
    assert plan.retrieval_requests == ()
    assert plan.unresolved_phrases == ()
    assert plan.state is QueryPlanState.DRAFT


def test_multiple_same_field_criteria_keep_unique_identifiers() -> None:
    plan = _plan("contrarian high-risk moonshot founders in Berlin")
    identifiers = [criterion.criterion_id for criterion in plan.criteria]
    assert len(identifiers) == len(set(identifiers))
    risk_criteria = [
        criterion
        for criterion in plan.criteria
        if criterion.field is QueryCriterionField.RISK_APPETITE
    ]
    assert len(risk_criteria) >= 1


def test_sector_exclusion_maps_to_a_hard_not_equals() -> None:
    plan = _plan("early-stage founders, but not crypto")
    sector = _criterion(plan, QueryCriterionField.SECTOR)
    assert sector.operator is QueryOperator.NOT_EQUALS
    assert sector.operands == ("crypto",)
    assert sector.strength is CriterionStrength.HARD_CONSTRAINT
    assert sector.unknown_policy is UnknownValuePolicy.MANUAL_REVIEW


def test_stage_range_maps_to_any_of_named_stages() -> None:
    plan = _plan("seed to series a founders in Europe")
    stage = _criterion(plan, QueryCriterionField.STAGE)
    assert stage.operator is QueryOperator.ANY_OF
    assert stage.operands == ("seed", "series_a")
    # The wider range wins over the two single-stage cues it contains.
    stage_criteria = [
        item for item in plan.criteria if item.field is QueryCriterionField.STAGE
    ]
    assert len(stage_criteria) == 1


def test_undisclosed_funding_uses_is_unknown_with_no_operands() -> None:
    plan = _plan("founders with funding undisclosed")
    backing = _criterion(plan, QueryCriterionField.PRIOR_VC_BACKING)
    assert backing.operator is QueryOperator.IS_UNKNOWN
    assert backing.operands == ()
    assert backing.unknown_policy is UnknownValuePolicy.PRESERVE_AS_UNKNOWN


def test_a_self_superseding_request_is_rejected_at_the_boundary() -> None:
    # A request whose supersedes id equals its own version id is rejected here as a clear
    # caller error, rather than crashing deep in plan construction on the frozen plan rule.
    with pytest.raises(ValidationError, match="cannot equal query_plan_version_id"):
        _request(
            "pre-seed fintech in Berlin",
            supersedes_query_plan_version_id="query-plan-version:1",
        )
