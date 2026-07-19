"""Atomic SQLite registration for deterministic deck-evidence projections."""

from __future__ import annotations

from founderlookup.application.deck_evidence import DeckEvidenceProjection
from founderlookup.domain.evidence import SourceArtifact
from founderlookup.infrastructure.persistence import (
    NewRecord,
    RecordCategory,
    SQLiteMemory,
    StoredRecord,
)


class SQLiteDeckEvidenceStore:
    """Persist one complete projection atomically and accept exact deterministic replay."""

    def __init__(self, memory: SQLiteMemory) -> None:
        self._memory = memory

    def persist(
        self,
        *,
        source_artifact: SourceArtifact,
        projection: DeckEvidenceProjection,
    ) -> tuple[StoredRecord, ...]:
        if projection.source_artifact_id != source_artifact.source_artifact_id:
            raise ValueError("projection does not belong to the supplied Source Artifact")

        records: list[NewRecord] = [
            NewRecord(
                category=RecordCategory.SOURCE_ARTIFACT,
                record_id=source_artifact.source_artifact_id,
                version_id=source_artifact.artifact_version_id,
                subject_id=source_artifact.artifact_series_id,
                recorded_at=source_artifact.retrieved_at,
                payload=source_artifact.model_dump(mode="json"),
            ),
            NewRecord(
                category=RecordCategory.DECK_EVIDENCE_PROJECTION,
                record_id=projection.projection_id,
                version_id=projection.projection_version,
                subject_id=projection.opportunity_id,
                recorded_at=projection.projected_at,
                payload=projection.model_dump(mode="json"),
            ),
        ]
        records.extend(
            NewRecord(
                category=RecordCategory.OBSERVATION,
                record_id=observation.observation_id,
                version_id=observation.observation_version_id,
                subject_id=observation.subject.subject_id,
                recorded_at=observation.retrieved_at,
                payload=observation.model_dump(mode="json"),
            )
            for observation in projection.observations
        )
        records.extend(
            NewRecord(
                category=RecordCategory.EVIDENCE,
                record_id=evidence.evidence_id,
                version_id=evidence.evidence_id,
                subject_id=evidence.claim_id,
                recorded_at=evidence.collected_at,
                payload=evidence.model_dump(mode="json"),
            )
            for evidence in projection.evidence
        )
        records.extend(
            NewRecord(
                category=RecordCategory.CLAIM,
                record_id=claim.claim_id,
                version_id=claim.claim_version_id,
                subject_id=claim.subject.subject_id,
                recorded_at=claim.created_at,
                payload=claim.model_dump(mode="json"),
            )
            for claim in projection.claims
        )
        records.extend(
            NewRecord(
                category=RecordCategory.CONTRADICTION,
                record_id=contradiction.contradiction_id,
                version_id=contradiction.contradiction_version_id,
                subject_id=projection.opportunity_id,
                recorded_at=contradiction.detected_at,
                payload=contradiction.model_dump(mode="json"),
            )
            for contradiction in projection.contradictions
        )
        return self._memory.append_many_idempotent(records)
