"""Deterministic structured-intelligence replay adapter."""

from collections.abc import Mapping

from founderlookup.domain.assessment import AssessmentEnvelope
from founderlookup.domain.evidence import ClaimStatus, EvidenceStance
from founderlookup.screening.analysis import (
    AdversarialValidation,
    FounderDossierAnalysis,
    IdeaNoveltyQualityAnalysis,
    MarketAnalysis,
    MemoSynthesis,
    StructuredAnalysis,
)
from founderlookup.screening.ports import AnalysisRequest, IntelligenceRequest


class InvalidFakeAnalysisError(ValueError):
    """Raised when a fixed analysis violates its immutable request."""


class MissingFakeAnalysisError(LookupError):
    """Raised when a deterministic analysis replay was not seeded."""


class _FakeStructuredAnalysisAdapter[AnalysisResultT: StructuredAnalysis]:
    """Deep replay implementation shared by the specialist adapters."""

    def __init__(self, responses: Mapping[str, AnalysisResultT]) -> None:
        self._responses = dict(responses)
        self._requests: list[AnalysisRequest] = []

    @property
    def requests(self) -> tuple[AnalysisRequest, ...]:
        return tuple(self._requests)

    def _replay(self, request: AnalysisRequest) -> AnalysisResultT:
        self._requests.append(request)
        try:
            result = self._responses[request.request_id]
        except KeyError as error:
            raise MissingFakeAnalysisError(
                f"No fixed analysis for request {request.request_id!r}"
            ) from error
        if (
            result.request_id != request.request_id
            or result.input_snapshot_id != request.input_snapshot_id
            or result.subject != request.subject
        ):
            raise InvalidFakeAnalysisError(
                f"fixed analysis {result.request_id!r} does not match request "
                f"{request.request_id!r}"
            )
        unsupported_claim_ids = {
            claim.claim_id for claim in request.claims if claim.status is ClaimStatus.UNSUPPORTED
        }
        cited_claim_ids = {
            claim_id
            for finding in result.findings
            for claim_id in (*finding.supporting_claim_ids, *finding.counter_claim_ids)
        }
        cited_evidence_ids = {
            evidence_id
            for finding in result.findings
            for evidence_id in (
                *finding.supporting_evidence_ids,
                *finding.counter_evidence_ids,
            )
        }
        available_claim_ids = {claim.claim_id for claim in request.claims}
        if not cited_claim_ids.issubset(available_claim_ids):
            raise InvalidFakeAnalysisError(
                "a cited Claim identifier is not present in the request snapshot"
            )
        if cited_claim_ids & unsupported_claim_ids:
            raise InvalidFakeAnalysisError(
                "structured factual findings cannot cite an unsupported Claim"
            )
        available_evidence_ids = {item.evidence_id for item in request.evidence}
        if not cited_evidence_ids.issubset(available_evidence_ids):
            raise InvalidFakeAnalysisError(
                "a cited Evidence identifier is not present in the request snapshot"
            )
        evidence_by_id = {item.evidence_id: item for item in request.evidence}
        for finding in result.findings:
            if any(
                evidence_by_id[evidence_id].claim_id not in finding.supporting_claim_ids
                for evidence_id in finding.supporting_evidence_ids
            ) or any(
                evidence_by_id[evidence_id].claim_id not in finding.counter_claim_ids
                for evidence_id in finding.counter_evidence_ids
            ):
                raise InvalidFakeAnalysisError(
                    "cited Evidence does not belong to the corresponding cited Claim"
                )
            if any(
                evidence_by_id[evidence_id].stance is not EvidenceStance.SUPPORTS
                for evidence_id in (
                    *finding.supporting_evidence_ids,
                    *finding.counter_evidence_ids,
                )
            ):
                raise InvalidFakeAnalysisError(
                    "factual citations require a supporting Evidence stance"
                )
        return result


class FakeMarketAnalysisAdapter(_FakeStructuredAnalysisAdapter[MarketAnalysis]):
    """Replay fixed market analyses through the production-facing seam."""

    async def analyze(self, request: AnalysisRequest) -> MarketAnalysis:
        return self._replay(request)


class FakeIdeaNoveltyQualityAdapter(_FakeStructuredAnalysisAdapter[IdeaNoveltyQualityAnalysis]):
    """Replay separate idea novelty and quality findings."""

    async def analyze(self, request: AnalysisRequest) -> IdeaNoveltyQualityAnalysis:
        return self._replay(request)


