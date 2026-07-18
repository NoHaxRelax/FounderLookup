"""Framework- and model-neutral screening boundaries."""

from typing import Protocol, runtime_checkable

from founderlookup.domain.assessment import AssessmentEnvelope
from founderlookup.domain.common import DomainModel, StableId, SubjectRef
from founderlookup.domain.lifecycles import AssessmentMode


class IntelligenceRequest(DomainModel):
    """Immutable pointer to the canonical snapshot an assessment may read."""

    request_id: StableId
    input_snapshot_id: StableId
    subject: SubjectRef
    mode: AssessmentMode


@runtime_checkable
class IntelligencePort(Protocol):
    """Produce one schema-valid assessment from an immutable input snapshot."""

    async def assess(self, request: IntelligenceRequest) -> AssessmentEnvelope:
        """Return a proposed structured assessment without mutating Memory."""
        ...
