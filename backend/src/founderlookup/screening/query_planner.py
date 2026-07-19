"""Deterministic, framework-neutral planning for compound sourcing queries.

The planner translates a small, allowlisted natural-language vocabulary into the
shared OpportunityQueryPlan contract.  It never executes the input and never
produces SQL, shell commands, or provider-specific expressions.  Text outside
the supported vocabulary remains inspectable as an unresolved source span.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Final, Protocol, Self, runtime_checkable

from pydantic import Field, StringConstraints, model_validator

from founderlookup.domain.common import DomainModel, NonBlankStr, ScalarValue
from founderlookup.domain.discovery import BoundedRetrievalRequest
from founderlookup.domain.evidence import SourceCategory
from founderlookup.domain.query import (
    CriterionStrength,
    OpportunityQueryPlan,
    QueryCriterion,
    QueryCriterionField,
    QueryOperator,
    QueryPlanningMode,
    QueryPlanState,
    UnknownValuePolicy,
    UnresolvedQueryPhrase,
)

DETERMINISTIC_QUERY_PLANNER_VERSION: Final = "deterministic-query-planner.v0"
MAX_QUERY_CHARACTERS: Final = 400
MAX_PLAN_RESULTS: Final = 100
MAX_RETRIEVAL_RESULTS: Final = 20
MAX_RETRIEVAL_PAGES: Final = 3
MAX_RETRIEVAL_TIMEOUT_SECONDS: Final = 30

QueryText = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=MAX_QUERY_CHARACTERS),
]
VocabularyPhrase = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=100),
]
BoundedPlanResults = Annotated[int, Field(strict=True, ge=1, le=MAX_PLAN_RESULTS)]
BoundedRetrievalResults = Annotated[int, Field(strict=True, ge=1, le=MAX_RETRIEVAL_RESULTS)]
BoundedRetrievalPages = Annotated[int, Field(strict=True, ge=1, le=MAX_RETRIEVAL_PAGES)]
BoundedRetrievalTimeout = Annotated[int, Field(strict=True, ge=1, le=MAX_RETRIEVAL_TIMEOUT_SECONDS)]

_CONTROLLED_FIELDS: Final = frozenset(
    {
        QueryCriterionField.GEOGRAPHY,
        QueryCriterionField.SECTOR,
        QueryCriterionField.ACCELERATOR,
    }
)
_DEFAULT_STRENGTHS: Final = {
    QueryCriterionField.TECHNICAL_FOUNDER: CriterionStrength.SCORED_PREFERENCE,
    QueryCriterionField.GEOGRAPHY: CriterionStrength.SCORED_PREFERENCE,
    QueryCriterionField.SECTOR: CriterionStrength.HARD_CONSTRAINT,
    QueryCriterionField.ENTERPRISE_TRACTION: CriterionStrength.SCORED_PREFERENCE,
    QueryCriterionField.PRIOR_VC_BACKING: CriterionStrength.HARD_CONSTRAINT,
    QueryCriterionField.ACCELERATOR: CriterionStrength.SCORED_PREFERENCE,
}
_SOURCE_CATEGORY_ORDER: Final = tuple(SourceCategory)
_SOURCE_CATEGORIES_BY_FIELD: Final = {
    QueryCriterionField.TECHNICAL_FOUNDER: (SourceCategory.DEVELOPER_ACTIVITY,),
    QueryCriterionField.GEOGRAPHY: (SourceCategory.COMPANY_UPDATE,),
    QueryCriterionField.SECTOR: (
        SourceCategory.PRODUCT_LAUNCH,
        SourceCategory.COMPANY_UPDATE,
    ),
    QueryCriterionField.ENTERPRISE_TRACTION: (
        SourceCategory.COMPANY_UPDATE,
        SourceCategory.PRODUCT_LAUNCH,
    ),
    QueryCriterionField.PRIOR_VC_BACKING: (SourceCategory.COMPANY_UPDATE,),
    QueryCriterionField.ACCELERATOR: (SourceCategory.ACCELERATOR_COHORT,),
}
_IGNORABLE_WORDS: Final = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "building",
        "company",
        "companies",
        "find",
        "for",
        "founder",
        "founders",
        "in",
        "is",
        "me",
        "of",
        "please",
        "show",
        "that",
        "the",
        "to",
        "with",
    }
)
_SUBJECTIVE_PATTERN: Final = re.compile(
    r"\b(?:top[- ]tier|world[- ]class|best[- ]in[- ]class|exceptional|promising)\b",
    re.IGNORECASE,
)
_UNSAFE_TEXT_PATTERNS: Final = (
    re.compile(
        r"\b(?:select|insert|update|delete|drop|alter|truncate|create)\b[^,;]*",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[\s;|&])(?:sudo|rm\s+-rf|curl|wget|bash|sh)\b[^,;]*",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bignore\s+(?:all\s+)?(?:previous|prior)\s+instructions\b[^,;]*",
        re.IGNORECASE,
    ),
)


class ControlledVocabularyEntry(DomainModel):
    """Human-supplied mapping for an otherwise subjective or local phrase."""

    phrase: VocabularyPhrase
    field: QueryCriterionField
    canonical_values: Annotated[tuple[NonBlankStr, ...], Field(min_length=1, max_length=20)]

    @model_validator(mode="after")
    def validate_mapping(self) -> Self:
        if self.field not in _CONTROLLED_FIELDS:
            raise ValueError("controlled vocabulary supports geography, sector, and accelerator")
        if not self.phrase.strip() or _contains_control_character(self.phrase):
            raise ValueError("controlled vocabulary phrase must be printable and non-blank")
        normalized_values = tuple(value.casefold() for value in self.canonical_values)
        if len(normalized_values) != len(set(normalized_values)):
            raise ValueError("controlled vocabulary values must be unique")
        return self


class QueryPlannerRequest(DomainModel):
    """One investor interaction plus explicit, provider-neutral resource budgets."""

    raw_query: QueryText
    max_results: BoundedPlanResults = 25
    retrieval_max_results: BoundedRetrievalResults = 10
    retrieval_max_pages: BoundedRetrievalPages = 2
    retrieval_timeout_seconds: BoundedRetrievalTimeout = 10
    controlled_vocabulary: tuple[ControlledVocabularyEntry, ...] = ()

    @model_validator(mode="after")
    def validate_input(self) -> Self:
        if not self.raw_query.strip():
            raise ValueError("raw query must contain non-whitespace text")
        if _contains_control_character(self.raw_query):
            raise ValueError("raw query must not contain control characters")
        phrases = tuple(entry.phrase.casefold() for entry in self.controlled_vocabulary)
        if len(phrases) != len(set(phrases)):
            raise ValueError("controlled vocabulary phrases must be unique")
        return self


@runtime_checkable
class QueryPlannerPort(Protocol):
    """Map one request to one inspectable plan without executing the plan."""

    def plan(self, request: QueryPlannerRequest) -> OpportunityQueryPlan: ...


@dataclass(frozen=True, slots=True)
class _Recognition:
    start: int
    end: int
    field: QueryCriterionField
    operator: QueryOperator
    operands: tuple[ScalarValue, ...]
    source_text: str


@dataclass(frozen=True, slots=True)
class _UnresolvedSpan:
    start: int
    end: int
    reason: str


@dataclass(frozen=True, slots=True)
class _BuiltinRule:
    field: QueryCriterionField
    pattern: re.Pattern[str]
    operator: QueryOperator
    operands: tuple[ScalarValue, ...]


_BUILTIN_RULES: Final = (
    _BuiltinRule(
        QueryCriterionField.TECHNICAL_FOUNDER,
        re.compile(r"\b(?:non[- ]technical|not\s+(?:a\s+)?technical)\s+founders?\b", re.I),
        QueryOperator.EQUALS,
        (False,),
    ),
    _BuiltinRule(
        QueryCriterionField.TECHNICAL_FOUNDER,
        re.compile(r"\btechnical\s+founders?\b", re.I),
        QueryOperator.EQUALS,
        (True,),
    ),
    _BuiltinRule(
        QueryCriterionField.GEOGRAPHY,
        re.compile(r"\bberlin\b", re.I),
        QueryOperator.EQUALS,
        ("Berlin",),
    ),
    _BuiltinRule(
        QueryCriterionField.SECTOR,
        re.compile(r"\b(?:ai|artificial\s+intelligence)[ -]?infra(?:structure)?\b", re.I),
        QueryOperator.EQUALS,
        ("ai_infrastructure",),
    ),
    _BuiltinRule(
        QueryCriterionField.ENTERPRISE_TRACTION,
        re.compile(r"\bno\s+enterprise\s+traction\b", re.I),
        QueryOperator.EQUALS,
        (False,),
    ),
    _BuiltinRule(
        QueryCriterionField.ENTERPRISE_TRACTION,
        re.compile(r"\benterprise\s+traction\b", re.I),
        QueryOperator.EQUALS,
        (True,),
    ),
    _BuiltinRule(
        QueryCriterionField.PRIOR_VC_BACKING,
        re.compile(
            r"\b(?:no|without)\s+(?:prior\s+)?(?:vc|venture\s+capital)\s+"
            r"(?:backing|funding)\b",
            re.I,
        ),
        QueryOperator.EQUALS,
        (False,),
    ),
    _BuiltinRule(
        QueryCriterionField.PRIOR_VC_BACKING,
        re.compile(r"\b(?:prior\s+)?(?:vc|venture\s+capital)\s+(?:backing|funding)\b", re.I),
        QueryOperator.EQUALS,
        (True,),
    ),
    _BuiltinRule(
        QueryCriterionField.ACCELERATOR,
        re.compile(r"\baccelerator(?:\s+(?:history|background|experience))?\b", re.I),
        QueryOperator.IS_KNOWN,
        (),
    ),
)


class DeterministicQueryPlanner:
    """Schema-constrained baseline for the P0 compound-query experience."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    def plan(self, request: QueryPlannerRequest) -> OpportunityQueryPlan:
        raw_query = request.raw_query
        recognized = self._recognize(raw_query, request.controlled_vocabulary)
        criteria_recognitions, duplicate_spans = _deduplicate_fields(recognized)
        unresolved = [*_unsafe_spans(raw_query), *duplicate_spans]
        occupied = [(item.start, item.end) for item in recognized]
        occupied.extend((item.start, item.end) for item in unresolved)

        for match in _SUBJECTIVE_PATTERN.finditer(raw_query):
            if not _overlaps(match.start(), match.end(), occupied):
                unresolved.append(
                    _UnresolvedSpan(
                        match.start(), match.end(), "subjective term needs confirmation"
                    )
                )
                occupied.append((match.start(), match.end()))

        unresolved.extend(_unrecognized_spans(raw_query, occupied))
        unresolved = _deduplicate_unresolved(unresolved)

        request_digest = _request_digest(request)
        criteria = tuple(
            _criterion(recognition, request_digest, index)
            for index, recognition in enumerate(criteria_recognitions, start=1)
        )
        retrieval_requests = self._retrieval_requests(
            request,
            criteria_recognitions,
            request_digest,
        )
        unresolved_phrases = tuple(
            UnresolvedQueryPhrase(
                text=raw_query[item.start : item.end],
                start_offset=item.start,
                end_offset=item.end,
                reason=item.reason,
            )
            for item in unresolved
        )
        state = QueryPlanState.VALIDATED if criteria else QueryPlanState.REJECTED

        return OpportunityQueryPlan(
            query_plan_id=f"query-plan:{request_digest[:24]}",
            query_plan_version_id=f"query-plan-version:{request_digest[:24]}",
            raw_query=raw_query,
            planning_mode=QueryPlanningMode.DETERMINISTIC,
            planner_version=DETERMINISTIC_QUERY_PLANNER_VERSION,
            state=state,
            criteria=criteria,
            retrieval_requests=retrieval_requests if state is QueryPlanState.VALIDATED else (),
            unresolved_phrases=unresolved_phrases,
            max_results=request.max_results,
            created_at=self._clock(),
        )

    @staticmethod
    def _recognize(
        raw_query: str,
        vocabulary: tuple[ControlledVocabularyEntry, ...],
    ) -> tuple[_Recognition, ...]:
        recognized: list[_Recognition] = []
        occupied: list[tuple[int, int]] = []

        entries = sorted(
            vocabulary,
            key=lambda item: (-len(item.phrase), item.field.value, item.phrase.casefold()),
        )
        for entry in entries:
            pattern = re.compile(
                rf"(?<!\w){re.escape(entry.phrase)}(?!\w)",
                re.IGNORECASE,
            )
            for match in pattern.finditer(raw_query):
                if _overlaps(match.start(), match.end(), occupied):
                    continue
                operator = (
                    QueryOperator.EQUALS
                    if len(entry.canonical_values) == 1
                    else QueryOperator.ANY_OF
                )
                recognized.append(
                    _Recognition(
                        start=match.start(),
                        end=match.end(),
                        field=entry.field,
                        operator=operator,
                        operands=entry.canonical_values,
                        source_text=match.group(),
                    )
                )
                occupied.append((match.start(), match.end()))

        for rule in _BUILTIN_RULES:
            for match in rule.pattern.finditer(raw_query):
                if _overlaps(match.start(), match.end(), occupied):
                    continue
                recognized.append(
                    _Recognition(
                        start=match.start(),
                        end=match.end(),
                        field=rule.field,
                        operator=rule.operator,
                        operands=rule.operands,
                        source_text=match.group(),
                    )
                )
                occupied.append((match.start(), match.end()))

        return tuple(sorted(recognized, key=lambda item: (item.start, item.end, item.field.value)))

    @staticmethod
    def _retrieval_requests(
        request: QueryPlannerRequest,
        recognized: tuple[_Recognition, ...],
        request_digest: str,
    ) -> tuple[BoundedRetrievalRequest, ...]:
        if not recognized:
            return ()
        query_parts: list[str] = []
        categories: set[SourceCategory] = set()
        for item in recognized:
            query_parts.append(_retrieval_text(item))
            categories.update(_SOURCE_CATEGORIES_BY_FIELD[item.field])
        query = " ".join(dict.fromkeys(query_parts))
        query = " ".join(query.split())[:MAX_QUERY_CHARACTERS].rstrip()
        ordered_categories = tuple(
            category for category in _SOURCE_CATEGORY_ORDER if category in categories
        )
        retrieval_digest = hashlib.sha256(query.encode()).hexdigest()[:16]
        return (
            BoundedRetrievalRequest(
                retrieval_request_id=(f"retrieval:{request_digest[:12]}:{retrieval_digest}"),
                query=query,
                source_categories=ordered_categories,
                max_results=request.retrieval_max_results,
                max_pages=request.retrieval_max_pages,
                timeout_seconds=request.retrieval_timeout_seconds,
            ),
        )


