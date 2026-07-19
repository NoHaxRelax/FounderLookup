"""Framework- and model-neutral inbound analysis interfaces and deterministic fakes.

The inbound reasoner (a LangGraph graph added later behind these interfaces, backed by a
human-gated live model added later still) orchestrates five sub-analyses. This module
defines ONLY the neutral seam those pieces plug into: five ``runtime_checkable`` async
Protocols, one shared request, five frozen result containers that carry the frozen domain
objects (:class:`Claim`, :class:`Evidence`, the relevant axis assessment,
:class:`Contradiction`, or the memo), and one deterministic replay FAKE per Protocol.

Nothing here imports a model, LangGraph, or any framework, and nothing performs I/O,
randomness, or wall-clock reads. Swapping a live model in later must not change any of
these interfaces; the fakes are simply one deterministic implementation of them, mirroring
:class:`founderlookup.screening.fakes.FakeIntelligenceAdapter`.

The five analyses
-----------------
1. :class:`MarketAnalysisPort`        -> :class:`MarketAnalysisResult`
   (market claims plus a market read)
2. :class:`IdeaNoveltyAnalysisPort`   -> :class:`IdeaNoveltyAnalysisResult`
   (idea-versus-market claims plus a read)
3. :class:`FounderDossierAnalysisPort`-> :class:`FounderDossierAnalysisResult`
   (founder claims plus a founder read; honors the presentation-proxy prohibition)
4. :class:`AdversarialValidationPort` -> :class:`AdversarialValidationResult`
   (contradictions, unsupported-claim flags, external corroboration)
5. :class:`MemoSynthesisPort`         -> :class:`MemoSynthesisResult`
   (a cited memo plus a recommendation)

Every result carries a small :class:`AnalysisResultHeader` (the request identity it
answers) so a replayed fake can verify the seed describes the request it is asked for,
exactly as ``FakeIntelligenceAdapter`` cross-checks ``input_snapshot_id`` and ``mode``.

Honesty guarantees (enforced in ``__post_init__``, so they ARE the tests task 3.7 needs)
----------------------------------------------------------------------------------------
- Citation integrity. Every result validates its claim/evidence graph: each carried
  Evidence attaches to a carried Claim, and each Claim's supporting/counter evidence ids
  resolve to carried Evidence whose stance and ``claim_id`` match. A claim can never cite
  evidence that is missing, mis-stanced, or attached to a different claim.
- Unsupported claims. A claim with no supporting evidence is a frozen
  ``ClaimStatus.UNSUPPORTED`` with an ``UNSUPPORTED`` trust state (the frozen validator
  enforces the pairing). Adversarial validation surfaces such claims through
  :attr:`AdversarialValidationResult.unsupported_claims` instead of letting them pass as
  fact, and the memo synthesis result REJECTS any material claim that lacks supporting
  evidence.
- Contradictions. Conflicting claims produce a :class:`Contradiction` (>=2 claim ids,
  >=2 evidence ids). Adversarial validation carries the contradiction and requires every
  claim it names to be marked ``ClaimStatus.CONTRADICTED``, so a conflict can never be
  reported over claims left standing as supported.
- Presentation proxies (bias prohibition). The founder dossier read must not let
  presentation, polish, charisma, follower reach, or pedigree drive founder QUALITY. The
  result carries the builder-signal read (whose taxonomy gives every vanity signal a hard
  zero) alongside the founder axis read, and rejects a ``STRONG`` founder read that is not
  backed by a ``STRONG`` builder-substance band. An all-polish, no-substance founder can
  therefore never yield a strong founder read.
- Citation completeness. Every material claim referenced by a memo section resolves to a
  carried claim that carries supporting evidence; a memo whose material claim lacks
  evidence is rejected. The recommendation may only cite material claims, and no material
  claim may be left uncited.

Determinism
-----------
The fakes replay seeded, schema-valid results keyed by ``request_id`` only. Identical
requests yield identical results; there is no randomness, no wall clock beyond
caller-supplied timestamps baked into the seed, and no network.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from founderlookup.domain.assessment import (
    Confidence,
    Contradiction,
    FounderAxisAssessment,
    FounderAxisRating,
    IdeaVsMarketAxisAssessment,
    InvestmentMemo,
    MarketAxisAssessment,
    Recommendation,
)
from founderlookup.domain.common import (
    DomainModel,
    KnowledgeValue,
    StableId,
    SubjectRef,
)
from founderlookup.domain.evidence import Claim, ClaimStatus, Evidence, EvidenceStance
from founderlookup.domain.lifecycles import AssessmentMode
from founderlookup.screening.founder_reads import (
    BuilderFundabilityGap,
    FounderRead,
    ReadKind,
)
from founderlookup.screening.rubrics import FounderBand, classify_founder_band

# ======================================================================================
# Errors
# ======================================================================================


class AnalysisResultError(ValueError):
    """Raised when a structured analysis result violates an honesty invariant."""


class MissingFakeAnalysisError(LookupError):
    """Raised when no fixed analysis result exists for an analysis request."""


class InvalidFakeAnalysisError(ValueError):
    """Raised when seeded analysis result data does not describe its request."""


# ======================================================================================
# Shared request and result header
# ======================================================================================


class AnalysisRequest(DomainModel):
    """Immutable pointer to the canonical snapshot one inbound analysis may read.

    Mirrors :class:`founderlookup.screening.ports.IntelligenceRequest` field-for-field so a
    live model swaps in behind the analysis Ports exactly as it does behind the intelligence
    Port. It is a pointer to the snapshot, never the snapshot itself.
    """

    request_id: StableId
    input_snapshot_id: StableId
    subject: SubjectRef
    mode: AssessmentMode


@dataclass(frozen=True)
class AnalysisResultHeader:
    """The request identity one analysis result answers.

    Carried by every result so a replayed fake can confirm the seed was authored for the
    request it is asked to answer, the analysis analogue of the ``input_snapshot_id`` and
    ``mode`` cross-check in ``FakeIntelligenceAdapter``.
    """

    request_id: StableId
    input_snapshot_id: StableId
    subject: SubjectRef
    mode: AssessmentMode


def _describes(header: AnalysisResultHeader, request: AnalysisRequest) -> bool:
    """Whether a seeded result's header matches the request being replayed."""
    return (
        header.request_id == request.request_id
        and header.input_snapshot_id == request.input_snapshot_id
        and header.subject == request.subject
        and header.mode == request.mode
    )


