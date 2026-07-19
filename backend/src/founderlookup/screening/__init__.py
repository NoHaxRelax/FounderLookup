"""Deterministic and framework-neutral screening capability."""

from founderlookup.screening.query_executor import (
    CanonicalValue,
    CriterionEvaluation,
    CriterionMatch,
    DeterministicQueryExecutor,
    DuplicateOverrideError,
    OpportunityMatch,
    OpportunityQueryRecord,
    OpportunityQueryResultSet,
    QueryExecutionError,
    RuleOverrideEvent,
    RuleOverrideLedger,
    RuleOverrideLedgerPort,
    UnsafeQueryPlanError,
)

__all__ = [
    "CanonicalValue",
    "CriterionEvaluation",
    "CriterionMatch",
    "DeterministicQueryExecutor",
    "DuplicateOverrideError",
    "OpportunityMatch",
    "OpportunityQueryRecord",
    "OpportunityQueryResultSet",
    "QueryExecutionError",
    "RuleOverrideEvent",
    "RuleOverrideLedger",
    "RuleOverrideLedgerPort",
    "UnsafeQueryPlanError",
]
