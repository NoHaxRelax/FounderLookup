"""True external seams consumed by the application and HTTP layers."""

from typing import Protocol, runtime_checkable

from founderlookup.domain.common import DomainModel, NonBlankStr, StableId, UTCDateTime


class IntakeSubmission(DomainModel):
    company_name: NonBlankStr
    display_name: NonBlankStr
    media_type: NonBlankStr
    deck_content: bytes
    idempotency_key: NonBlankStr


class AcceptedApplication(DomainModel):
    application_id: StableId
    company_id: StableId
    run_id: StableId
    source_artifact_id: StableId
    source_artifact_sha256: NonBlankStr
    received_at: UTCDateTime
    replayed: bool = False


@runtime_checkable
class ApplicationIntakePort(Protocol):
    async def submit(self, submission: IntakeSubmission) -> AcceptedApplication:
        """Accept or idempotently replay one validated minimum Application."""
        ...


@runtime_checkable
class PrivateArtifactReadPort(Protocol):
    def read(
        self,
        artifact_id: str,
        *,
        principal_id: str,
        expected_sha256: str,
    ) -> bytes:
        """Return content-verified bytes after server-side authorization."""
        ...
