"""Observable, retryable pipeline-run snapshots with UTC timing invariants."""

from __future__ import annotations

from enum import StrEnum
from typing import Final, Literal, Self

from pydantic import model_validator

from founderlookup.domain.common import (
    DomainModel,
    NonBlankStr,
    PositiveInt,
    StableId,
    UTCDateTime,
    VersionManifest,
)
from founderlookup.domain.lifecycles import PipelineRunStatus, PipelineStageStatus

PIPELINE_RUN_SCHEMA_VERSION: Final = "pipeline-run.v0"


class PipelineRunKind(StrEnum):
    INGESTION = "ingestion"
    SOURCING = "sourcing"
    SCREENING = "screening"
    INTELLIGENCE = "intelligence"
    MEMO = "memo"


class PipelineFailure(DomainModel):
    failure_id: StableId
    stage_key: NonBlankStr
    safe_code: NonBlankStr
    safe_message: NonBlankStr
    retryable: bool
    occurred_at: UTCDateTime


class PipelineStage(DomainModel):
    stage_key: NonBlankStr
    status: PipelineStageStatus
    queued_at: UTCDateTime
    started_at: UTCDateTime | None = None
    completed_at: UTCDateTime | None = None
    accepted_output_ids: tuple[StableId, ...] = ()
    failure_ids: tuple[StableId, ...] = ()

    @model_validator(mode="after")
    def validate_timing(self) -> Self:
        terminal = {
            PipelineStageStatus.SUCCEEDED,
            PipelineStageStatus.SKIPPED,
            PipelineStageStatus.FAILED,
        }
        if self.status is PipelineStageStatus.QUEUED:
            if self.started_at is not None or self.completed_at is not None:
                raise ValueError("queued stage cannot have start or completion time")
        elif self.status is PipelineStageStatus.RUNNING:
            if self.started_at is None or self.completed_at is not None:
                raise ValueError("running stage requires start and no completion time")
        elif self.status in terminal and (self.started_at is None or self.completed_at is None):
            raise ValueError("terminal stage requires start and completion times")

        if self.started_at is not None and self.started_at < self.queued_at:
            raise ValueError("stage cannot start before it is queued")
        if (
            self.started_at is not None
            and self.completed_at is not None
            and self.completed_at < self.started_at
        ):
            raise ValueError("stage cannot complete before it starts")
        if self.status is PipelineStageStatus.FAILED and not self.failure_ids:
            raise ValueError("failed stage requires a failure reference")
        if self.status is not PipelineStageStatus.FAILED and self.failure_ids:
            raise ValueError("only failed stages can carry failure references")
        return self


class PipelineRun(DomainModel):
    schema_version: Literal["pipeline-run.v0"] = PIPELINE_RUN_SCHEMA_VERSION
    run_id: StableId
    kind: PipelineRunKind
    status: PipelineRunStatus
    versions: VersionManifest
    input_snapshot_id: StableId
    input_snapshot_as_of: UTCDateTime
    queued_at: UTCDateTime
    started_at: UTCDateTime | None = None
    completed_at: UTCDateTime | None = None
    stages: tuple[PipelineStage, ...] = ()
    accepted_output_ids: tuple[StableId, ...] = ()
    failures: tuple[PipelineFailure, ...] = ()
    retry_of_run_id: StableId | None = None
    attempt: PositiveInt = 1

    @model_validator(mode="after")
    def validate_run_state(self) -> Self:
        terminal = {
            PipelineRunStatus.SUCCEEDED,
            PipelineRunStatus.PARTIALLY_SUCCEEDED,
            PipelineRunStatus.FAILED,
        }
        if self.status is PipelineRunStatus.QUEUED:
            if self.started_at is not None or self.completed_at is not None:
                raise ValueError("queued run cannot have start or completion time")
        elif self.status is PipelineRunStatus.RUNNING:
            if self.started_at is None or self.completed_at is not None:
                raise ValueError("running run requires start and no completion time")
        elif self.status in terminal and (self.started_at is None or self.completed_at is None):
            raise ValueError("terminal run requires start and completion times")

        if self.started_at is not None and self.started_at < self.queued_at:
            raise ValueError("run cannot start before it is queued")
        if (
            self.started_at is not None
            and self.completed_at is not None
            and self.completed_at < self.started_at
        ):
            raise ValueError("run cannot complete before it starts")

        stage_keys = tuple(stage.stage_key for stage in self.stages)
        if len(stage_keys) != len(set(stage_keys)):
            raise ValueError("pipeline stage keys must be unique")
        failure_ids = tuple(failure.failure_id for failure in self.failures)
        if len(failure_ids) != len(set(failure_ids)):
            raise ValueError("pipeline failure identifiers must be unique")
        stage_failure_ids = {
            failure_id for stage in self.stages for failure_id in stage.failure_ids
        }
        if not stage_failure_ids.issubset(failure_ids):
            raise ValueError("stage failures must reference run failures")

        if self.status is PipelineRunStatus.SUCCEEDED and self.failures:
            raise ValueError("succeeded run cannot carry failures")
        if self.status is PipelineRunStatus.PARTIALLY_SUCCEEDED and (
            not self.accepted_output_ids or not self.failures
        ):
            raise ValueError("partial run requires accepted outputs and failures")
        if self.status is PipelineRunStatus.FAILED and not self.failures:
            raise ValueError("failed run requires a safe failure")
        if self.retry_of_run_id is None and self.attempt != 1:
            raise ValueError("only linked retry runs can have attempt greater than one")
        if self.retry_of_run_id == self.run_id:
            raise ValueError("run cannot retry itself")
        return self
