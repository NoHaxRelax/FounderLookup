"""Common preliminary/full screening outputs and human workflow contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final, Literal, Self

from pydantic import Field, model_validator

from founderlookup.domain.common import (
    DomainModel,
    KnowledgeState,
    KnowledgeValue,
    LongText,
    NonBlankStr,
    ScalarValue,
    StableId,
    SubjectRef,
    UTCDateTime,
    VersionId,
    VersionManifest,
)
from founderlookup.domain.lifecycles import DecisionReadinessStatus, OpportunityOrigin
from founderlookup.domain.scoring import CoverageSummary, FounderScoreSnapshot

ASSESSMENT_ENVELOPE_SCHEMA_VERSION: Final = "assessment-envelope.v0"
AXIS_ASSESSMENT_SCHEMA_VERSION: Final = "axis-assessment.v0"
DECISION_READINESS_SCHEMA_VERSION: Final = "decision-readiness.v0"
INVESTMENT_MEMO_SCHEMA_VERSION: Final = "investment-memo.v0"
RECOMMENDATION_SCHEMA_VERSION: Final = "recommendation.v0"
HUMAN_DECISION_SCHEMA_VERSION: Final = "human-decision.v0"

Confidence = Annotated[float, Field(strict=True, ge=0.0, le=1.0)]


class RuleOutcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INDETERMINATE = "indeterminate"
    NOT_EVALUATED = "not_evaluated"


class RuleInput(DomainModel):
    field: NonBlankStr
    value: KnowledgeValue[ScalarValue]


class RuleOverride(DomainModel):
    override_id: StableId
    replacement_outcome: RuleOutcome
    actor_id: StableId
    recorded_at: UTCDateTime
    rationale: NonBlankStr


class DeterministicRuleResult(DomainModel):
    result_id: StableId
    rule_id: StableId
    rule_version: VersionId
    outcome: RuleOutcome
    inputs: Annotated[tuple[RuleInput, ...], Field(min_length=1)]
    reason: NonBlankStr
    override: RuleOverride | None = None


class Trend(StrEnum):
    IMPROVING = "improving"
    STABLE = "stable"
    DECLINING = "declining"
    UNKNOWN = "unknown"


class FounderAxisRating(StrEnum):
    STRONG = "strong"
    MIXED = "mixed"
    WEAK = "weak"
    UNKNOWN = "unknown"


class MarketAxisRating(StrEnum):
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEAR = "bear"
    UNKNOWN = "unknown"


class IdeaVsMarketAxisRating(StrEnum):
    VIABLE = "viable"
    PIVOTABLE = "pivotable"
    WEAK = "weak"
    UNKNOWN = "unknown"


class _AxisAssessmentBase(DomainModel):
    schema_version: Literal["axis-assessment.v0"] = AXIS_ASSESSMENT_SCHEMA_VERSION
    assessment_id: StableId
    assessment_version_id: StableId
    rubric_version: VersionId
    trend: Trend
    confidence: KnowledgeValue[Confidence]
    coverage: CoverageSummary
    supporting_claim_ids: tuple[StableId, ...] = ()
    counter_claim_ids: tuple[StableId, ...] = ()
    open_questions: tuple[NonBlankStr, ...] = ()

    @model_validator(mode="after")
    def reject_overlapping_claims(self) -> Self:
        if set(self.supporting_claim_ids) & set(self.counter_claim_ids):
            raise ValueError("an axis claim cannot be both supporting and counter evidence")
        return self


class FounderAxisAssessment(_AxisAssessmentBase):
    axis: Literal["founder"] = "founder"
    rating: FounderAxisRating


class MarketAxisAssessment(_AxisAssessmentBase):
    axis: Literal["market"] = "market"
    rating: MarketAxisRating


class IdeaVsMarketAxisAssessment(_AxisAssessmentBase):
    axis: Literal["idea_vs_market"] = "idea_vs_market"
    rating: IdeaVsMarketAxisRating


class IndependentAxes(DomainModel):
    """No aggregate field exists by design: the three axes cannot be averaged here."""

    founder: FounderAxisAssessment
    market: MarketAxisAssessment
    idea_vs_market: IdeaVsMarketAxisAssessment


class ContradictionStatus(StrEnum):
    UNRESOLVED = "unresolved"
    RESOLVED = "resolved"
    ACCEPTED_RISK = "accepted_risk"


class Contradiction(DomainModel):
    contradiction_id: StableId
    contradiction_version_id: StableId
    claim_ids: Annotated[tuple[StableId, ...], Field(min_length=2)]
    evidence_ids: Annotated[tuple[StableId, ...], Field(min_length=2)]
    status: ContradictionStatus
    blocking: bool
    summary: NonBlankStr
    detected_at: UTCDateTime
    resolution: NonBlankStr | None = None

    @model_validator(mode="after")
    def validate_resolution(self) -> Self:
        if self.status is ContradictionStatus.UNRESOLVED and self.resolution is not None:
            raise ValueError("unresolved contradiction cannot carry a resolution")
        if self.status is not ContradictionStatus.UNRESOLVED and self.resolution is None:
            raise ValueError("resolved or accepted contradiction requires resolution")
        return self


class DiligenceActionStatus(StrEnum):
    OPEN = "open"
    COMPLETED = "completed"
    DISMISSED = "dismissed"


class DiligenceAction(DomainModel):
    action_id: StableId
    status: DiligenceActionStatus
    description: NonBlankStr
    resolves_claim_ids: tuple[StableId, ...] = ()
    resolves_contradiction_ids: tuple[StableId, ...] = ()
    requested_evidence: NonBlankStr


class PreliminaryAssessmentIdentity(DomainModel):
    mode: Literal["preliminary"] = "preliminary"
    origin: Literal["outbound"] = "outbound"
    outbound_candidate_id: StableId
    founder_id: KnowledgeValue[StableId]
    company_id: KnowledgeValue[StableId]


class FullAssessmentIdentity(DomainModel):
    mode: Literal["full"] = "full"
    origin: OpportunityOrigin
    application_id: StableId
    outbound_candidate_id: StableId | None = None
    opportunity_id: StableId
    screening_case_id: StableId
    company_id: StableId
    founder_id: KnowledgeValue[StableId]

    @model_validator(mode="after")
    def outbound_origin_requires_candidate(self) -> Self:
        if self.origin is OpportunityOrigin.OUTBOUND and self.outbound_candidate_id is None:
            raise ValueError("outbound full assessment requires an Outbound Candidate")
        if self.origin is OpportunityOrigin.INBOUND and self.outbound_candidate_id is not None:
            raise ValueError("inbound full assessment cannot reference an Outbound Candidate")
        return self


AssessmentIdentity = PreliminaryAssessmentIdentity | FullAssessmentIdentity


class ReadinessCheckStatus(StrEnum):
    SATISFIED = "satisfied"
    BLOCKING = "blocking"
    NOT_EVALUATED = "not_evaluated"


class ReadinessCheck(DomainModel):
    check_key: NonBlankStr
    status: ReadinessCheckStatus
    reason: NonBlankStr
    related_record_ids: tuple[StableId, ...] = ()


class ReadinessBlocker(DomainModel):
    blocker_id: StableId
    check_key: NonBlankStr
    reason: NonBlankStr
    related_record_ids: tuple[StableId, ...] = ()


class AcceptedReadinessRisk(DomainModel):
    blocker_id: StableId
    actor_id: StableId
    accepted_at: UTCDateTime
    rationale: NonBlankStr


class DecisionReadiness(DomainModel):
    schema_version: Literal["decision-readiness.v0"] = DECISION_READINESS_SCHEMA_VERSION
    readiness_id: StableId
    readiness_version_id: StableId
    screening_case_id: StableId
    policy_version: VersionId
    evaluated_at: UTCDateTime
    status: DecisionReadinessStatus
    checks: tuple[ReadinessCheck, ...]
    blockers: tuple[ReadinessBlocker, ...] = ()
    accepted_risks: tuple[AcceptedReadinessRisk, ...] = ()

    @model_validator(mode="after")
    def validate_readiness_state(self) -> Self:
        blocker_ids = tuple(blocker.blocker_id for blocker in self.blockers)
        if len(blocker_ids) != len(set(blocker_ids)):
            raise ValueError("readiness blocker identifiers must be unique")
        accepted_ids = tuple(risk.blocker_id for risk in self.accepted_risks)
        if len(accepted_ids) != len(set(accepted_ids)):
            raise ValueError("a readiness blocker can be accepted only once per revision")
        if not set(accepted_ids).issubset(blocker_ids):
            raise ValueError("accepted risks must reference current blockers")

        if self.status is DecisionReadinessStatus.BLOCKED and not self.blockers:
            raise ValueError("blocked readiness requires at least one blocker")
        if self.status is DecisionReadinessStatus.READY and (self.blockers or self.accepted_risks):
            raise ValueError("ready status cannot retain blockers or accepted risks")
        if self.status is DecisionReadinessStatus.READY_WITH_ACCEPTED_RISK and (
            not blocker_ids or set(accepted_ids) != set(blocker_ids)
        ):
            raise ValueError("ready with accepted risk requires acceptance of every blocker")
        if self.status is DecisionReadinessStatus.NOT_EVALUATED and self.accepted_risks:
            raise ValueError("not-evaluated readiness cannot accept risks")
        return self


class MemoSectionKind(StrEnum):
    COMPANY_SNAPSHOT = "company_snapshot"
    INVESTMENT_HYPOTHESES = "investment_hypotheses"
    SWOT = "swot"
    PROBLEM_AND_PRODUCT = "problem_and_product"
    TRACTION_AND_KPIS = "traction_and_kpis"
    RISKS_AND_DILIGENCE = "risks_and_diligence"
    TEAM = "team"
    FINANCIALS = "financials"


REQUIRED_MEMO_SECTIONS = frozenset(
    {
        MemoSectionKind.COMPANY_SNAPSHOT,
        MemoSectionKind.INVESTMENT_HYPOTHESES,
        MemoSectionKind.SWOT,
        MemoSectionKind.PROBLEM_AND_PRODUCT,
        MemoSectionKind.TRACTION_AND_KPIS,
    }
)


class MemoSection(DomainModel):
    kind: MemoSectionKind
    content: KnowledgeValue[LongText]
    material_claim_ids: tuple[StableId, ...] = ()


class InvestmentMemo(DomainModel):
    schema_version: Literal["investment-memo.v0"] = INVESTMENT_MEMO_SCHEMA_VERSION
    memo_id: StableId
    memo_version_id: StableId
    opportunity_id: StableId
    screening_case_id: StableId
    assessment_id: StableId
    run_id: StableId
    thesis_version: VersionId
    evidence_as_of: UTCDateTime
    generated_at: UTCDateTime
    sections: Annotated[tuple[MemoSection, ...], Field(min_length=5)]

    @model_validator(mode="after")
    def require_core_sections(self) -> Self:
        kinds = tuple(section.kind for section in self.sections)
        if len(kinds) != len(set(kinds)):
            raise ValueError("memo section kinds must be unique")
        if not REQUIRED_MEMO_SECTIONS.issubset(kinds):
            raise ValueError("memo must contain all five required sections")
        if self.generated_at < self.evidence_as_of:
            raise ValueError("memo cannot be generated before its evidence snapshot")
        return self


class RecommendationAction(StrEnum):
    ACTIVATE = "activate"
    ADVANCE = "advance"
    NEEDS_INFORMATION = "needs_information"
    MANUAL_REVIEW = "manual_review"
    DO_NOT_PURSUE = "do_not_pursue"


class RecommendationReason(DomainModel):
    summary: NonBlankStr
    claim_ids: tuple[StableId, ...] = ()


class Recommendation(DomainModel):
    schema_version: Literal["recommendation.v0"] = RECOMMENDATION_SCHEMA_VERSION
    recommendation_id: StableId
    recommendation_version_id: StableId
    subject: SubjectRef
    assessment_id: StableId
    policy_version: VersionId
    action: RecommendationAction
    reasons: Annotated[tuple[RecommendationReason, ...], Field(min_length=1)]
    next_actions: Annotated[tuple[NonBlankStr, ...], Field(min_length=1)]
    created_at: UTCDateTime


class HumanDecisionDisposition(StrEnum):
    ADVANCE = "advance"
    DECLINE = "decline"
    HOLD = "hold"
    REQUEST_MORE_INFORMATION = "request_more_information"


class Decision(DomainModel):
    """Append-only human event, separate from any system Recommendation."""

    schema_version: Literal["human-decision.v0"] = HUMAN_DECISION_SCHEMA_VERSION
    decision_id: StableId
    screening_case_id: StableId
    opportunity_id: StableId
    assessment_id: StableId
    memo_id: StableId
    reviewed_recommendation_id: StableId
    disposition: HumanDecisionDisposition
    actor_id: StableId
    rationale: NonBlankStr
    decided_at: UTCDateTime


# Descriptive compatibility name for callers that want to emphasize human authorship.
HumanDecision = Decision


class AssessmentEnvelope(DomainModel):
    """Shared output for candidate-keyed preliminary and full case assessment."""

    schema_version: Literal["assessment-envelope.v0"] = ASSESSMENT_ENVELOPE_SCHEMA_VERSION
    assessment_id: StableId
    assessment_version_id: StableId
    identity: AssessmentIdentity
    versions: VersionManifest
    input_snapshot_id: StableId
    input_snapshot_as_of: UTCDateTime
    coverage: CoverageSummary
    deterministic_results: tuple[DeterministicRuleResult, ...]
    founder_score: KnowledgeValue[FounderScoreSnapshot]
    axes: IndependentAxes
    claim_ids: tuple[StableId, ...]
    evidence_ids: tuple[StableId, ...]
    contradictions: tuple[Contradiction, ...] = ()
    diligence_actions: tuple[DiligenceAction, ...] = ()
    decision_readiness: DecisionReadiness | None = None
    memo: InvestmentMemo | None = None
    recommendation: Recommendation | None = None
    run_id: StableId
    created_at: UTCDateTime
    human_decision_ids: tuple[StableId, ...] = ()

    @model_validator(mode="after")
    def validate_identity_and_children(self) -> Self:
        if isinstance(self.identity, PreliminaryAssessmentIdentity):
            if self.decision_readiness is not None or self.memo is not None:
                raise ValueError("preliminary assessment cannot claim full-case readiness or memo")
        else:
            case_id = self.identity.screening_case_id
            if (
                self.decision_readiness is not None
                and self.decision_readiness.screening_case_id != case_id
            ):
                raise ValueError("readiness must belong to the envelope screening case")
            if self.memo is not None:
                if self.memo.screening_case_id != case_id:
                    raise ValueError("memo must belong to the envelope screening case")
                if self.memo.assessment_id != self.assessment_id:
                    raise ValueError("memo must reference the envelope assessment")

        if self.founder_score.state is KnowledgeState.KNOWN:
            score = self.founder_score.value
            if score is None:  # pragma: no cover - KnowledgeValue guards this
                raise ValueError("known founder score requires snapshot")
            identity_founder = self.identity.founder_id
            if identity_founder.state is not KnowledgeState.KNOWN:
                raise ValueError("founder score cannot be known while founder identity is unknown")
            if score.founder_id != identity_founder.value:
                raise ValueError("founder score must belong to the assessed founder")

        if self.memo is not None and self.memo.run_id != self.run_id:
            raise ValueError("memo run must match the envelope run")
        if (
            self.recommendation is not None
            and self.recommendation.assessment_id != self.assessment_id
        ):
            raise ValueError("recommendation must reference the envelope assessment")
        return self
