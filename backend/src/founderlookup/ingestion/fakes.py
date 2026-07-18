"""Deterministic replay adapters for ingestion contract tests and local development."""

from collections.abc import Mapping

from founderlookup.domain.discovery import (
    AcquisitionRequest,
    AcquisitionResult,
    DiscoveryRequest,
    DiscoveryResult,
)


class MissingFakeResponseError(LookupError):
    """Raised when a replay fake has no response for a request identifier."""


class FakeDiscoveryAdapter:
    """Replay fixed discovery results without network or provider dependencies."""

    def __init__(self, responses: Mapping[str, DiscoveryResult]) -> None:
        self._responses = dict(responses)
        self._requests: list[DiscoveryRequest] = []

    @property
    def requests(self) -> tuple[DiscoveryRequest, ...]:
        """Requests observed by this adapter, in call order."""
        return tuple(self._requests)

    async def discover(self, request: DiscoveryRequest) -> DiscoveryResult:
        """Return the fixed response keyed by ``request.request_id``."""
        self._requests.append(request)
        try:
            return self._responses[request.request_id]
        except KeyError as error:
            raise MissingFakeResponseError(
                f"No fake discovery response for request {request.request_id!r}"
            ) from error


class FakeAcquisitionAdapter:
    """Replay fixed acquisition results without fetching source content."""

    def __init__(self, responses: Mapping[str, AcquisitionResult]) -> None:
        self._responses = dict(responses)
        self._requests: list[AcquisitionRequest] = []

    @property
    def requests(self) -> tuple[AcquisitionRequest, ...]:
        """Requests observed by this adapter, in call order."""
        return tuple(self._requests)

    async def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        """Return the fixed response keyed by ``request.request_id``."""
        self._requests.append(request)
        try:
            return self._responses[request.request_id]
        except KeyError as error:
            raise MissingFakeResponseError(
                f"No fake acquisition response for request {request.request_id!r}"
            ) from error
