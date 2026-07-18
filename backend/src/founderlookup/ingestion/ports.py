"""Provider-neutral boundaries for outbound discovery and content acquisition."""

from typing import Protocol, runtime_checkable

from founderlookup.domain.discovery import (
    AcquisitionRequest,
    AcquisitionResult,
    DiscoveryRequest,
    DiscoveryResult,
)


@runtime_checkable
class DiscoveryPort(Protocol):
    """Find original-source leads for one bounded discovery request."""

    async def discover(self, request: DiscoveryRequest) -> DiscoveryResult:
        """Return provider-neutral leads and collection telemetry."""
        ...


@runtime_checkable
class AcquisitionPort(Protocol):
    """Acquire permitted content from one selected original-source URL."""

    async def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        """Return captured source content and retrieval metadata."""
        ...
