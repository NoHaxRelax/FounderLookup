"""Append-only SQLite memory for immutable domain snapshots.

The schema deliberately stores versioned JSON documents instead of mirroring every
domain field.  Domain contracts remain the validation boundary, while this module
owns durable ordering, atomicity, and history preservation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, NoReturn, Self

from founderlookup.domain.assessment import InvestmentMemo
from founderlookup.domain.evidence import Evidence, SourceArtifact
from founderlookup.domain.runs import PipelineRun
from founderlookup.domain.scoring import FounderScoreSnapshot

_STABLE_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS immutable_records (
    category TEXT NOT NULL,
    record_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    subject_id TEXT,
    recorded_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    PRIMARY KEY (category, record_id, version_id)
) STRICT;

CREATE INDEX IF NOT EXISTS immutable_records_subject_history
    ON immutable_records (category, subject_id, recorded_at, record_id, version_id);
CREATE INDEX IF NOT EXISTS immutable_records_record_history
    ON immutable_records (category, record_id, recorded_at, version_id);

CREATE TABLE IF NOT EXISTS audit_events (
    event_id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    details_json TEXT NOT NULL,
    details_sha256 TEXT NOT NULL
) STRICT;

CREATE INDEX IF NOT EXISTS audit_events_subject_history
    ON audit_events (subject_id, occurred_at, event_id);

CREATE TRIGGER IF NOT EXISTS immutable_records_reject_update
BEFORE UPDATE ON immutable_records
BEGIN
    SELECT RAISE(ABORT, 'immutable records cannot be updated');
END;

CREATE TRIGGER IF NOT EXISTS immutable_records_reject_delete
BEFORE DELETE ON immutable_records
BEGIN
    SELECT RAISE(ABORT, 'immutable records cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS audit_events_reject_update
BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit events cannot be updated');
END;

CREATE TRIGGER IF NOT EXISTS audit_events_reject_delete
BEFORE DELETE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit events cannot be deleted');
END;
"""


class PersistenceError(RuntimeError):
    """Base error for the local Memory implementation."""


class InvalidRecordError(PersistenceError, ValueError):
    """A record cannot be represented by the immutable store."""


class RecordAlreadyExistsError(PersistenceError):
    """The exact category, record identifier, and version already exist."""


class RecordCategory(StrEnum):
    """Stable persistence namespaces; payloads retain their domain schema version."""

    CANONICAL_ENTITY = "canonical_entity"
    SOURCE_ARTIFACT = "source_artifact"
    EVIDENCE = "evidence"
    FOUNDER_SCORE_SNAPSHOT = "founder_score_snapshot"
    PIPELINE_RUN = "pipeline_run"
    MEMO = "memo"


JsonObject = Mapping[str, object]
_DICT_MUTATORS: Final = frozenset(
    {
        "clear",
        "pop",
        "popitem",
        "setdefault",
        "update",
    }
)


class _ImmutableJsonDict(dict[str, object]):
    """Dictionary-compatible, recursively immutable JSON object snapshot."""

    def __init__(self, value: Mapping[str, object]) -> None:
        dict.__init__(
            self,
            ((key, _freeze_json_value(item)) for key, item in value.items()),
        )

    def __getattribute__(self, name: str) -> object:
        if name in _DICT_MUTATORS:
            return self._reject_mutation
        return super().__getattribute__(name)

    @staticmethod
    def _reject_mutation(*_args: object, **_kwargs: object) -> NoReturn:
        raise TypeError("stored JSON snapshots are immutable")

    def __setitem__(self, _key: str, _value: object) -> None:
        self._reject_mutation()

    def __delitem__(self, _key: str) -> None:
        self._reject_mutation()

    def __ior__(self, _value: object) -> Self:  # type: ignore[override,misc]
        self._reject_mutation()


def _freeze_json_value(value: object) -> object:
    if isinstance(value, dict):
        return _ImmutableJsonDict(value)
    if isinstance(value, list):
        return tuple(_freeze_json_value(item) for item in value)
    return value


