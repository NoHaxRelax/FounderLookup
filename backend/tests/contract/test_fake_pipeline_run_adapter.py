"""Shared contract tests for deterministic asynchronous run snapshots."""

import asyncio
from datetime import UTC, datetime

import pytest

from founderlookup.domain.common import VersionManifest
from founderlookup.domain.lifecycles import PipelineRunStatus
from founderlookup.domain.runs import PipelineRun, PipelineRunKind
from founderlookup.infrastructure.fakes import (
    FakePipelineRunAdapter,
    InvalidFakeRunError,
    MissingFakeRunError,
)
from founderlookup.infrastructure.ports import PipelineRunPort, PipelineRunRequest

FIXED_TIME = datetime(2026, 7, 1, 14, tzinfo=UTC)


def _request() -> PipelineRunRequest:
    return PipelineRunRequest(
        request_id="pipeline-request-001",
        kind=PipelineRunKind.INTELLIGENCE,
        versions=VersionManifest(),
        input_snapshot_id="snapshot-001",
        input_snapshot_as_of=FIXED_TIME,
    )


def _queued_run(*, input_snapshot_id: str = "snapshot-001") -> PipelineRun:
    return PipelineRun(
        run_id="run-001",
        kind=PipelineRunKind.INTELLIGENCE,
        status=PipelineRunStatus.QUEUED,
        versions=VersionManifest(),
        input_snapshot_id=input_snapshot_id,
        input_snapshot_as_of=FIXED_TIME,
        queued_at=FIXED_TIME,
    )


def test_fake_run_adapter_replays_fixed_identity_and_clock() -> None:
    request = _request()
    expected = _queued_run()
    adapter = FakePipelineRunAdapter({request.request_id: expected})

    assert isinstance(adapter, PipelineRunPort)
    assert asyncio.run(adapter.get(expected.run_id)) is None

    first = asyncio.run(adapter.submit(request))
    second = asyncio.run(adapter.submit(request))

    assert first == expected
    assert second == expected
    assert first.run_id == "run-001"
    assert first.queued_at == FIXED_TIME
    assert PipelineRun.model_validate(first.model_dump(mode="python")) == first
    assert asyncio.run(adapter.get(first.run_id)) == first
    assert adapter.submissions == (request, request)
    assert adapter.lookups == (expected.run_id, expected.run_id)


def test_fake_run_adapter_fails_explicitly_for_missing_or_mismatched_data() -> None:
    request = _request()
    missing = FakePipelineRunAdapter({})
    mismatched = FakePipelineRunAdapter(
        {request.request_id: _queued_run(input_snapshot_id="different-snapshot")}
    )

    with pytest.raises(MissingFakeRunError, match=request.request_id):
        asyncio.run(missing.submit(request))

    with pytest.raises(InvalidFakeRunError, match=request.request_id):
        asyncio.run(mismatched.submit(request))