# ======================================================================================
# Shared claim/evidence graph validation
# ======================================================================================


def _require_unique(ids: Sequence[StableId], label: str) -> None:
    """Reject duplicate identifiers in a carried collection."""
    seen: set[str] = set()
    for identifier in ids:
        if identifier in seen:
            raise AnalysisResultError(f"duplicate {label}: {identifier}")
        seen.add(identifier)


def _check_evidence_side(
    claim: Claim,
    evidence_ids: Sequence[StableId],
    stance: EvidenceStance,
    evidence_by_id: Mapping[str, Evidence],
    label: str,
) -> None:
    """Validate one side of a claim's citations resolves to matching carried evidence."""
    for evidence_id in evidence_ids:
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            raise AnalysisResultError(
                f"claim {claim.claim_id} cites missing {label} evidence {evidence_id}"
            )
        if evidence.claim_id != claim.claim_id:
            raise AnalysisResultError(
                f"{label} evidence {evidence_id} does not attach to claim {claim.claim_id}"
            )
        if evidence.stance is not stance:
            raise AnalysisResultError(
                f"{label} evidence {evidence_id} has stance {evidence.stance.value}, "
                f"expected {stance.value}"
            )


def _validate_claim_citations(
    claims: Sequence[Claim],
    evidence: Sequence[Evidence],
) -> None:
    """Validate the carried claim/evidence graph is internally closed and coherent.

    Every carried Evidence must attach to a carried Claim, and every Claim's supporting and
    counter evidence ids must resolve to carried Evidence whose ``claim_id`` and stance
    match. This is the shared backbone of the unsupported-claim and citation-completeness
    guarantees: a claim can never reference evidence that is missing or mis-stanced.
    """
    _require_unique([claim.claim_id for claim in claims], "claim id")
    _require_unique([item.evidence_id for item in evidence], "evidence id")
    claim_ids = {claim.claim_id for claim in claims}
    evidence_by_id = {item.evidence_id: item for item in evidence}
    for item in evidence:
        if item.claim_id not in claim_ids:
            raise AnalysisResultError(
                f"evidence {item.evidence_id} attaches to unknown claim {item.claim_id}"
            )
    for claim in claims:
        _check_evidence_side(
            claim, claim.supporting_evidence_ids, EvidenceStance.SUPPORTS, evidence_by_id,
            "supporting",
        )
        _check_evidence_side(
            claim, claim.counter_evidence_ids, EvidenceStance.REFUTES, evidence_by_id,
            "counter",
        )