def _criterion(
    recognition: _Recognition,
    request_digest: str,
    index: int,
) -> QueryCriterion:
    strength = _DEFAULT_STRENGTHS[recognition.field]
    unknown_policy = (
        UnknownValuePolicy.MANUAL_REVIEW
        if strength is CriterionStrength.HARD_CONSTRAINT
        else UnknownValuePolicy.PRESERVE_AS_UNKNOWN
    )
    criterion_digest = hashlib.sha256(
        (
            f"{recognition.field.value}|{recognition.operator.value}|"
            f"{recognition.operands!r}|{recognition.start}|{recognition.end}"
        ).encode()
    ).hexdigest()[:16]
    return QueryCriterion(
        criterion_id=f"criterion:{request_digest[:10]}:{index}:{criterion_digest}",
        field=recognition.field,
        operator=recognition.operator,
        operands=recognition.operands,
        strength=strength,
        unknown_policy=unknown_policy,
        source_text=recognition.source_text,
    )


def _deduplicate_fields(
    recognized: Iterable[_Recognition],
) -> tuple[tuple[_Recognition, ...], tuple[_UnresolvedSpan, ...]]:
    accepted: list[_Recognition] = []
    unresolved: list[_UnresolvedSpan] = []
    fields: set[QueryCriterionField] = set()
    for item in recognized:
        if item.field in fields:
            unresolved.append(
                _UnresolvedSpan(
                    item.start,
                    item.end,
                    "multiple phrases map to the same criterion field",
                )
            )
            continue
        fields.add(item.field)
        accepted.append(item)
    return tuple(accepted), tuple(unresolved)


