"""Durable append-only ledger for deterministic-rule override events."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from founderlookup.domain.assessment import (
    DeterministicRuleResult,
    RuleOutcome,
    RuleOverride,
)
from founderlookup.infrastructure.persistence import (
    NewAuditEvent,
    PersistenceError,
    RecordAlreadyExistsError,
    SQLiteMemory,
    StoredAuditEvent,
)
from founderlookup.screening.query_executor import (
    DuplicateOverrideError,
    RuleOverrideEvent,
)

_OVERRIDE_ACTION: Final = "deterministic_rule.override_recorded"


class SQLiteRuleOverrideLedger:
    """Persist attributed overrides in the immutable SQLite audit stream."""

    def __init__(self, memory: SQLiteMemory) -> None:
        self._memory = memory

    def record(
        self,
        result: DeterministicRuleResult,
        override: RuleOverride,
    ) -> RuleOverrideEvent:
        event = RuleOverrideEvent.from_result(result, override)
        try:
            self._memory.append_audit(
                NewAuditEvent(
                    event_id=event.override_id,
                    subject_id=event.result_id,
                    actor_id=event.actor_id,
                    action=_OVERRIDE_ACTION,
                    occurred_at=event.recorded_at,
                    details={
                        "result_id": event.result_id,
                        "original_outcome": event.original_outcome.value,
                        "replacement_outcome": event.replacement_outcome.value,
                        "rationale": event.rationale,
                        "original_result": result.model_dump(mode="json"),
                    },
                )
            )
        except RecordAlreadyExistsError as error:
            raise DuplicateOverrideError(
                f"override {event.override_id!r} already exists"
            ) from error
        return event

    def history(self, result_id: str | None = None) -> tuple[RuleOverrideEvent, ...]:
        events = self._memory.audit_history(subject_id=result_id)
        return tuple(self._decode(event) for event in events if event.action == _OVERRIDE_ACTION)

    @staticmethod
    def _decode(stored: StoredAuditEvent) -> RuleOverrideEvent:
        details = stored.details
        result_id = _required_text(details, "result_id")
        rationale = _required_text(details, "rationale")
        if stored.subject_id != result_id:
            raise PersistenceError("stored rule override has inconsistent subject identity")
        original_result = details.get("original_result")
        if not isinstance(original_result, Mapping):
            raise PersistenceError("stored rule override is missing its original result")
        if original_result.get("result_id") != result_id:
            raise PersistenceError("stored rule override has inconsistent original result")
        original_text = _required_text(details, "original_outcome")
        replacement_text = _required_text(details, "replacement_outcome")
        if original_result.get("outcome") != original_text:
            raise PersistenceError("stored rule override has inconsistent original outcome")
        try:
            original_outcome = RuleOutcome(original_text)
            replacement_outcome = RuleOutcome(replacement_text)
        except ValueError as error:
            raise PersistenceError("stored rule override contains an invalid outcome") from error
        return RuleOverrideEvent(
            override_id=stored.event_id,
            result_id=result_id,
            original_outcome=original_outcome,
            replacement_outcome=replacement_outcome,
            actor_id=stored.actor_id,
            recorded_at=stored.occurred_at,
            rationale=rationale,
        )


def _required_text(details: Mapping[str, object], key: str) -> str:
    value = details.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PersistenceError("stored rule override failed validation")
    return value
