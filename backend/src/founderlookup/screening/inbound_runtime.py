"""Runtime-facing one-shot composition for the live inbound graph."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from founderlookup.screening.inbound_analysis import AnalysisRequest
from founderlookup.screening.inbound_graph import (
    BoundedInboundAnalysisGraph,
    InboundAnalysisRunResult,
    InboundGraphLimits,
)
from founderlookup.screening.live_analyses import (
    DeterministicInboundFallbackAdapter,
    InboundAnalysisSnapshot,
    InMemoryAnalysisSnapshotResolver,
    OpenAIInboundAnalysisAdapter,
    Reasoner,
)


class RuntimeInboundIntelligence:
    """Create a source-neutral graph for one already accepted immutable snapshot."""

    def __init__(
        self,
        reasoner: Reasoner,
        *,
        clock: Callable[[], datetime],
        max_input_bytes: int,
        limits: InboundGraphLimits,
    ) -> None:
        self._reasoner = reasoner
        self._clock = clock
        self._max_input_bytes = max_input_bytes
        self._limits = limits

    @property
    def topology(self) -> tuple[tuple[str, str], ...]:
        """Expose the fixed topology without requiring a snapshot or provider call."""

        return (
            ("start", "market"),
            ("start", "idea"),
            ("start", "founder"),
            ("market+idea+founder", "adversarial"),
            ("adversarial", "memo"),
            ("memo", "end"),
        )

    async def analyze(
        self,
        request: AnalysisRequest,
        snapshot: InboundAnalysisSnapshot,
    ) -> InboundAnalysisRunResult:
        """Return proposals only; acceptance into Memory remains a separate application step."""

        resolver = InMemoryAnalysisSnapshotResolver({snapshot.input_snapshot_id: snapshot})
        live = OpenAIInboundAnalysisAdapter(
            self._reasoner,
            resolver,
            clock=self._clock,
            max_input_bytes=self._max_input_bytes,
        )
        fallback = DeterministicInboundFallbackAdapter(resolver, clock=self._clock)
        graph = BoundedInboundAnalysisGraph(
            market=live,
            idea=live,
            founder=live,
            adversarial=live,
            memo=live,
            fallback_market=fallback,
            fallback_idea=fallback,
            fallback_founder=fallback,
            fallback_adversarial=fallback,
            fallback_memo=fallback,
            limits=self._limits,
        )
        return await graph.run(request)


__all__ = ["RuntimeInboundIntelligence"]