def _validate_axis_claims(
    supporting_claim_ids: Sequence[StableId],
    counter_claim_ids: Sequence[StableId],
    claim_ids: frozenset[str],
    axis_label: str,
) -> None:
    """Validate an axis read only routes claims the result actually carries."""
    referenced = set(supporting_claim_ids) | set(counter_claim_ids)
    missing = referenced - claim_ids
    if missing:
        joined = ", ".join(sorted(missing))
        raise AnalysisResultError(
            f"{axis_label} read routes claims not carried by the result: {joined}"
        )


def _require_scored_band(read: FounderRead, label: str) -> None:
    """Reject a founder read whose band does not match the band its score classifies to.

    ``FounderRead.band`` is a plain field with no validator tying it to ``score``, so a live
    model behind the port could forge a strong band onto a weak score. Requiring
    ``band == classify_founder_band(score)`` makes a carried read unable to misrepresent its
    own strength, which is what the presentation-proxy guard relies on.
    """
    expected = classify_founder_band(read.score)
    if read.band is not expected:
        raise AnalysisResultError(
            f"{label} band {read.band.value} does not match its score {read.score} "
            f"(expected {expected.value})"
        )


# ======================================================================================
# Result containers
# ======================================================================================


@dataclass(frozen=True)
class MarketAnalysisResult:
    """Market claims, their evidence, and a market read over them.

    ``confidence`` and ``open_questions`` are read straight off ``market_read`` so the
    result never disagrees with the axis it carries: the axis is the single source of truth.
    """

    header: AnalysisResultHeader
    claims: tuple[Claim, ...]
    evidence: tuple[Evidence, ...]
    market_read: MarketAxisAssessment

    def __post_init__(self) -> None:
        _validate_claim_citations(self.claims, self.evidence)
        _validate_axis_claims(
            self.market_read.supporting_claim_ids,
            self.market_read.counter_claim_ids,
            frozenset(claim.claim_id for claim in self.claims),
            "market",
        )

    @property
    def confidence(self) -> KnowledgeValue[Confidence]:
        """Headline confidence, taken from the carried market read."""
        return self.market_read.confidence

    @property
    def open_questions(self) -> tuple[str, ...]:
        """Open questions, taken from the carried market read."""
        return self.market_read.open_questions


@dataclass(frozen=True)
class IdeaNoveltyAnalysisResult:
    """Idea-versus-market claims, their evidence, and an idea novelty/quality read.

    ``confidence`` and ``open_questions`` are read straight off ``idea_read`` so the result
    never disagrees with the axis it carries.
    """

    header: AnalysisResultHeader
    claims: tuple[Claim, ...]
    evidence: tuple[Evidence, ...]
    idea_read: IdeaVsMarketAxisAssessment

    def __post_init__(self) -> None:
        _validate_claim_citations(self.claims, self.evidence)
        _validate_axis_claims(
            self.idea_read.supporting_claim_ids,
            self.idea_read.counter_claim_ids,
            frozenset(claim.claim_id for claim in self.claims),
            "idea_vs_market",
        )

    @property
    def confidence(self) -> KnowledgeValue[Confidence]:
        """Headline confidence, taken from the carried idea read."""
        return self.idea_read.confidence

    @property
    def open_questions(self) -> tuple[str, ...]:
        """Open questions, taken from the carried idea read."""
        return self.idea_read.open_questions


