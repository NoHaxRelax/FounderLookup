"""Structured outputs shared by deterministic and later model-backed analyzers."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, field_validator, model_validator

from founderlookup.domain.assessment import (
    Confidence,
    Contradiction,
    InvestmentMemo,
    MemoSectionKind,
)
from founderlookup.domain.common import (
    DomainModel,
    EntityKind,
    KnowledgeState,
    KnowledgeValue,
    LongText,
    NonBlankStr,
    StableId,
    SubjectRef,
    UTCDateTime,
)

_PROHIBITED_PRESENTATION_PROXY = re.compile(
    r"\b(?:appearance|accent|name|charisma|charismatic|polish|polished|"
    r"production\s+value|age|young|youth|gender|race|ethnicity|religion|"
    r"disability|socioeconomic|wealth|pedigree)\b",
    flags=re.IGNORECASE,
)


class AnalysisGap(DomainModel):
    """One explicit non-known input and the smallest useful diligence request."""

    gap_id: StableId
    topic: NonBlankStr
    state: KnowledgeState
    reason: NonBlankStr
    requested_evidence: NonBlankStr
    related_claim_ids: tuple[StableId, ...] = ()
    related_evidence_ids: tuple[StableId, ...] = ()
    memo_section: MemoSectionKind | None = None

    @model_validator(mode="after")
    def reject_known_gap(self) -> Self:
        if self.state is KnowledgeState.KNOWN:
            raise ValueError("an analysis gap cannot have Known state")
        return self


class MarketFindingKind(StrEnum):
    """Investor-relevant market dimensions, kept independently inspectable."""

    DIRECTION = "direction"
    SIZING_ASSUMPTIONS = "sizing_assumptions"
    COMPETITORS = "competitors"
    SWOT = "swot"


class AnalysisFinding(DomainModel):
    """One knowledge-state-aware conclusion with two-sided citations."""

    finding_id: StableId
    conclusion: KnowledgeValue[LongText]
    confidence: KnowledgeValue[Confidence]
    supporting_claim_ids: tuple[StableId, ...]
    supporting_evidence_ids: tuple[StableId, ...]
    counter_claim_ids: tuple[StableId, ...]
    counter_evidence_ids: tuple[StableId, ...]
    gap_ids: tuple[StableId, ...]

    @model_validator(mode="after")
    def require_cited_known_conclusion(self) -> Self:
        if bool(self.supporting_claim_ids) != bool(self.supporting_evidence_ids):
            raise ValueError("supporting Claim and Evidence citations must be paired")
        if bool(self.counter_claim_ids) != bool(self.counter_evidence_ids):
            raise ValueError("counter Claim and Evidence citations must be paired")
        if set(self.supporting_claim_ids) & set(self.counter_claim_ids) or set(
            self.supporting_evidence_ids
        ) & set(self.counter_evidence_ids):
            raise ValueError("a citation cannot both support and counter one finding")
        if self.conclusion.state is KnowledgeState.KNOWN and (
            not self.supporting_claim_ids or not self.supporting_evidence_ids
        ):
            raise ValueError("known finding requires supporting Claim and Evidence citations")
        if self.conclusion.state is not KnowledgeState.KNOWN and not self.gap_ids:
            raise ValueError("unknown finding requires an explicit gap")
        return self


class MarketFinding(AnalysisFinding):
    kind: MarketFindingKind


class StructuredAnalysis(DomainModel):
    """Shared immutable request identity, findings, gaps, and generation time."""

    request_id: StableId
    input_snapshot_id: StableId
    subject: SubjectRef
    findings: Annotated[tuple[AnalysisFinding, ...], Field(min_length=1)]
    gaps: tuple[AnalysisGap, ...]
    generated_at: UTCDateTime

    @model_validator(mode="after")
    def require_declared_gap_references(self) -> Self:
        gap_ids = {gap.gap_id for gap in self.gaps}
        referenced_gap_ids = {gap_id for finding in self.findings for gap_id in finding.gap_ids}
        if not referenced_gap_ids.issubset(gap_ids):
            raise ValueError("an analysis finding references an undeclared gap")
        return self


class MarketAnalysis(StructuredAnalysis):
    findings: Annotated[tuple[MarketFinding, ...], Field(min_length=1)]


class IdeaFindingKind(StrEnum):
    PROBLEM_PRODUCT_COHERENCE = "problem_product_coherence"
    NOVELTY = "novelty"
    QUALITY = "quality"
    DEFENSIBILITY = "defensibility"
    VIABILITY = "viability"


class IdeaFinding(AnalysisFinding):
    kind: IdeaFindingKind


class IdeaNoveltyQualityAnalysis(StructuredAnalysis):
    findings: Annotated[tuple[IdeaFinding, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def require_novelty_and_quality(self) -> Self:
        kinds = {finding.kind for finding in self.findings}
        if not {IdeaFindingKind.NOVELTY, IdeaFindingKind.QUALITY}.issubset(kinds):
            raise ValueError("idea analysis requires separate novelty and quality findings")
        return self


class FounderFindingKind(StrEnum):
    SKILLS = "skills"
    MILESTONES = "milestones"
    CONSISTENCY = "consistency"
    FOUNDER_MARKET_FIT = "founder_market_fit"


class FounderPresentationDimension(StrEnum):
    """The complete allowlist for presentation-related founder analysis."""

    CLAIM_CLARITY = "claim_clarity"
    EVIDENCE_CONSISTENCY = "evidence_consistency"
    RESPONSIVENESS = "responsiveness"
    EVIDENCE_QUALITY = "evidence_quality"


class FounderFinding(AnalysisFinding):
    kind: FounderFindingKind


class FounderPresentationFinding(AnalysisFinding):
    dimension: FounderPresentationDimension

    @field_validator("dimension", mode="before")
    @classmethod
    def allow_only_policy_dimensions(
        cls,
        value: object,
    ) -> FounderPresentationDimension:
        if isinstance(value, FounderPresentationDimension):
            return value
        if not isinstance(value, str):
            raise ValueError("prohibited presentation proxy or invalid dimension")
        try:
            return FounderPresentationDimension(value)
        except ValueError as error:
            raise ValueError(
                "prohibited presentation proxy; only claim clarity, evidence "
                "consistency, responsiveness, and evidence quality are allowed"
            ) from error

    @model_validator(mode="after")
    def reject_proxy_language(self) -> Self:
        text = self.conclusion.value or self.conclusion.reason or ""
        if _PROHIBITED_PRESENTATION_PROXY.search(text):
            raise ValueError("prohibited presentation proxy appears in conclusion")
        return self


class FounderDossierAnalysis(StructuredAnalysis):
    findings: Annotated[
        tuple[FounderFinding | FounderPresentationFinding, ...],
        Field(min_length=1),
    ]

    @model_validator(mode="after")
    def require_builder_evidence_without_proxies(self) -> Self:
        if not any(isinstance(finding, FounderFinding) for finding in self.findings):
            raise ValueError("founder dossier requires builder evidence")
        for finding in self.findings:
            text = finding.conclusion.value or finding.conclusion.reason or ""
            if _PROHIBITED_PRESENTATION_PROXY.search(text):
                raise ValueError("prohibited presentation proxy appears in dossier")
        return self


class AdversarialFindingKind(StrEnum):
    CORROBORATION = "corroboration"
    FRAGILE_ASSUMPTION = "fragile_assumption"
    STALE_SOURCE = "stale_source"
    WEAKNESS = "weakness"


class AdversarialFinding(AnalysisFinding):
    kind: AdversarialFindingKind


class AdversarialValidation(StructuredAnalysis):
    findings: Annotated[tuple[AdversarialFinding, ...], Field(min_length=1)]
    contradictions: tuple[Contradiction, ...]
    unsupported_claim_ids: tuple[StableId, ...]


class MemoSectionCitation(DomainModel):
    section: MemoSectionKind
    claim_ids: tuple[StableId, ...]
    evidence_ids: tuple[StableId, ...]


class MemoSynthesis(DomainModel):
    request_id: StableId
    input_snapshot_id: StableId
    subject: SubjectRef
    memo: InvestmentMemo
    section_citations: Annotated[
        tuple[MemoSectionCitation, ...],
        Field(min_length=5),
    ]
    gaps: tuple[AnalysisGap, ...]
    contradiction_ids: tuple[StableId, ...]

    @model_validator(mode="after")
    def require_one_citation_record_per_section(self) -> Self:
        if (
            self.subject.kind is not EntityKind.OPPORTUNITY
            or self.memo.opportunity_id != self.subject.subject_id
        ):
            raise ValueError("memo Opportunity must match the requested subject")
        memo_sections = {section.kind for section in self.memo.sections}
        cited_sections = tuple(item.section for item in self.section_citations)
        if len(cited_sections) != len(set(cited_sections)) or set(cited_sections) != memo_sections:
            raise ValueError("citation records must exactly mirror memo sections")
        citations_by_section = {citation.section: citation for citation in self.section_citations}
        for section in self.memo.sections:
            citation = citations_by_section[section.kind]
            if section.content.state is KnowledgeState.KNOWN and (
                not citation.claim_ids or not citation.evidence_ids
            ):
                raise ValueError(
                    "known memo section requires material Claim citations and Evidence"
                )
            if set(citation.claim_ids) != set(section.material_claim_ids) or (
                section.material_claim_ids and not citation.evidence_ids
            ):
                raise ValueError("memo material Claim citations must include their Evidence")
            if section.content.state is not KnowledgeState.KNOWN and section.kind not in {
                gap.memo_section for gap in self.gaps
            }:
                raise ValueError("non-known memo section requires an explicit section gap")
        return self
