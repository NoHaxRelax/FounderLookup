"""Interface-level tests for the durable SQLite intake adapter."""

import asyncio
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from threading import Lock

import pytest

from founderlookup.application.ports import IntakeSubmission
from founderlookup.domain.common import KnowledgeState, KnowledgeValue, VersionId
from founderlookup.infrastructure.artifacts import PrivateArtifactStore
from founderlookup.infrastructure.intake_repository import SQLiteIntakeRepository
from founderlookup.ingestion.extraction import (
    ExtractedPdfPage,
    FakePdfExtractor,
    PdfExtractionResult,
    PdfExtractionUsage,
    PdfPageConfidence,
)
from founderlookup.ingestion.intake import (
    ApplicationIntakeService,
    ArtifactStorageError,
    DeckExtractionFailedError,
    ExtractionAttemptStatus,
    IdempotencyConflictError,
    PrivateArtifactStorePort,
)

NOW = datetime(2026, 7, 19, 9, 30, tzinfo=UTC)
PDF = b"%PDF-1.7\nfictional durable intake deck\n%%EOF\n"


class _Ids:
    def __init__(self, label: str) -> None:
        self._label = label
        self._counts: dict[str, int] = {}
        self._lock = Lock()

    def __call__(self, prefix: str) -> str:
        with self._lock:
            value = self._counts.get(prefix, 0) + 1
            self._counts[prefix] = value
        return f"{prefix}:{self._label}:{value}"


class _FailFirstPut:
    def __init__(self, delegate: PrivateArtifactStore) -> None:
        self._delegate = delegate
        self._failed = False

    def put(self, artifact_id: str, content: bytes, *, expected_sha256: str) -> object:
        if not self._failed:
            self._failed = True
            raise RuntimeError("simulated process interruption before durable artifact mark")
        return self._delegate.put(artifact_id, content, expected_sha256=expected_sha256)

    def read(
        self,
        artifact_id: str,
        *,
        principal_id: str,
        expected_sha256: str,
    ) -> bytes:
        return self._delegate.read(
            artifact_id,
            principal_id=principal_id,
            expected_sha256=expected_sha256,
        )


def _store(tmp_path: Path) -> PrivateArtifactStore:
    return PrivateArtifactStore(
        (tmp_path / "artifacts").resolve(),
        authorize_read=lambda principal_id, _artifact_id: (
            principal_id in {"system:pdf-extraction", "investor"}
        ),
    )


def _service(
    repository: SQLiteIntakeRepository,
    store: PrivateArtifactStorePort,
    *,
    label: str,
    extractor: FakePdfExtractor | None = None,
) -> ApplicationIntakeService:
    return ApplicationIntakeService(
        repository=repository,
        artifact_store=store,
        extractor=extractor or FakePdfExtractor({}),
        clock=lambda: NOW,
        id_factory=_Ids(label),
        max_pdf_bytes=1_000_000,
    )


def _submission(*, key: str, content: bytes = PDF) -> IntakeSubmission:
    return IntakeSubmission(
        company_name="  Celadon   Systems ",
        display_name=r"C:\fakepath\deck.pdf",
        media_type="application/pdf",
        deck_content=content,
        idempotency_key=key,
    )


def _extraction(artifact_id: str) -> PdfExtractionResult:
    digest = sha256(PDF).hexdigest()
    confidence = PdfPageConfidence(
        average=KnowledgeValue[float].known(0.96),
        minimum=KnowledgeValue[float].known(0.91),
    )
    return PdfExtractionResult(
        extraction_id="pdf-extraction:durable:1",
        source_artifact_id=artifact_id,
        input_sha256=digest,
        extractor_version="fake-pdf.v0",
        model_version=KnowledgeValue[VersionId].unknown("deterministic fake has no model"),
        extracted_at=NOW,
        pages=(
            ExtractedPdfPage(
                page_index=0,
                locator="page:0",
                markdown="# Celadon Systems",
                confidence=confidence,
            ),
        ),
        usage=PdfExtractionUsage(
            pages_processed=KnowledgeValue[int].known(1),
            document_size_bytes=KnowledgeValue[int].known(len(PDF)),
        ),
    )


