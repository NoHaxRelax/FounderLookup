"""Bounded LangGraph orchestration over the five neutral inbound-analysis ports."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from founderlookup.domain.lifecycles import AssessmentMode
from founderlookup.screening.inbound_analysis import (
    AdversarialValidationPort,
    AdversarialValidationResult,
    AnalysisRequest,
    FounderDossierAnalysisPort,
    FounderDossierAnalysisResult,
    IdeaNoveltyAnalysisPort,
    IdeaNoveltyAnalysisResult,
    MarketAnalysisPort,
    MarketAnalysisResult,
    MemoSynthesisPort,
    MemoSynthesisResult,
)

StageName = Literal["market", "idea", "founder", "adversarial", "memo"]
StageStatus = Literal["live", "fallback"]


class InboundGraphError(RuntimeError):
    """The bounded graph could not produce a complete proposed analysis."""


class _CallBudgetExhausted(InboundGraphError):
    pass


@dataclass(frozen=True)
class InboundGraphLimits:
    """Hard ceilings applied to one graph invocation."""

    max_model_calls: int = 5
    stage_timeout_seconds: float = 30.0
    total_timeout_seconds: float = 100.0
    recursion_limit: int = 12

    def __post_init__(self) -> None:
        if not 1 <= self.max_model_calls <= 5:
            raise ValueError("max_model_calls must be between one and five")
        if self.stage_timeout_seconds <= 0 or self.total_timeout_seconds <= 0:
            raise ValueError("graph timeouts must be positive")
        if self.total_timeout_seconds < self.stage_timeout_seconds:
            raise ValueError("total timeout cannot be shorter than one stage timeout")
        if self.recursion_limit < 8:
            raise ValueError("recursion_limit is too small for the fixed graph")


@dataclass(frozen=True)
class InboundStageAudit:
    """Safe structured stage telemetry; never includes provider or private error text."""

    stage: StageName
    status: StageStatus
    safe_code: str | None = None


@dataclass(frozen=True)
class InboundAnalysisRunResult:
    """Complete proposed specialist output; deliberately contains no human Decision."""

    request: AnalysisRequest
    market: MarketAnalysisResult
    idea: IdeaNoveltyAnalysisResult
    founder: FounderDossierAnalysisResult
    adversarial: AdversarialValidationResult
    memo: MemoSynthesisResult
    audit: tuple[InboundStageAudit, ...]

    @property
    def used_fallback(self) -> bool:
        return any(item.status == "fallback" for item in self.audit)


class _Budget:
    def __init__(self, maximum: int) -> None:
        self._maximum = maximum
        self._used = 0
        self._lock = asyncio.Lock()

    async def claim(self) -> None:
        async with self._lock:
            if self._used >= self._maximum:
                raise _CallBudgetExhausted("live analysis call budget exhausted")
            self._used += 1


class _GraphState(TypedDict):
    request: AnalysisRequest
    budget: _Budget
    market: MarketAnalysisResult | None
    idea: IdeaNoveltyAnalysisResult | None
    founder: FounderDossierAnalysisResult | None
    adversarial: AdversarialValidationResult | None
    memo: MemoSynthesisResult | None
    market_audit: InboundStageAudit | None
    idea_audit: InboundStageAudit | None
    founder_audit: InboundStageAudit | None
    adversarial_audit: InboundStageAudit | None
    memo_audit: InboundStageAudit | None


ResultT = (
    MarketAnalysisResult
    | IdeaNoveltyAnalysisResult
    | FounderDossierAnalysisResult
    | AdversarialValidationResult
    | MemoSynthesisResult
)


def _safe_code(error: Exception) -> str:
    if isinstance(error, _CallBudgetExhausted):
        return "call_budget_exhausted"
    if isinstance(error, TimeoutError):
        return "stage_timeout"
    return "invalid_or_unavailable_live_output"


def _describes(result: ResultT, request: AnalysisRequest) -> bool:
    header = result.header
    return (
        header.request_id == request.request_id
        and header.input_snapshot_id == request.input_snapshot_id
        and header.subject == request.subject
        and header.mode == request.mode
    )


class BoundedInboundAnalysisGraph:
    """Five calls maximum: three independent reads, validation, then cited memo."""

    def __init__(
        self,
        *,
        market: MarketAnalysisPort,
        idea: IdeaNoveltyAnalysisPort,
        founder: FounderDossierAnalysisPort,
        adversarial: AdversarialValidationPort,
        memo: MemoSynthesisPort,
        fallback_market: MarketAnalysisPort,
        fallback_idea: IdeaNoveltyAnalysisPort,
        fallback_founder: FounderDossierAnalysisPort,
        fallback_adversarial: AdversarialValidationPort,
        fallback_memo: MemoSynthesisPort,
        limits: InboundGraphLimits | None = None,
    ) -> None:
        self._market = market
        self._idea = idea
        self._founder = founder
        self._adversarial = adversarial
        self._memo = memo
        self._fallback_market = fallback_market
        self._fallback_idea = fallback_idea
        self._fallback_founder = fallback_founder
        self._fallback_adversarial = fallback_adversarial
        self._fallback_memo = fallback_memo
        self._limits = limits or InboundGraphLimits()
        self._graph = self._compile()

    @property
    def topology(self) -> tuple[tuple[str, str], ...]:
        """Stable, audit-friendly topology without exposing LangGraph state internals."""

        return (
            ("start", "market"),
            ("start", "idea"),
            ("start", "founder"),
            ("market+idea+founder", "adversarial"),
            ("adversarial", "memo"),
            ("memo", "end"),
        )

    async def _call[T: ResultT](
        self,
        *,
        stage: StageName,
        state: _GraphState,
        live: Callable[[AnalysisRequest], Awaitable[T]],
        fallback: Callable[[AnalysisRequest], Awaitable[T]],
    ) -> tuple[T, InboundStageAudit]:
        request = state["request"]
        try:
            await state["budget"].claim()
            async with asyncio.timeout(self._limits.stage_timeout_seconds):
                result = await live(request)
            if not _describes(result, request):
                raise InboundGraphError("live result does not describe its request")
            return result, InboundStageAudit(stage=stage, status="live")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            result = await fallback(request)
            if not _describes(result, request):
                raise InboundGraphError("fallback result does not describe its request") from None
            return result, InboundStageAudit(
                stage=stage,
                status="fallback",
                safe_code=_safe_code(error),
            )

    def _compile(
        self,
    ) -> CompiledStateGraph[_GraphState, None, _GraphState, _GraphState]:
        async def market_node(state: _GraphState) -> dict[str, object]:
            result, audit = await self._call(
                stage="market",
                state=state,
                live=self._market.analyze_market,
                fallback=self._fallback_market.analyze_market,
            )
            return {"market": result, "market_audit": audit}

        async def idea_node(state: _GraphState) -> dict[str, object]:
            result, audit = await self._call(
                stage="idea",
                state=state,
                live=self._idea.analyze_idea_novelty,
                fallback=self._fallback_idea.analyze_idea_novelty,
            )
            return {"idea": result, "idea_audit": audit}

        async def founder_node(state: _GraphState) -> dict[str, object]:
            result, audit = await self._call(
                stage="founder",
                state=state,
                live=self._founder.analyze_founder_dossier,
                fallback=self._fallback_founder.analyze_founder_dossier,
            )
            return {"founder": result, "founder_audit": audit}

        async def adversarial_node(state: _GraphState) -> dict[str, object]:
            result, audit = await self._call(
                stage="adversarial",
                state=state,
                live=self._adversarial.validate,
                fallback=self._fallback_adversarial.validate,
            )
            return {"adversarial": result, "adversarial_audit": audit}

        async def memo_node(state: _GraphState) -> dict[str, object]:
            result, audit = await self._call(
                stage="memo",
                state=state,
                live=self._memo.synthesize_memo,
                fallback=self._fallback_memo.synthesize_memo,
            )
            return {"memo": result, "memo_audit": audit}

        builder = StateGraph(_GraphState)
        builder.add_node("market", market_node)
        builder.add_node("idea", idea_node)
        builder.add_node("founder", founder_node)
        builder.add_node("adversarial", adversarial_node)
        builder.add_node("memo", memo_node)
        builder.add_edge(START, "market")
        builder.add_edge(START, "idea")
        builder.add_edge(START, "founder")
        builder.add_edge(["market", "idea", "founder"], "adversarial")
        builder.add_edge("adversarial", "memo")
        builder.add_edge("memo", END)
        return builder.compile(name="founderlookup-inbound-intelligence")

    async def _all_fallback(
        self, request: AnalysisRequest, *, safe_code: str
    ) -> InboundAnalysisRunResult:
        market, idea, founder = await asyncio.gather(
            self._fallback_market.analyze_market(request),
            self._fallback_idea.analyze_idea_novelty(request),
            self._fallback_founder.analyze_founder_dossier(request),
        )
        adversarial = await self._fallback_adversarial.validate(request)
        memo = await self._fallback_memo.synthesize_memo(request)
        stages: tuple[StageName, ...] = (
            "market",
            "idea",
            "founder",
            "adversarial",
            "memo",
        )
        return InboundAnalysisRunResult(
            request=request,
            market=market,
            idea=idea,
            founder=founder,
            adversarial=adversarial,
            memo=memo,
            audit=tuple(
                InboundStageAudit(stage=stage, status="fallback", safe_code=safe_code)
                for stage in stages
            ),
        )

    async def run(self, request: AnalysisRequest) -> InboundAnalysisRunResult:
        """Run one full proposed analysis without mutating canonical Memory."""

        if request.mode is not AssessmentMode.FULL:
            raise InboundGraphError("the five-stage inbound graph requires full assessment mode")
        initial: _GraphState = {
            "request": request,
            "budget": _Budget(self._limits.max_model_calls),
            "market": None,
            "idea": None,
            "founder": None,
            "adversarial": None,
            "memo": None,
            "market_audit": None,
            "idea_audit": None,
            "founder_audit": None,
            "adversarial_audit": None,
            "memo_audit": None,
        }
        try:
            async with asyncio.timeout(self._limits.total_timeout_seconds):
                raw = await self._graph.ainvoke(
                    initial, {"recursion_limit": self._limits.recursion_limit}
                )
        except TimeoutError:
            return await self._all_fallback(request, safe_code="graph_timeout")
        state = cast(_GraphState, raw)
        results = (
            state["market"],
            state["idea"],
            state["founder"],
            state["adversarial"],
            state["memo"],
        )
        audits = (
            state["market_audit"],
            state["idea_audit"],
            state["founder_audit"],
            state["adversarial_audit"],
            state["memo_audit"],
        )
        if any(item is None for item in (*results, *audits)):
            raise InboundGraphError("inbound graph returned an incomplete state")
        return InboundAnalysisRunResult(
            request=request,
            market=cast(MarketAnalysisResult, state["market"]),
            idea=cast(IdeaNoveltyAnalysisResult, state["idea"]),
            founder=cast(FounderDossierAnalysisResult, state["founder"]),
            adversarial=cast(AdversarialValidationResult, state["adversarial"]),
            memo=cast(MemoSynthesisResult, state["memo"]),
            audit=cast(tuple[InboundStageAudit, ...], audits),
        )


__all__ = [
    "BoundedInboundAnalysisGraph",
    "InboundAnalysisRunResult",
    "InboundGraphError",
    "InboundGraphLimits",
    "InboundStageAudit",
]
