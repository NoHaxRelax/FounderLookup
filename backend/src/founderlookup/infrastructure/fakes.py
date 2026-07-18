"""Deterministic replay adapter for pipeline-run contract development."""

from collections.abc import Iterable, Mapping

from founderlookup.domain.runs import PipelineRun
from founderlookup.infrastructure.ports import PipelineRunRequest


class MissingFakeRunError(LookupError):
    """Raised when a replay fake has no run for a submitted request."""


class InvalidFakeRunError(ValueError):
    """Raised when seeded run data does not describe its request."""


class FakePipelineRunAdapter:
    """Replay fixed run snapshots with fixed identifiers and timestamps."""

    def __init__(
        self,
        responses: Mapping[str, PipelineRun],
        *,
        seeded_runs: Iterable[PipelineRun] = (),
    ) -> None:
        self._responses = dict(responses)
        self._runs = {run.run_id: run for run in seeded_runs}
        self._submissions: list[PipelineRunRequest] = []
        self._lookups: list[str] = []

    @property
    def submissions(self) -> tuple[PipelineRunRequest, ...]:
        """Requests observed by this adapter, in call order."""
        return tuple(self._submissions)

    @property
    def lookups(self) -> tuple[str, ...]:
        """Stable run identifiers looked up, in call order."""
        return tuple(self._lookups)

    async def submit(self, request: PipelineRunRequest) -> PipelineRun:
        """Return and expose the fixed run keyed by ``request.request_id``."""
        self._submissions.append(request)
        try:
            run = self._responses[request.request_id]
        except KeyError as error:
            raise MissingFakeRunError(
                f"No fake pipeline run for request {request.request_id!r}"
            ) from error

        if (
            run.kind is not request.kind
            or run.versions != request.versions
            or run.input_snapshot_id != request.input_snapshot_id
            or run.input_snapshot_as_of != request.input_snapshot_as_of
            or run.retry_of_run_id != request.retry_of_run_id
            or run.attempt != request.attempt
        ):
            raise InvalidFakeRunError(
                f"Fake pipeline run {run.run_id!r} does not match request {request.request_id!r}"
            )

        self._runs[run.run_id] = run
        return run

    async def get(self, run_id: str) -> PipelineRun | None:
        """Return a seeded or previously submitted run snapshot."""
        self._lookups.append(run_id)
        return self._runs.get(run_id)
