"""Strict, snapshot-backed live implementations of the five inbound analysis ports.

The model may propose classifications, citations, memo prose, and a Recommendation.  It
cannot mint accepted Claims or Evidence, alter trust scores, choose record identities, or
write Memory.  Every citation is resolved against one immutable caller-supplied snapshot
before a frozen domain result is constructed.  A separate deterministic adapter produces
honest Unknown/needs-information results for orchestration fallback.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Protocol, TypeVar
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from founderlookup.domain.assessment import (
    REQUIRED_MEMO_SECTIONS,
    Confidence,
    Contradiction,
    FounderAxisAssessment,
    FounderAxisRating,
    IdeaVsMarketAxisAssessment,
    IdeaVsMarketAxisRating,
    InvestmentMemo,
    MarketAxisAssessment,
    MarketAxisRating,
    MemoSection,
    MemoSectionKind,
    Recommendation,
    RecommendationAction,
    RecommendationReason,
    Trend,
)
from founderlookup.domain.common import KnowledgeValue, StableId, UTCDateTime, VersionId
from founderlookup.domain.evidence import Claim, ClaimStatus, Evidence, EvidenceStance
from founderlookup.domain.lifecycles import AssessmentMode
from founderlookup.domain.scoring import CoverageSummary
from founderlookup.screening.founder_reads import (
    GradedObservation,
    builder_fundability_gap,
    builder_signal_read,
    fundability_read,
)
from founderlookup.screening.inbound_analysis import (
    AdversarialValidationResult,
    AnalysisRequest,
    AnalysisResultHeader,
    FounderDossierAnalysisResult,
    IdeaNoveltyAnalysisResult,
    MarketAnalysisResult,
    MemoSynthesisResult,
)

SchemaT = TypeVar("SchemaT", bound=BaseModel)

_PROHIBITED_FOUNDER_PROXY = re.compile(
    r"\b(?:appearance|accent|charisma|charismatic|polish|polished|production\s+value|"
    r"age|young|youth|gender|race|ethnicity|religion|disability|socioeconomic|wealth|"
    r"pedigree|follower\s+(?:count|reach)|social\s+reach)\b",
    flags=re.IGNORECASE,
)

_COMMON_INSTRUCTIONS = """
You are proposing a bounded investment-analysis record from an immutable evidence
snapshot. Return only the requested structured object. Cite only Claim identifiers present
in the snapshot. Never invent or rewrite a Claim, Evidence item, identity, timestamp, or
source. Absence is Unknown, never negative evidence. Do not reveal private chain-of-thought;
return only concise conclusions, questions, and citation identifiers. This output is a
proposal for human review, never a Decision, outreach instruction, or authorization to move
funds.
""".strip()


class LiveAnalysisError(RuntimeError):
    """A safe-to-mask live-analysis validation or snapshot error."""


class SnapshotNotFoundError(LiveAnalysisError):
    """The immutable snapshot named by an analysis request is unavailable."""


class Reasoner(Protocol):
    """Provider-neutral strict structured-output capability."""

    async def extract(
        self,
        *,
        schema: type[SchemaT],
        instructions: str,
        content: str,
    ) -> SchemaT: ...


@dataclass(frozen=True)
class MemoIdentity:
    """Caller-owned identities needed to construct a proposed memo revision."""

    opportunity_id: StableId
    screening_case_id: StableId
    assessment_id: StableId
    run_id: StableId
    thesis_version: VersionId
    evidence_as_of: UTCDateTime


@dataclass(frozen=True)
class InboundAnalysisSnapshot:
    """One validated immutable input snapshot available to all five specialists."""

    input_snapshot_id: StableId
    claims: tuple[Claim, ...]
    evidence: tuple[Evidence, ...]
    coverage: CoverageSummary
    memo_identity: MemoIdentity
    contradictions: tuple[Contradiction, ...] = ()
    founder_observations: tuple[GradedObservation, ...] = ()
    public_lookup_urls: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        claim_by_id = {item.claim_id: item for item in self.claims}
        evidence_by_id = {item.evidence_id: item for item in self.evidence}
        if len(claim_by_id) != len(self.claims):
            raise LiveAnalysisError("snapshot Claim identifiers must be unique")
        if len(evidence_by_id) != len(self.evidence):
            raise LiveAnalysisError("snapshot Evidence identifiers must be unique")
        for item in self.evidence:
            if item.claim_id not in claim_by_id:
                raise LiveAnalysisError("snapshot Evidence must attach to a snapshot Claim")
        for claim in self.claims:
            for evidence_id, stance in (
                *((item, EvidenceStance.SUPPORTS) for item in claim.supporting_evidence_ids),
                *((item, EvidenceStance.REFUTES) for item in claim.counter_evidence_ids),
            ):
                evidence = evidence_by_id.get(evidence_id)
                if (
                    evidence is None
                    or evidence.claim_id != claim.claim_id
                    or evidence.stance is not stance
                ):
                    raise LiveAnalysisError("snapshot Claim citation graph is not closed")
        contradiction_ids = tuple(item.contradiction_id for item in self.contradictions)
        if len(contradiction_ids) != len(set(contradiction_ids)):
            raise LiveAnalysisError("snapshot Contradiction identifiers must be unique")
        for contradiction in self.contradictions:
            if not set(contradiction.claim_ids).issubset(claim_by_id) or not set(
                contradiction.evidence_ids
            ).issubset(evidence_by_id):
                raise LiveAnalysisError("snapshot Contradiction citations are not closed")
            if any(
                claim_by_id[claim_id].status is not ClaimStatus.CONTRADICTED
                for claim_id in contradiction.claim_ids
            ):
                raise LiveAnalysisError(
                    "snapshot Contradiction Claims must already be canonical contradicted revisions"
                )
        available_evidence = set(evidence_by_id)
        if any(
            not set(observation.evidence_ids).issubset(available_evidence)
            for observation in self.founder_observations
        ):
            raise LiveAnalysisError("founder observations must cite snapshot Evidence")
        if len(self.public_lookup_urls) != len(set(self.public_lookup_urls)) or any(
            urlsplit(value).scheme != "https" or not urlsplit(value).hostname
            for value in self.public_lookup_urls
        ):
            raise LiveAnalysisError("public lookup leads must be unique HTTPS URLs")


class AnalysisSnapshotResolver(Protocol):
    """Read-only lookup for already accepted immutable input snapshots."""

    async def resolve(self, input_snapshot_id: StableId) -> InboundAnalysisSnapshot: ...


class InMemoryAnalysisSnapshotResolver:
    """Deterministic resolver useful for composition, tests, and one-shot live runs."""

    def __init__(self, snapshots: Mapping[str, InboundAnalysisSnapshot]) -> None:
        self._snapshots = dict(snapshots)

    async def resolve(self, input_snapshot_id: StableId) -> InboundAnalysisSnapshot:
        try:
            return self._snapshots[input_snapshot_id]
        except KeyError:
            raise SnapshotNotFoundError("accepted inbound snapshot is unavailable") from None


class _StrictProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")


ConfidenceValue = Annotated[float | None, Field(strict=True, ge=0.0, le=1.0)]
BoundedIds = Annotated[tuple[StableId, ...], Field(max_length=64)]
BoundedQuestions = Annotated[tuple[str, ...], Field(max_length=12)]


class _MarketProposal(_StrictProposal):
    rating: MarketAxisRating
    trend: Trend
    confidence: ConfidenceValue
    supporting_claim_ids: BoundedIds
    counter_claim_ids: BoundedIds
    open_questions: BoundedQuestions


class _IdeaProposal(_StrictProposal):
    rating: IdeaVsMarketAxisRating
    trend: Trend
    confidence: ConfidenceValue
    supporting_claim_ids: BoundedIds
    counter_claim_ids: BoundedIds
    open_questions: BoundedQuestions


class _FounderProposal(_StrictProposal):
    rating: FounderAxisRating
    trend: Trend
    confidence: ConfidenceValue
    supporting_claim_ids: BoundedIds
    counter_claim_ids: BoundedIds
    open_questions: BoundedQuestions


class _AdversarialProposal(_StrictProposal):
    contradiction_ids: BoundedIds
    confidence: ConfidenceValue
    open_questions: BoundedQuestions


class _MemoSectionProposal(_StrictProposal):
    kind: MemoSectionKind
    content: str | None
    material_claim_ids: BoundedIds
    gap_reason: str | None
    requested_evidence: str | None

    @model_validator(mode="after")
    def require_known_or_gap(self) -> _MemoSectionProposal:
        if self.content is None:
            if not self.gap_reason or not self.requested_evidence:
                raise ValueError("an unknown memo section requires a reason and evidence request")
            if self.material_claim_ids:
                raise ValueError("an unknown memo section cannot cite material Claims")
        elif not self.content.strip() or not self.material_claim_ids:
            raise ValueError("a known memo section requires prose and material Claims")
        return self


class _RecommendationReasonProposal(_StrictProposal):
    summary: str
    claim_ids: BoundedIds


class _RecommendationProposal(_StrictProposal):
    action: RecommendationAction
    reasons: Annotated[tuple[_RecommendationReasonProposal, ...], Field(min_length=1, max_length=8)]
    next_actions: Annotated[tuple[str, ...], Field(min_length=1, max_length=8)]


class _MemoProposal(_StrictProposal):
    sections: Annotated[tuple[_MemoSectionProposal, ...], Field(min_length=5, max_length=5)]
    recommendation: _RecommendationProposal
    contradiction_ids: BoundedIds
    confidence: ConfidenceValue
    open_questions: BoundedQuestions


def _derived_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:24]
    return f"{prefix}:{digest}"


def _header(request: AnalysisRequest) -> AnalysisResultHeader:
    return AnalysisResultHeader(
        request_id=request.request_id,
        input_snapshot_id=request.input_snapshot_id,
        subject=request.subject,
        mode=request.mode,
    )


def _unknown_confidence(reason: str) -> KnowledgeValue[Confidence]:
    return KnowledgeValue[Confidence].unknown(reason)


def _known_confidence(
    value: float | None, *, rating_is_unknown: bool
) -> KnowledgeValue[Confidence]:
    if rating_is_unknown:
        return _unknown_confidence("The evidence snapshot does not support a directional read.")
    if value is None:
        raise LiveAnalysisError("a directional analysis requires explicit confidence")
    return KnowledgeValue[Confidence].known(value)


def _proposal_snapshot_json(
    request: AnalysisRequest,
    snapshot: InboundAnalysisSnapshot,
    *,
    max_input_bytes: int,
) -> str:
    observations = tuple(
        {
            "factor_key": item.factor_key,
            "tier": item.tier.value,
            "grade": item.grade.value,
            "observed_value": item.observed_value.model_dump(mode="json"),
            "rationale": item.rationale,
            "evidence_ids": item.evidence_ids,
        }
        for item in snapshot.founder_observations
    )
    payload = {
        "request": {
            "request_id": request.request_id,
            "input_snapshot_id": request.input_snapshot_id,
            "subject": request.subject.model_dump(mode="json"),
            "mode": request.mode.value,
        },
        "claims": tuple(item.model_dump(mode="json") for item in snapshot.claims),
        "evidence": tuple(item.model_dump(mode="json") for item in snapshot.evidence),
        "coverage": snapshot.coverage.model_dump(mode="json"),
        "contradictions": tuple(item.model_dump(mode="json") for item in snapshot.contradictions),
        "founder_observations": observations,
        "public_lookup_leads": {
            "status": "founder_provided_unverified_not_evidence",
            "urls": snapshot.public_lookup_urls,
        },
    }
    content = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    if len(content.encode()) > max_input_bytes:
        raise LiveAnalysisError("accepted inbound snapshot exceeds the live input limit")
    return content


def _validate_request_snapshot(request: AnalysisRequest, snapshot: InboundAnalysisSnapshot) -> None:
    if snapshot.input_snapshot_id != request.input_snapshot_id:
        raise LiveAnalysisError("snapshot resolver returned the wrong immutable snapshot")


def _select_claim_graph(
    snapshot: InboundAnalysisSnapshot,
    supporting_ids: Sequence[str],
    counter_ids: Sequence[str],
) -> tuple[tuple[Claim, ...], tuple[Evidence, ...]]:
    if len(supporting_ids) != len(set(supporting_ids)) or len(counter_ids) != len(set(counter_ids)):
        raise LiveAnalysisError("model citation identifiers must be unique")
    if set(supporting_ids) & set(counter_ids):
        raise LiveAnalysisError("a Claim cannot support and counter the same axis read")
    selected_ids = set(supporting_ids) | set(counter_ids)
    claim_by_id = {item.claim_id: item for item in snapshot.claims}
    if not selected_ids.issubset(claim_by_id):
        raise LiveAnalysisError("model cited a Claim outside the immutable snapshot")
    selected = tuple(item for item in snapshot.claims if item.claim_id in selected_ids)
    if any(
        item.status is not ClaimStatus.SUPPORTED or not item.supporting_evidence_ids
        for item in selected
    ):
        raise LiveAnalysisError("axis findings may cite only supported canonical Claims")
    evidence_ids = {
        evidence_id
        for item in selected
        for evidence_id in (*item.supporting_evidence_ids, *item.counter_evidence_ids)
    }
    evidence = tuple(item for item in snapshot.evidence if item.evidence_id in evidence_ids)
    if len(evidence) != len(evidence_ids):
        raise LiveAnalysisError("selected Claim evidence is missing from the snapshot")
    return selected, evidence


def _require_directional_support(rating_is_unknown: bool, supporting_ids: Sequence[str]) -> None:
    if not rating_is_unknown and not supporting_ids:
        raise LiveAnalysisError("a directional analysis requires a supported Claim")


class OpenAIInboundAnalysisAdapter:
    """One strict OpenAI-backed implementation satisfying all five neutral ports."""

    def __init__(
        self,
        reasoner: Reasoner,
        resolver: AnalysisSnapshotResolver,
        *,
        clock: Callable[[], datetime],
        max_input_bytes: int = 200_000,
    ) -> None:
        if max_input_bytes < 1:
            raise ValueError("max_input_bytes must be positive")
        self._reasoner = reasoner
        self._resolver = resolver
        self._clock = clock
        self._max_input_bytes = max_input_bytes

    async def _load(self, request: AnalysisRequest) -> tuple[InboundAnalysisSnapshot, str]:
        snapshot = await self._resolver.resolve(request.input_snapshot_id)
        _validate_request_snapshot(request, snapshot)
        return snapshot, _proposal_snapshot_json(
            request, snapshot, max_input_bytes=self._max_input_bytes
        )

    async def analyze_market(self, request: AnalysisRequest) -> MarketAnalysisResult:
        snapshot, content = await self._load(request)
        proposal = await self._reasoner.extract(
            schema=_MarketProposal,
            instructions=(
                f"{_COMMON_INSTRUCTIONS}\nAssess market direction, sizing assumptions, "
                "competition, and SWOT. A non-unknown rating must cite supported Claims."
            ),
            content=content,
        )
        unknown = proposal.rating is MarketAxisRating.UNKNOWN
        _require_directional_support(unknown, proposal.supporting_claim_ids)
        claims, evidence = _select_claim_graph(
            snapshot, proposal.supporting_claim_ids, proposal.counter_claim_ids
        )
        axis_id = _derived_id("axis-market", request.request_id)
        return MarketAnalysisResult(
            header=_header(request),
            claims=claims,
            evidence=evidence,
            market_read=MarketAxisAssessment(
                assessment_id=axis_id,
                assessment_version_id=f"{axis_id}.v1",
                rubric_version="live-market-axis.v1",
                rating=proposal.rating,
                trend=proposal.trend,
                confidence=_known_confidence(proposal.confidence, rating_is_unknown=unknown),
                coverage=snapshot.coverage,
                supporting_claim_ids=proposal.supporting_claim_ids,
                counter_claim_ids=proposal.counter_claim_ids,
                open_questions=proposal.open_questions,
            ),
        )

    async def analyze_idea_novelty(self, request: AnalysisRequest) -> IdeaNoveltyAnalysisResult:
        snapshot, content = await self._load(request)
        proposal = await self._reasoner.extract(
            schema=_IdeaProposal,
            instructions=(
                f"{_COMMON_INSTRUCTIONS}\nAssess problem/product coherence, novelty, "
                "quality, defensibility, and viability separately from market size."
            ),
            content=content,
        )
        unknown = proposal.rating is IdeaVsMarketAxisRating.UNKNOWN
        _require_directional_support(unknown, proposal.supporting_claim_ids)
        claims, evidence = _select_claim_graph(
            snapshot, proposal.supporting_claim_ids, proposal.counter_claim_ids
        )
        axis_id = _derived_id("axis-idea", request.request_id)
        return IdeaNoveltyAnalysisResult(
            header=_header(request),
            claims=claims,
            evidence=evidence,
            idea_read=IdeaVsMarketAxisAssessment(
                assessment_id=axis_id,
                assessment_version_id=f"{axis_id}.v1",
                rubric_version="live-idea-axis.v1",
                rating=proposal.rating,
                trend=proposal.trend,
                confidence=_known_confidence(proposal.confidence, rating_is_unknown=unknown),
                coverage=snapshot.coverage,
                supporting_claim_ids=proposal.supporting_claim_ids,
                counter_claim_ids=proposal.counter_claim_ids,
                open_questions=proposal.open_questions,
            ),
        )

    async def analyze_founder_dossier(
        self, request: AnalysisRequest
    ) -> FounderDossierAnalysisResult:
        snapshot, content = await self._load(request)
        proposal = await self._reasoner.extract(
            schema=_FounderProposal,
            instructions=(
                f"{_COMMON_INSTRUCTIONS}\nAssess only evidence-backed skills, shipped "
                "milestones, consistency, and founder-market fit. Never use appearance, "
                "accent, name, charisma, presentation polish, reach, pedigree, a protected "
                "trait, or socioeconomic proxy as founder quality."
            ),
            content=content,
        )
        generated_text = " ".join(proposal.open_questions)
        if _PROHIBITED_FOUNDER_PROXY.search(generated_text):
            raise LiveAnalysisError("founder proposal contains a prohibited presentation proxy")
        unknown = proposal.rating is FounderAxisRating.UNKNOWN
        _require_directional_support(unknown, proposal.supporting_claim_ids)
        claims, evidence = _select_claim_graph(
            snapshot, proposal.supporting_claim_ids, proposal.counter_claim_ids
        )
        cited_support = set(proposal.supporting_claim_ids)
        if any(
            _PROHIBITED_FOUNDER_PROXY.search(f"{item.predicate} {item.statement}")
            for item in claims
            if item.claim_id in cited_support
        ):
            raise LiveAnalysisError("founder quality may not be supported by a proxy Claim")
        builder = builder_signal_read(snapshot.founder_observations)
        fundability = fundability_read(snapshot.founder_observations)
        axis_id = _derived_id("axis-founder", request.request_id)
        return FounderDossierAnalysisResult(
            header=_header(request),
            claims=claims,
            evidence=evidence,
            founder_read=FounderAxisAssessment(
                assessment_id=axis_id,
                assessment_version_id=f"{axis_id}.v1",
                rubric_version="live-founder-axis.v1",
                rating=proposal.rating,
                trend=proposal.trend,
                confidence=_known_confidence(proposal.confidence, rating_is_unknown=unknown),
                coverage=snapshot.coverage,
                supporting_claim_ids=proposal.supporting_claim_ids,
                counter_claim_ids=proposal.counter_claim_ids,
                open_questions=proposal.open_questions,
            ),
            builder_read=builder,
            fundability_read=fundability,
            gap=builder_fundability_gap(builder, fundability),
        )

    async def validate(self, request: AnalysisRequest) -> AdversarialValidationResult:
        snapshot, content = await self._load(request)
        proposal = await self._reasoner.extract(
            schema=_AdversarialProposal,
            instructions=(
                f"{_COMMON_INSTRUCTIONS}\nCross-examine the accepted graph. Return every "
                "canonical Contradiction identifier already evidenced in the snapshot; do "
                "not manufacture a new accepted contradiction. Surface the smallest useful "
                "questions for unsupported, stale, fragile, or conflicting inputs."
            ),
            content=content,
        )
        canonical = {item.contradiction_id for item in snapshot.contradictions}
        if set(proposal.contradiction_ids) != canonical or len(proposal.contradiction_ids) != len(
            canonical
        ):
            raise LiveAnalysisError(
                "adversarial output must preserve the canonical Contradiction inventory"
            )
        confidence = (
            _unknown_confidence("Adversarial confidence was not returned.")
            if proposal.confidence is None
            else KnowledgeValue[Confidence].known(proposal.confidence)
        )
        return AdversarialValidationResult(
            header=_header(request),
            claims=snapshot.claims,
            evidence=snapshot.evidence,
            contradictions=snapshot.contradictions,
            confidence=confidence,
            open_questions=proposal.open_questions,
        )

    async def synthesize_memo(self, request: AnalysisRequest) -> MemoSynthesisResult:
        if request.mode is not AssessmentMode.FULL:
            raise LiveAnalysisError("memo synthesis is available only for full assessment")
        snapshot, content = await self._load(request)
        proposal = await self._reasoner.extract(
            schema=_MemoProposal,
            instructions=(
                f"{_COMMON_INSTRUCTIONS}\nWrite exactly the five required memo sections: "
                "company_snapshot, investment_hypotheses, swot, problem_and_product, and "
                "traction_and_kpis. Every factual section and substantive Recommendation "
                "reason must cite supported Claims. Unknown sections must state a gap and "
                "the evidence needed. A Recommendation is advisory and is not a Decision."
            ),
            content=content,
        )
        section_kinds = tuple(item.kind for item in proposal.sections)
        if (
            len(section_kinds) != len(set(section_kinds))
            or set(section_kinds) != REQUIRED_MEMO_SECTIONS
        ):
            raise LiveAnalysisError("memo proposal must contain each required section once")
        contradiction_ids = {item.contradiction_id for item in snapshot.contradictions}
        if not set(proposal.contradiction_ids).issubset(contradiction_ids):
            raise LiveAnalysisError("memo cited a Contradiction outside the snapshot")
        section_claim_ids = tuple(
            claim_id for section in proposal.sections for claim_id in section.material_claim_ids
        )
        reason_claim_ids = tuple(
            claim_id for reason in proposal.recommendation.reasons for claim_id in reason.claim_ids
        )
        material_ids = tuple(dict.fromkeys((*section_claim_ids, *reason_claim_ids)))
        material, evidence = _select_claim_graph(snapshot, material_ids, ())
        if proposal.recommendation.action not in {
            RecommendationAction.NEEDS_INFORMATION,
            RecommendationAction.MANUAL_REVIEW,
        } and any(not item.claim_ids for item in proposal.recommendation.reasons):
            raise LiveAnalysisError("a substantive Recommendation reason requires Claims")
        evidence_by_claim: dict[str, tuple[str, ...]] = {
            claim.claim_id: claim.supporting_evidence_ids for claim in material
        }
        sections = []
        for section in proposal.sections:
            section_evidence = tuple(
                dict.fromkeys(
                    evidence_id
                    for claim_id in section.material_claim_ids
                    for evidence_id in evidence_by_claim[claim_id]
                )
            )
            if section.content is None:
                assert section.gap_reason is not None
                sections.append(
                    MemoSection(
                        kind=section.kind,
                        content=KnowledgeValue[str].unknown(section.gap_reason),
                    )
                )
            else:
                sections.append(
                    MemoSection(
                        kind=section.kind,
                        content=KnowledgeValue[str].known(
                            section.content, evidence_ids=section_evidence
                        ),
                        material_claim_ids=section.material_claim_ids,
                    )
                )
        now = self._clock()
        if now < snapshot.memo_identity.evidence_as_of:
            raise LiveAnalysisError("memo clock predates its immutable evidence snapshot")
        memo_id = _derived_id("memo", request.request_id)
        recommendation_id = _derived_id("recommendation", request.request_id)
        memo = InvestmentMemo(
            memo_id=memo_id,
            memo_version_id=f"{memo_id}.v1",
            opportunity_id=snapshot.memo_identity.opportunity_id,
            screening_case_id=snapshot.memo_identity.screening_case_id,
            assessment_id=snapshot.memo_identity.assessment_id,
            run_id=snapshot.memo_identity.run_id,
            thesis_version=snapshot.memo_identity.thesis_version,
            evidence_as_of=snapshot.memo_identity.evidence_as_of,
            generated_at=now,
            sections=tuple(sections),
        )
        recommendation = Recommendation(
            recommendation_id=recommendation_id,
            recommendation_version_id=f"{recommendation_id}.v1",
            subject=request.subject,
            assessment_id=snapshot.memo_identity.assessment_id,
            policy_version="live-recommendation.v1",
            action=proposal.recommendation.action,
            reasons=tuple(
                RecommendationReason(summary=item.summary, claim_ids=item.claim_ids)
                for item in proposal.recommendation.reasons
            ),
            next_actions=proposal.recommendation.next_actions,
            created_at=now,
        )
        confidence = (
            _unknown_confidence("Memo confidence was not returned.")
            if proposal.confidence is None
            else KnowledgeValue[Confidence].known(proposal.confidence)
        )
        return MemoSynthesisResult(
            header=_header(request),
            memo=memo,
            recommendation=recommendation,
            material_claims=material,
            evidence=evidence,
            confidence=confidence,
            open_questions=proposal.open_questions,
        )


class DeterministicInboundFallbackAdapter:
    """No-network, no-fact-invention fallback implementing all five ports."""

    def __init__(
        self,
        resolver: AnalysisSnapshotResolver,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._resolver = resolver
        self._clock = clock

    async def _snapshot(self, request: AnalysisRequest) -> InboundAnalysisSnapshot:
        snapshot = await self._resolver.resolve(request.input_snapshot_id)
        _validate_request_snapshot(request, snapshot)
        return snapshot

    async def analyze_market(self, request: AnalysisRequest) -> MarketAnalysisResult:
        snapshot = await self._snapshot(request)
        axis_id = _derived_id("axis-market-fallback", request.request_id)
        return MarketAnalysisResult(
            header=_header(request),
            claims=(),
            evidence=(),
            market_read=MarketAxisAssessment(
                assessment_id=axis_id,
                assessment_version_id=f"{axis_id}.v1",
                rubric_version="deterministic-fallback.v1",
                rating=MarketAxisRating.UNKNOWN,
                trend=Trend.UNKNOWN,
                confidence=_unknown_confidence("Live market analysis was unavailable."),
                coverage=snapshot.coverage,
                open_questions=("What evidence supports a directional market read?",),
            ),
        )

    async def analyze_idea_novelty(self, request: AnalysisRequest) -> IdeaNoveltyAnalysisResult:
        snapshot = await self._snapshot(request)
        axis_id = _derived_id("axis-idea-fallback", request.request_id)
        return IdeaNoveltyAnalysisResult(
            header=_header(request),
            claims=(),
            evidence=(),
            idea_read=IdeaVsMarketAxisAssessment(
                assessment_id=axis_id,
                assessment_version_id=f"{axis_id}.v1",
                rubric_version="deterministic-fallback.v1",
                rating=IdeaVsMarketAxisRating.UNKNOWN,
                trend=Trend.UNKNOWN,
                confidence=_unknown_confidence("Live idea analysis was unavailable."),
                coverage=snapshot.coverage,
                open_questions=("What evidence supports novelty and product quality?",),
            ),
        )

    async def analyze_founder_dossier(
        self, request: AnalysisRequest
    ) -> FounderDossierAnalysisResult:
        snapshot = await self._snapshot(request)
        builder = builder_signal_read(snapshot.founder_observations)
        fundability = fundability_read(snapshot.founder_observations)
        axis_id = _derived_id("axis-founder-fallback", request.request_id)
        return FounderDossierAnalysisResult(
            header=_header(request),
            claims=(),
            evidence=(),
            founder_read=FounderAxisAssessment(
                assessment_id=axis_id,
                assessment_version_id=f"{axis_id}.v1",
                rubric_version="deterministic-fallback.v1",
                rating=FounderAxisRating.UNKNOWN,
                trend=Trend.UNKNOWN,
                confidence=_unknown_confidence("Live founder analysis was unavailable."),
                coverage=snapshot.coverage,
                open_questions=("What evidence demonstrates costly-to-fake builder substance?",),
            ),
            builder_read=builder,
            fundability_read=fundability,
            gap=builder_fundability_gap(builder, fundability),
        )

    async def validate(self, request: AnalysisRequest) -> AdversarialValidationResult:
        snapshot = await self._snapshot(request)
        return AdversarialValidationResult(
            header=_header(request),
            claims=snapshot.claims,
            evidence=snapshot.evidence,
            contradictions=snapshot.contradictions,
            confidence=_unknown_confidence("Live adversarial validation was unavailable."),
            open_questions=("Which unsupported or conflicting Claims require review?",),
        )

    async def synthesize_memo(self, request: AnalysisRequest) -> MemoSynthesisResult:
        if request.mode is not AssessmentMode.FULL:
            raise LiveAnalysisError("memo synthesis is available only for full assessment")
        snapshot = await self._snapshot(request)
        now = self._clock()
        if now < snapshot.memo_identity.evidence_as_of:
            raise LiveAnalysisError("memo clock predates its immutable evidence snapshot")
        sections = tuple(
            MemoSection(
                kind=kind,
                content=KnowledgeValue[str].unknown(
                    "Live synthesis was unavailable; no factual memo prose was accepted."
                ),
            )
            for kind in sorted(REQUIRED_MEMO_SECTIONS, key=lambda item: item.value)
        )
        memo_id = _derived_id("memo-fallback", request.request_id)
        recommendation_id = _derived_id("recommendation-fallback", request.request_id)
        return MemoSynthesisResult(
            header=_header(request),
            memo=InvestmentMemo(
                memo_id=memo_id,
                memo_version_id=f"{memo_id}.v1",
                opportunity_id=snapshot.memo_identity.opportunity_id,
                screening_case_id=snapshot.memo_identity.screening_case_id,
                assessment_id=snapshot.memo_identity.assessment_id,
                run_id=snapshot.memo_identity.run_id,
                thesis_version=snapshot.memo_identity.thesis_version,
                evidence_as_of=snapshot.memo_identity.evidence_as_of,
                generated_at=now,
                sections=sections,
            ),
            recommendation=Recommendation(
                recommendation_id=recommendation_id,
                recommendation_version_id=f"{recommendation_id}.v1",
                subject=request.subject,
                assessment_id=snapshot.memo_identity.assessment_id,
                policy_version="deterministic-fallback.v1",
                action=RecommendationAction.NEEDS_INFORMATION,
                reasons=(
                    RecommendationReason(
                        summary=(
                            "Live synthesis failed closed; human review requires more information."
                        )
                    ),
                ),
                next_actions=("Review the accepted Claims and Evidence manually.",),
                created_at=now,
            ),
            material_claims=(),
            evidence=(),
            confidence=_unknown_confidence("Live memo synthesis was unavailable."),
            open_questions=("Which evidence is required before a recommendation can be reviewed?",),
        )


__all__ = [
    "AnalysisSnapshotResolver",
    "DeterministicInboundFallbackAdapter",
    "InMemoryAnalysisSnapshotResolver",
    "InboundAnalysisSnapshot",
    "LiveAnalysisError",
    "MemoIdentity",
    "OpenAIInboundAnalysisAdapter",
    "Reasoner",
    "SnapshotNotFoundError",
]
