"""Canonical v0 workflow vocabularies; transition policy lives above these enums."""

from enum import StrEnum
from typing import Final

LIFECYCLE_SCHEMA_VERSION: Final = "lifecycle.v0"


class OpportunityOrigin(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class OutboundCandidateStatus(StrEnum):
    DISCOVERED = "discovered"
    PRELIMINARY_ASSESSMENT = "preliminary_assessment"
    READY_FOR_ACTIVATION = "ready_for_activation"
    ACTIVATED = "activated"
    CONTACTED = "contacted"
    APPLIED = "applied"
    CLOSED = "closed"


class ApplicationStatus(StrEnum):
    RECEIVED = "received"
    INGESTING = "ingesting"
    READY_FOR_SCREENING = "ready_for_screening"
    LINKED_TO_SCREENING_CASE = "linked_to_screening_case"
    WITHDRAWN = "withdrawn"
    FAILED = "failed"


class ScreeningCaseStatus(StrEnum):
    FIRST_PASS = "first_pass"
    SCREENING = "screening"
    DILIGENCE = "diligence"
    READINESS_REVIEW = "readiness_review"
    BLOCKED = "blocked"
    DECISION_READY = "decision_ready"
    DECIDED = "decided"
    CLOSED = "closed"


class DecisionReadinessStatus(StrEnum):
    NOT_EVALUATED = "not_evaluated"
    BLOCKED = "blocked"
    READY = "ready"
    READY_WITH_ACCEPTED_RISK = "ready_with_accepted_risk"


class PipelineRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    FAILED = "failed"


class PipelineStageStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"
    FAILED = "failed"


class AssessmentMode(StrEnum):
    PRELIMINARY = "preliminary"
    FULL = "full"
