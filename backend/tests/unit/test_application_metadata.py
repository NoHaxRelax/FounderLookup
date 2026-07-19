"""Validation and provenance tests for optional founder Application metadata."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from founderlookup.api.app import create_app
from founderlookup.api.settings import APISettings, RuntimeEnvironment
from founderlookup.application.application_metadata import project_application_metadata
from founderlookup.application.ports import (
    AcceptedApplication,
    ApplicationFounderProfile,
    ApplicationSubmittedMetadata,
    IntakeSubmission,
)
from founderlookup.application.service import FakeVCBrainService
from founderlookup.domain.evidence import ClaimStatus, DataClassification
from founderlookup.infrastructure.application_metadata import SQLiteApplicationMetadataStore
from founderlookup.infrastructure.persistence import RecordCategory, SQLiteMemory

NOW = datetime(2026, 7, 19, 12, tzinfo=UTC)
PDF = b"%PDF-1.7\nsynthetic metadata fixture\n%%EOF\n"


def _metadata() -> ApplicationSubmittedMetadata:
    return ApplicationSubmittedMetadata(
        website="https://signal-forge.example/product",
        one_line_pitch="Audit automation for fictional regulated operators.",
        location="Zurich, Switzerland",
        stage="pre-seed",
        contact_email="team@signal-forge.example",
        founders=(
            ApplicationFounderProfile(
                full_name="Avery Example",
                role_title="Founder and CTO",
                email="avery@signal-forge.example",
                linkedin_url="https://www.linkedin.com/in/avery-example",
                github_url="https://github.com/avery-example",
                previous_companies=("Example Labs",),
                background="Built fictional audit tools and shipped one pilot.",
            ),
        ),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("website", "http://signal-forge.example"),
        ("website", "https://localhost/product"),
        ("website", "https://127.0.0.1/product"),
        ("website", "https://user:secret@signal-forge.example/product"),
        ("website", "https://signal-forge.example/product#private"),
    ],
)
def test_company_website_rejects_nonpublic_or_unsafe_urls(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        ApplicationSubmittedMetadata.model_validate({field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("github_url", "https://gitlab.com/avery-example"),
        ("github_url", "https://github.com/avery-example/repository"),
        ("linkedin_url", "https://linkedin.com/company/example"),
        ("linkedin_url", "https://evil.example/in/avery-example"),
    ],
)
def test_founder_profiles_reject_wrong_hosts_and_nonsane_paths(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        ApplicationFounderProfile.model_validate({"full_name": "Avery", field: value})


def test_old_minimum_submission_remains_valid() -> None:
    submission = IntakeSubmission(
        company_name="Signal Forge",
        display_name="deck.pdf",
        media_type="application/pdf",
        deck_content=PDF,
        idempotency_key="minimum-only",
    )

    assert submission.metadata == ApplicationSubmittedMetadata()


def test_projection_preserves_private_provenance_and_never_verifies_identity() -> None:
    projection = project_application_metadata(
        application_id="application:metadata",
        company_id="company:metadata",
        company_name="Signal Forge",
        metadata=_metadata(),
        received_at=NOW,
    )
    replay = project_application_metadata(
        application_id="application:metadata",
        company_id="company:metadata",
        company_name="Signal Forge",
        metadata=_metadata(),
        received_at=NOW,
    )

    assert replay == projection
    assert projection.source_artifact.classification is DataClassification.FOUNDER_PRIVATE
    assert projection.source_artifact.media_type == "application/json"
    assert {item.status for item in projection.claims} == {ClaimStatus.ASSERTED_UNVERIFIED}
    assert all("applicant submitted" in item.statement for item in projection.claims)
    assert all(item.supporting_evidence_ids for item in projection.claims)
    assert projection.public_lookup_urls == (
        "https://signal-forge.example/product",
        "https://www.linkedin.com/in/avery-example",
        "https://github.com/avery-example",
    )
    assert "team@signal-forge.example" not in projection.public_lookup_urls


def test_metadata_projection_persists_atomically_and_idempotently(tmp_path: Path) -> None:
    projection = project_application_metadata(
        application_id="application:metadata-store",
        company_id="company:metadata-store",
        company_name="Signal Forge",
        metadata=_metadata(),
        received_at=NOW,
    )
    memory = SQLiteMemory((tmp_path / "memory.sqlite3").resolve())
    store = SQLiteApplicationMetadataStore(memory)

    store.persist(projection)
    store.persist(projection)

    assert (
        memory.latest(RecordCategory.APPLICATION_METADATA_PROJECTION, projection.projection_id)
        is not None
    )
    assert len(memory.list_records(category=RecordCategory.CLAIM)) == len(projection.claims)
    assert len(memory.list_records(category=RecordCategory.EVIDENCE)) == len(projection.evidence)


class _CapturingIntake:
    submission: IntakeSubmission | None = None

    async def submit(self, submission: IntakeSubmission) -> AcceptedApplication:
        self.submission = submission
        return AcceptedApplication(
            application_id="application:metadata-api",
            company_id="company:metadata-api",
            run_id="run:metadata-api",
            source_artifact_id="artifact:metadata-api-deck",
            source_artifact_sha256=hashlib.sha256(submission.deck_content).hexdigest(),
            received_at=NOW,
            company_name=submission.company_name,
            metadata=submission.metadata,
        )


def _settings() -> APISettings:
    return APISettings(
        _env_file=None,  # type: ignore[call-arg]
        environment=RuntimeEnvironment.TEST,
        investor_api_key=SecretStr("fictional-investor-key"),
        founder_status_pepper=SecretStr("fictional-status-pepper"),
        maximum_deck_bytes=1_000_000,
    )


@pytest.mark.anyio
async def test_multipart_metadata_is_optional_structured_and_absent_from_receipt() -> None:
    intake = _CapturingIntake()
    app = create_app(
        settings=_settings(),
        service=FakeVCBrainService(clock=lambda: NOW, id_factory=lambda: "opaque-id"),
        intake_service=intake,
    )
    founders_json = json.dumps([item.model_dump(mode="json") for item in _metadata().founders])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/applications",
            headers={"Idempotency-Key": "metadata-api"},
            data={
                "company_name": "Signal Forge",
                "website": _metadata().website,
                "one_line_pitch": _metadata().one_line_pitch,
                "location": _metadata().location,
                "stage": _metadata().stage,
                "contact_email": _metadata().contact_email,
                "founders": founders_json,
            },
            files={"deck": ("deck.pdf", PDF, "application/pdf")},
        )

    assert response.status_code == 202
    assert intake.submission is not None
    assert intake.submission.metadata == _metadata()
    assert "contact_email" not in response.json()
    assert "founders" not in response.json()


@pytest.mark.anyio
async def test_invalid_founder_json_or_profile_url_fails_before_intake() -> None:
    intake = _CapturingIntake()
    app = create_app(
        settings=_settings(),
        service=FakeVCBrainService(clock=lambda: NOW, id_factory=lambda: "opaque-id"),
        intake_service=intake,
    )
    invalid = json.dumps([{"full_name": "Avery Example", "github_url": "https://localhost/avery"}])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/applications",
            headers={"Idempotency-Key": "metadata-invalid"},
            data={"company_name": "Signal Forge", "founders": invalid},
            files={"deck": ("deck.pdf", PDF, "application/pdf")},
        )

    assert response.status_code == 422
    assert intake.submission is None
    assert "localhost" not in response.text