@dataclass(frozen=True)
class FounderDossierAnalysisResult:
    """Founder claims, their evidence, a founder read, and the builder/fundability lenses.

    The bias prohibition lives here. ``builder_read`` is the substance lens whose taxonomy
    gives every vanity signal (follower reach, pedigree, presentation polish, team size) a
    hard-zero weight, so no amount of polish can lift it. A ``STRONG`` founder read is
    rejected unless the builder-substance band, derived from the builder score rather than
    trusted off the (forgeable) band field, is ``STRONG``, so an all-polish, no-substance
    founder can never produce a strong founder read. ``fundability_read`` and ``gap`` carry
    the conventional-VC lens and the signed divergence so the reviewer sees the gap, never a
    single blended number. ``confidence`` and ``open_questions`` come from ``founder_read``.
    """

    header: AnalysisResultHeader
    claims: tuple[Claim, ...]
    evidence: tuple[Evidence, ...]
    founder_read: FounderAxisAssessment
    builder_read: FounderRead
    fundability_read: FounderRead
    gap: BuilderFundabilityGap

    def __post_init__(self) -> None:
        _validate_claim_citations(self.claims, self.evidence)
        _validate_axis_claims(
            self.founder_read.supporting_claim_ids,
            self.founder_read.counter_claim_ids,
            frozenset(claim.claim_id for claim in self.claims),
            "founder",
        )
        if self.builder_read.kind is not ReadKind.BUILDER_SIGNAL:
            raise AnalysisResultError("builder_read must be a builder-signal read")
        if self.fundability_read.kind is not ReadKind.FUNDABILITY:
            raise AnalysisResultError("fundability_read must be a fundability read")
        if self.gap.builder_score != self.builder_read.score:
            raise AnalysisResultError("gap builder score must match the builder read")
        if self.gap.fundability_score != self.fundability_read.score:
            raise AnalysisResultError("gap fundability score must match the fundability read")
        # A carried read may not lie about its own band: reject a band that does not match
        # the score it classifies to, so a forged strong band cannot slip past the guard.
        _require_scored_band(self.builder_read, "builder_read")
        _require_scored_band(self.fundability_read, "fundability_read")
        # Presentation-proxy guard: a strong founder QUALITY read must be earned by strong,
        # costly-to-fake building SUBSTANCE, never by presentation, pedigree, or reach. The
        # substance band is DERIVED from the builder score, never trusted off the field, so a
        # forged band cannot manufacture a strong read.
        if (
            self.founder_read.rating is FounderAxisRating.STRONG
            and classify_founder_band(self.builder_read.score) is not FounderBand.STRONG
        ):
            raise AnalysisResultError(
                "a strong founder read requires strong builder substance, not presentation"
            )

    @property
    def confidence(self) -> KnowledgeValue[Confidence]:
        """Headline confidence, taken from the carried founder read."""
        return self.founder_read.confidence

    @property
    def open_questions(self) -> tuple[str, ...]:
        """Open questions, taken from the carried founder read."""
        return self.founder_read.open_questions


@dataclass(frozen=True)
class AdversarialValidationResult:
    """A cross-examination of a claim/evidence graph: contradictions and weak spots.

    Carries the reviewed claims, their full evidence graph (supporting, refuting, and
    context stances), and any detected contradictions. Every claim named by a carried
    contradiction must be marked ``ClaimStatus.CONTRADICTED``, so a conflict can never be
    reported over claims left standing. The convenience properties surface the unsupported
    claims, the contradicted claims, and the external corroborating evidence without storing
    them redundantly, so the derived views can never drift from the claims themselves.
    """

    header: AnalysisResultHeader
    claims: tuple[Claim, ...]
    evidence: tuple[Evidence, ...]
    contradictions: tuple[Contradiction, ...]
    confidence: KnowledgeValue[Confidence]
    open_questions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_claim_citations(self.claims, self.evidence)
        claim_by_id = {claim.claim_id: claim for claim in self.claims}
        evidence_ids = {item.evidence_id for item in self.evidence}
        for contradiction in self.contradictions:
            for claim_id in contradiction.claim_ids:
                claim = claim_by_id.get(claim_id)
                if claim is None:
                    raise AnalysisResultError(
                        f"contradiction {contradiction.contradiction_id} names unknown "
                        f"claim {claim_id}"
                    )
                if claim.status is not ClaimStatus.CONTRADICTED:
                    raise AnalysisResultError(
                        f"claim {claim_id} in contradiction "
                        f"{contradiction.contradiction_id} must be marked CONTRADICTED"
                    )
            for evidence_id in contradiction.evidence_ids:
                if evidence_id not in evidence_ids:
                    raise AnalysisResultError(
                        f"contradiction {contradiction.contradiction_id} cites evidence "
                        f"{evidence_id} the result does not carry"
                    )

    @property
    def unsupported_claims(self) -> tuple[Claim, ...]:
        """Reviewed claims that carry no supporting evidence, surfaced rather than passed
        off as fact.

        Evidence-based, not status-based: any carried claim without supporting evidence is
        surfaced here, a frozen UNSUPPORTED claim or an as-yet-unverified assertion alike, so
        an evidence-free claim can never hide behind a softer status and escape the surface.
        A claim can only stay off this list by actually carrying supporting evidence.
        """
        return tuple(c for c in self.claims if not c.supporting_evidence_ids)

    @property
    def contradicted_claims(self) -> tuple[Claim, ...]:
        """Reviewed claims a detected contradiction marked contradicted."""
        return tuple(c for c in self.claims if c.status is ClaimStatus.CONTRADICTED)

    @property
    def corroborated_claims(self) -> tuple[Claim, ...]:
        """Reviewed claims that carry supporting evidence and stand as supported."""
        return tuple(c for c in self.claims if c.status is ClaimStatus.SUPPORTED)

    @property
    def corroborating_evidence(self) -> tuple[Evidence, ...]:
        """External corroboration: the carried evidence whose stance supports a claim."""
        return tuple(e for e in self.evidence if e.stance is EvidenceStance.SUPPORTS)


