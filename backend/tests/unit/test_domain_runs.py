"""Pipeline run lifecycle and timing contract tests."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from founderlookup.domain import (
    PipelineFailure,
    PipelineRun,
    PipelineRunKind,
    PipelineRunStatus,
    VersionManifest,
)

NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


def test_queued_run_has_no_started_or_completed_time() -> None:
    run = PipelineRun(
        run_id="run:1",
        kind=PipelineRunKind.INGESTION,
        status=PipelineRunStatus.QUEUED,
        versions=VersionManifest(),
        input_snapshot_id="snapshot:1",
        input_snapshot_as_of=NOW,
        queued_at=NOW,
    )
    assert run.attempt == 1

    with pytest.raises(ValidationError, match="queued run"):
        PipelineRun(
            run_id="run:2",
            kind=PipelineRunKind.INGESTION,
            status=PipelineRunStatus.QUEUED,
            versions=VersionManifest(),
            input_snapshot_id="snapshot:1",
            input_snapshot_as_of=NOW,
            queued_at=NOW,
            started_at=NOW,
        )


def test_partial_run_preserves_outputs_and_safe_failures() -> None:
    failure = PipelineFailure(
        failure_id="failure:1",
        stage_key="external-source",
        safe_code="source_timeout",
        safe_message="The bounded source request timed out",
        retryable=True,
        occurred_at=NOW + timedelta(seconds=5),
    )
    run = PipelineRun(
        run_id="run:partial",
        kind=PipelineRunKind.SOURCING,
        status=PipelineRunStatus.PARTIALLY_SUCCEEDED,
        versions=VersionManifest(),
        input_snapshot_id="snapshot:1",
        input_snapshot_as_of=NOW,
        queued_at=NOW,
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=10),
        accepted_output_ids=("artifact:1",),
        failures=(failure,),
    )
    assert run.accepted_output_ids == ("artifact:1",)

    with pytest.raises(ValidationError, match="partial run requires"):
        PipelineRun(
            run_id="run:invalid-partial",
            kind=PipelineRunKind.SOURCING,
            status=PipelineRunStatus.PARTIALLY_SUCCEEDED,
            versions=VersionManifest(),
            input_snapshot_id="snapshot:1",
            input_snapshot_as_of=NOW,
            queued_at=NOW,
            started_at=NOW,
            completed_at=NOW,
            failures=(failure,),
        )
