"""Atomic, version-preserving SQLite adapter for the intake repository seam."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager, suppress
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, TypeVar

from founderlookup.domain.common import DomainModel, KnowledgeValue
from founderlookup.ingestion.extraction import PdfExtractionResult
from founderlookup.ingestion.intake import (
    ApplicationIntakeRecord,
    ApplicationReservation,
    ExtractionAttemptStatus,
    FocusedArtifactRecord,
    FocusedArtifactReservation,
    IdempotencyConflictError,
    PdfExtractionAttempt,
)

_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS intake_entities (
    kind TEXT NOT NULL CHECK (kind IN ('application', 'focused_artifact')),
    entity_id TEXT NOT NULL,
    canonical_fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (kind, entity_id),
    UNIQUE (kind, canonical_fingerprint)
) STRICT;

CREATE TABLE IF NOT EXISTS intake_idempotency_keys (
    kind TEXT NOT NULL CHECK (kind IN ('application', 'focused_artifact')),
    idempotency_key_sha256 TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (kind, idempotency_key_sha256),
    FOREIGN KEY (kind, entity_id) REFERENCES intake_entities (kind, entity_id)
) STRICT;

CREATE TABLE IF NOT EXISTS intake_revisions (
    kind TEXT NOT NULL CHECK (kind IN ('application', 'focused_artifact')),
    entity_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK (revision_number > 0),
    recorded_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    PRIMARY KEY (kind, entity_id, revision_number),
    FOREIGN KEY (kind, entity_id) REFERENCES intake_entities (kind, entity_id)
) STRICT;

CREATE INDEX IF NOT EXISTS intake_revisions_history
    ON intake_revisions (kind, entity_id, revision_number);

CREATE TRIGGER IF NOT EXISTS intake_entities_reject_update
BEFORE UPDATE ON intake_entities BEGIN
    SELECT RAISE(ABORT, 'intake entities cannot be updated');
END;
CREATE TRIGGER IF NOT EXISTS intake_entities_reject_delete
BEFORE DELETE ON intake_entities BEGIN
    SELECT RAISE(ABORT, 'intake entities cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS intake_idempotency_keys_reject_update
BEFORE UPDATE ON intake_idempotency_keys BEGIN
    SELECT RAISE(ABORT, 'intake idempotency keys cannot be updated');
END;
CREATE TRIGGER IF NOT EXISTS intake_idempotency_keys_reject_delete
BEFORE DELETE ON intake_idempotency_keys BEGIN
    SELECT RAISE(ABORT, 'intake idempotency keys cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS intake_revisions_reject_update
BEFORE UPDATE ON intake_revisions BEGIN
    SELECT RAISE(ABORT, 'intake revisions cannot be updated');
END;
CREATE TRIGGER IF NOT EXISTS intake_revisions_reject_delete
BEFORE DELETE ON intake_revisions BEGIN
    SELECT RAISE(ABORT, 'intake revisions cannot be deleted');
END;
"""


class IntakeRepositoryError(RuntimeError):
    """Base error for durable intake state."""


class IntakeRepositoryIntegrityError(IntakeRepositoryError):
    """Stored state or a requested transition violates an immutable invariant."""


class IntakeRepositoryRecordNotFound(IntakeRepositoryError):
    """A requested intake entity does not exist."""


class _RecordKind(StrEnum):
    APPLICATION = "application"
    FOCUSED_ARTIFACT = "focused_artifact"


