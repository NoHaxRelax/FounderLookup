"""Durability tests for attributed deterministic-rule overrides."""

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from founderlookup.domain import (
    DeterministicRuleResult,
    KnowledgeValue,
    RuleInput,
    RuleOutcome,
    RuleOverride,
)
from founderlookup.infrastructure import SQLiteMemory, SQLiteRuleOverrideLedger
from founderlookup.screening import DuplicateOverrideError

NOW = datetime(2026, 7, 18, 10, tzinfo=UTC)


def _result() -> DeterministicRuleResult:
    return DeterministicRuleResult(
        result_id="rule-result-001",
        rule_id="prior-vc-backing",
        rule_version="rules.v1",
        outcome=RuleOutcome.INDETERMINATE,
        inputs=(
            RuleInput(
                field="prior_vc_backing",
                value=KnowledgeValue.unknown("No reliable funding source"),
            ),
        ),
        reason="Unknown funding history requires review",
    )


def _override() -> RuleOverride:
    return RuleOverride(
        override_id="override-001",
        replacement_outcome=RuleOutcome.PASS,
        actor_id="investor-001",
        recorded_at=NOW,
        rationale="Investor accepted the documented diligence gap",
    )


def test_sqlite_override_history_survives_restart_and_preserves_original(
    tmp_path: Path,
) -> None:
    database_path = (tmp_path / "memory.sqlite3").resolve()
    result = _result()
    override = _override()
    event = SQLiteRuleOverrideLedger(SQLiteMemory(database_path)).record(result, override)

    restarted = SQLiteRuleOverrideLedger(SQLiteMemory(database_path))

    assert result.outcome is RuleOutcome.INDETERMINATE
    assert result.override is None
    assert restarted.history(result.result_id) == (event,)
    assert event.actor_id == override.actor_id
    assert event.recorded_at == override.recorded_at
    assert event.rationale == override.rationale

    stored = SQLiteMemory(database_path).audit_history(subject_id=result.result_id)[0]
    original_result = stored.details["original_result"]
    assert isinstance(original_result, Mapping)
    assert original_result["result_id"] == result.result_id
    assert original_result["rule_id"] == result.rule_id
    assert original_result["rule_version"] == result.rule_version
    assert original_result["outcome"] == result.outcome.value
    assert original_result["reason"] == result.reason
    assert stored.details["original_outcome"] == RuleOutcome.INDETERMINATE.value


def test_sqlite_override_identifiers_are_globally_append_only(tmp_path: Path) -> None:
    database_path = (tmp_path / "memory.sqlite3").resolve()
    first_process = SQLiteRuleOverrideLedger(SQLiteMemory(database_path))
    first_process.record(_result(), _override())

    restarted = SQLiteRuleOverrideLedger(SQLiteMemory(database_path))
    with pytest.raises(DuplicateOverrideError, match="override-001"):
        restarted.record(_result(), _override())

    assert len(restarted.history()) == 1
