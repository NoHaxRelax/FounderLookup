"""Framework- and model-neutral screening boundaries."""

from typing import Protocol, Self, runtime_checkable

from pydantic import model_validator

from founderlookup.domain.assessment import AssessmentEnvelope
from founderlookup.domain.common import DomainModel, StableId, SubjectRef
from founderlookup.domain.evidence import Claim, Evidence
from founderlookup.domain.lifecycles import AssessmentMode
from founderlookup.screening.analysis import (
    AdversarialValidation,
    FounderDossierAnalysis,
    IdeaNoveltyQualityAnalysis,
    MarketAnalysis,
    MemoSynthesis,
)


class AnalysisRequest(DomainModel):
    """Immutable accepted Claims and Evidence read by one logical analyzer."""

    request_id: StableId
    input_snapshot_id: StableId
    subject: SubjectRef
    claims: tuple[Claim, ...] = ()
    evidence: tuple[Evidence, ...] = ()

    @model_validator(mode="after")
    def require_unique_input_records(self) -> Self:
        claim_ids = tuple(claim.claim_id for claim in self.claims)
        evidence_ids = tuple(item.evidence_id for item in self.evidence)
        if len(claim_ids) != len(set(claim_ids)) or len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Claim and Evidence identifiers must be unique")
        if any(item.claim_id not in claim_ids for item in self.evidence):
            raise ValueError("Evidence must reference an input Claim")
        return self


@runtime_checkable
class MarketAnalysisPort(Protocol):
    """Analyze market conditions without prescribing a model or framework."""

    async def analyze(self, request: AnalysisRequest) -> MarketAnalysis:
        """Return a structured market analysis for the immutable request."""
        ...


@runtime_checkable
class IdeaNoveltyQualityPort(Protocol):
    """Assess idea novelty and quality without prescribing orchestration."""

    async def analyze(self, request: AnalysisRequest) -> IdeaNoveltyQualityAnalysis:
        """Return separate, structured novelty and quality findings."""
        ...


@runtime_checkable
class FounderDossierPort(Protocol):
    """Assess founder evidence without presentation or identity proxies."""

    async def analyze(self, request: AnalysisRequest) -> FounderDossierAnalysis:
        """Return sourced builder and allowlisted presentation findings."""
        ...


@runtime_checkable
class AdversarialValidationPort(Protocol):
    """Surface corroboration, contradictions, unsupported Claims, and gaps."""

    async def validate(self, request: AnalysisRequest) -> AdversarialValidation:
        """Return the structured adversarial view of accepted snapshot inputs."""
        ...


@runtime_checkable
class MemoSynthesisPort(Protocol):
    """Synthesize only accepted structured inputs into a cited memo."""

    async def synthesize(self, request: AnalysisRequest) -> MemoSynthesis:
        """Return five required memo sections with Claim and Evidence citations."""
        ...


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
