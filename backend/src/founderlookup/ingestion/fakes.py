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


class InvalidFakeResponseError(ValueError):
    """Raised when seeded result data does not describe its request."""


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
            result = self._responses[request.request_id]
        except KeyError as error:
            raise MissingFakeResponseError(
                f"No fake discovery response for request {request.request_id!r}"
            ) from error
        if result.request_id != request.request_id:
            raise InvalidFakeResponseError(
                f"Fake discovery result {result.result_id!r} does not match request "
                f"{request.request_id!r}"
            )
        return result


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
        """Return the fixed response keyed by ``request.acquisition_request_id``."""
        self._requests.append(request)
        try:
            result = self._responses[request.acquisition_request_id]
        except KeyError as error:
            raise MissingFakeResponseError(
                f"No fake acquisition response for request {request.acquisition_request_id!r}"
            ) from error
        if (
            result.acquisition_request_id != request.acquisition_request_id
            or result.original_url != request.original_url
        ):
            raise InvalidFakeResponseError(
                f"Fake acquisition result {result.result_id!r} does not match request "
                f"{request.acquisition_request_id!r}"
            )
        return result
