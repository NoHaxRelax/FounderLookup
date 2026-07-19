"""Contract-level tests for deterministic query execution."""

from datetime import UTC, datetime

import pytest

from founderlookup.domain.assessment import (
    DeterministicRuleResult,
    RuleInput,
    RuleOutcome,
    RuleOverride,
)
from founderlookup.domain.common import KnowledgeValue
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
from founderlookup.screening.query_executor import (
    CanonicalValue,
    CriterionMatch,
    DeterministicQueryExecutor,
    DuplicateOverrideError,
    OpportunityQueryRecord,
    RuleOverrideLedger,
    UnsafeQueryPlanError,
)

NOW = datetime(2026, 7, 18, 10, tzinfo=UTC)


def _criterion(
    criterion_id: str,
    field: QueryCriterionField,
    operand: str | bool | int | float,
    *,
    strength: CriterionStrength = CriterionStrength.HARD_CONSTRAINT,
) -> QueryCriterion:
    return QueryCriterion(
        criterion_id=criterion_id,
        field=field,
        operator=QueryOperator.EQUALS,
        operands=(operand,),
        strength=strength,
        unknown_policy=UnknownValuePolicy.MANUAL_REVIEW,
        source_text=f"{field.value} equals {operand}",
    )


def _plan(*criteria: QueryCriterion, max_results: int = 20) -> OpportunityQueryPlan:
    return OpportunityQueryPlan(
        query_plan_id="plan-001",
        query_plan_version_id="plan-version-001",
        raw_query="technical founder in Berlin building AI infrastructure",
        planning_mode=QueryPlanningMode.DETERMINISTIC,
        planner_version="deterministic-parser.v0",
        state=QueryPlanState.VALIDATED,
        criteria=criteria,
        max_results=max_results,
        created_at=NOW,
    )


def test_known_hard_mismatch_is_excluded_but_unknown_remains_inspectable() -> None:
    plan = _plan(
        _criterion("criterion-tech", QueryCriterionField.TECHNICAL_FOUNDER, True),
        _criterion("criterion-geo", QueryCriterionField.GEOGRAPHY, "Berlin"),
    )
    records = (
        OpportunityQueryRecord(
            "opportunity-match",
            {
                QueryCriterionField.TECHNICAL_FOUNDER: KnowledgeValue.known(True),
                QueryCriterionField.GEOGRAPHY: KnowledgeValue.known("Berlin"),
            },
        ),
        OpportunityQueryRecord(
            "opportunity-unknown",
            {
                QueryCriterionField.TECHNICAL_FOUNDER: KnowledgeValue.known(True),
                QueryCriterionField.GEOGRAPHY: KnowledgeValue.unknown(
                    "No reliable geography source"
                ),
            },
        ),
        OpportunityQueryRecord(
            "opportunity-mismatch",
            {
                QueryCriterionField.TECHNICAL_FOUNDER: KnowledgeValue.known(False),
                QueryCriterionField.GEOGRAPHY: KnowledgeValue.known("Berlin"),
            },
        ),
    )

    result = DeterministicQueryExecutor().execute(plan, records)

    assert [item.opportunity_id for item in result.items] == [
        "opportunity-match",
        "opportunity-unknown",
    ]
    unknown_geo = result.items[1].criteria[1]
    assert unknown_geo.match is CriterionMatch.UNKNOWN
    assert unknown_geo.unknown_policy is UnknownValuePolicy.MANUAL_REVIEW
    assert "No reliable geography" in unknown_geo.reason


def test_preferences_rank_stably_without_converting_unknown_to_zero() -> None:
    plan = _plan(
        _criterion(
            "criterion-sector",
            QueryCriterionField.SECTOR,
            "AI infrastructure",
            strength=CriterionStrength.SCORED_PREFERENCE,
        )
    )
    records = (
        OpportunityQueryRecord(
            "opportunity-z",
            {QueryCriterionField.SECTOR: KnowledgeValue.known("AI infrastructure")},
        ),
        OpportunityQueryRecord(
            "opportunity-a",
            {QueryCriterionField.SECTOR: KnowledgeValue.unknown("Sector not extracted")},
        ),
        OpportunityQueryRecord(
            "opportunity-b",
            {QueryCriterionField.SECTOR: KnowledgeValue.known("AI infrastructure")},
        ),
    )

    result = DeterministicQueryExecutor().execute(plan, records)

    assert [item.opportunity_id for item in result.items] == [
        "opportunity-b",
        "opportunity-z",
        "opportunity-a",
    ]
    assert result.items[-1].evaluated_preferences == 0


