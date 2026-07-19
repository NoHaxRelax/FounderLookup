"""Deterministic pitch-deck projection into the immutable Evidence graph.

This module deliberately does less than a semantic extractor. It accepts only explicit,
allowlisted Markdown labels from a provider-neutral PDF extraction result. Unlabelled prose,
provider summaries, and search silence cannot create facts. Every accepted value remains a
deck assertion anchored to an exact page excerpt unless separately accepted corroboration is
supplied by the caller.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Final, Literal

from pydantic import Field, model_validator

from founderlookup.domain.assessment import (
    Contradiction,
    ContradictionStatus,
    MemoSection,
    MemoSectionKind,
)
from founderlookup.domain.common import (
    DomainModel,
    EntityKind,
    KnowledgeAlternative,
    KnowledgeState,
    KnowledgeValue,
    LongText,
    NonBlankStr,
    NonNegativeInt,
    ScalarValue,
    StableId,
    SubjectRef,
    UTCDateTime,
)
from founderlookup.domain.evidence import (
    ArtifactAvailability,
    Claim,
    ClaimOrigin,
    ClaimStatus,
    Evidence,
    EvidenceStance,
    ExtractionMethod,
    Observation,
    SourceArtifact,
    SourceCategory,
    SourceLocator,
    SourceLocatorKind,
    VerificationState,
)
from founderlookup.domain.scoring import TrustFactorKind, TrustFactorSignal
from founderlookup.ingestion.extraction import (
    PdfExtractionResult,
    PdfPageConfidence,
)
from founderlookup.screening.rubrics import TrustFactorInput, score_claim_trust

DECK_EVIDENCE_PROJECTION_VERSION: Final = "deck-evidence-projection.v0"


class DeckField(StrEnum):
    """Allowlisted labels that may become deterministic deck assertions."""

    COMPANY = "company"
    PROBLEM = "problem"
    PRODUCT = "product"
    MARKET = "market"
    TRACTION_AND_KPIS = "traction_and_kpis"
    FOUNDERS = "founders"
    FUNDING = "funding"


_FIELD_ORDER: Final = tuple(DeckField)
_FIELD_LABEL: Final[dict[DeckField, str]] = {
    DeckField.COMPANY: "Company",
    DeckField.PROBLEM: "Problem",
    DeckField.PRODUCT: "Product",
    DeckField.MARKET: "Market",
    DeckField.TRACTION_AND_KPIS: "Traction/KPIs",
    DeckField.FOUNDERS: "Founder(s)",
    DeckField.FUNDING: "Funding",
}
_FIELD_PREDICATE: Final[dict[DeckField, str]] = {
    DeckField.COMPANY: "company.name_assertion",
    DeckField.PROBLEM: "opportunity.problem_assertion",
    DeckField.PRODUCT: "opportunity.product_assertion",
    DeckField.MARKET: "opportunity.market_assertion",
    DeckField.TRACTION_AND_KPIS: "opportunity.traction_kpis_assertion",
    DeckField.FOUNDERS: "company.founders_assertion",
    DeckField.FUNDING: "company.funding_assertion",
}
_COMPANY_FIELDS: Final = {
    DeckField.COMPANY,
    DeckField.FOUNDERS,
    DeckField.FUNDING,
}
_LABEL_ALIASES: Final[dict[str, DeckField]] = {
    "company": DeckField.COMPANY,
    "company name": DeckField.COMPANY,
    "problem": DeckField.PROBLEM,
    "problem statement": DeckField.PROBLEM,
    "product": DeckField.PRODUCT,
    "solution": DeckField.PRODUCT,
    "product solution": DeckField.PRODUCT,
    "market": DeckField.MARKET,
    "target market": DeckField.MARKET,
    "traction": DeckField.TRACTION_AND_KPIS,
    "traction kpis": DeckField.TRACTION_AND_KPIS,
    "traction and kpis": DeckField.TRACTION_AND_KPIS,
    "kpis": DeckField.TRACTION_AND_KPIS,
    "key metrics": DeckField.TRACTION_AND_KPIS,
    "founder": DeckField.FOUNDERS,
    "founders": DeckField.FOUNDERS,
    "founding team": DeckField.FOUNDERS,
    "funding": DeckField.FUNDING,
    "funding status": DeckField.FUNDING,
    "financing": DeckField.FUNDING,
    "capital raised": DeckField.FUNDING,
}
_INLINE_LABEL = re.compile(r"^(?P<label>[^:]{1,48})\s*:\s*(?P<value>.+)$")
_HEADING = re.compile(r"^\s*#{1,6}\s+")
_BULLET = re.compile(r"^\s*[-+*]\s+")
_MARKDOWN_EMPHASIS = re.compile(r"(?:\*\*|__|`)")
_LABEL_NORMALIZER = re.compile(r"[^a-z0-9]+")
_VALUE_WHITESPACE = re.compile(r"\s+")


class DeckEvidenceLimits(DomainModel):
    """Hard upper bounds that prevent a projection from becoming a raw deck dump."""

    max_pages: Annotated[int, Field(strict=True, ge=1, le=50)] = 30
    max_lines_per_page: Annotated[int, Field(strict=True, ge=1, le=2_000)] = 500
    max_fields_per_page: Annotated[int, Field(strict=True, ge=1, le=32)] = 12
    max_fields: Annotated[int, Field(strict=True, ge=1, le=128)] = 64
    max_value_chars: Annotated[int, Field(strict=True, ge=32, le=2_000)] = 800
    max_excerpt_chars: Annotated[int, Field(strict=True, ge=32, le=1_000)] = 600
    max_section_chars: Annotated[int, Field(strict=True, ge=1_024, le=8_000)] = 4_000


class AcceptedClaimCorroboration(DomainModel):
    """Previously accepted Evidence that explicitly corroborates one exact deck value.

    The projector does not fetch or validate external content. Its integration caller owns
    checking that these identifiers resolve to accepted Evidence before supplying this input.
    """

    field: DeckField
    asserted_value: NonBlankStr
    evidence_ids: Annotated[tuple[StableId, ...], Field(min_length=1)]
    independent_source: bool = True

    @model_validator(mode="after")
    def reject_duplicate_evidence(self) -> AcceptedClaimCorroboration:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("corroborating Evidence identifiers must be unique")
        return self


class SupportedMemoSectionInput(DomainModel):
    """Accepted analysis input for a section that deck facts cannot safely derive."""

    kind: MemoSectionKind
    content: LongText
    material_claim_ids: Annotated[tuple[StableId, ...], Field(min_length=1)]
    evidence_ids: Annotated[tuple[StableId, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_supported_section(self) -> SupportedMemoSectionInput:
        if self.kind not in {
            MemoSectionKind.INVESTMENT_HYPOTHESES,
            MemoSectionKind.SWOT,
        }:
            raise ValueError("only hypotheses and SWOT accept explicit analysis input here")
        if len(self.material_claim_ids) != len(set(self.material_claim_ids)):
            raise ValueError("material Claim identifiers must be unique")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("section Evidence identifiers must be unique")
        return self


class DeckEvidenceProjection(DomainModel):
    """Immutable, bounded projection ready for application-service registration."""

    projection_version: Literal["deck-evidence-projection.v0"] = DECK_EVIDENCE_PROJECTION_VERSION
    projection_id: StableId
    projected_at: UTCDateTime
    extraction_id: StableId
    source_artifact_id: StableId
    application_id: StableId
    company_id: StableId
    opportunity_id: StableId
    pages_examined: NonNegativeInt
    omitted_page_count: NonNegativeInt
    omitted_field_count: NonNegativeInt
    truncated: bool
    observations: tuple[Observation, ...]
    evidence: tuple[Evidence, ...]
    claims: tuple[Claim, ...]
    contradictions: tuple[Contradiction, ...]
    memo_sections: Annotated[tuple[MemoSection, ...], Field(min_length=5, max_length=5)]

    @model_validator(mode="after")
    def preserve_graph_integrity(self) -> DeckEvidenceProjection:
        observation_ids = tuple(item.observation_id for item in self.observations)
        evidence_ids = tuple(item.evidence_id for item in self.evidence)
        claim_ids = tuple(item.claim_id for item in self.claims)
        if len(observation_ids) != len(set(observation_ids)):
            raise ValueError("projected Observation identifiers must be unique")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("projected Evidence identifiers must be unique")
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("projected Claim identifiers must be unique")
        expected_sections = (
            MemoSectionKind.COMPANY_SNAPSHOT,
            MemoSectionKind.INVESTMENT_HYPOTHESES,
            MemoSectionKind.SWOT,
            MemoSectionKind.PROBLEM_AND_PRODUCT,
            MemoSectionKind.TRACTION_AND_KPIS,
        )
        if tuple(section.kind for section in self.memo_sections) != expected_sections:
            raise ValueError("projection must contain the five required memo sections in order")
        return self


@dataclass(frozen=True, slots=True)
class _DeckAssertion:
    field: DeckField
    value: str
    normalized_value: str
    excerpt: str
    page_index: int
    line_index: int
    confidence: PdfPageConfidence

    @property
    def locator(self) -> str:
        return f"page:{self.page_index}#line:{self.line_index + 1}"


@dataclass(frozen=True, slots=True)
class _ProjectedClaim:
    field: DeckField
    value: str
    claim: Claim


def _stable_id(kind: str, *parts: object) -> StableId:
    material = "\x1f".join(str(part) for part in parts)
    digest = sha256(material.encode("utf-8")).hexdigest()[:32]
    return f"{kind}:deck:{digest}"


def _normalize_label(value: str) -> str:
    value = _MARKDOWN_EMPHASIS.sub("", value).casefold()
    return _LABEL_NORMALIZER.sub(" ", value).strip()


def _normalize_value(value: str) -> str:
    """Collapse presentation-only differences without claiming semantic equivalence.

    Values that still differ after case, whitespace, and terminal-punctuation normalization
    compete explicitly; the deterministic layer never guesses that two phrasings mean the
    same thing.
    """

    return _VALUE_WHITESPACE.sub(" ", value).strip().rstrip(".;,").casefold()


def _display_value(value: str, *, max_chars: int) -> str:
    cleaned = _VALUE_WHITESPACE.sub(" ", _MARKDOWN_EMPHASIS.sub("", value)).strip()
    return cleaned[:max_chars].strip()


def _field_for_label(label: str) -> DeckField | None:
    return _LABEL_ALIASES.get(_normalize_label(label))


def _strip_prefix_markdown(line: str) -> str:
    candidate = _HEADING.sub("", line, count=1)
    candidate = _BULLET.sub("", candidate, count=1)
    return _MARKDOWN_EMPHASIS.sub("", candidate).strip()


def _inline_assertion(line: str) -> tuple[DeckField, str] | None:
    candidate = _strip_prefix_markdown(line)
    match = _INLINE_LABEL.fullmatch(candidate)
    if match is None:
        return None
    field = _field_for_label(match.group("label"))
    value = match.group("value").strip()
    if field is None or not value:
        return None
    return field, value


def _table_assertion(line: str) -> tuple[DeckField, str] | None:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    cells = tuple(cell.strip() for cell in stripped.strip("|").split("|"))
    if len(cells) != 2:
        return None
    field = _field_for_label(cells[0])
    if field is None or not cells[1] or set(cells[1]) <= {"-", ":", " "}:
        return None
    return field, cells[1]


def _heading_field(line: str) -> DeckField | None:
    if _HEADING.match(line) is None:
        return None
    return _field_for_label(_strip_prefix_markdown(line))


def _parse_page(
    *,
    markdown: str,
    page_index: int,
    confidence: PdfPageConfidence,
    limits: DeckEvidenceLimits,
) -> tuple[tuple[_DeckAssertion, ...], bool]:
    all_lines = markdown.splitlines()
    lines = all_lines[: limits.max_lines_per_page]
    truncated = len(all_lines) > len(lines)
    assertions: list[_DeckAssertion] = []
    index = 0
    while index < len(lines):
        raw_line = lines[index]
        parsed = _table_assertion(raw_line) or _inline_assertion(raw_line)
        if parsed is not None:
            field, raw_value = parsed
            value = _display_value(raw_value, max_chars=limits.max_value_chars)
            normalized_value = _normalize_value(value)
            if normalized_value:
                assertions.append(
                    _DeckAssertion(
                        field=field,
                        value=value,
                        normalized_value=normalized_value,
                        excerpt=raw_line[: limits.max_excerpt_chars],
                        page_index=page_index,
                        line_index=index,
                        confidence=confidence,
                    )
                )
            index += 1
            continue

        heading_field = _heading_field(raw_line)
        if heading_field is None:
            index += 1
            continue

        value_lines: list[str] = []
        excerpt_lines = [raw_line]
        cursor = index + 1
        while cursor < len(lines) and len(value_lines) < 4:
            following = lines[cursor]
            if _HEADING.match(following) is not None:
                break
            if _table_assertion(following) is not None or _inline_assertion(following) is not None:
                break
            if not following.strip():
                if value_lines:
                    break
                cursor += 1
                continue
            value_lines.append(_strip_prefix_markdown(following))
            excerpt_lines.append(following)
            cursor += 1
        raw_value = " ".join(value_lines)
        value = _display_value(raw_value, max_chars=limits.max_value_chars)
        normalized_value = _normalize_value(value)
        if normalized_value:
            assertions.append(
                _DeckAssertion(
                    field=heading_field,
                    value=value,
                    normalized_value=normalized_value,
                    excerpt="\n".join(excerpt_lines)[: limits.max_excerpt_chars],
                    page_index=page_index,
                    line_index=index,
                    confidence=confidence,
                )
            )
        index = max(index + 1, cursor)

    return tuple(assertions), truncated


def _known_signal(signal: TrustFactorSignal) -> KnowledgeValue[TrustFactorSignal]:
    return KnowledgeValue[TrustFactorSignal].known(signal)


def _recency_signal(
    source_artifact: SourceArtifact,
    extracted_at: datetime,
    evidence_ids: tuple[StableId, ...],
) -> TrustFactorInput:
    event_time = source_artifact.source_event_time
    if event_time.state is not KnowledgeState.KNOWN or event_time.value is None:
        signal = KnowledgeValue[TrustFactorSignal].unknown(
            "The deck's effective date is not established."
        )
        rationale = "Collection time alone does not establish when the assertion was current."
    else:
        age = extracted_at - event_time.value
        if age < -timedelta(days=1):
            signal = KnowledgeValue[TrustFactorSignal].unknown(
                "The source event time is later than extraction."
            )
            rationale = "A future-dated source cannot be assigned a recency signal safely."
        elif age <= timedelta(days=180):
            signal = _known_signal(TrustFactorSignal.STRENGTHENS)
            rationale = "The explicit source event time is within 180 days of extraction."
        elif age <= timedelta(days=730):
            signal = _known_signal(TrustFactorSignal.NEUTRAL)
            rationale = "The explicit source event time is between 181 days and two years old."
        else:
            signal = _known_signal(TrustFactorSignal.WEAKENS)
            rationale = "The explicit source event time is more than two years old."
    return TrustFactorInput(
        kind=TrustFactorKind.RECENCY,
        signal=signal,
        evidence_ids=evidence_ids,
        rationale=rationale,
    )


def _extraction_certainty_signal(
    assertions: tuple[_DeckAssertion, ...], evidence_ids: tuple[StableId, ...]
) -> TrustFactorInput:
    confidence_values: list[float] = []
    for assertion in assertions:
        confidence = assertion.confidence.minimum
        if confidence.state is not KnowledgeState.KNOWN or confidence.value is None:
            confidence = assertion.confidence.average
        if confidence.state is not KnowledgeState.KNOWN or confidence.value is None:
            return TrustFactorInput(
                kind=TrustFactorKind.EXTRACTION_CERTAINTY,
                signal=KnowledgeValue[TrustFactorSignal].unknown(
                    "Page-level OCR confidence is unavailable for at least one excerpt."
                ),
                evidence_ids=evidence_ids,
                rationale="Unavailable confidence remains Unknown and is not treated as weakness.",
            )
        confidence_values.append(confidence.value)

    minimum = min(confidence_values)
    if minimum >= 0.90:
        signal = TrustFactorSignal.STRENGTHENS
        rationale = "Every cited page has OCR confidence of at least 0.90."
    elif minimum < 0.60:
        signal = TrustFactorSignal.WEAKENS
        rationale = "At least one cited page has OCR confidence below 0.60."
    else:
        signal = TrustFactorSignal.NEUTRAL
        rationale = "Cited-page OCR confidence is between 0.60 and 0.90."
    return TrustFactorInput(
        kind=TrustFactorKind.EXTRACTION_CERTAINTY,
        signal=_known_signal(signal),
        evidence_ids=evidence_ids,
        rationale=rationale,
    )


def _trust_factors(
    *,
    assertions: tuple[_DeckAssertion, ...],
    deck_evidence_ids: tuple[StableId, ...],
    independent_evidence_ids: tuple[StableId, ...],
    source_artifact: SourceArtifact,
    extracted_at: datetime,
    conflicted: bool,
) -> tuple[TrustFactorInput, ...]:
    provenance_signal = (
        TrustFactorSignal.STRENGTHENS
        if source_artifact.availability is ArtifactAvailability.AVAILABLE
        else TrustFactorSignal.WEAKENS
    )
    independence_signal = (
        TrustFactorSignal.STRENGTHENS if independent_evidence_ids else TrustFactorSignal.WEAKENS
    )
    return (
        TrustFactorInput(
            kind=TrustFactorKind.PROVENANCE,
            signal=_known_signal(provenance_signal),
            evidence_ids=deck_evidence_ids,
            rationale=(
                "The excerpt resolves to the matching immutable deck artifact and page."
                if provenance_signal is TrustFactorSignal.STRENGTHENS
                else "The original deck artifact is no longer available for inspection."
            ),
        ),
        TrustFactorInput(
            kind=TrustFactorKind.INDEPENDENCE,
            signal=_known_signal(independence_signal),
            evidence_ids=independent_evidence_ids or deck_evidence_ids,
            rationale=(
                "Accepted Evidence from an independent source matches this exact assertion."
                if independent_evidence_ids
                else "A self-authored application deck is not independent verification."
            ),
        ),
        _recency_signal(source_artifact, extracted_at, deck_evidence_ids),
        _extraction_certainty_signal(assertions, deck_evidence_ids),
        TrustFactorInput(
            kind=TrustFactorKind.CORROBORATION,
            signal=_known_signal(
                TrustFactorSignal.STRENGTHENS
                if independent_evidence_ids
                else TrustFactorSignal.NEUTRAL
            ),
            evidence_ids=independent_evidence_ids,
            rationale=(
                "Independent accepted Evidence corroborates this exact value."
                if independent_evidence_ids
                else "No corroboration was supplied; absence is neutral, not disproof."
            ),
        ),
        TrustFactorInput(
            kind=TrustFactorKind.CONTRADICTION,
            signal=_known_signal(
                TrustFactorSignal.WEAKENS if conflicted else TrustFactorSignal.NEUTRAL
            ),
            evidence_ids=deck_evidence_ids,
            rationale=(
                "The deck contains a materially different labeled value for this predicate."
                if conflicted
                else "No competing labeled deck value was detected."
            ),
        ),
    )


def _memo_section(
    *,
    kind: MemoSectionKind,
    fields: tuple[DeckField, ...],
    projected_claims: tuple[_ProjectedClaim, ...],
    conflicted_fields: frozenset[DeckField],
    max_chars: int,
) -> tuple[MemoSection, bool]:
    selected = tuple(item for item in projected_claims if item.field in fields)
    if not selected:
        return (
            MemoSection(
                kind=kind,
                content=KnowledgeValue[LongText].unknown(
                    "No explicit labeled deck evidence supports this section."
                ),
            ),
            False,
        )

    selected_conflicts = tuple(item for item in selected if item.field in conflicted_fields)
    claim_ids = tuple(item.claim.claim_id for item in selected)
    if selected_conflicts:
        alternatives = tuple(
            KnowledgeAlternative[LongText](
                value=item.claim.statement,
                evidence_ids=item.claim.supporting_evidence_ids,
            )
            for item in selected_conflicts
        )
        return (
            MemoSection(
                kind=kind,
                content=KnowledgeValue[LongText].conflicted(
                    "The deck contains competing labeled assertions for this section.",
                    alternatives,
                ),
                material_claim_ids=claim_ids,
            ),
            False,
        )

    lines: list[str] = []
    omitted = False
    for item in selected:
        line = item.claim.statement
        candidate = "\n".join((*lines, line))
        if len(candidate) > max_chars:
            omitted = True
            continue
        lines.append(line)
    evidence_ids = tuple(
        dict.fromkeys(
            evidence_id for item in selected for evidence_id in item.claim.supporting_evidence_ids
        )
    )
    if not lines:
        return (
            MemoSection(
                kind=kind,
                content=KnowledgeValue[LongText].unknown(
                    "Source-backed claims exceed the bounded memo-section display limit; "
                    "inspect the cited Claims and Evidence directly."
                ),
                material_claim_ids=claim_ids,
            ),
            True,
        )
    return (
        MemoSection(
            kind=kind,
            content=KnowledgeValue[LongText].known(
                "\n".join(lines),
                evidence_ids=evidence_ids,
            ),
            material_claim_ids=claim_ids,
        ),
        omitted,
    )


def project_deck_evidence(
    *,
    extraction: PdfExtractionResult,
    source_artifact: SourceArtifact,
    application_id: StableId,
    company_id: StableId,
    opportunity_id: StableId,
    corroboration: tuple[AcceptedClaimCorroboration, ...] = (),
    supported_memo_sections: tuple[SupportedMemoSectionInput, ...] = (),
    limits: DeckEvidenceLimits | None = None,
) -> DeckEvidenceProjection:
    """Project explicit deck labels into bounded, page-cited domain records.

    Integration callers may persist/register the returned records atomically. They must
    validate externally supplied corroborating identifiers against canonical Memory first.
    """

    active_limits = limits or DeckEvidenceLimits()
    if source_artifact.source_category is not SourceCategory.APPLICATION_DECK:
        raise ValueError("deck evidence projection requires an application-deck artifact")
    if extraction.source_artifact_id != source_artifact.source_artifact_id:
        raise ValueError("extraction and Source Artifact identifiers do not match")
    if extraction.input_sha256 != source_artifact.content_sha256:
        raise ValueError("extraction and Source Artifact hashes do not match")
    if extraction.extracted_at < source_artifact.retrieved_at:
        raise ValueError("deck extraction cannot predate artifact retrieval")

    explicit_sections: dict[MemoSectionKind, SupportedMemoSectionInput] = {}
    for section in supported_memo_sections:
        if section.kind in explicit_sections:
            raise ValueError(f"duplicate supported memo section: {section.kind.value}")
        if len(section.content) > active_limits.max_section_chars:
            raise ValueError(
                f"supported memo section exceeds {active_limits.max_section_chars} characters"
            )
        explicit_sections[section.kind] = section

    parsed_assertions: list[_DeckAssertion] = []
    pages = extraction.pages[: active_limits.max_pages]
    truncated = len(extraction.pages) > len(pages)
    omitted_fields = 0
    for page in pages:
        page_assertions, page_truncated = _parse_page(
            markdown=page.markdown,
            page_index=page.page_index,
            confidence=page.confidence,
            limits=active_limits,
        )
        truncated = truncated or page_truncated
        accepted = page_assertions[: active_limits.max_fields_per_page]
        omitted_fields += len(page_assertions) - len(accepted)
        parsed_assertions.extend(accepted)

    if len(parsed_assertions) > active_limits.max_fields:
        omitted_fields += len(parsed_assertions) - active_limits.max_fields
        parsed_assertions = parsed_assertions[: active_limits.max_fields]
    truncated = truncated or omitted_fields > 0

    grouped: defaultdict[tuple[DeckField, str], list[_DeckAssertion]] = defaultdict(list)
    values_by_field: defaultdict[DeckField, set[str]] = defaultdict(set)
    for assertion in parsed_assertions:
        grouped[(assertion.field, assertion.normalized_value)].append(assertion)
        values_by_field[assertion.field].add(assertion.normalized_value)
    conflicted_fields = frozenset(
        field for field, values in values_by_field.items() if len(values) > 1
    )

    corroboration_by_value: defaultdict[tuple[DeckField, str], list[AcceptedClaimCorroboration]] = (
        defaultdict(list)
    )
    for item in corroboration:
        corroboration_by_value[(item.field, _normalize_value(item.asserted_value))].append(item)

    field_position = {field: index for index, field in enumerate(_FIELD_ORDER)}
    group_keys = sorted(grouped, key=lambda item: (field_position[item[0]], item[1]))
    observations: list[Observation] = []
    evidence: list[Evidence] = []
    projected_claims: list[_ProjectedClaim] = []
    evidence_by_field: defaultdict[DeckField, list[StableId]] = defaultdict(list)

    for field, normalized_value in group_keys:
        assertions = tuple(
            sorted(
                grouped[(field, normalized_value)],
                key=lambda item: (item.page_index, item.line_index),
            )
        )
        subject = SubjectRef(
            kind=EntityKind.COMPANY if field in _COMPANY_FIELDS else EntityKind.OPPORTUNITY,
            subject_id=company_id if field in _COMPANY_FIELDS else opportunity_id,
        )
        claim_id = _stable_id(
            "claim",
            source_artifact.source_artifact_id,
            application_id,
            field.value,
            normalized_value,
        )
        deck_evidence_ids: list[StableId] = []
        group_observations: list[Observation] = []
        group_evidence: list[Evidence] = []
        for assertion in assertions:
            observation_id = _stable_id(
                "observation",
                source_artifact.source_artifact_id,
                field.value,
                normalized_value,
                assertion.page_index,
                assertion.line_index,
            )
            evidence_id = _stable_id("evidence", claim_id, observation_id)
            deck_evidence_ids.append(evidence_id)
            group_observations.append(
                Observation(
                    observation_id=observation_id,
                    observation_version_id=_stable_id("observation-version", observation_id),
                    source_artifact_id=source_artifact.source_artifact_id,
                    subject=subject,
                    predicate=_FIELD_PREDICATE[field],
                    observed_value=KnowledgeValue[ScalarValue].known(assertion.value),
                    locator=SourceLocator(
                        kind=SourceLocatorKind.DOCUMENT_PAGE,
                        locator=assertion.locator,
                        excerpt=assertion.excerpt,
                    ),
                    retrieved_at=source_artifact.retrieved_at,
                    source_event_time=source_artifact.source_event_time,
                    extraction_method=ExtractionMethod.DETERMINISTIC,
                    extraction_version=extraction.extractor_version,
                    verification_state=(
                        VerificationState.DISPUTED
                        if field in conflicted_fields
                        else VerificationState.SOURCE_ASSERTED
                    ),
                )
            )
            group_evidence.append(
                Evidence(
                    evidence_id=evidence_id,
                    claim_id=claim_id,
                    source_artifact_id=source_artifact.source_artifact_id,
                    observation_id=observation_id,
                    stance=EvidenceStance.SUPPORTS,
                    locator=SourceLocator(
                        kind=SourceLocatorKind.DOCUMENT_PAGE,
                        locator=assertion.locator,
                        excerpt=assertion.excerpt,
                    ),
                    collected_at=source_artifact.retrieved_at,
                    source_event_time=source_artifact.source_event_time,
                    availability=source_artifact.availability,
                )
            )

        independent_evidence_ids = tuple(
            dict.fromkeys(
                evidence_id
                for item in corroboration_by_value[(field, normalized_value)]
                if item.independent_source
                for evidence_id in item.evidence_ids
            )
        )
        deck_ids = tuple(deck_evidence_ids)
        is_conflicted = field in conflicted_fields
        trust = score_claim_trust(
            _trust_factors(
                assertions=assertions,
                deck_evidence_ids=deck_ids,
                independent_evidence_ids=independent_evidence_ids,
                source_artifact=source_artifact,
                extracted_at=extraction.extracted_at,
                conflicted=is_conflicted,
            ),
            has_supporting_evidence=bool(deck_ids),
            unresolved_blocking_contradiction=is_conflicted,
        )
        if is_conflicted:
            status = ClaimStatus.UNRESOLVED
        elif independent_evidence_ids:
            status = ClaimStatus.SUPPORTED
            group_observations = [
                item.model_copy(update={"verification_state": VerificationState.CORROBORATED})
                for item in group_observations
            ]
        else:
            status = ClaimStatus.ASSERTED_UNVERIFIED
        claim = Claim(
            claim_id=claim_id,
            claim_version_id=_stable_id("claim-version", claim_id),
            subject=subject,
            predicate=_FIELD_PREDICATE[field],
            statement=f"Pitch deck asserts {_FIELD_LABEL[field]}: {assertions[0].value}",
            status=status,
            origin=ClaimOrigin.SOURCE_ASSERTION,
            as_of=source_artifact.retrieved_at,
            created_at=extraction.extracted_at,
            supporting_evidence_ids=(*deck_ids, *independent_evidence_ids),
            trust=trust,
        )
        observations.extend(group_observations)
        evidence.extend(group_evidence)
        evidence_by_field[field].extend(deck_ids)
        projected_claims.append(
            _ProjectedClaim(field=field, value=assertions[0].value, claim=claim)
        )

    contradictions: list[Contradiction] = []
    for field in _FIELD_ORDER:
        if field not in conflicted_fields:
            continue
        competing = tuple(item for item in projected_claims if item.field is field)
        contradiction_id = _stable_id(
            "contradiction",
            source_artifact.source_artifact_id,
            field.value,
            *(item.claim.claim_id for item in competing),
        )
        contradictions.append(
            Contradiction(
                contradiction_id=contradiction_id,
                contradiction_version_id=_stable_id("contradiction-version", contradiction_id),
                claim_ids=tuple(item.claim.claim_id for item in competing),
                evidence_ids=tuple(evidence_by_field[field]),
                status=ContradictionStatus.UNRESOLVED,
                blocking=True,
                summary=(
                    f"Pitch deck contains competing labeled {_FIELD_LABEL[field]} assertions."
                ),
                detected_at=extraction.extracted_at,
            )
        )

    projected_tuple = tuple(projected_claims)
    company_snapshot, company_omitted = _memo_section(
        kind=MemoSectionKind.COMPANY_SNAPSHOT,
        fields=(DeckField.COMPANY, DeckField.MARKET, DeckField.FOUNDERS, DeckField.FUNDING),
        projected_claims=projected_tuple,
        conflicted_fields=conflicted_fields,
        max_chars=active_limits.max_section_chars,
    )
    problem_product, problem_omitted = _memo_section(
        kind=MemoSectionKind.PROBLEM_AND_PRODUCT,
        fields=(DeckField.PROBLEM, DeckField.PRODUCT),
        projected_claims=projected_tuple,
        conflicted_fields=conflicted_fields,
        max_chars=active_limits.max_section_chars,
    )
    traction, traction_omitted = _memo_section(
        kind=MemoSectionKind.TRACTION_AND_KPIS,
        fields=(DeckField.TRACTION_AND_KPIS,),
        projected_claims=projected_tuple,
        conflicted_fields=conflicted_fields,
        max_chars=active_limits.max_section_chars,
    )

    def analysis_section(kind: MemoSectionKind) -> MemoSection:
        supplied = explicit_sections.get(kind)
        if supplied is None:
            return MemoSection(
                kind=kind,
                content=KnowledgeValue[LongText].unknown(
                    "This section requires explicit supported analysis and is not derived "
                    "from deck prose."
                ),
            )
        return MemoSection(
            kind=kind,
            content=KnowledgeValue[LongText].known(
                supplied.content,
                evidence_ids=supplied.evidence_ids,
            ),
            material_claim_ids=supplied.material_claim_ids,
        )

    memo_sections = (
        company_snapshot,
        analysis_section(MemoSectionKind.INVESTMENT_HYPOTHESES),
        analysis_section(MemoSectionKind.SWOT),
        problem_product,
        traction,
    )
    truncated = truncated or company_omitted or problem_omitted or traction_omitted

    return DeckEvidenceProjection(
        projection_id=_stable_id(
            "deck-projection",
            extraction.extraction_id,
            application_id,
            opportunity_id,
            DECK_EVIDENCE_PROJECTION_VERSION,
        ),
        projected_at=extraction.extracted_at,
        extraction_id=extraction.extraction_id,
        source_artifact_id=source_artifact.source_artifact_id,
        application_id=application_id,
        company_id=company_id,
        opportunity_id=opportunity_id,
        pages_examined=len(pages),
        omitted_page_count=len(extraction.pages) - len(pages),
        omitted_field_count=omitted_fields,
        truncated=truncated,
        observations=tuple(observations),
        evidence=tuple(evidence),
        claims=tuple(item.claim for item in projected_claims),
        contradictions=tuple(contradictions),
        memo_sections=memo_sections,
    )
