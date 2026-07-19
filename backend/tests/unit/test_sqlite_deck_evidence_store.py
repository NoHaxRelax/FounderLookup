"""Atomic persistence tests for deterministic deck-Evidence projections."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from founderlookup.application.deck_evidence import DeckEvidenceProjection, project_deck_evidence
from founderlookup.domain.common import KnowledgeValue, VersionId
from founderlookup.domain.evidence import (
    DataClassification,
    SourceArtifact,
    SourceArtifactKind,
    SourceCategory,
)
from founderlookup.infrastructure.deck_evidence import SQLiteDeckEvidenceStore
from founderlookup.infrastructure.persistence import (
    ImmutableRecordConflictError,
    NewRecord,
    RecordCategory,
    SQLiteMemory,
    StoredRecord,
)
from founderlookup.ingestion.extraction import (
    ExtractedPdfPage,
    PdfExtractionResult,
    PdfExtractionUsage,
    PdfPageConfidence,
)

RETRIEVED_AT = datetime(2026, 7, 19, 12, tzinfo=UTC)
EXTRACTED_AT = RETRIEVED_AT + timedelta(seconds=1)
CONTENT_HASH = "a" * 64


def _memory(tmp_path: Path) -> SQLiteMemory:
    return SQLiteMemory((tmp_path / "private" / "memory.sqlite3").resolve())


def _artifact() -> SourceArtifact:
    return SourceArtifact(
        source_artifact_id="source-artifact:deck:store-test",
        artifact_series_id="artifact-series:deck:store-test",
        artifact_version_id="artifact-version:deck:store-test",
        version_number=1,
        kind=SourceArtifactKind.DOCUMENT,
        source_category=SourceCategory.APPLICATION_DECK,
        classification=DataClassification.FOUNDER_PRIVATE,
        origin_locator="private-artifact:deck:store-test",
        display_name="fictional-store-test.pdf",
        media_type="application/pdf",
        content_sha256=CONTENT_HASH,
        retrieved_at=RETRIEVED_AT,
        source_event_time=KnowledgeValue[datetime].unknown(
            "deck effective date is not disclosed"
        ),
    )


def _projection(artifact: SourceArtifact) -> DeckEvidenceProjection:
    unknown_confidence = KnowledgeValue[float].unknown(
        "fixture provider omitted page confidence"
    )
    extraction = PdfExtractionResult(
        extraction_id="pdf-extraction:deck:store-test",
        source_artifact_id=artifact.source_artifact_id,
        input_sha256=artifact.content_sha256,
        extractor_version="deterministic-store-fixture.v1",
        model_version=KnowledgeValue[VersionId].known("fixture-ocr.v1"),
        extracted_at=EXTRACTED_AT,
        pages=(
            ExtractedPdfPage(
                page_index=0,
                locator="page:0",
                markdown="Company: Fictional Store Test",
                confidence=PdfPageConfidence(
                    average=unknown_confidence,
                    minimum=unknown_confidence,
                ),
            ),
        ),
        usage=PdfExtractionUsage(
            pages_processed=KnowledgeValue[int].known(1),
            document_size_bytes=KnowledgeValue[int].known(1_024),
        ),
    )
    return project_deck_evidence(
        extraction=extraction,
        source_artifact=artifact,
        application_id="application:store-test",
        company_id="company:store-test",
        opportunity_id="opportunity:store-test",
    )


def _snapshot(memory: SQLiteMemory) -> dict[RecordCategory, tuple[StoredRecord, ...]]:
    return {category: memory.list_records(category) for category in RecordCategory}


def test_exact_projection_replay_is_idempotent(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    store = SQLiteDeckEvidenceStore(memory)
    artifact = _artifact()
    projection = _projection(artifact)

    first = store.persist(source_artifact=artifact, projection=projection)
    second = store.persist(source_artifact=artifact, projection=projection)

    assert second == first
    assert tuple(record.category for record in first) == (
        RecordCategory.SOURCE_ARTIFACT,
        RecordCategory.DECK_EVIDENCE_PROJECTION,
        RecordCategory.OBSERVATION,
        RecordCategory.EVIDENCE,
        RecordCategory.CLAIM,
    )
    assert sum(len(records) for records in _snapshot(memory).values()) == len(first)


def test_late_record_conflict_rolls_back_the_whole_projection(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    store = SQLiteDeckEvidenceStore(memory)
    artifact = _artifact()
    projection = _projection(artifact)
    claim = projection.claims[0]
    conflicting_claim = memory.append(
        NewRecord(
            category=RecordCategory.CLAIM,
            record_id=claim.claim_id,
            version_id=claim.claim_version_id,
            subject_id=claim.subject.subject_id,
            recorded_at=claim.created_at,
            payload={"sentinel": "pre-existing immutable conflict"},
        )
    )

    with pytest.raises(ImmutableRecordConflictError, match="different immutable content"):
        store.persist(source_artifact=artifact, projection=projection)

    snapshot = _snapshot(memory)
    assert snapshot[RecordCategory.CLAIM] == (conflicting_claim,)
    assert all(
        records == ()
        for category, records in snapshot.items()
        if category is not RecordCategory.CLAIM
    )


def test_conflicting_projection_cannot_replace_accepted_immutable_content(
    tmp_path: Path,
) -> None:
    memory = _memory(tmp_path)
    store = SQLiteDeckEvidenceStore(memory)
    artifact = _artifact()
    projection = _projection(artifact)
    store.persist(source_artifact=artifact, projection=projection)
    accepted = _snapshot(memory)
    conflicting_projection = projection.model_copy(
        update={"pages_examined": projection.pages_examined + 1}
    )

    with pytest.raises(ImmutableRecordConflictError, match="different immutable content"):
        store.persist(source_artifact=artifact, projection=conflicting_projection)

    assert _snapshot(memory) == accepted


def test_projection_for_another_artifact_is_rejected_before_any_write(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    store = SQLiteDeckEvidenceStore(memory)
    artifact = _artifact()
    projection = _projection(artifact)
    other_artifact = artifact.model_copy(
        update={"source_artifact_id": "source-artifact:deck:other"}
    )

    with pytest.raises(ValueError, match="does not belong"):
        store.persist(source_artifact=other_artifact, projection=projection)

    assert all(records == () for records in _snapshot(memory).values())