def test_prd_compound_query_executes_once_without_treating_search_silence_as_no_vc() -> None:
    fields_and_values: tuple[tuple[QueryCriterionField, str | bool], ...] = (
        (QueryCriterionField.TECHNICAL_FOUNDER, True),
        (QueryCriterionField.GEOGRAPHY, "Berlin"),
        (QueryCriterionField.SECTOR, "AI infrastructure"),
        (QueryCriterionField.ENTERPRISE_TRACTION, True),
        (QueryCriterionField.PRIOR_VC_BACKING, False),
        (QueryCriterionField.ACCELERATOR, "top-tier"),
    )
    plan = _plan(
        *(
            _criterion(f"criterion-{index}", field, value)
            for index, (field, value) in enumerate(fields_and_values, start=1)
        )
    )
    candidate_values: dict[QueryCriterionField, KnowledgeValue[CanonicalValue]] = {
        field: KnowledgeValue[CanonicalValue].known(value) for field, value in fields_and_values
    }
    candidate_values[QueryCriterionField.PRIOR_VC_BACKING] = KnowledgeValue.unknown(
        "No reliable funding record was found; source silence is not proof"
    )
    mismatch_values = dict(candidate_values)
    mismatch_values[QueryCriterionField.TECHNICAL_FOUNDER] = KnowledgeValue.known(False)

    result = DeterministicQueryExecutor().execute(
        plan,
        (
            OpportunityQueryRecord("opportunity-compound", candidate_values),
            OpportunityQueryRecord("opportunity-hard-mismatch", mismatch_values),
        ),
    )

    assert [item.opportunity_id for item in result.items] == ["opportunity-compound"]
    evaluations = result.items[0].criteria
    assert len(evaluations) == 6
    prior_vc = next(
        item for item in evaluations if item.field is QueryCriterionField.PRIOR_VC_BACKING
    )
    assert prior_vc.match is CriterionMatch.UNKNOWN
    assert "not proof" in prior_vc.reason


def test_result_bound_is_explicit_and_deterministic() -> None:
    result = DeterministicQueryExecutor(maximum_results=2).execute(
        _plan(
            _criterion("criterion-origin", QueryCriterionField.ORIGIN, "inbound"),
            max_results=2,
        ),
        (
            OpportunityQueryRecord(
                opportunity_id,
                {QueryCriterionField.ORIGIN: KnowledgeValue.known("inbound")},
            )
            for opportunity_id in ("opportunity-c", "opportunity-a", "opportunity-b")
        ),
    )

    assert [item.opportunity_id for item in result.items] == [
        "opportunity-a",
        "opportunity-b",
    ]
    assert result.eligible_count == 3
    assert result.truncated is True


def test_executor_rejects_unvalidated_or_over_budget_plans() -> None:
    draft = _plan(_criterion("criterion-origin", QueryCriterionField.ORIGIN, "inbound")).model_copy(
        update={"state": QueryPlanState.DRAFT}
    )
    with pytest.raises(UnsafeQueryPlanError, match="validated"):
        DeterministicQueryExecutor().execute(draft, ())

    with pytest.raises(UnsafeQueryPlanError, match="policy maximum"):
        DeterministicQueryExecutor(maximum_results=1).execute(
            _plan(
                _criterion("criterion-origin", QueryCriterionField.ORIGIN, "inbound"),
                max_results=2,
            ),
            (),
        )


def test_executor_rejects_operator_field_and_operand_type_confusion() -> None:
    geography_between = QueryCriterion(
        criterion_id="criterion-geography-range",
        field=QueryCriterionField.GEOGRAPHY,
        operator=QueryOperator.BETWEEN,
        operands=(1, 2),
        strength=CriterionStrength.HARD_CONSTRAINT,
        unknown_policy=UnknownValuePolicy.MANUAL_REVIEW,
        source_text="geography between numeric bounds",
    )
    numeric_boolean = QueryCriterion(
        criterion_id="criterion-technical-numeric",
        field=QueryCriterionField.TECHNICAL_FOUNDER,
        operator=QueryOperator.EQUALS,
        operands=(1,),
        strength=CriterionStrength.HARD_CONSTRAINT,
        unknown_policy=UnknownValuePolicy.MANUAL_REVIEW,
        source_text="technical founder equals one",
    )

    with pytest.raises(UnsafeQueryPlanError, match="not allowed"):
        DeterministicQueryExecutor().execute(_plan(geography_between), ())
    with pytest.raises(UnsafeQueryPlanError, match="operands do not match"):
        DeterministicQueryExecutor().execute(_plan(numeric_boolean), ())


@pytest.mark.parametrize(
    ("field", "canonical_value"),
    (
        (QueryCriterionField.TECHNICAL_FOUNDER, 1),
        (QueryCriterionField.CHECK_SIZE, True),
    ),
)
def test_executor_rejects_wrong_typed_known_canonical_values(
    field: QueryCriterionField,
    canonical_value: CanonicalValue,
) -> None:
    operand: bool | int = True if field is QueryCriterionField.TECHNICAL_FOUNDER else 100_000
    criterion = _criterion("criterion-typed", field, operand)
    record = OpportunityQueryRecord(
        "opportunity-malformed",
        {field: KnowledgeValue[CanonicalValue].known(canonical_value)},
    )

    with pytest.raises(UnsafeQueryPlanError, match="canonical value"):
        DeterministicQueryExecutor().execute(_plan(criterion), (record,))