def test_replay_survives_restart_and_new_key_reuses_identical_natural_request(
    tmp_path: Path,
) -> None:
    database = (tmp_path / "memory" / "intake.sqlite3").resolve()
    store = _store(tmp_path)
    first_repository = SQLiteIntakeRepository(database, clock=lambda: NOW)
    first = asyncio.run(
        _service(first_repository, store, label="first").submit(_submission(key="attempt-1"))
    )

    restarted_repository = SQLiteIntakeRepository(database, clock=lambda: NOW)
    replay = asyncio.run(
        _service(restarted_repository, store, label="restart").submit(_submission(key="attempt-2"))
    )

    assert replay.replayed is True
    assert replay.application_id == first.application_id
    assert replay.run_id == first.run_id
    assert replay.source_artifact_id == first.source_artifact_id
    assert len(restarted_repository.application_history(first.application_id)) == 2
    assert stat.S_IMODE(database.stat().st_mode) == 0o600


def test_key_reuse_with_different_content_conflicts_without_replacing_original(
    tmp_path: Path,
) -> None:
    repository = SQLiteIntakeRepository(
        (tmp_path / "memory" / "intake.sqlite3").resolve(),
        clock=lambda: NOW,
    )
    service = _service(repository, _store(tmp_path), label="conflict")
    first = asyncio.run(service.submit(_submission(key="same-key")))

    with pytest.raises(IdempotencyConflictError):
        asyncio.run(
            service.submit(
                _submission(key="same-key", content=b"%PDF-1.7\ndifferent bytes\n%%EOF\n")
            )
        )

    record = repository.get_application(first.application_id)
    assert record is not None
    assert record.source_artifact.content_sha256 == sha256(PDF).hexdigest()


def test_reservation_recovers_after_interruption_between_reserve_store_and_mark(
    tmp_path: Path,
) -> None:
    database = (tmp_path / "memory" / "intake.sqlite3").resolve()
    durable_store = _store(tmp_path)
    first_repository = SQLiteIntakeRepository(database, clock=lambda: NOW)
    interrupted = _service(
        first_repository,
        _FailFirstPut(durable_store),
        label="interrupted",
    )

    with pytest.raises(ArtifactStorageError):
        asyncio.run(interrupted.submit(_submission(key="recoverable-key")))
    (reserved,) = first_repository.application_history("application:interrupted:1")
    assert reserved.artifact_stored is False

    restarted_repository = SQLiteIntakeRepository(database, clock=lambda: NOW)
    accepted = asyncio.run(
        _service(restarted_repository, durable_store, label="restart").submit(
            _submission(key="recoverable-key")
        )
    )
    recovered = restarted_repository.get_application(accepted.application_id)
    assert accepted.application_id == "application:interrupted:1"
    assert accepted.replayed is True
    assert recovered is not None and recovered.artifact_stored is True


def test_failed_then_successful_extraction_attempts_remain_versioned_after_restart(
    tmp_path: Path,
) -> None:
    database = (tmp_path / "memory" / "intake.sqlite3").resolve()
    store = _store(tmp_path)
    repository = SQLiteIntakeRepository(database, clock=lambda: NOW)
    failing = _service(repository, store, label="extract-fail")
    accepted = asyncio.run(failing.submit(_submission(key="extract-key")))

    with pytest.raises(DeckExtractionFailedError):
        asyncio.run(failing.extract_deck(accepted.application_id))

    successful = _service(
        repository,
        store,
        label="extract-success",
        extractor=FakePdfExtractor(
            {sha256(PDF).hexdigest(): _extraction(accepted.source_artifact_id)}
        ),
    )
    result = asyncio.run(successful.extract_deck(accepted.application_id))
    restarted = SQLiteIntakeRepository(database, clock=lambda: NOW)
    record = restarted.get_application(accepted.application_id)

    assert result.pages[0].locator == "page:0"
    assert record is not None
    assert record.extraction.state is KnowledgeState.KNOWN
    assert tuple(attempt.status for attempt in record.extraction_attempts) == (
        ExtractionAttemptStatus.FAILED,
        ExtractionAttemptStatus.SUCCEEDED,
    )
    assert len(restarted.application_history(accepted.application_id)) == 4


def test_concurrent_same_key_reservation_creates_one_application(tmp_path: Path) -> None:
    database = (tmp_path / "memory" / "intake.sqlite3").resolve()
    store = _store(tmp_path)

    def submit(index: int) -> tuple[str, bool]:
        repository = SQLiteIntakeRepository(database, clock=lambda: NOW)
        accepted = asyncio.run(
            _service(repository, store, label=f"worker-{index}").submit(
                _submission(key="concurrent-key")
            )
        )
        return accepted.application_id, accepted.replayed

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = tuple(executor.map(submit, range(8)))

    assert len({application_id for application_id, _replayed in results}) == 1
    assert sum(not replayed for _application_id, replayed in results) == 1
