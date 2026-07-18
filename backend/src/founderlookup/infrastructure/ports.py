"""Infrastructure boundaries shared by application services."""

from typing import Protocol, runtime_checkable

from founderlookup.domain.common import (
    DomainModel,
    PositiveInt,
    StableId,
    UTCDateTime,
    VersionManifest,
)
from founderlookup.domain.runs import PipelineRun, PipelineRunKind


class PipelineRunRequest(DomainModel):
    """A versioned, immutable request to create one observable pipeline run."""

    request_id: StableId
    kind: PipelineRunKind
    versions: VersionManifest
    input_snapshot_id: StableId
    input_snapshot_as_of: UTCDateTime
    retry_of_run_id: StableId | None = None
    attempt: PositiveInt = 1


@runtime_checkable
class PipelineRunPort(Protocol):
    """Submit and retrieve asynchronous pipeline-run snapshots."""

    async def submit(self, request: PipelineRunRequest) -> PipelineRun:
        """Return the observable run created for a request."""
        ...

    async def get(self, run_id: str) -> PipelineRun | None:
        """Return the latest snapshot for a stable run identifier."""
        ...