def _validate_id(value: str, *, field: str) -> str:
    if _STABLE_ID_PATTERN.fullmatch(value) is None:
        raise InvalidRecordError(f"{field} must be a stable opaque identifier")
    return value


def _utc_text(value: datetime, *, field: str) -> str:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise InvalidRecordError(f"{field} must be timezone-aware UTC")
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise PersistenceError("stored timestamp is not UTC")
    return parsed


def _json_document(value: JsonObject, *, field: str) -> tuple[str, str]:
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise InvalidRecordError(f"{field} must be a finite JSON object") from error
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return encoded, digest


def _decode_document(value: str, expected_sha256: str) -> dict[str, object]:
    actual_sha256 = hashlib.sha256(value.encode("utf-8")).hexdigest()
    if actual_sha256 != expected_sha256:
        raise PersistenceError("stored JSON payload failed content verification")
    decoded = json.loads(value)
    if not isinstance(decoded, dict):  # pragma: no cover - writes prevent this state
        raise PersistenceError("stored JSON payload is not an object")
    return _ImmutableJsonDict(decoded)


@dataclass(frozen=True, slots=True)
class NewRecord:
    """One version-preserving document to append."""

    category: RecordCategory
    record_id: str
    version_id: str
    recorded_at: datetime
    payload: JsonObject
    subject_id: str | None = None

    def __post_init__(self) -> None:
        _validate_id(self.record_id, field="record_id")
        _validate_id(self.version_id, field="version_id")
        if self.subject_id is not None:
            _validate_id(self.subject_id, field="subject_id")
        _utc_text(self.recorded_at, field="recorded_at")
        _json_document(self.payload, field="payload")


@dataclass(frozen=True, slots=True)
class StoredRecord:
    category: RecordCategory
    record_id: str
    version_id: str
    recorded_at: datetime
    payload: dict[str, object]
    payload_sha256: str
    subject_id: str | None = None


@dataclass(frozen=True, slots=True)
class NewAuditEvent:
    """Concise attributed event; details must not contain private reasoning."""

    event_id: str
    subject_id: str
    actor_id: str
    action: str
    occurred_at: datetime
    details: JsonObject

    def __post_init__(self) -> None:
        _validate_id(self.event_id, field="event_id")
        _validate_id(self.subject_id, field="subject_id")
        _validate_id(self.actor_id, field="actor_id")
        if not self.action.strip() or len(self.action) > 200:
            raise InvalidRecordError("action must be non-blank and at most 200 characters")
        _utc_text(self.occurred_at, field="occurred_at")
        _json_document(self.details, field="details")


@dataclass(frozen=True, slots=True)
class StoredAuditEvent:
    event_id: str
    subject_id: str
    actor_id: str
    action: str
    occurred_at: datetime
    details: dict[str, object]
    details_sha256: str


