"""Interface tests for the append-only SQLite Memory implementation."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from founderlookup.domain import (
    ArtifactAvailability,
    CoverageLevel,
    CoverageSummary,
    DataClassification,
    Evidence,
    EvidenceStance,
    FounderScoreSnapshot,
    InvestmentMemo,
    KnowledgeValue,
    MemoSection,
    MemoSectionKind,
    PipelineRun,
    PipelineRunKind,
    PipelineRunStatus,
    QualitativeUncertainty,
    SourceArtifact,
    SourceArtifactKind,
    SourceCategory,
    SourceLocator,
    SourceLocatorKind,
    VersionManifest,
)
from founderlookup.infrastructure.persistence import (
    InvalidRecordError,
    NewAuditEvent,
    NewRecord,
    RecordAlreadyExistsError,
    RecordCategory,
    SQLiteMemory,
)

NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)


def _memory(tmp_path: Path) -> SQLiteMemory:
    return SQLiteMemory((tmp_path / "private" / "memory.sqlite3").resolve())


def _record(
    version_id: str,
    *,
    recorded_at: datetime = NOW,
    record_id: str = "company_01",
) -> NewRecord:
    return NewRecord(
        category=RecordCategory.CANONICAL_ENTITY,
        record_id=record_id,
        version_id=version_id,
        subject_id=record_id,
        recorded_at=recorded_at,
        payload={"company_id": record_id, "name": "Jade Systems", "version": version_id},
    )


def _audit(event_id: str = "audit_01") -> NewAuditEvent:
    return NewAuditEvent(
        event_id=event_id,
        subject_id="company_01",
        actor_id="investor_01",
        action="company.version_recorded",
        occurred_at=NOW,
        details={"version_id": "company_version_01"},
    )


def test_versions_are_append_only_and_history_has_deterministic_order(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    later = _record("company_version_02", recorded_at=NOW + timedelta(minutes=1))
    earlier = _record("company_version_01")

    # Write order is deliberately different from historical order.
    memory.append_many((later, earlier))

    history = memory.history(RecordCategory.CANONICAL_ENTITY, "company_01")
    assert tuple(item.version_id for item in history) == (
        "company_version_01",
        "company_version_02",
    )
    assert memory.latest(RecordCategory.CANONICAL_ENTITY, "company_01") == history[-1]
    assert (
        memory.get(
            RecordCategory.CANONICAL_ENTITY,
            "company_01",
            "company_version_01",
        )
        == history[0]
    )
    assert len(history[0].payload_sha256) == 64

    with pytest.raises(RecordAlreadyExistsError, match="already exists"):
        memory.append(earlier)
    assert memory.history(RecordCategory.CANONICAL_ENTITY, "company_01") == history


def test_transaction_rolls_back_records_and_audit_together(tmp_path: Path) -> None:
    memory = _memory(tmp_path)

    with (
        pytest.raises(RuntimeError, match="simulated failure"),
        memory.transaction() as transaction,
    ):
        transaction.append(_record("company_version_01"))
        transaction.append_audit(_audit())
        raise RuntimeError("simulated failure")

    assert memory.history(RecordCategory.CANONICAL_ENTITY, "company_01") == ()
    assert memory.audit_history(subject_id="company_01") == ()


def test_database_triggers_reject_record_and_audit_mutation(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    memory.append(_record("company_version_01"))
    memory.append_audit(_audit())

    connection = sqlite3.connect(memory.database_path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="immutable records cannot be updated"):
            connection.execute("UPDATE immutable_records SET payload_json = '{}' ")
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="audit events cannot be deleted"):
            connection.execute("DELETE FROM audit_events")
        connection.rollback()
    finally:
        connection.close()

    assert len(memory.history(RecordCategory.CANONICAL_ENTITY, "company_01")) == 1
    assert memory.audit_history(subject_id="company_01")[0].event_id == "audit_01"


def test_preexisting_database_symlink_is_rejected_without_touching_target(
    tmp_path: Path,
) -> None:
    private_dir = tmp_path / "private"
    private_dir.mkdir()
    outside_database = tmp_path / "outside.sqlite3"
    outside_database.touch()
    database_path = private_dir / "memory.sqlite3"
    database_path.symlink_to(outside_database)

    with pytest.raises(ValueError, match="symbolic link"):
        SQLiteMemory(database_path)

    assert database_path.is_symlink()
    assert outside_database.read_bytes() == b""


def test_invalid_ids_non_utc_times_and_non_json_values_are_rejected(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    with pytest.raises(InvalidRecordError, match="stable opaque"):
        memory.append(_record("../client-path"))
    with pytest.raises(InvalidRecordError, match="timezone-aware UTC"):
        memory.append(_record("company_version_01", recorded_at=NOW.replace(tzinfo=None)))
    with pytest.raises(InvalidRecordError, match="finite JSON"):
        memory.append(
            NewRecord(
                category=RecordCategory.CANONICAL_ENTITY,
                record_id="company_01",
                version_id="company_version_01",
                recorded_at=NOW,
                payload={"not_json": object()},
            )
        )


def test_domain_conveniences_preserve_contract_payloads(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    artifact = SourceArtifact(
        source_artifact_id="artifact_01",
        artifact_series_id="artifact_series_01",
        artifact_version_id="artifact_version_01",
        version_number=1,
        kind=SourceArtifactKind.DOCUMENT,
        source_category=SourceCategory.APPLICATION_DECK,
        classification=DataClassification.FOUNDER_PRIVATE,
        origin_locator="upload:deck",
        display_name="deck.pdf",
        media_type="application/pdf",
        content_sha256="a" * 64,
        retrieved_at=NOW,
        source_event_time=KnowledgeValue[datetime].unknown("No document date"),
    )
    evidence = Evidence(
        evidence_id="evidence_01",
        claim_id="claim_01",
        source_artifact_id=artifact.source_artifact_id,
        stance=EvidenceStance.CONTEXT,
        locator=SourceLocator(kind=SourceLocatorKind.DOCUMENT_PAGE, locator="page:1"),
        collected_at=NOW,
        source_event_time=KnowledgeValue[datetime].unknown("No source event time"),
        availability=ArtifactAvailability.AVAILABLE,
    )
    coverage = CoverageSummary(
        level=CoverageLevel.LOW,
        source_count=1,
        artifact_count=1,
        evidence_count=1,
        freshest_evidence_at=KnowledgeValue[datetime].known(NOW),
    )
    score = FounderScoreSnapshot(
        snapshot_id="founder_score_01",
        snapshot_version_id="founder_score_version_01",
        founder_id="founder_01",
        score_policy_version="founder-score-policy.v0",
        as_of=NOW,
        score=50.0,
        factors=(),
        coverage=coverage,
        uncertainty=QualitativeUncertainty.HIGH,
        provisional=True,
    )
    run = PipelineRun(
        run_id="run_01",
        kind=PipelineRunKind.INGESTION,
        status=PipelineRunStatus.QUEUED,
        versions=VersionManifest(),
        input_snapshot_id="input_01",
        input_snapshot_as_of=NOW,
        queued_at=NOW,
    )
    sections = tuple(
        MemoSection(kind=kind, content=KnowledgeValue[str].known(f"Content for {kind.value}"))
        for kind in (
            MemoSectionKind.COMPANY_SNAPSHOT,
            MemoSectionKind.INVESTMENT_HYPOTHESES,
            MemoSectionKind.SWOT,
            MemoSectionKind.PROBLEM_AND_PRODUCT,
            MemoSectionKind.TRACTION_AND_KPIS,
        )
    )
    memo = InvestmentMemo(
        memo_id="memo_01",
        memo_version_id="memo_version_01",
        opportunity_id="opportunity_01",
        screening_case_id="screening_case_01",
        assessment_id="assessment_01",
        run_id=run.run_id,
        thesis_version="thesis.v0",
        evidence_as_of=NOW,
        generated_at=NOW,
        sections=sections,
    )

    stored = (
        memory.append_source_artifact(artifact),
        memory.append_evidence(evidence),
        memory.append_founder_score(score),
        memory.append_pipeline_run(
            run,
            snapshot_version_id="run_snapshot_01",
            recorded_at=NOW,
        ),
        memory.append_memo(memo),
    )

    assert tuple(item.category for item in stored) == (
        RecordCategory.SOURCE_ARTIFACT,
        RecordCategory.EVIDENCE,
        RecordCategory.FOUNDER_SCORE_SNAPSHOT,
        RecordCategory.PIPELINE_RUN,
        RecordCategory.MEMO,
    )
    assert PipelineRun.model_validate_json(json.dumps(stored[3].payload)) == run
    assert InvestmentMemo.model_validate_json(json.dumps(stored[4].payload)) == memo


def test_returned_record_and_audit_json_snapshots_are_deeply_immutable(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    stored = memory.append(
        NewRecord(
            category=RecordCategory.CANONICAL_ENTITY,
            record_id="company_immutable",
            version_id="company_immutable_v1",
            subject_id="company_immutable",
            recorded_at=NOW,
            payload={
                "profile": {
                    "aliases": [
                        {"name": "Jade Systems"},
                        {"name": "Jade Labs"},
                    ]
                }
            },
        )
    )
    audit = memory.append_audit(
        NewAuditEvent(
            event_id="audit_immutable",
            subject_id="company_immutable",
            actor_id="investor_01",
            action="company.aliases_recorded",
            occurred_at=NOW,
            details={"review": {"reasons": ["source-backed", "human-reviewed"]}},
        )
    )

    with pytest.raises(TypeError, match="immutable"):
        stored.payload["replacement"] = True
    profile = cast(dict[str, object], stored.payload["profile"])
    with pytest.raises(TypeError, match="immutable"):
        profile.update({"status": "changed"})
    aliases = cast(tuple[object, ...], profile["aliases"])
    first_alias = cast(dict[str, object], aliases[0])
    with pytest.raises(TypeError, match="immutable"):
        first_alias["name"] = "Mutated"

    review = cast(dict[str, object], audit.details["review"])
    with pytest.raises(TypeError, match="immutable"):
        review.pop("reasons")
    assert cast(tuple[object, ...], review["reasons"]) == (
        "source-backed",
        "human-reviewed",
    )

    reread = memory.get(
        RecordCategory.CANONICAL_ENTITY,
        "company_immutable",
        "company_immutable_v1",
    )
    assert reread is not None
    assert json.loads(json.dumps(reread.payload))["profile"]["aliases"][0]["name"] == (
        "Jade Systems"
    )
