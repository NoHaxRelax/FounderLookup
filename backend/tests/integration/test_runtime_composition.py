"""Executable-runtime coverage for durable intake and fail-closed OCR composition."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from founderlookup.api.settings import APISettings
from founderlookup.domain.common import KnowledgeValue
from founderlookup.infrastructure.intake_repository import SQLiteIntakeRepository
from founderlookup.ingestion.extraction import (
    ExtractedPdfPage,
    PdfExtractionBlockedError,
    PdfExtractionRequest,
    PdfExtractionResult,
    PdfExtractionUsage,
    PdfPageConfidence,
)
from founderlookup.ingestion.intake import ExtractionAttemptStatus
from founderlookup.runtime import create_runtime_app

NOW = datetime(2026, 7, 19, 8, tzinfo=UTC)
PDF = b"%PDF-1.7\nfictional runtime deck"


class RuntimeFakeExtractor:
    def __init__(self) -> None:
        self.requests: list[PdfExtractionRequest] = []

    async def extract(self, request: PdfExtractionRequest) -> PdfExtractionResult:
        self.requests.append(request)
        return self.result(request)

    @staticmethod
    def result(request: PdfExtractionRequest) -> PdfExtractionResult:
        return PdfExtractionResult(
            extraction_id="pdf-extraction:runtime-fake",
            source_artifact_id=request.source_artifact_id,
            input_sha256=request.input_sha256,
            extractor_version="runtime-fake.v0",
            model_version=KnowledgeValue[str].known("runtime-fake-model.v0"),
            extracted_at=NOW,
            pages=(
                ExtractedPdfPage(
                    page_index=0,
                    locator="page:0",
                    markdown="# Fictional runtime deck",
                    confidence=PdfPageConfidence(
                        average=KnowledgeValue[float].unknown("fake omitted confidence"),
                        minimum=KnowledgeValue[float].unknown("fake omitted confidence"),
                    ),
                ),
            ),
            usage=PdfExtractionUsage(
                pages_processed=KnowledgeValue[int].known(1),
                document_size_bytes=KnowledgeValue[int].known(len(request.content)),
            ),
        )


class BlockingRuntimeFakeExtractor(RuntimeFakeExtractor):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def extract(self, request: PdfExtractionRequest) -> PdfExtractionResult:
        self.requests.append(request)
        self.started.set()
        await self.release.wait()
        return self.result(request)


class RecoveringRuntimeFakeExtractor(RuntimeFakeExtractor):
    async def extract(self, request: PdfExtractionRequest) -> PdfExtractionResult:
        if not self.requests:
            self.requests.append(request)
            raise PdfExtractionBlockedError
        return await super().extract(request)


def _settings(data_dir: Path) -> APISettings:
    return APISettings(
        _env_file=None,  # type: ignore[call-arg]  # pydantic-settings runtime override
        data_dir=data_dir,
        investor_api_key=SecretStr("runtime-investor-token"),
        founder_status_pepper=SecretStr("runtime-founder-status-pepper"),
        cors_origins="https://vc.example",
        maximum_deck_bytes=1_000_000,
        mistral_api_key=None,
    )


async def _submit(client: httpx.AsyncClient, *, key: str) -> httpx.Response:
    return await client.post(
        "/api/v1/applications",
        data={"company_name": "Celadon Systems"},
        files={"deck": ("deck.pdf", PDF, "application/pdf")},
        headers={"Idempotency-Key": key},
    )


@pytest.mark.anyio
async def test_runtime_persists_private_intake_and_runs_injected_extraction(
    tmp_path: Path,
) -> None:
    extractor = RuntimeFakeExtractor()
    application = create_runtime_app(settings=_settings(tmp_path), extractor=extractor)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="https://test.example",
    ) as client:
        response = await _submit(client, key="runtime-attempt-01")
        artifact_response = await client.get(
            f"/api/v1/artifacts/{response.json()['source_artifact_id']}",
            headers={"Authorization": "Bearer runtime-investor-token"},
        )
        run_response = await client.get(
            response.headers["Location"],
            headers={"Authorization": "Bearer runtime-investor-token"},
        )

    assert response.status_code == 202
    assert artifact_response.status_code == 200
    assert artifact_response.content == PDF
    assert run_response.status_code == 200
    assert run_response.json()["status"] == "succeeded"
    assert run_response.json()["stages"][0]["status"] == "succeeded"
    assert run_response.json()["accepted_output_ids"] == [
        response.json()["source_artifact_id"],
        "pdf-extraction:runtime-fake",
    ]
    assert len(extractor.requests) == 1

    repository = SQLiteIntakeRepository(tmp_path / "intake.sqlite3")
    stored = repository.get_application(response.json()["application_id"])
    assert stored is not None
    assert stored.artifact_stored is True
    assert stored.extraction.value is not None
    assert stored.extraction.value.pages[0].locator == "page:0"
    assert tuple(attempt.status for attempt in stored.extraction_attempts) == (
        ExtractionAttemptStatus.SUCCEEDED,
    )


@pytest.mark.anyio
async def test_runtime_accepts_intake_but_records_block_when_ocr_is_unconfigured(
    tmp_path: Path,
) -> None:
    application = create_runtime_app(settings=_settings(tmp_path))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="https://test.example",
    ) as client:
        response = await _submit(client, key="runtime-attempt-02")
        run_response = await client.get(
            response.headers["Location"],
            headers={"Authorization": "Bearer runtime-investor-token"},
        )

    assert response.status_code == 202
    repository = SQLiteIntakeRepository(tmp_path / "intake.sqlite3")
    stored = repository.get_application(response.json()["application_id"])
    assert stored is not None
    assert stored.extraction.value is None
    assert tuple(attempt.status for attempt in stored.extraction_attempts) == (
        ExtractionAttemptStatus.BLOCKED,
    )
    assert stored.extraction_attempts[0].safe_code == "mistral_ocr_configuration_invalid"
    assert run_response.status_code == 200
    assert run_response.json()["status"] == "partially_succeeded"
    assert run_response.json()["stages"][0]["status"] == "failed"
    assert run_response.json()["failures"][0]["safe_code"] == ("mistral_ocr_configuration_invalid")
    assert run_response.json()["accepted_output_ids"] == [response.json()["source_artifact_id"]]


@pytest.mark.anyio
async def test_runtime_coalesces_concurrent_idempotent_extraction_attempts(
    tmp_path: Path,
) -> None:
    extractor = BlockingRuntimeFakeExtractor()
    application = create_runtime_app(settings=_settings(tmp_path), extractor=extractor)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="https://test.example",
    ) as client:
        first = asyncio.create_task(_submit(client, key="runtime-concurrent-attempt"))
        await asyncio.wait_for(extractor.started.wait(), timeout=1)
        second = asyncio.create_task(_submit(client, key="runtime-concurrent-attempt"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        extractor.release.set()
        responses = await asyncio.gather(first, second)

    assert [response.status_code for response in responses] == [202, 202]
    assert responses[0].json()["application_id"] == responses[1].json()["application_id"]
    assert len(extractor.requests) == 1


@pytest.mark.anyio
async def test_later_replay_recovers_after_a_coalesced_blocked_attempt(
    tmp_path: Path,
) -> None:
    extractor = RecoveringRuntimeFakeExtractor()
    application = create_runtime_app(settings=_settings(tmp_path), extractor=extractor)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="https://test.example",
    ) as client:
        blocked = await _submit(client, key="runtime-recovery-attempt")
        recovered = await _submit(client, key="runtime-recovery-attempt")
        run_response = await client.get(
            recovered.headers["Location"],
            headers={"Authorization": "Bearer runtime-investor-token"},
        )

    assert blocked.status_code == 202
    assert recovered.status_code == 202
    assert recovered.json()["replayed"] is True
    assert recovered.json()["application_id"] == blocked.json()["application_id"]
    assert len(extractor.requests) == 2
    assert run_response.json()["status"] == "succeeded"

    repository = SQLiteIntakeRepository(tmp_path / "intake.sqlite3")
    stored = repository.get_application(recovered.json()["application_id"])
    assert stored is not None
    assert stored.extraction.value is not None
    assert tuple(attempt.status for attempt in stored.extraction_attempts) == (
        ExtractionAttemptStatus.BLOCKED,
        ExtractionAttemptStatus.SUCCEEDED,
    )