def _unsafe_spans(raw_query: str) -> tuple[_UnresolvedSpan, ...]:
    spans: list[_UnresolvedSpan] = []
    occupied: list[tuple[int, int]] = []
    for pattern in _UNSAFE_TEXT_PATTERNS:
        for match in pattern.finditer(raw_query):
            start, end = _trim_span(raw_query, match.start(), match.end())
            if start == end or _overlaps(start, end, occupied):
                continue
            spans.append(_UnresolvedSpan(start, end, "executable-looking text is never planned"))
            occupied.append((start, end))
    return tuple(spans)


def _unrecognized_spans(
    raw_query: str,
    occupied: list[tuple[int, int]],
) -> tuple[_UnresolvedSpan, ...]:
    spans: list[_UnresolvedSpan] = []
    cursor = 0
    for start, end in sorted(occupied):
        if cursor < start:
            candidate = _meaningful_span(raw_query, cursor, start)
            if candidate is not None:
                spans.append(_UnresolvedSpan(*candidate, "phrase is outside supported vocabulary"))
        cursor = max(cursor, end)
    if cursor < len(raw_query):
        candidate = _meaningful_span(raw_query, cursor, len(raw_query))
        if candidate is not None:
            spans.append(_UnresolvedSpan(*candidate, "phrase is outside supported vocabulary"))
    return tuple(spans)


