"""Atomic SQLite registration for founder-submitted metadata provenance."""

from founderlookup.application.application_metadata import ApplicationMetadataProjection
from founderlookup.infrastructure.persistence import NewRecord, RecordCategory, SQLiteMemory


class SQLiteApplicationMetadataStore:
    """Persist one metadata projection and its private provenance atomically."""

    def __init__(self, memory: SQLiteMemory) -> None:
        self._memory = memory

    def persist(self, projection: ApplicationMetadataProjection) -> None:
        source = projection.source_artifact
        records = [
            NewRecord(
                category=RecordCategory.SOURCE_ARTIFACT,
                record_id=source.source_artifact_id,
                version_id=source.artifact_version_id,
                subject_id=source.artifact_series_id,
                recorded_at=source.retrieved_at,
                payload=source.model_dump(mode="json"),
            ),
            NewRecord(
                category=RecordCategory.APPLICATION_METADATA_PROJECTION,
                record_id=projection.projection_id,
                version_id=projection.projection_version,
                subject_id=projection.application_id,
                recorded_at=source.retrieved_at,
                payload=projection.model_dump(mode="json"),
            ),
        ]
        records.extend(
            NewRecord(
                category=RecordCategory.EVIDENCE,
                record_id=item.evidence_id,
                version_id=item.evidence_id,
                subject_id=item.claim_id,
                recorded_at=item.collected_at,
                payload=item.model_dump(mode="json"),
            )
            for item in projection.evidence
        )
        records.extend(
            NewRecord(
                category=RecordCategory.CLAIM,
                record_id=item.claim_id,
                version_id=item.claim_version_id,
                subject_id=item.subject.subject_id,
                recorded_at=item.created_at,
                payload=item.model_dump(mode="json"),
            )
            for item in projection.claims
        )
        self._memory.append_many_idempotent(records)


__all__ = ["SQLiteApplicationMetadataStore"]