class FakeFounderDossierAdapter(_FakeStructuredAnalysisAdapter[FounderDossierAnalysis]):
    """Replay founder dossiers with only the presentation dimensions in policy."""

    async def analyze(self, request: AnalysisRequest) -> FounderDossierAnalysis:
        return self._replay(request)


class FakeAdversarialValidationAdapter(_FakeStructuredAnalysisAdapter[AdversarialValidation]):
    """Replay a deterministic adversarial view for contract evaluation."""

    async def validate(self, request: AnalysisRequest) -> AdversarialValidation:
        result = self._replay(request)
        expected_unsupported = {
            claim.claim_id for claim in request.claims if claim.status is ClaimStatus.UNSUPPORTED
        }
        if set(result.unsupported_claim_ids) != expected_unsupported:
            raise InvalidFakeAnalysisError(
                "adversarial output does not match the unsupported Claim inventory"
            )
        available_claim_ids = {claim.claim_id for claim in request.claims}
        available_evidence_ids = {item.evidence_id for item in request.evidence}
        contradiction_claim_ids = {
            claim_id
            for contradiction in result.contradictions
            for claim_id in contradiction.claim_ids
        }
        contradiction_evidence_ids = {
            evidence_id
            for contradiction in result.contradictions
            for evidence_id in contradiction.evidence_ids
        }
        if not contradiction_claim_ids.issubset(
            available_claim_ids
        ) or not contradiction_evidence_ids.issubset(available_evidence_ids):
            raise InvalidFakeAnalysisError(
                "Contradiction citations must belong to the request snapshot"
            )
        return result


class FakeMemoSynthesisAdapter:
    """Replay cited memo synthesis without choosing an intelligence provider."""

    def __init__(self, responses: Mapping[str, MemoSynthesis]) -> None:
        self._responses = dict(responses)
        self._requests: list[AnalysisRequest] = []

    @property
    def requests(self) -> tuple[AnalysisRequest, ...]:
        return tuple(self._requests)

    async def synthesize(self, request: AnalysisRequest) -> MemoSynthesis:
        self._requests.append(request)
        try:
            result = self._responses[request.request_id]
        except KeyError as error:
            raise MissingFakeAnalysisError(
                f"No fixed analysis for request {request.request_id!r}"
            ) from error
        if (
            result.request_id != request.request_id
            or result.input_snapshot_id != request.input_snapshot_id
            or result.subject != request.subject
        ):
            raise InvalidFakeAnalysisError(
                f"fixed memo {result.request_id!r} does not match request {request.request_id!r}"
            )
        claim_by_id = {claim.claim_id: claim for claim in request.claims}
        cited_claim_ids = {
            claim_id for citation in result.section_citations for claim_id in citation.claim_ids
        }
        if not cited_claim_ids.issubset(claim_by_id):
            raise InvalidFakeAnalysisError("memo cites a Claim not present in the request snapshot")
        if any(
            claim_by_id[claim_id].status is ClaimStatus.UNSUPPORTED for claim_id in cited_claim_ids
        ):
            raise InvalidFakeAnalysisError("memo cannot present an unsupported Claim as factual")
        available_evidence_ids = {item.evidence_id for item in request.evidence}
        cited_evidence_ids = {
            evidence_id
            for citation in result.section_citations
            for evidence_id in citation.evidence_ids
        }
        if not cited_evidence_ids.issubset(available_evidence_ids):
            raise InvalidFakeAnalysisError(
                "memo cites Evidence not present in the request snapshot"
            )
        evidence_by_id = {item.evidence_id: item for item in request.evidence}
        if any(
            evidence_by_id[evidence_id].claim_id not in citation.claim_ids
            for citation in result.section_citations
            for evidence_id in citation.evidence_ids
        ):
            raise InvalidFakeAnalysisError("memo Evidence does not belong to its cited Claim")
        return result


class MissingFakeAssessmentError(LookupError):
    """Raised when no fixed assessment exists for an intelligence request."""


class InvalidFakeAssessmentError(ValueError):
    """Raised when seeded assessment data does not describe its request."""


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
            assessment = self._responses[request.request_id]
        except KeyError as error:
            raise MissingFakeAssessmentError(
                f"No fake assessment for request {request.request_id!r}"
            ) from error
        if (
            assessment.input_snapshot_id != request.input_snapshot_id
            or assessment.identity.mode != request.mode.value
        ):
            raise InvalidFakeAssessmentError(
                f"Fake assessment {assessment.assessment_id!r} does not match request "
                f"{request.request_id!r}"
            )
        return assessment