class SQLiteTransaction:
    """Write-only transaction handle so several immutable appends commit together."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def append(self, record: NewRecord) -> StoredRecord:
        payload_json, payload_sha256 = _json_document(record.payload, field="payload")
        try:
            self._connection.execute(
                """
                INSERT INTO immutable_records (
                    category, record_id, version_id, subject_id, recorded_at,
                    payload_json, payload_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.category.value,
                    record.record_id,
                    record.version_id,
                    record.subject_id,
                    _utc_text(record.recorded_at, field="recorded_at"),
                    payload_json,
                    payload_sha256,
                ),
            )
        except sqlite3.IntegrityError as error:
            raise RecordAlreadyExistsError(
                f"{record.category.value}/{record.record_id}/{record.version_id} already exists"
            ) from error
        return StoredRecord(
            category=record.category,
            record_id=record.record_id,
            version_id=record.version_id,
            subject_id=record.subject_id,
            recorded_at=record.recorded_at,
            payload=_decode_document(payload_json, payload_sha256),
            payload_sha256=payload_sha256,
        )

    def append_audit(self, event: NewAuditEvent) -> StoredAuditEvent:
        details_json, details_sha256 = _json_document(event.details, field="details")
        try:
            self._connection.execute(
                """
                INSERT INTO audit_events (
                    event_id, subject_id, actor_id, action, occurred_at,
                    details_json, details_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.subject_id,
                    event.actor_id,
                    event.action,
                    _utc_text(event.occurred_at, field="occurred_at"),
                    details_json,
                    details_sha256,
                ),
            )
        except sqlite3.IntegrityError as error:
            raise RecordAlreadyExistsError(
                f"audit event {event.event_id} already exists"
            ) from error
        return StoredAuditEvent(
            event_id=event.event_id,
            subject_id=event.subject_id,
            actor_id=event.actor_id,
            action=event.action,
            occurred_at=event.occurred_at,
            details=_decode_document(details_json, details_sha256),
            details_sha256=details_sha256,
        )


class SQLiteMemory:
    """Small durable interface over append-only versioned records and audit history."""

    def __init__(self, database_path: Path) -> None:
        if not database_path.is_absolute():
            raise ValueError("database_path must be an absolute server-controlled path")
        if database_path.is_symlink():
            raise ValueError("database_path cannot be a symbolic link")
        self._database_path = database_path
        database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._restrict_mode(database_path.parent, 0o700)
        with closing(self._connect()) as connection:
            connection.executescript(_SCHEMA)
            connection.commit()
        self._restrict_mode(database_path, 0o600)

    @property
    def database_path(self) -> Path:
        """Server configuration path, never a value accepted from an HTTP client."""

        return self._database_path

    @staticmethod
    def _restrict_mode(path: Path, mode: int) -> None:
        with suppress(OSError):
            path.chmod(mode)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[SQLiteTransaction]:
        """Atomically commit all appends, or roll all of them back on any error."""

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield SQLiteTransaction(connection)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def append(self, record: NewRecord) -> StoredRecord:
        with self.transaction() as transaction:
            return transaction.append(record)

    def append_many(self, records: Sequence[NewRecord]) -> tuple[StoredRecord, ...]:
        with self.transaction() as transaction:
            return tuple(transaction.append(record) for record in records)

    def append_audit(self, event: NewAuditEvent) -> StoredAuditEvent:
        with self.transaction() as transaction:
            return transaction.append_audit(event)

    def get(
        self,
        category: RecordCategory,
        record_id: str,
        version_id: str,
    ) -> StoredRecord | None:
        _validate_id(record_id, field="record_id")
        _validate_id(version_id, field="version_id")
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM immutable_records
                WHERE category = ? AND record_id = ? AND version_id = ?
                """,
                (category.value, record_id, version_id),
            ).fetchone()
        return None if row is None else self._stored_record(row)

    def latest(self, category: RecordCategory, record_id: str) -> StoredRecord | None:
        _validate_id(record_id, field="record_id")
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM immutable_records
                WHERE category = ? AND record_id = ?
                ORDER BY recorded_at DESC, version_id DESC
                LIMIT 1
                """,
                (category.value, record_id),
            ).fetchone()
        return None if row is None else self._stored_record(row)

    def history(self, category: RecordCategory, record_id: str) -> tuple[StoredRecord, ...]:
        _validate_id(record_id, field="record_id")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM immutable_records
                WHERE category = ? AND record_id = ?
                ORDER BY recorded_at ASC, version_id ASC
                """,
                (category.value, record_id),
            ).fetchall()
        return tuple(self._stored_record(row) for row in rows)

    def list_records(
        self,
        category: RecordCategory,
        *,
        subject_id: str | None = None,
    ) -> tuple[StoredRecord, ...]:
        if subject_id is not None:
            _validate_id(subject_id, field="subject_id")
        query = "SELECT * FROM immutable_records WHERE category = ?"
        parameters: tuple[str, ...] = (category.value,)
        if subject_id is not None:
            query += " AND subject_id = ?"
            parameters += (subject_id,)
        query += " ORDER BY recorded_at ASC, record_id ASC, version_id ASC"
        with closing(self._connect()) as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(self._stored_record(row) for row in rows)

    def audit_history(self, *, subject_id: str | None = None) -> tuple[StoredAuditEvent, ...]:
        if subject_id is not None:
            _validate_id(subject_id, field="subject_id")
        query = "SELECT * FROM audit_events"
        parameters: tuple[str, ...] = ()
        if subject_id is not None:
            query += " WHERE subject_id = ?"
            parameters = (subject_id,)
        query += " ORDER BY occurred_at ASC, event_id ASC"
        with closing(self._connect()) as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(self._stored_audit(row) for row in rows)

    def append_source_artifact(self, artifact: SourceArtifact) -> StoredRecord:
        return self.append(
            NewRecord(
                category=RecordCategory.SOURCE_ARTIFACT,
                record_id=artifact.source_artifact_id,
                version_id=artifact.artifact_version_id,
                subject_id=artifact.artifact_series_id,
                recorded_at=artifact.retrieved_at,
                payload=artifact.model_dump(mode="json"),
            )
        )

    def append_evidence(self, evidence: Evidence) -> StoredRecord:
        return self.append(
            NewRecord(
                category=RecordCategory.EVIDENCE,
                record_id=evidence.evidence_id,
                version_id=evidence.evidence_id,
                subject_id=evidence.claim_id,
                recorded_at=evidence.collected_at,
                payload=evidence.model_dump(mode="json"),
            )
        )

    def append_founder_score(self, snapshot: FounderScoreSnapshot) -> StoredRecord:
        return self.append(
            NewRecord(
                category=RecordCategory.FOUNDER_SCORE_SNAPSHOT,
                record_id=snapshot.founder_id,
                version_id=snapshot.snapshot_version_id,
                subject_id=snapshot.founder_id,
                recorded_at=snapshot.as_of,
                payload=snapshot.model_dump(mode="json"),
            )
        )

    def append_pipeline_run(
        self,
        run: PipelineRun,
        *,
        snapshot_version_id: str,
        recorded_at: datetime,
    ) -> StoredRecord:
        return self.append(
            NewRecord(
                category=RecordCategory.PIPELINE_RUN,
                record_id=run.run_id,
                version_id=snapshot_version_id,
                subject_id=run.input_snapshot_id,
                recorded_at=recorded_at,
                payload=run.model_dump(mode="json"),
            )
        )

    def append_memo(self, memo: InvestmentMemo) -> StoredRecord:
        return self.append(
            NewRecord(
                category=RecordCategory.MEMO,
                record_id=memo.memo_id,
                version_id=memo.memo_version_id,
                subject_id=memo.opportunity_id,
                recorded_at=memo.generated_at,
                payload=memo.model_dump(mode="json"),
            )
        )

    @staticmethod
    def _stored_record(row: sqlite3.Row) -> StoredRecord:
        return StoredRecord(
            category=RecordCategory(row["category"]),
            record_id=row["record_id"],
            version_id=row["version_id"],
            subject_id=row["subject_id"],
            recorded_at=_parse_utc(row["recorded_at"]),
            payload=_decode_document(row["payload_json"], row["payload_sha256"]),
            payload_sha256=row["payload_sha256"],
        )

    @staticmethod
    def _stored_audit(row: sqlite3.Row) -> StoredAuditEvent:
        return StoredAuditEvent(
            event_id=row["event_id"],
            subject_id=row["subject_id"],
            actor_id=row["actor_id"],
            action=row["action"],
            occurred_at=_parse_utc(row["occurred_at"]),
            details=_decode_document(row["details_json"], row["details_sha256"]),
            details_sha256=row["details_sha256"],
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        # Connections are operation-scoped; provided for ergonomic composition.
        return None


def new_opaque_id() -> str:
    """Create an unguessable, URL-safe identifier without encoding domain data."""

    return os.urandom(16).hex()
