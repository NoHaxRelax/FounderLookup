"""Background coordinator for API-compatible live Screening execution."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from founderlookup.application.models import RunAccepted
from founderlookup.application.service import (
    FakeVCBrainService,
    LiveScreeningContext,
)
from founderlookup.domain.assessment import AssessmentEnvelope
from founderlookup.domain.runs import PipelineRun
from founderlookup.screening.inbound_graph import InboundStageAudit
from founderlookup.screening.inbound_runtime import RuntimeInboundIntelligence

AcceptedLiveScreeningHook = Callable[
    [
        LiveScreeningContext,
        AssessmentEnvelope,
        PipelineRun,
        tuple[InboundStageAudit, ...] | None,
    ],
    None,
]


@runtime_checkable
class LiveScreeningCoordinatorPort(Protocol):
    """Queue promptly, then execute through a bounded background task."""

    def enqueue(self, opportunity_id: str) -> RunAccepted: ...

    async def execute(self, run_id: str) -> None: ...


class LiveScreeningCoordinator:
    """Bridge the async graph to canonical application acceptance and run polling."""

    def __init__(
        self,
        *,
        service: FakeVCBrainService,
        intelligence: RuntimeInboundIntelligence,
        on_accepted: AcceptedLiveScreeningHook | None = None,
    ) -> None:
        self._service = service
        self._intelligence = intelligence
        self._on_accepted = on_accepted

    def enqueue(self, opportunity_id: str) -> RunAccepted:
        return self._service.queue_live_screening(opportunity_id)

    async def execute(self, run_id: str) -> None:
        context = self._service.live_screening_context(run_id)
        self._service.mark_live_screening_running(run_id)
        audit: tuple[InboundStageAudit, ...] | None
        try:
            result = await self._intelligence.analyze(context.request, context.snapshot)
            assessment = self._service.accept_live_screening(run_id, result)
            audit = result.audit
        except asyncio.CancelledError:
            raise
        except Exception:
            # No exception text crosses the boundary; the prepared deterministic assessment
            # remains the only accepted fallback and carries no model-invented facts.
            assessment = self._service.accept_live_screening_failure(run_id)
            audit = None
        if self._on_accepted is not None:
            self._on_accepted(
                context,
                assessment,
                self._service.get_run(run_id),
                audit,
            )


__all__ = [
    "AcceptedLiveScreeningHook",
    "LiveScreeningCoordinator",
    "LiveScreeningCoordinatorPort",
]
