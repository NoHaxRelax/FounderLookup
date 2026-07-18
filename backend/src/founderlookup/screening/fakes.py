"""Deterministic structured-intelligence replay adapter."""

from collections.abc import Mapping

from founderlookup.domain.assessment import AssessmentEnvelope
from founderlookup.screening.ports import IntelligenceRequest


class MissingFakeAssessmentError(LookupError):
    """Raised when no fixed assessment exists for an intelligence request."""


class FakeIntelligenceAdapter:
    """Replay schema-valid assessments without selecting a model or framework."""

    def __init__(self, responses: Mapping[str, AssessmentEnvelope]) -> None:
        self._responses = dict(responses)
        self._requests: list[IntelligenceRequest] = []

    @property
    def requests(self) -> tuple[IntelligenceRequest, ...]:
        """Requests observed by this adapter, in call order."""
        return tuple(self._requests)

    async def assess(self, request: IntelligenceRequest) -> AssessmentEnvelope:
        """Return the fixed assessment keyed by ``request.request_id``."""
        self._requests.append(request)
        try:
            return self._responses[request.request_id]
        except KeyError as error:
            raise MissingFakeAssessmentError(
                f"No fake assessment for request {request.request_id!r}"
            ) from error