RecordT = TypeVar("RecordT", ApplicationIntakeRecord, FocusedArtifactRecord)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise IntakeRepositoryIntegrityError("intake timestamps must use UTC")
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _encode(record: DomainModel) -> tuple[str, str]:
    try:
        payload = json.dumps(
            record.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:  # pragma: no cover - domain models prevent this
        raise IntakeRepositoryIntegrityError("intake record is not finite JSON") from error
    return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _decode(  # noqa: UP047 - shared constrained TypeVar also serves repository helpers
    payload: str,
    expected_sha256: str,
    model: type[RecordT],
) -> RecordT:
    actual_sha256 = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(actual_sha256, expected_sha256):
        raise IntakeRepositoryIntegrityError("stored intake record failed content verification")
    try:
        return model.model_validate_json(payload)
    except ValueError as error:
        raise IntakeRepositoryIntegrityError(
            "stored intake record failed schema validation"
        ) from error


class SQLiteIntakeRepository:
    """Durable adapter hiding idempotency, concurrency, and revision history.

    A natural request fingerprint deduplicates an identical normalized company/deck
    submission even when a client retries with a new idempotency key. The key still
    remains authoritative for detecting unsafe reuse with different content.
    """

    def __init__(
        self,
        database_path: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not database_path.is_absolute():
            raise ValueError("database_path must be an absolute server-controlled path")
        if database_path.is_symlink():
            raise ValueError("database_path cannot be a symbolic link")
        database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with suppress(OSError):
            database_path.parent.chmod(0o700)
        self._database_path = database_path
        self._clock = clock or (lambda: datetime.now(UTC))
        with closing(self._connect()) as connection:
            connection.executescript(_SCHEMA)
            connection.commit()
        with suppress(OSError):
            database_path.chmod(0o600)

    @property
    def database_path(self) -> Path:
        return self._database_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def reserve_application(self, record: ApplicationIntakeRecord) -> ApplicationReservation:
        reserved, created = self._reserve(
            kind=_RecordKind.APPLICATION,
            entity_id=record.application_id,
            idempotency_key_sha256=record.idempotency_key_sha256,
            request_fingerprint=record.request_fingerprint,
            record=record,
            model=ApplicationIntakeRecord,
            recorded_at=record.received_at,
        )
        return ApplicationReservation(record=reserved, created=created)

    def reserve_focused_artifact(
        self,
        record: FocusedArtifactRecord,
    ) -> FocusedArtifactReservation:
        reserved, created = self._reserve(
            kind=_RecordKind.FOCUSED_ARTIFACT,
            entity_id=record.import_id,
            idempotency_key_sha256=record.idempotency_key_sha256,
            request_fingerprint=record.request_fingerprint,
            record=record,
            model=FocusedArtifactRecord,
            recorded_at=record.received_at,
        )
        return FocusedArtifactReservation(record=reserved, created=created)

    def _reserve(
        self,
        *,
        kind: _RecordKind,
        entity_id: str,
        idempotency_key_sha256: str,
        request_fingerprint: str,
        record: RecordT,
        model: type[RecordT],
        recorded_at: datetime,
    ) -> tuple[RecordT, bool]:
        with self._transaction() as connection:
            by_key = connection.execute(
                """
                SELECT entity_id, request_fingerprint
                FROM intake_idempotency_keys
                WHERE kind = ? AND idempotency_key_sha256 = ?
                """,
                (kind.value, idempotency_key_sha256),
            ).fetchone()
            if by_key is not None:
                if not hmac.compare_digest(by_key["request_fingerprint"], request_fingerprint):
                    raise IdempotencyConflictError
                return self._latest_required(
                    connection,
                    kind=kind,
                    entity_id=by_key["entity_id"],
                    model=model,
                ), False

            by_fingerprint = connection.execute(
                """
                SELECT entity_id FROM intake_entities
                WHERE kind = ? AND canonical_fingerprint = ?
                """,
                (kind.value, request_fingerprint),
            ).fetchone()
            if by_fingerprint is not None:
                existing_id = by_fingerprint["entity_id"]
                self._append_idempotency_key(
                    connection,
                    kind=kind,
                    entity_id=existing_id,
                    idempotency_key_sha256=idempotency_key_sha256,
                    request_fingerprint=request_fingerprint,
                    recorded_at=self._now(),
                )
                return self._latest_required(
                    connection,
                    kind=kind,
                    entity_id=existing_id,
                    model=model,
                ), False

            try:
                connection.execute(
                    """
                    INSERT INTO intake_entities (
                        kind, entity_id, canonical_fingerprint, created_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (kind.value, entity_id, request_fingerprint, _utc_text(recorded_at)),
                )
                self._append_idempotency_key(
                    connection,
                    kind=kind,
                    entity_id=entity_id,
                    idempotency_key_sha256=idempotency_key_sha256,
                    request_fingerprint=request_fingerprint,
                    recorded_at=recorded_at,
                )
                self._append_revision(
                    connection,
                    kind=kind,
                    entity_id=entity_id,
                    record=record,
                    recorded_at=recorded_at,
                )
            except sqlite3.IntegrityError as error:
                raise IntakeRepositoryIntegrityError(
                    "intake reservation collided with an existing immutable identifier"
                ) from error
            return record, True

    @staticmethod
    def _append_idempotency_key(
        connection: sqlite3.Connection,
        *,
        kind: _RecordKind,
        entity_id: str,
        idempotency_key_sha256: str,
        request_fingerprint: str,
        recorded_at: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO intake_idempotency_keys (
                kind, idempotency_key_sha256, entity_id, request_fingerprint, recorded_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                kind.value,
                idempotency_key_sha256,
                entity_id,
                request_fingerprint,
                _utc_text(recorded_at),
            ),
        )

    def get_application(self, application_id: str) -> ApplicationIntakeRecord | None:
        with closing(self._connect()) as connection:
            return self._latest(
                connection,
                kind=_RecordKind.APPLICATION,
                entity_id=application_id,
                model=ApplicationIntakeRecord,
            )

    def mark_application_artifact_stored(
        self,
        application_id: str,
        content_sha256: str,
    ) -> ApplicationIntakeRecord:
        with self._transaction() as connection:
            current = self._latest_required(
                connection,
                kind=_RecordKind.APPLICATION,
                entity_id=application_id,
                model=ApplicationIntakeRecord,
            )
            if not hmac.compare_digest(current.source_artifact.content_sha256, content_sha256):
                raise IntakeRepositoryIntegrityError("artifact hash does not match reservation")
            if current.artifact_stored:
                return current
            updated = current.model_copy(update={"artifact_stored": True})
            self._append_revision(
                connection,
                kind=_RecordKind.APPLICATION,
                entity_id=application_id,
                record=updated,
                recorded_at=self._now(),
            )
            return updated

    def record_application_extraction(
        self,
        application_id: str,
        extraction: PdfExtractionResult,
    ) -> ApplicationIntakeRecord:
        with self._transaction() as connection:
            current = self._latest_required(
                connection,
                kind=_RecordKind.APPLICATION,
                entity_id=application_id,
                model=ApplicationIntakeRecord,
            )
            if (
                extraction.source_artifact_id != current.source_artifact.source_artifact_id
                or not hmac.compare_digest(
                    extraction.input_sha256,
                    current.source_artifact.content_sha256,
                )
            ):
                raise IntakeRepositoryIntegrityError("extraction does not match reserved artifact")
            if current.extraction.value is not None:
                if current.extraction.value == extraction:
                    return current
                raise IntakeRepositoryIntegrityError("accepted extraction cannot be replaced")
            attempt = PdfExtractionAttempt(
                status=ExtractionAttemptStatus.SUCCEEDED,
                safe_code="extraction_succeeded",
                attempted_at=extraction.extracted_at,
            )
            updated = current.model_copy(
                update={
                    "extraction": KnowledgeValue[PdfExtractionResult].known(extraction),
                    "extraction_attempts": (*current.extraction_attempts, attempt),
                }
            )
            self._append_revision(
                connection,
                kind=_RecordKind.APPLICATION,
                entity_id=application_id,
                record=updated,
                recorded_at=extraction.extracted_at,
            )
            return updated

    def record_application_extraction_failure(
        self,
        application_id: str,
        *,
        status: ExtractionAttemptStatus,
        safe_code: str,
        attempted_at: datetime,
    ) -> ApplicationIntakeRecord:
        if status is ExtractionAttemptStatus.SUCCEEDED:
            raise IntakeRepositoryIntegrityError("failure transition cannot be succeeded")
        attempt = PdfExtractionAttempt(
            status=status,
            safe_code=safe_code,
            attempted_at=attempted_at,
        )
        with self._transaction() as connection:
            current = self._latest_required(
                connection,
                kind=_RecordKind.APPLICATION,
                entity_id=application_id,
                model=ApplicationIntakeRecord,
            )
            if current.extraction.value is not None:
                raise IntakeRepositoryIntegrityError("accepted extraction cannot be replaced")
            updated = current.model_copy(
                update={
                    "extraction": KnowledgeValue[PdfExtractionResult].unknown(safe_code),
                    "extraction_attempts": (*current.extraction_attempts, attempt),
                }
            )
            self._append_revision(
                connection,
                kind=_RecordKind.APPLICATION,
                entity_id=application_id,
                record=updated,
                recorded_at=attempted_at,
            )
            return updated

    def mark_focused_artifact_stored(
        self,
        import_id: str,
        content_sha256: str,
    ) -> FocusedArtifactRecord:
        with self._transaction() as connection:
            current = self._latest_required(
                connection,
                kind=_RecordKind.FOCUSED_ARTIFACT,
                entity_id=import_id,
                model=FocusedArtifactRecord,
            )
            if not hmac.compare_digest(current.source_artifact.content_sha256, content_sha256):
                raise IntakeRepositoryIntegrityError("artifact hash does not match reservation")
            if current.artifact_stored:
                return current
            updated = current.model_copy(update={"artifact_stored": True})
            self._append_revision(
                connection,
                kind=_RecordKind.FOCUSED_ARTIFACT,
                entity_id=import_id,
                record=updated,
                recorded_at=self._now(),
            )
            return updated

    def application_history(self, application_id: str) -> tuple[ApplicationIntakeRecord, ...]:
        with closing(self._connect()) as connection:
            return self._history(
                connection,
                kind=_RecordKind.APPLICATION,
                entity_id=application_id,
                model=ApplicationIntakeRecord,
            )

    def focused_artifact_history(self, import_id: str) -> tuple[FocusedArtifactRecord, ...]:
        with closing(self._connect()) as connection:
            return self._history(
                connection,
                kind=_RecordKind.FOCUSED_ARTIFACT,
                entity_id=import_id,
                model=FocusedArtifactRecord,
            )

    def _now(self) -> datetime:
        value = self._clock()
        _utc_text(value)
        return value

    @staticmethod
    def _append_revision(
        connection: sqlite3.Connection,
        *,
        kind: _RecordKind,
        entity_id: str,
        record: DomainModel,
        recorded_at: datetime,
    ) -> None:
        row = connection.execute(
            """
            SELECT COALESCE(MAX(revision_number), 0) AS current_revision
            FROM intake_revisions WHERE kind = ? AND entity_id = ?
            """,
            (kind.value, entity_id),
        ).fetchone()
        revision = int(row["current_revision"]) + 1
        payload, payload_sha256 = _encode(record)
        connection.execute(
            """
            INSERT INTO intake_revisions (
                kind, entity_id, revision_number, recorded_at, payload_json, payload_sha256
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                kind.value,
                entity_id,
                revision,
                _utc_text(recorded_at),
                payload,
                payload_sha256,
            ),
        )

    @classmethod
    def _latest_required(
        cls,
        connection: sqlite3.Connection,
        *,
        kind: _RecordKind,
        entity_id: str,
        model: type[RecordT],
    ) -> RecordT:
        record = cls._latest(
            connection,
            kind=kind,
            entity_id=entity_id,
            model=model,
        )
        if record is None:
            raise IntakeRepositoryRecordNotFound("intake record does not exist")
        return record

    @staticmethod
    def _latest(
        connection: sqlite3.Connection,
        *,
        kind: _RecordKind,
        entity_id: str,
        model: type[RecordT],
    ) -> RecordT | None:
        row = connection.execute(
            """
            SELECT payload_json, payload_sha256
            FROM intake_revisions
            WHERE kind = ? AND entity_id = ?
            ORDER BY revision_number DESC
            LIMIT 1
            """,
            (kind.value, entity_id),
        ).fetchone()
        if row is None:
            return None
        return _decode(row["payload_json"], row["payload_sha256"], model)

    @staticmethod
    def _history(
        connection: sqlite3.Connection,
        *,
        kind: _RecordKind,
        entity_id: str,
        model: type[RecordT],
    ) -> tuple[RecordT, ...]:
        rows = connection.execute(
            """
            SELECT payload_json, payload_sha256
            FROM intake_revisions
            WHERE kind = ? AND entity_id = ?
            ORDER BY revision_number ASC
            """,
            (kind.value, entity_id),
        ).fetchall()
        return tuple(_decode(row["payload_json"], row["payload_sha256"], model) for row in rows)