def test_numeric_ranges_and_multi_value_text_fields_remain_supported() -> None:
    check_size = QueryCriterion(
        criterion_id="criterion-check-size",
        field=QueryCriterionField.CHECK_SIZE,
        operator=QueryOperator.BETWEEN,
        operands=(50_000, 250_000),
        strength=CriterionStrength.HARD_CONSTRAINT,
        unknown_policy=UnknownValuePolicy.MANUAL_REVIEW,
        source_text="check size between 50k and 250k",
    )
    sector = QueryCriterion(
        criterion_id="criterion-sectors",
        field=QueryCriterionField.SECTOR,
        operator=QueryOperator.ALL_OF,
        operands=("AI", "infrastructure"),
        strength=CriterionStrength.HARD_CONSTRAINT,
        unknown_policy=UnknownValuePolicy.MANUAL_REVIEW,
        source_text="AI and infrastructure",
    )
    record = OpportunityQueryRecord(
        "opportunity-supported",
        {
            QueryCriterionField.CHECK_SIZE: KnowledgeValue[CanonicalValue].known(100_000),
            QueryCriterionField.SECTOR: KnowledgeValue[CanonicalValue].known(
                ("AI", "infrastructure")
            ),
        },
    )

    result = DeterministicQueryExecutor().execute(_plan(check_size, sector), (record,))

    assert tuple(item.opportunity_id for item in result.items) == ("opportunity-supported",)


def test_is_unknown_does_not_collapse_other_missingness_states() -> None:
    criterion = QueryCriterion(
        criterion_id="criterion-knowledge",
        field=QueryCriterionField.GEOGRAPHY,
        operator=QueryOperator.IS_UNKNOWN,
        strength=CriterionStrength.SCORED_PREFERENCE,
        unknown_policy=UnknownValuePolicy.PRESERVE_AS_UNKNOWN,
        source_text="geography is unknown",
    )
    result = DeterministicQueryExecutor().execute(
        _plan(criterion),
        (
            OpportunityQueryRecord(
                "opportunity-unknown",
                {QueryCriterionField.GEOGRAPHY: KnowledgeValue.unknown("Not established")},
            ),
            OpportunityQueryRecord(
                "opportunity-withheld",
                {
                    QueryCriterionField.GEOGRAPHY: KnowledgeValue.not_disclosed(
                        "Founder withheld the location"
                    )
                },
            ),
        ),
    )

    by_id = {item.opportunity_id: item for item in result.items}
    assert by_id["opportunity-unknown"].criteria[0].match is CriterionMatch.MATCH
    assert by_id["opportunity-withheld"].criteria[0].match is CriterionMatch.MISMATCH


def test_duplicate_canonical_records_are_rejected() -> None:
    plan = _plan(_criterion("criterion-origin", QueryCriterionField.ORIGIN, "inbound"))
    duplicate = OpportunityQueryRecord(
        "opportunity-duplicate",
        {QueryCriterionField.ORIGIN: KnowledgeValue.known("inbound")},
    )
    with pytest.raises(UnsafeQueryPlanError, match="duplicate opportunity"):
        DeterministicQueryExecutor().execute(plan, (duplicate, duplicate))


def test_query_record_snapshots_its_canonical_projection() -> None:
    values: dict[QueryCriterionField, KnowledgeValue[CanonicalValue]] = {
        QueryCriterionField.ORIGIN: KnowledgeValue[CanonicalValue].known("inbound")
    }
    record = OpportunityQueryRecord("opportunity-snapshot", values)

    values[QueryCriterionField.ORIGIN] = KnowledgeValue[CanonicalValue].known("outbound")

    assert record.values[QueryCriterionField.ORIGIN].value == "inbound"


def test_rule_override_ledger_preserves_original_outcome_and_is_append_only() -> None:
    result = DeterministicRuleResult(
        result_id="rule-result-001",
        rule_id="rule-001",
        rule_version="rule.v1",
        outcome=RuleOutcome.INDETERMINATE,
        inputs=(
            RuleInput(
                field="prior_vc_backing",
                value=KnowledgeValue.unknown("No reliable funding source"),
            ),
        ),
        reason="Unknown funding history requires review",
    )
    override = RuleOverride(
        override_id="override-001",
        replacement_outcome=RuleOutcome.PASS,
        actor_id="investor-001",
        recorded_at=NOW,
        rationale="Investor accepted the identified diligence gap",
    )
    ledger = RuleOverrideLedger()
    event = ledger.record(result, override)

    assert result.outcome is RuleOutcome.INDETERMINATE
    assert event.original_outcome is RuleOutcome.INDETERMINATE
    assert event.replacement_outcome is RuleOutcome.PASS
    assert ledger.history("rule-result-001") == (event,)
    with pytest.raises(DuplicateOverrideError, match="override-001"):
        ledger.record(result, override)