@dataclass(frozen=True)
class MemoSynthesisResult:
    """A cited investment memo and a recommendation, with citation completeness enforced.

    Every carried material claim must carry supporting evidence, so a memo whose material
    claim lacks evidence is rejected no matter whether a section or the recommendation is
    what cites it. Every material claim a memo section references must resolve to a carried
    material claim, the recommendation may only cite material claims, and no carried material
    claim may be left uncited, so the memo, its citations, and the recommendation always
    describe the same evidence-backed claims.
    """

    header: AnalysisResultHeader
    memo: InvestmentMemo
    recommendation: Recommendation
    material_claims: tuple[Claim, ...]
    evidence: tuple[Evidence, ...]
    confidence: KnowledgeValue[Confidence]
    open_questions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_claim_citations(self.material_claims, self.evidence)
        material_by_id = {claim.claim_id: claim for claim in self.material_claims}
        # Strict citation completeness: every material claim the memo leans on carries
        # supporting evidence. Checked over the whole material set, not just section-cited
        # claims, so an unsupported claim cannot ride in through the recommendation.
        for claim in self.material_claims:
            if not claim.supporting_evidence_ids:
                raise AnalysisResultError(
                    f"material claim {claim.claim_id} lacks supporting evidence"
                )
        cited: set[str] = set()
        for section in self.memo.sections:
            for claim_id in section.material_claim_ids:
                if claim_id not in material_by_id:
                    raise AnalysisResultError(
                        f"memo section {section.kind.value} cites claim {claim_id} that is "
                        "not carried as a material claim"
                    )
                cited.add(claim_id)
        for reason in self.recommendation.reasons:
            for claim_id in reason.claim_ids:
                if claim_id not in material_by_id:
                    raise AnalysisResultError(
                        f"recommendation cites claim {claim_id} that is not a material claim"
                    )
                cited.add(claim_id)
        orphans = set(material_by_id) - cited
        if orphans:
            joined = ", ".join(sorted(orphans))
            raise AnalysisResultError(
                f"material claims are carried but cited by no section or recommendation: "
                f"{joined}"
            )


# ======================================================================================
# Ports
# ======================================================================================


@runtime_checkable
class MarketAnalysisPort(Protocol):
    """Produce market claims and a market read from an immutable input snapshot."""

    async def analyze_market(self, request: AnalysisRequest) -> MarketAnalysisResult:
        """Return a proposed market analysis without mutating Memory."""
        ...


@runtime_checkable
class IdeaNoveltyAnalysisPort(Protocol):
    """Produce idea novelty/quality claims and an idea-versus-market read."""

    async def analyze_idea_novelty(
        self, request: AnalysisRequest
    ) -> IdeaNoveltyAnalysisResult:
        """Return a proposed idea novelty/quality analysis without mutating Memory."""
        ...


@runtime_checkable
class FounderDossierAnalysisPort(Protocol):
    """Produce founder claims and a founder read that honors the bias prohibition."""

    async def analyze_founder_dossier(
        self, request: AnalysisRequest
    ) -> FounderDossierAnalysisResult:
        """Return a proposed founder dossier analysis without mutating Memory."""
        ...


@runtime_checkable
class AdversarialValidationPort(Protocol):
    """Detect contradictions, flag unsupported claims, and surface external corroboration."""

    async def validate(self, request: AnalysisRequest) -> AdversarialValidationResult:
        """Return a proposed adversarial validation without mutating Memory."""
        ...


@runtime_checkable
class MemoSynthesisPort(Protocol):
    """Synthesize a cited investment memo and a recommendation."""

    async def synthesize_memo(self, request: AnalysisRequest) -> MemoSynthesisResult:
        """Return a proposed memo synthesis without mutating Memory."""
        ...


# ======================================================================================
# Deterministic replay fakes
# ======================================================================================


