"""Deterministic execution of validated Opportunity Query Plans.

The executor intentionally accepts only the shared typed plan. It has no SQL,
shell, or provider-expression escape hatch, so a planner can propose criteria but
cannot acquire execution authority.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Protocol

from founderlookup.domain.assessment import DeterministicRuleResult, RuleOutcome, RuleOverride
from founderlookup.domain.common import KnowledgeState, KnowledgeValue, ScalarValue
from founderlookup.domain.query import (
    CriterionStrength,
    OpportunityQueryPlan,
    QueryCriterion,
    QueryCriterionField,
    QueryOperator,
    QueryPlanState,
    UnknownValuePolicy,
)

type CanonicalValue = ScalarValue | tuple[ScalarValue, ...]


class _ValueKind(StrEnum):
    BOOLEAN = "boolean"
    NUMBER = "number"
    TEXT = "text"


@dataclass(frozen=True, slots=True)
class _FieldPolicy:
    value_kind: _ValueKind
    operators: frozenset[QueryOperator]
    allow_multiple: bool = False


_KNOWLEDGE_OPERATORS: Final = frozenset({QueryOperator.IS_KNOWN, QueryOperator.IS_UNKNOWN})
_ORDERING_OPERATORS: Final = frozenset(
    {
        QueryOperator.GREATER_THAN_OR_EQUAL,
        QueryOperator.LESS_THAN_OR_EQUAL,
        QueryOperator.BETWEEN,
    }
)
_SUPPORTED_OPERATORS: Final = frozenset(QueryOperator)
_BOOLEAN_OPERATORS: Final = _KNOWLEDGE_OPERATORS | frozenset(
    {QueryOperator.EQUALS, QueryOperator.NOT_EQUALS}
)
_NUMBER_OPERATORS: Final = _KNOWLEDGE_OPERATORS | frozenset(
    {
        QueryOperator.EQUALS,
        QueryOperator.NOT_EQUALS,
        QueryOperator.GREATER_THAN_OR_EQUAL,
        QueryOperator.LESS_THAN_OR_EQUAL,
        QueryOperator.BETWEEN,
    }
)
_TEXT_OPERATORS: Final = _KNOWLEDGE_OPERATORS | frozenset(
    {
        QueryOperator.EQUALS,
        QueryOperator.NOT_EQUALS,
        QueryOperator.ANY_OF,
        QueryOperator.CONTAINS,
    }
)
_MULTI_TEXT_OPERATORS: Final = _TEXT_OPERATORS | frozenset({QueryOperator.ALL_OF})
_FIELD_POLICIES: Final[Mapping[QueryCriterionField, _FieldPolicy]] = MappingProxyType(
    {
        QueryCriterionField.TECHNICAL_FOUNDER: _FieldPolicy(_ValueKind.BOOLEAN, _BOOLEAN_OPERATORS),
        QueryCriterionField.GEOGRAPHY: _FieldPolicy(
            _ValueKind.TEXT, _MULTI_TEXT_OPERATORS, allow_multiple=True
        ),
        QueryCriterionField.SECTOR: _FieldPolicy(
            _ValueKind.TEXT, _MULTI_TEXT_OPERATORS, allow_multiple=True
        ),
        QueryCriterionField.STAGE: _FieldPolicy(_ValueKind.TEXT, _TEXT_OPERATORS),
        QueryCriterionField.CHECK_SIZE: _FieldPolicy(_ValueKind.NUMBER, _NUMBER_OPERATORS),
        QueryCriterionField.OWNERSHIP_TARGET: _FieldPolicy(_ValueKind.NUMBER, _NUMBER_OPERATORS),
        QueryCriterionField.RISK_APPETITE: _FieldPolicy(_ValueKind.TEXT, _TEXT_OPERATORS),
        QueryCriterionField.ENTERPRISE_TRACTION: _FieldPolicy(
            _ValueKind.BOOLEAN, _BOOLEAN_OPERATORS
        ),
        QueryCriterionField.PRIOR_VC_BACKING: _FieldPolicy(_ValueKind.BOOLEAN, _BOOLEAN_OPERATORS),
        QueryCriterionField.ACCELERATOR: _FieldPolicy(
            _ValueKind.TEXT, _MULTI_TEXT_OPERATORS, allow_multiple=True
        ),
        QueryCriterionField.SOURCE_CATEGORY: _FieldPolicy(
            _ValueKind.TEXT, _MULTI_TEXT_OPERATORS, allow_multiple=True
        ),
        QueryCriterionField.ORIGIN: _FieldPolicy(_ValueKind.TEXT, _TEXT_OPERATORS),
        QueryCriterionField.WORKFLOW_STATE: _FieldPolicy(_ValueKind.TEXT, _TEXT_OPERATORS),
        QueryCriterionField.RECOMMENDATION: _FieldPolicy(_ValueKind.TEXT, _TEXT_OPERATORS),
        QueryCriterionField.FOUNDER_AXIS: _FieldPolicy(_ValueKind.TEXT, _TEXT_OPERATORS),
        QueryCriterionField.MARKET_AXIS: _FieldPolicy(_ValueKind.TEXT, _TEXT_OPERATORS),
        QueryCriterionField.IDEA_VS_MARKET_AXIS: _FieldPolicy(_ValueKind.TEXT, _TEXT_OPERATORS),
        QueryCriterionField.TREND: _FieldPolicy(_ValueKind.TEXT, _TEXT_OPERATORS),
        QueryCriterionField.CONTRADICTION_STATE: _FieldPolicy(_ValueKind.TEXT, _TEXT_OPERATORS),
        QueryCriterionField.EVIDENCE_COVERAGE: _FieldPolicy(_ValueKind.TEXT, _TEXT_OPERATORS),
        QueryCriterionField.KNOWLEDGE_STATE: _FieldPolicy(_ValueKind.TEXT, _TEXT_OPERATORS),
    }
)


class QueryExecutionError(RuntimeError):
    """Base class for errors safe to translate at an application boundary."""


class UnsafeQueryPlanError(QueryExecutionError, ValueError):
    """The proposed plan did not pass the deterministic execution boundary."""


class DuplicateOverrideError(QueryExecutionError):
    """An immutable override identifier was reused."""


class CriterionMatch(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class OpportunityQueryRecord:
    """Normalized canonical projection used by deterministic filtering."""

    opportunity_id: str
    values: Mapping[QueryCriterionField, KnowledgeValue[CanonicalValue]]

    def __post_init__(self) -> None:
        if not self.opportunity_id or not self.opportunity_id.strip():
            raise ValueError("opportunity_id must be non-blank")
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))


@dataclass(frozen=True, slots=True)
class CriterionEvaluation:
    criterion_id: str
    field: QueryCriterionField
    strength: CriterionStrength
    match: CriterionMatch
    reason: str
    knowledge_state: KnowledgeState
    unknown_policy: UnknownValuePolicy


@dataclass(frozen=True, slots=True)
class OpportunityMatch:
    opportunity_id: str
    criteria: tuple[CriterionEvaluation, ...]
    matched_preferences: int
    evaluated_preferences: int


@dataclass(frozen=True, slots=True)
class OpportunityQueryResultSet:
    query_plan_id: str
    query_plan_version_id: str
    items: tuple[OpportunityMatch, ...]
    eligible_count: int
    truncated: bool
    ordering: str = "matched_preferences_desc,opportunity_id_asc"


@dataclass(frozen=True, slots=True)
class RuleOverrideEvent:
    """Append-only human event; the referenced rule result stays unchanged."""

    override_id: str
    result_id: str
    original_outcome: RuleOutcome
    replacement_outcome: RuleOutcome
    actor_id: str
    recorded_at: datetime
    rationale: str

    @classmethod
    def from_result(
        cls,
        result: DeterministicRuleResult,
        override: RuleOverride,
    ) -> RuleOverrideEvent:
        return cls(
            override_id=override.override_id,
            result_id=result.result_id,
            original_outcome=result.outcome,
            replacement_outcome=override.replacement_outcome,
            actor_id=override.actor_id,
            recorded_at=override.recorded_at,
            rationale=override.rationale,
        )


class RuleOverrideLedgerPort(Protocol):
    """Append-only persistence seam for attributed deterministic-rule overrides."""

    def record(
        self,
        result: DeterministicRuleResult,
        override: RuleOverride,
    ) -> RuleOverrideEvent: ...

    def history(self, result_id: str | None = None) -> tuple[RuleOverrideEvent, ...]: ...


class RuleOverrideLedger:
    """Minimal in-memory append-only ledger suitable for fake-backed services."""

    def __init__(self) -> None:
        self._events: list[RuleOverrideEvent] = []
        self._ids: set[str] = set()

    def record(
        self,
        result: DeterministicRuleResult,
        override: RuleOverride,
    ) -> RuleOverrideEvent:
        """Derive the event from the immutable result instead of trusting its caller."""

        event = RuleOverrideEvent.from_result(result, override)
        if event.override_id in self._ids:
            raise DuplicateOverrideError(f"override {event.override_id!r} already exists")
        self._ids.add(event.override_id)
        self._events.append(event)
        return event

    def history(self, result_id: str | None = None) -> tuple[RuleOverrideEvent, ...]:
        events = self._events
        if result_id is not None:
            events = [event for event in events if event.result_id == result_id]
        return tuple(sorted(events, key=lambda event: (event.recorded_at, event.override_id)))


class DeterministicQueryExecutor:
    """Apply allowlisted operators with explicit hard/preference/Unknown behavior."""

    def __init__(self, *, maximum_results: int = 100) -> None:
        if maximum_results < 1:
            raise ValueError("maximum_results must be positive")
        self._maximum_results = maximum_results

    def execute(
        self,
        plan: OpportunityQueryPlan,
        records: Iterable[OpportunityQueryRecord],
    ) -> OpportunityQueryResultSet:
        self._validate_plan(plan)
        canonical_records = tuple(records)
        record_ids = tuple(record.opportunity_id for record in canonical_records)
        if len(record_ids) != len(set(record_ids)):
            raise UnsafeQueryPlanError("canonical query input contains duplicate opportunity IDs")
        matches = [self._evaluate_record(plan.criteria, record) for record in canonical_records]
        eligible = [match for match in matches if match is not None]
        eligible.sort(key=lambda item: (-item.matched_preferences, item.opportunity_id))
        limit = min(plan.max_results, self._maximum_results)
        return OpportunityQueryResultSet(
            query_plan_id=plan.query_plan_id,
            query_plan_version_id=plan.query_plan_version_id,
            items=tuple(eligible[:limit]),
            eligible_count=len(eligible),
            truncated=len(eligible) > limit,
        )

    def _validate_plan(self, plan: OpportunityQueryPlan) -> None:
        if plan.state is not QueryPlanState.VALIDATED:
            raise UnsafeQueryPlanError("only a validated typed query plan may execute")
        if plan.max_results > self._maximum_results:
            raise UnsafeQueryPlanError(
                f"plan max_results exceeds the policy maximum of {self._maximum_results}"
            )
        for criterion in plan.criteria:
            if criterion.operator not in _SUPPORTED_OPERATORS:
                raise UnsafeQueryPlanError("query criterion contains an unsupported operator")
            policy = _FIELD_POLICIES[criterion.field]
            if criterion.operator not in policy.operators:
                raise UnsafeQueryPlanError(
                    f"operator {criterion.operator.value!r} is not allowed for "
                    f"field {criterion.field.value!r}"
                )
            if criterion.operator not in _KNOWLEDGE_OPERATORS and not all(
                _matches_value_kind(operand, policy.value_kind) for operand in criterion.operands
            ):
                raise UnsafeQueryPlanError(
                    f"operands do not match the value type for field {criterion.field.value!r}"
                )

    def _evaluate_record(
        self,
        criteria: Sequence[QueryCriterion],
        record: OpportunityQueryRecord,
    ) -> OpportunityMatch | None:
        evaluations: list[CriterionEvaluation] = []
        matched_preferences = 0
        evaluated_preferences = 0

        for criterion in criteria:
            knowledge = record.values.get(criterion.field)
            if knowledge is None:
                knowledge = KnowledgeValue[CanonicalValue].unknown(
                    f"{criterion.field.value} is absent from canonical Memory"
                )
            evaluation = self._evaluate_criterion(criterion, knowledge)
            evaluations.append(evaluation)

            if criterion.strength is CriterionStrength.HARD_CONSTRAINT:
                # Known mismatches are authoritative. Unknown remains visible and
                # eligible for its declared collection or review path.
                if evaluation.match is CriterionMatch.MISMATCH:
                    return None
            else:
                if evaluation.match is not CriterionMatch.UNKNOWN:
                    evaluated_preferences += 1
                if evaluation.match is CriterionMatch.MATCH:
                    matched_preferences += 1

        return OpportunityMatch(
            opportunity_id=record.opportunity_id,
            criteria=tuple(evaluations),
            matched_preferences=matched_preferences,
            evaluated_preferences=evaluated_preferences,
        )

    @staticmethod
    def _evaluate_criterion(
        criterion: QueryCriterion,
        knowledge: KnowledgeValue[CanonicalValue],
    ) -> CriterionEvaluation:
        if knowledge.state is KnowledgeState.KNOWN:
            candidate = knowledge.value
            if candidate is None:  # pragma: no cover - KnowledgeValue validates this invariant
                raise UnsafeQueryPlanError("known query value is missing")
            _validate_canonical_value(criterion.field, candidate)

        if criterion.operator in _KNOWLEDGE_OPERATORS:
            expected_state = (
                KnowledgeState.KNOWN
                if criterion.operator is QueryOperator.IS_KNOWN
                else KnowledgeState.UNKNOWN
            )
            return CriterionEvaluation(
                criterion_id=criterion.criterion_id,
                field=criterion.field,
                strength=criterion.strength,
                match=(
                    CriterionMatch.MATCH
                    if knowledge.state is expected_state
                    else CriterionMatch.MISMATCH
                ),
                reason=(
                    f"knowledge state is {knowledge.state.value}; "
                    f"criterion requested {criterion.operator.value}"
                ),
                knowledge_state=knowledge.state,
                unknown_policy=criterion.unknown_policy,
            )

        if knowledge.state is not KnowledgeState.KNOWN:
            return CriterionEvaluation(
                criterion_id=criterion.criterion_id,
                field=criterion.field,
                strength=criterion.strength,
                match=CriterionMatch.UNKNOWN,
                reason=knowledge.reason or f"value is {knowledge.state.value}",
                knowledge_state=knowledge.state,
                unknown_policy=criterion.unknown_policy,
            )

        candidate = knowledge.value
        if candidate is None:  # pragma: no cover - checked above for Known
            raise UnsafeQueryPlanError("known query value is missing")
        matched = _compare(candidate, criterion.operator, criterion.operands)
        return CriterionEvaluation(
            criterion_id=criterion.criterion_id,
            field=criterion.field,
            strength=criterion.strength,
            match=CriterionMatch.MATCH if matched else CriterionMatch.MISMATCH,
            reason=(
                f"canonical value {'satisfies' if matched else 'does not satisfy'} "
                f"{criterion.operator.value}"
            ),
            knowledge_state=knowledge.state,
            unknown_policy=criterion.unknown_policy,
        )


def _compare(
    candidate: CanonicalValue,
    operator: QueryOperator,
    operands: tuple[ScalarValue, ...],
) -> bool:
    values = candidate if isinstance(candidate, tuple) else (candidate,)
    target = operands[0] if operands else None

    if operator is QueryOperator.EQUALS:
        return any(_strict_equal(value, target) for value in values)
    if operator is QueryOperator.NOT_EQUALS:
        return all(not _strict_equal(value, target) for value in values)
    if operator is QueryOperator.ANY_OF:
        return any(_strict_equal(value, operand) for value in values for operand in operands)
    if operator is QueryOperator.ALL_OF:
        return all(any(_strict_equal(value, operand) for value in values) for operand in operands)
    if operator is QueryOperator.CONTAINS:
        return any(
            isinstance(value, str)
            and isinstance(target, str)
            and target.casefold() in value.casefold()
            for value in values
        )
    if operator in _ORDERING_OPERATORS:
        if len(values) != 1 or not _is_number(values[0]):
            raise UnsafeQueryPlanError("ordering operators require one numeric canonical value")
        numeric = float(values[0])
        if not all(_is_number(operand) for operand in operands):
            raise UnsafeQueryPlanError("ordering operators require numeric operands")
        numbers = tuple(float(operand) for operand in operands)
        if operator is QueryOperator.GREATER_THAN_OR_EQUAL:
            return numeric >= numbers[0]
        if operator is QueryOperator.LESS_THAN_OR_EQUAL:
            return numeric <= numbers[0]
        return numbers[0] <= numeric <= numbers[1]
    raise UnsafeQueryPlanError(f"operator {operator.value!r} is not executable")


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _matches_value_kind(value: object, kind: _ValueKind) -> bool:
    if kind is _ValueKind.BOOLEAN:
        return isinstance(value, bool)
    if kind is _ValueKind.NUMBER:
        return _is_number(value)
    return isinstance(value, str)


def _validate_canonical_value(field: QueryCriterionField, candidate: CanonicalValue) -> None:
    policy = _FIELD_POLICIES[field]
    if isinstance(candidate, tuple):
        if not policy.allow_multiple or not candidate:
            raise UnsafeQueryPlanError(
                f"field {field.value!r} does not accept this canonical value shape"
            )
        values = candidate
    else:
        values = (candidate,)
    if not all(_matches_value_kind(value, policy.value_kind) for value in values):
        raise UnsafeQueryPlanError(
            f"canonical value does not match the value type for field {field.value!r}"
        )


def _strict_equal(left: object, right: object) -> bool:
    if _is_number(left) and _is_number(right):
        return left == right
    return type(left) is type(right) and left == right