def _meaningful_span(raw_query: str, start: int, end: int) -> tuple[int, int] | None:
    start, end = _trim_span(raw_query, start, end)
    if start == end:
        return None
    words = re.findall(r"[\w'-]+", raw_query[start:end].casefold())
    if not words or all(word in _IGNORABLE_WORDS for word in words):
        return None
    return start, end


def _deduplicate_unresolved(spans: Iterable[_UnresolvedSpan]) -> list[_UnresolvedSpan]:
    result: list[_UnresolvedSpan] = []
    for item in sorted(spans, key=lambda span: (span.start, span.end, span.reason)):
        if any(_overlaps(item.start, item.end, [(other.start, other.end)]) for other in result):
            continue
        result.append(item)
    return result


def _retrieval_text(recognition: _Recognition) -> str:
    if recognition.field is QueryCriterionField.PRIOR_VC_BACKING:
        # Discovery silence cannot prove a negative, so retrieve affirmative
        # financing evidence and let deterministic evaluation preserve Unknown.
        return "prior venture capital backing"
    if recognition.field is QueryCriterionField.ACCELERATOR:
        if recognition.operands:
            return " ".join(str(value) for value in recognition.operands)
        return "accelerator history"
    if recognition.field is QueryCriterionField.SECTOR:
        return " ".join(str(value).replace("_", " ") for value in recognition.operands)
    return recognition.source_text


def _request_digest(request: QueryPlannerRequest) -> str:
    payload = {
        "planner_version": DETERMINISTIC_QUERY_PLANNER_VERSION,
        "request": request.model_dump(mode="json"),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _trim_span(value: str, start: int, end: int) -> tuple[int, int]:
    while start < end and (value[start].isspace() or value[start] in ",;:|&-"):
        start += 1
    while end > start and (value[end - 1].isspace() or value[end - 1] in ",;:|&-"):
        end -= 1
    return start, end


def _overlaps(start: int, end: int, spans: Iterable[tuple[int, int]]) -> bool:
    return any(start < other_end and end > other_start for other_start, other_end in spans)


def _contains_control_character(value: str) -> bool:
    return any(unicodedata.category(character).startswith("C") for character in value)