class _CarriesHeader(Protocol):
    """Structural bound for any result that carries an :class:`AnalysisResultHeader`."""

    @property
    def header(self) -> AnalysisResultHeader: ...


class _AnalysisReplayAdapter[ResultT: _CarriesHeader]:
    """Shared seeded-replay mechanics for every analysis fake.

    Mirrors :class:`founderlookup.screening.fakes.FakeIntelligenceAdapter`: fixed results
    are keyed by ``request_id``, observed requests are recorded in call order, an unseeded
    request raises :class:`MissingFakeAnalysisError`, and a seed whose header does not
    describe the request raises :class:`InvalidFakeAnalysisError`. No model, no framework,
    no randomness, no network: identical requests replay identical results.
    """

    def __init__(self, responses: Mapping[str, ResultT]) -> None:
        self._responses: dict[str, ResultT] = dict(responses)
        self._requests: list[AnalysisRequest] = []

    @property
    def requests(self) -> tuple[AnalysisRequest, ...]:
        """Requests observed by this adapter, in call order."""
        return tuple(self._requests)

    def _resolve(self, request: AnalysisRequest) -> ResultT:
        """Return the fixed result keyed by ``request.request_id`` if it matches."""
        self._requests.append(request)
        try:
            result = self._responses[request.request_id]
        except KeyError as error:
            raise MissingFakeAnalysisError(
                f"No fake analysis result for request {request.request_id!r}"
            ) from error
        if not _describes(result.header, request):
            raise InvalidFakeAnalysisError(
                f"Fake analysis result for request {request.request_id!r} does not describe it"
            )
        return result


class FakeMarketAnalysisAdapter(_AnalysisReplayAdapter[MarketAnalysisResult]):
    """Replay schema-valid market analyses without selecting a model or framework."""

    async def analyze_market(self, request: AnalysisRequest) -> MarketAnalysisResult:
        """Return the fixed market analysis keyed by ``request.request_id``."""
        return self._resolve(request)


class FakeIdeaNoveltyAnalysisAdapter(_AnalysisReplayAdapter[IdeaNoveltyAnalysisResult]):
    """Replay schema-valid idea novelty/quality analyses without a model or framework."""

    async def analyze_idea_novelty(
        self, request: AnalysisRequest
    ) -> IdeaNoveltyAnalysisResult:
        """Return the fixed idea novelty analysis keyed by ``request.request_id``."""
        return self._resolve(request)


class FakeFounderDossierAnalysisAdapter(
    _AnalysisReplayAdapter[FounderDossierAnalysisResult]
):
    """Replay schema-valid founder dossier analyses without a model or framework."""

    async def analyze_founder_dossier(
        self, request: AnalysisRequest
    ) -> FounderDossierAnalysisResult:
        """Return the fixed founder dossier analysis keyed by ``request.request_id``."""
        return self._resolve(request)


class FakeAdversarialValidationAdapter(
    _AnalysisReplayAdapter[AdversarialValidationResult]
):
    """Replay schema-valid adversarial validations without a model or framework."""

    async def validate(self, request: AnalysisRequest) -> AdversarialValidationResult:
        """Return the fixed adversarial validation keyed by ``request.request_id``."""
        return self._resolve(request)


class FakeMemoSynthesisAdapter(_AnalysisReplayAdapter[MemoSynthesisResult]):
    """Replay schema-valid memo syntheses without selecting a model or framework."""

    async def synthesize_memo(self, request: AnalysisRequest) -> MemoSynthesisResult:
        """Return the fixed memo synthesis keyed by ``request.request_id``."""
        return self._resolve(request)


__all__ = [
    "AdversarialValidationPort",
    "AdversarialValidationResult",
    "AnalysisRequest",
    "AnalysisResultError",
    "AnalysisResultHeader",
    "FakeAdversarialValidationAdapter",
    "FakeFounderDossierAnalysisAdapter",
    "FakeIdeaNoveltyAnalysisAdapter",
    "FakeMarketAnalysisAdapter",
    "FakeMemoSynthesisAdapter",
    "FounderDossierAnalysisPort",
    "FounderDossierAnalysisResult",
    "IdeaNoveltyAnalysisPort",
    "IdeaNoveltyAnalysisResult",
    "InvalidFakeAnalysisError",
    "MarketAnalysisPort",
    "MarketAnalysisResult",
    "MemoSynthesisPort",
    "MemoSynthesisResult",
    "MissingFakeAnalysisError",
]
