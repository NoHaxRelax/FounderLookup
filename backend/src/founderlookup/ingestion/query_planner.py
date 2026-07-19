"""Framework-neutral query-planner port and a deterministic baseline.

This module translates ONE natural-language sourcing request (an investor
thesis) into a validated ``OpportunityQueryPlan``. It exposes two things:

``QueryPlannerPort``
    A ``runtime_checkable`` Protocol with a single ``async def plan`` method,
    mirroring the async port style used elsewhere in the ingestion and
    screening lanes. Nothing here imports a model or an agent framework; a
    model-assisted planner is a separate, gated implementation of this port.

``DeterministicQueryPlanner``
    A pure, deterministic baseline. It scans the raw query against a FIXED,
    documented lexicon of cue phrases (plus a few numeric patterns) and maps
    each recognized cue to a typed ``QueryCriterion`` and, where the cue names
    a source, to a ``BoundedRetrievalRequest``. Identical input always yields
    an identical plan: the lexicon is fixed, the scan order is fixed, there is
    no randomness, and the only clock value used is the caller-supplied
    ``created_at``.

Design lens: HONESTY and UNRESOLVED SPANS.
    * The planner NEVER invents a criterion for text it did not match. Any
      meaningful span it does not recognize is recorded as an
      ``UnresolvedQueryPhrase`` rather than guessed at.
    * Offsets are honest: for every unresolved phrase ``raw_query[start:end]``
      equals ``text`` exactly, and ``end_offset > start_offset``.
    * Every emitted criterion carries an explicit ``unknown_policy`` and a
      deliberately chosen ``strength`` (hard constraint vs scored preference),
      derived from cue words such as "must"/"only" and "prefer"/"ideally".

The lexicon
    Each ``_CueRule`` binds a set of lowercase cue phrases to a target field,
    operator, operands, a default strength, an Unknown policy, and an optional
    source category. Source cues additionally emit one bounded retrieval
    request per distinct category. Numeric cues (check size, ownership target)
    are handled by a small, explicit set of regular expressions so that a
    stated range maps to ``between`` and a qualified single value maps to a
    directional comparison; a bare, unqualified amount is deliberately left
    unresolved rather than guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, Self, runtime_checkable

from pydantic import model_validator

from founderlookup.domain.common import (
    DomainModel,
    NonBlankStr,
    PositiveInt,
    ScalarValue,
    StableId,
    UTCDateTime,
)
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

DETERMINISTIC_QUERY_PLANNER_VERSION = "deterministic-query-planner.v0"

_Field = QueryCriterionField
_Op = QueryOperator
_Strength = CriterionStrength
_Policy = UnknownValuePolicy
_HARD = _Strength.HARD_CONSTRAINT
_PREF = _Strength.SCORED_PREFERENCE


class QueryPlanRequest(DomainModel):
    """Immutable input for one planning call.

    Carries the raw thesis, the identifiers and clock the plan must be stamped
    with, and the retrieval bounds the caller wants written onto every emitted
    ``BoundedRetrievalRequest``. ``allowed_source_categories`` restricts which
    source categories may produce a retrieval request; empty means no
    restriction.
    """

    raw_query: NonBlankStr
    query_plan_id: StableId
    query_plan_version_id: StableId
    supersedes_query_plan_version_id: StableId | None = None
    created_at: UTCDateTime
    max_results: PositiveInt = 25
    allowed_source_categories: tuple[SourceCategory, ...] = ()
    allowed_domains: tuple[NonBlankStr, ...] = ()
    retrieval_max_results: PositiveInt = 25
    retrieval_max_pages: PositiveInt = 3
    retrieval_timeout_seconds: PositiveInt = 15

    @model_validator(mode="after")
    def _reject_self_supersede(self) -> Self:
        # Mirror the frozen OpportunityQueryPlan rule at the port boundary, so an invalid
        # id pairing fails as a clear caller error here rather than crashing deep in plan
        # construction. Do not coerce it silently: that would rewrite caller intent.
        if self.supersedes_query_plan_version_id == self.query_plan_version_id:
            raise ValueError("supersedes_query_plan_version_id cannot equal query_plan_version_id")
        return self


@runtime_checkable
class QueryPlannerPort(Protocol):
    """Turn one natural-language sourcing request into a validated plan."""

    async def plan(self, request: QueryPlanRequest) -> OpportunityQueryPlan:
        """Return a schema-valid plan; never perform real I/O or mutate state."""
        ...


@dataclass(frozen=True)
class _CueRule:
    """One fixed lexicon entry: a set of phrases and the criterion they mean."""

    phrases: tuple[str, ...]
    field: QueryCriterionField
    operator: QueryOperator
    operands: tuple[ScalarValue, ...]
    default_strength: CriterionStrength
    unknown_policy: UnknownValuePolicy
    source_category: SourceCategory | None = None


@dataclass(frozen=True)
class _Match:
    """A recognized span resolved to a concrete criterion intent."""

    start: int
    end: int
    field: QueryCriterionField
    operator: QueryOperator
    operands: tuple[ScalarValue, ...]
    default_strength: CriterionStrength
    unknown_policy: UnknownValuePolicy
    source_category: SourceCategory | None
    priority: int


def _source_rule(phrases: tuple[str, ...], category: SourceCategory) -> _CueRule:
    """Build a source-category cue that also drives a retrieval request."""

    return _CueRule(
        phrases=phrases,
        field=_Field.SOURCE_CATEGORY,
        operator=_Op.EQUALS,
        operands=(category.value,),
        default_strength=_PREF,
        unknown_policy=_Policy.PRESERVE_AS_UNKNOWN,
        source_category=category,
    )


# The FIXED lexicon. Order is significant only as a tie-break when two rules
# match an identical span; longer spans always win over shorter ones first.
_LEXICON: tuple[_CueRule, ...] = (
    _CueRule(
        phrases=(
            "technical co-founders",
            "technical co-founder",
            "technical cofounder",
            "technical founders",
            "technical founder",
            "deeply technical",
            "strong engineering background",
            "engineering background",
        ),
        field=_Field.TECHNICAL_FOUNDER,
        operator=_Op.EQUALS,
        operands=(True,),
        default_strength=_PREF,
        unknown_policy=_Policy.NEEDS_INFORMATION,
        source_category=SourceCategory.DEVELOPER_ACTIVITY,
    ),
    # Geography: each place is its own equals cue. Unknown geography is
    # preserved as Unknown and never read as "outside the thesis".
    _CueRule(
        ("berlin",), _Field.GEOGRAPHY, _Op.EQUALS, ("Berlin",), _HARD, _Policy.PRESERVE_AS_UNKNOWN
    ),
    _CueRule(
        ("san francisco", "bay area", "sf"),
        _Field.GEOGRAPHY,
        _Op.EQUALS,
        ("San Francisco Bay Area",),
        _HARD,
        _Policy.PRESERVE_AS_UNKNOWN,
    ),
    _CueRule(
        ("new york", "nyc"),
        _Field.GEOGRAPHY,
        _Op.EQUALS,
        ("New York",),
        _HARD,
        _Policy.PRESERVE_AS_UNKNOWN,
    ),
    _CueRule(
        ("london",), _Field.GEOGRAPHY, _Op.EQUALS, ("London",), _HARD, _Policy.PRESERVE_AS_UNKNOWN
    ),
    _CueRule(
        ("europe", "european"),
        _Field.GEOGRAPHY,
        _Op.EQUALS,
        ("Europe",),
        _HARD,
        _Policy.PRESERVE_AS_UNKNOWN,
    ),
    _CueRule(
        ("united states", "usa", "u.s."),
        _Field.GEOGRAPHY,
        _Op.EQUALS,
        ("United States",),
        _HARD,
        _Policy.PRESERVE_AS_UNKNOWN,
    ),
    _CueRule(
        ("nordics", "nordic"),
        _Field.GEOGRAPHY,
        _Op.EQUALS,
        ("Nordics",),
        _HARD,
        _Policy.PRESERVE_AS_UNKNOWN,
    ),
    _CueRule(
        ("india",), _Field.GEOGRAPHY, _Op.EQUALS, ("India",), _HARD, _Policy.PRESERVE_AS_UNKNOWN
    ),
    # Sector.
    _CueRule(
        ("ai infrastructure", "ai infra"),
        _Field.SECTOR,
        _Op.EQUALS,
        ("AI infrastructure",),
        _HARD,
        _Policy.NEEDS_INFORMATION,
    ),
    _CueRule(
        ("artificial intelligence", "machine learning", "ai/ml", "ai", "ml"),
        _Field.SECTOR,
        _Op.EQUALS,
        ("AI/ML",),
        _HARD,
        _Policy.NEEDS_INFORMATION,
    ),
    _CueRule(
        ("fintech", "financial technology"),
        _Field.SECTOR,
        _Op.EQUALS,
        ("fintech",),
        _HARD,
        _Policy.NEEDS_INFORMATION,
    ),
    _CueRule(
        ("climate tech", "cleantech", "climate"),
        _Field.SECTOR,
        _Op.EQUALS,
        ("climate tech",),
        _HARD,
        _Policy.NEEDS_INFORMATION,
    ),
    _CueRule(
        ("developer tools", "developer tooling", "devtools", "dev tools"),
        _Field.SECTOR,
        _Op.EQUALS,
        ("developer tools",),
        _HARD,
        _Policy.NEEDS_INFORMATION,
    ),
    _CueRule(
        ("cybersecurity", "cyber security", "infosec"),
        _Field.SECTOR,
        _Op.EQUALS,
        ("cybersecurity",),
        _HARD,
        _Policy.NEEDS_INFORMATION,
    ),
    _CueRule(
        ("digital health", "health tech", "healthtech", "healthcare"),
        _Field.SECTOR,
        _Op.EQUALS,
        ("healthcare",),
        _HARD,
        _Policy.NEEDS_INFORMATION,
    ),
    _CueRule(
        ("biotechnology", "biotech"),
        _Field.SECTOR,
        _Op.EQUALS,
        ("biotech",),
        _HARD,
        _Policy.NEEDS_INFORMATION,
    ),
    _CueRule(("saas",), _Field.SECTOR, _Op.EQUALS, ("SaaS",), _HARD, _Policy.NEEDS_INFORMATION),
    _CueRule(
        ("robotics",), _Field.SECTOR, _Op.EQUALS, ("robotics",), _HARD, _Policy.NEEDS_INFORMATION
    ),
    _CueRule(
        ("blockchain", "web3", "crypto"),
        _Field.SECTOR,
        _Op.EQUALS,
        ("crypto",),
        _HARD,
        _Policy.NEEDS_INFORMATION,
    ),
    # Stage.
    _CueRule(
        ("pre-seed", "pre seed", "preseed"),
        _Field.STAGE,
        _Op.EQUALS,
        ("pre_seed",),
        _HARD,
        _Policy.NEEDS_INFORMATION,
    ),
    _CueRule(
        ("series a",), _Field.STAGE, _Op.EQUALS, ("series_a",), _HARD, _Policy.NEEDS_INFORMATION
    ),
    _CueRule(
        ("series b",), _Field.STAGE, _Op.EQUALS, ("series_b",), _HARD, _Policy.NEEDS_INFORMATION
    ),
    _CueRule(
        ("early stage", "early-stage"),
        _Field.STAGE,
        _Op.EQUALS,
        ("early_stage",),
        _HARD,
        _Policy.NEEDS_INFORMATION,
    ),
    _CueRule(
        ("growth stage", "growth-stage"),
        _Field.STAGE,
        _Op.EQUALS,
        ("growth_stage",),
        _HARD,
        _Policy.NEEDS_INFORMATION,
    ),
    _CueRule(("seed",), _Field.STAGE, _Op.EQUALS, ("seed",), _HARD, _Policy.NEEDS_INFORMATION),
    # Risk appetite.
    _CueRule(
        ("high risk", "high-risk", "contrarian", "moonshot", "frontier"),
        _Field.RISK_APPETITE,
        _Op.EQUALS,
        ("high",),
        _PREF,
        _Policy.PRESERVE_AS_UNKNOWN,
    ),
    _CueRule(
        ("low risk", "low-risk"),
        _Field.RISK_APPETITE,
        _Op.EQUALS,
        ("low",),
        _PREF,
        _Policy.PRESERVE_AS_UNKNOWN,
    ),
    # Traction.
    _CueRule(
        phrases=(
            "paying enterprise customers",
            "enterprise customers",
            "enterprise traction",
            "enterprise revenue",
            "enterprise adoption",
            "enterprise pilots",
        ),
        field=_Field.ENTERPRISE_TRACTION,
        operator=_Op.EQUALS,
        operands=(True,),
        default_strength=_PREF,
        unknown_policy=_Policy.NEEDS_INFORMATION,
    ),
    # Prior VC backing.
    _CueRule(
        phrases=(
            "venture-backed",
            "venture backed",
            "vc-backed",
            "vc backed",
            "previously raised",
            "previously funded",
            "previously backed",
            "prior funding",
        ),
        field=_Field.PRIOR_VC_BACKING,
        operator=_Op.EQUALS,
        operands=(True,),
        default_strength=_PREF,
        unknown_policy=_Policy.NEEDS_INFORMATION,
    ),
    # Prior VC backing, explicitly Unknown: a cold-start cue asserting the
    # backing status is unknown. is_unknown takes no operands.
    _CueRule(
        phrases=("funding undisclosed", "undisclosed funding", "backing undisclosed"),
        field=_Field.PRIOR_VC_BACKING,
        operator=_Op.IS_UNKNOWN,
        operands=(),
        default_strength=_PREF,
        unknown_policy=_Policy.PRESERVE_AS_UNKNOWN,
    ),
    # Accelerator, named. A named accelerator is a verifiable factual claim, so
    # an unknown value is routed to manual review rather than auto-preserved.
    _CueRule(
        ("y combinator", "ycombinator", "yc-backed", "yc"),
        _Field.ACCELERATOR,
        _Op.EQUALS,
        ("Y Combinator",),
        _PREF,
        _Policy.MANUAL_REVIEW,
        SourceCategory.ACCELERATOR_COHORT,
    ),
    _CueRule(
        ("techstars-backed", "techstars"),
        _Field.ACCELERATOR,
        _Op.EQUALS,
        ("Techstars",),
        _PREF,
        _Policy.MANUAL_REVIEW,
        SourceCategory.ACCELERATOR_COHORT,
    ),
    _CueRule(
        ("entrepreneur first",),
        _Field.ACCELERATOR,
        _Op.EQUALS,
        ("Entrepreneur First",),
        _PREF,
        _Policy.MANUAL_REVIEW,
        SourceCategory.ACCELERATOR_COHORT,
    ),
    _CueRule(
        ("antler",),
        _Field.ACCELERATOR,
        _Op.EQUALS,
        ("Antler",),
        _PREF,
        _Policy.MANUAL_REVIEW,
        SourceCategory.ACCELERATOR_COHORT,
    ),
    # Accelerator, any. Presence-only, so it uses is_known and takes no operands.
    _CueRule(
        ("accelerator cohort", "accelerator alumni", "accelerator alum", "accelerator"),
        _Field.ACCELERATOR,
        _Op.IS_KNOWN,
        (),
        _PREF,
        _Policy.PRESERVE_AS_UNKNOWN,
        SourceCategory.ACCELERATOR_COHORT,
    ),
    # Source-category cues: each names where to look and emits a retrieval.
    _source_rule(
        (
            "developer activity",
            "active on github",
            "commit history",
            "open-source",
            "open source",
            "github",
            "commits",
        ),
        SourceCategory.DEVELOPER_ACTIVITY,
    ),
    _source_rule(
        (
            "published research",
            "research papers",
            "published papers",
            "peer-reviewed",
            "academic research",
            "publications",
            "arxiv",
            "ph.d.",
            "phd",
        ),
        SourceCategory.RESEARCH,
    ),
    _source_rule(("patent filings", "granted patent", "patents", "patent"), SourceCategory.PATENT),
    _source_rule(("hackathon winners", "hackathons", "hackathon"), SourceCategory.HACKATHON),
    _source_rule(
        (
            "launched on product hunt",
            "product launch",
            "product hunt",
            "recently launched",
            "launched a product",
        ),
        SourceCategory.PRODUCT_LAUNCH,
    ),
    _source_rule(
        ("building in public", "public social", "social media", "linkedin", "twitter"),
        SourceCategory.PUBLIC_SOCIAL,
    ),
)


def _phrase_operand_map(field: QueryCriterionField) -> dict[str, ScalarValue]:
    """Map each lowercase phrase to its canonical operand for one equals field."""

    mapping: dict[str, ScalarValue] = {}
    for rule in _LEXICON:
        if rule.field is field and rule.operator is _Op.EQUALS and len(rule.operands) == 1:
            for phrase in rule.phrases:
                mapping[phrase] = rule.operands[0]
    return mapping


def _alternation(mapping: dict[str, ScalarValue]) -> str:
    """Longest-first regex alternation so multi-word phrases win over prefixes."""

    return "|".join(re.escape(phrase) for phrase in sorted(mapping, key=len, reverse=True))


_SECTOR_OPERANDS = _phrase_operand_map(_Field.SECTOR)
_STAGE_OPERANDS = _phrase_operand_map(_Field.STAGE)

# Excluding a sector reads as a hard NOT_EQUALS; an unknown sector is routed to
# manual review rather than silently dropped.
_SECTOR_EXCLUSION = re.compile(
    r"\b(?:no|not|without|excluding|exclude|excludes|avoid|except)\s+("
    + _alternation(_SECTOR_OPERANDS)
    + r")\b"
)
# A stated stage span such as "seed to series a" is ANY_OF the two named stages.
_STAGE_RANGE = re.compile(
    r"\b("
    + _alternation(_STAGE_OPERANDS)
    + r")\s+(?:to|through|or)\s+("
    + _alternation(_STAGE_OPERANDS)
    + r")\b"
)

# Strength modifiers. HARD wins ties with PREFERENCE at equal distance.
_HARD_MODIFIERS: tuple[str, ...] = (
    "non-negotiable",
    "hard requirement",
    "mandatory",
    "essential",
    "requires",
    "required",
    "require",
    "must",
    "only",
)
_PREFERENCE_MODIFIERS: tuple[str, ...] = (
    "nice to have",
    "nice-to-have",
    "preferably",
    "preferred",
    "prefers",
    "prefer",
    "ideally",
    "ideal",
    "bonus",
    "a plus",
    "open to",
    "would love",
    "would like",
    "leaning",
)

# Generic function words and sourcing verbs. Covering them keeps them out of the
# unresolved output; they carry no criterion of their own.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "with",
        "who",
        "whom",
        "whose",
        "that",
        "which",
        "of",
        "to",
        "in",
        "on",
        "for",
        "from",
        "at",
        "by",
        "as",
        "is",
        "are",
        "be",
        "been",
        "being",
        "am",
        "has",
        "have",
        "had",
        "we",
        "i",
        "our",
        "us",
        "their",
        "them",
        "they",
        "my",
        "your",
        "its",
        "it",
        "this",
        "these",
        "those",
        "all",
        "any",
        "some",
        "more",
        "most",
        "based",
        "me",
        "myself",
        "give",
        "get",
        "show",
        "please",
        "need",
        "needs",
        "let",
        "find",
        "finding",
        "look",
        "looking",
        "seek",
        "seeking",
        "want",
        "wants",
        "wanting",
        "interested",
        "focus",
        "focused",
        "focusing",
        "fund",
        "funds",
        "funding",
        "invest",
        "investing",
        "investment",
        "source",
        "sourcing",
        "discover",
        "discovering",
        "company",
        "companies",
        "startup",
        "startups",
        "business",
        "businesses",
        "founder",
        "founders",
        "team",
        "teams",
        "people",
        "folks",
        "someone",
        "anyone",
        "building",
        "build",
        "builds",
        "work",
        "works",
        "working",
        "check",
        "checks",
        "write",
        "writing",
        "writes",
        "ticket",
        "tickets",
        "size",
        "raise",
        "raising",
        "raised",
        "no",
        "not",
        "without",
        "excluding",
        "exclude",
        "excludes",
        "avoid",
        "except",
        "through",
        "other",
        "than",
    }
)

_WORD_TOKEN = re.compile(r"[a-z0-9']+")
_CLAUSE_SEPARATOR = re.compile(r"[,;.\n]|\band\b|\bbut\b|\bor\b")
_MEANINGFUL = re.compile(r"[A-Za-z0-9]{2,}")

_UNIT_VALUE: dict[str, float] = {"k": 1_000.0, "m": 1_000_000.0, "b": 1_000_000_000.0}

# Numeric cues. A range maps to between; a qualified single value maps to a
# directional comparison. A bare amount with no qualifier is left unresolved.
_CHECK_RANGE = re.compile(
    r"\$\s?(\d+(?:\.\d+)?)\s?([kmb])\s*(?:-|to|and)\s*\$?\s?(\d+(?:\.\d+)?)\s?([kmb])\b"
)
_CHECK_SINGLE = re.compile(
    r"(up to|at most|at least|minimum|maximum)\s+\$\s?(\d+(?:\.\d+)?)\s?([kmb])\b"
)
_CHECK_PLUS = re.compile(r"\$\s?(\d+(?:\.\d+)?)\s?([kmb])\+")
_OWNERSHIP = re.compile(
    r"(at least|up to|at most|minimum|maximum)?\s*(\d{1,3}(?:\.\d+)?)\s?%\s+"
    r"(?:ownership|equity|stake)"
)
_GTE_WORDS = frozenset({"at least", "minimum"})
_LTE_WORDS = frozenset({"up to", "at most", "maximum"})


def _aligned_lower(text: str) -> str:
    """Lowercase while preserving length so offsets stay index-aligned.

    A few code points (for example the dotted capital I) lowercase to more than
    one character; keeping the original in that rare case guarantees that the
    lowercased string indexes identically to ``text``.
    """

    out: list[str] = []
    for char in text:
        low = char.lower()
        out.append(low if len(low) == 1 else char)
    return "".join(out)


def _is_word_char(char: str) -> bool:
    return bool(char) and (char.isalnum() or char == "_")


def _iter_phrase(lower: str, phrase: str) -> list[tuple[int, int]]:
    """All boundary-respecting occurrences of ``phrase`` in ``lower``."""

    spans: list[tuple[int, int]] = []
    length = len(phrase)
    start = lower.find(phrase)
    while start != -1:
        end = start + length
        before = lower[start - 1] if start > 0 else ""
        after = lower[end] if end < len(lower) else ""
        if not _is_word_char(before) and not _is_word_char(after):
            spans.append((start, end))
        start = lower.find(phrase, start + 1)
    return spans


def _money(number: str, unit: str) -> float:
    return float(number) * _UNIT_VALUE[unit]


def _scan_numeric(lower: str) -> list[_Match]:
    """Resolve check-size and ownership patterns to concrete matches."""

    matches: list[_Match] = []
    priority = 1_000
    for hit in _CHECK_RANGE.finditer(lower):
        low = _money(hit.group(1), hit.group(2))
        high = _money(hit.group(3), hit.group(4))
        pair = (low, high) if low <= high else (high, low)
        matches.append(
            _Match(
                hit.start(),
                hit.end(),
                _Field.CHECK_SIZE,
                _Op.BETWEEN,
                pair,
                _HARD,
                _Policy.NEEDS_INFORMATION,
                None,
                priority,
            )
        )
    for hit in _CHECK_SINGLE.finditer(lower):
        word = hit.group(1)
        operator = _Op.LESS_THAN_OR_EQUAL if word in _LTE_WORDS else _Op.GREATER_THAN_OR_EQUAL
        amount = _money(hit.group(2), hit.group(3))
        matches.append(
            _Match(
                hit.start(),
                hit.end(),
                _Field.CHECK_SIZE,
                operator,
                (amount,),
                _HARD,
                _Policy.NEEDS_INFORMATION,
                None,
                priority,
            )
        )
    for hit in _CHECK_PLUS.finditer(lower):
        amount = _money(hit.group(1), hit.group(2))
        matches.append(
            _Match(
                hit.start(),
                hit.end(),
                _Field.CHECK_SIZE,
                _Op.GREATER_THAN_OR_EQUAL,
                (amount,),
                _HARD,
                _Policy.NEEDS_INFORMATION,
                None,
                priority,
            )
        )
    for hit in _OWNERSHIP.finditer(lower):
        word = hit.group(1) or ""
        operator = _Op.LESS_THAN_OR_EQUAL if word in _LTE_WORDS else _Op.GREATER_THAN_OR_EQUAL
        percent = float(hit.group(2))
        matches.append(
            _Match(
                hit.start(),
                hit.end(),
                _Field.OWNERSHIP_TARGET,
                operator,
                (percent,),
                _PREF,
                _Policy.NEEDS_INFORMATION,
                None,
                priority,
            )
        )
    return matches


def _scan_exclusions(lower: str) -> list[_Match]:
    """Sector exclusions map to a hard NOT_EQUALS on the named sector."""

    matches: list[_Match] = []
    for hit in _SECTOR_EXCLUSION.finditer(lower):
        operand = _SECTOR_OPERANDS[hit.group(1)]
        matches.append(
            _Match(
                hit.start(),
                hit.end(),
                _Field.SECTOR,
                _Op.NOT_EQUALS,
                (operand,),
                _HARD,
                _Policy.MANUAL_REVIEW,
                None,
                900,
            )
        )
    return matches


def _scan_stage_ranges(lower: str) -> list[_Match]:
    """A stated stage span maps to ANY_OF the two named stages."""

    matches: list[_Match] = []
    for hit in _STAGE_RANGE.finditer(lower):
        low = _STAGE_OPERANDS[hit.group(1)]
        high = _STAGE_OPERANDS[hit.group(2)]
        operands: tuple[ScalarValue, ...] = (low,) if low == high else (low, high)
        matches.append(
            _Match(
                hit.start(),
                hit.end(),
                _Field.STAGE,
                _Op.ANY_OF,
                operands,
                _HARD,
                _Policy.NEEDS_INFORMATION,
                None,
                900,
            )
        )
    return matches


def _scan_lexicon(lower: str) -> list[_Match]:
    """All boundary-respecting cue matches, phrase-based and numeric."""

    matches: list[_Match] = []
    for priority, rule in enumerate(_LEXICON):
        for phrase in rule.phrases:
            for start, end in _iter_phrase(lower, phrase):
                matches.append(
                    _Match(
                        start,
                        end,
                        rule.field,
                        rule.operator,
                        rule.operands,
                        rule.default_strength,
                        rule.unknown_policy,
                        rule.source_category,
                        priority,
                    )
                )
    matches.extend(_scan_numeric(lower))
    matches.extend(_scan_exclusions(lower))
    matches.extend(_scan_stage_ranges(lower))
    return matches


def _select(matches: list[_Match]) -> list[_Match]:
    """Greedily keep non-overlapping matches, longest span first at each start."""

    ordered = sorted(matches, key=lambda m: (m.start, -(m.end - m.start), m.priority))
    accepted: list[_Match] = []
    cursor = 0
    for match in ordered:
        if match.start >= cursor:
            accepted.append(match)
            cursor = match.end
    return accepted


def _clause_bounds(lower: str, start: int, end: int) -> tuple[int, int]:
    """The [start, end) clause window around a match, split on separators."""

    clause_start = 0
    for hit in _CLAUSE_SEPARATOR.finditer(lower, 0, start):
        clause_start = hit.end()
    tail = _CLAUSE_SEPARATOR.search(lower, end)
    clause_end = tail.start() if tail is not None else len(lower)
    return clause_start, clause_end


def _nearest_modifier(
    lower: str, start: int, end: int, window: tuple[int, int], default: CriterionStrength
) -> CriterionStrength:
    """Pick the strength from the closest modifier inside the clause window.

    Ties in distance are resolved in favour of a hard constraint: an explicit
    hard word next to a preference word makes the constraint binding.
    """

    window_start, window_end = window
    best: tuple[int, int, CriterionStrength] | None = None
    candidates = (
        (_HARD_MODIFIERS, 0, _HARD),
        (_PREFERENCE_MODIFIERS, 1, _PREF),
    )
    for phrases, rank, strength in candidates:
        for phrase in phrases:
            for m_start, m_end in _iter_phrase(lower, phrase):
                if m_start < window_start or m_end > window_end:
                    continue
                if m_end <= start:
                    distance = start - m_end
                elif m_start >= end:
                    distance = m_start - end
                else:
                    distance = 0
                key = (distance, rank, strength)
                if best is None or (distance, rank) < (best[0], best[1]):
                    best = key
    return best[2] if best is not None else default


def _covered_mask(raw_query: str, lower: str, accepted: list[_Match]) -> list[bool]:
    """Mark cue spans, modifier phrases, and stopword tokens as covered."""

    covered = [False] * len(raw_query)

    def mark(start: int, end: int) -> None:
        for index in range(start, end):
            covered[index] = True

    for match in accepted:
        mark(match.start, match.end)
    for phrase in (*_HARD_MODIFIERS, *_PREFERENCE_MODIFIERS):
        for start, end in _iter_phrase(lower, phrase):
            mark(start, end)
    for token in _WORD_TOKEN.finditer(lower):
        if token.group() in _STOPWORDS:
            mark(token.start(), token.end())
    return covered


def _unresolved_phrases(raw_query: str, covered: list[bool]) -> list[UnresolvedQueryPhrase]:
    """Emit one honest phrase per maximal uncovered, meaningful run."""

    phrases: list[UnresolvedQueryPhrase] = []
    index = 0
    length = len(raw_query)
    while index < length:
        if covered[index]:
            index += 1
            continue
        run_start = index
        while index < length and not covered[index]:
            index += 1
        run_end = index
        start = run_start
        while start < run_end and not _is_word_char(raw_query[start]):
            start += 1
        end = run_end
        while end > start and not _is_word_char(raw_query[end - 1]):
            end -= 1
        if end <= start:
            continue
        text = raw_query[start:end]
        if _MEANINGFUL.search(text) is None:
            continue
        phrases.append(
            UnresolvedQueryPhrase(
                text=text,
                start_offset=start,
                end_offset=end,
                reason="no lexicon cue recognized this span",
            )
        )
    return phrases


def _criterion(
    match: _Match, ordinal: int, raw_query: str, strength: CriterionStrength
) -> QueryCriterion:
    return QueryCriterion(
        criterion_id=f"criterion:{ordinal:02d}:{match.field.value}",
        field=match.field,
        operator=match.operator,
        operands=match.operands,
        strength=strength,
        unknown_policy=match.unknown_policy,
        source_text=raw_query[match.start : match.end],
    )


class DeterministicQueryPlanner:
    """Pure, deterministic ``QueryPlannerPort`` baseline over a fixed lexicon."""

    def __init__(self, *, planner_version: str = DETERMINISTIC_QUERY_PLANNER_VERSION) -> None:
        self._planner_version = planner_version

    async def plan(self, request: QueryPlanRequest) -> OpportunityQueryPlan:
        """Return a validated plan without any I/O; identical input is identical."""

        return self._build(request)

    def _build(self, request: QueryPlanRequest) -> OpportunityQueryPlan:
        raw_query = request.raw_query
        lower = _aligned_lower(raw_query)
        accepted = _select(_scan_lexicon(lower))

        criteria: list[QueryCriterion] = []
        for ordinal, match in enumerate(accepted):
            window = _clause_bounds(lower, match.start, match.end)
            strength = _nearest_modifier(
                lower, match.start, match.end, window, match.default_strength
            )
            criteria.append(_criterion(match, ordinal, raw_query, strength))

        retrievals = self._retrievals(request, raw_query, accepted)
        covered = _covered_mask(raw_query, lower, accepted)
        unresolved = _unresolved_phrases(raw_query, covered)
        state = self._state(criteria, retrievals, unresolved)

        return OpportunityQueryPlan(
            query_plan_id=request.query_plan_id,
            query_plan_version_id=request.query_plan_version_id,
            supersedes_query_plan_version_id=request.supersedes_query_plan_version_id,
            raw_query=raw_query,
            planning_mode=QueryPlanningMode.DETERMINISTIC,
            planner_version=self._planner_version,
            state=state,
            criteria=tuple(criteria),
            retrieval_requests=tuple(retrievals),
            unresolved_phrases=tuple(unresolved),
            semantic_rerank=None,
            max_results=request.max_results,
            created_at=request.created_at,
        )

    def _retrievals(
        self, request: QueryPlanRequest, raw_query: str, accepted: list[_Match]
    ) -> list[BoundedRetrievalRequest]:
        allowed = set(request.allowed_source_categories)
        seen: set[SourceCategory] = set()
        requests: list[BoundedRetrievalRequest] = []
        for match in accepted:
            category = match.source_category
            if category is None or category in seen:
                continue
            if allowed and category not in allowed:
                continue
            seen.add(category)
            requests.append(
                BoundedRetrievalRequest(
                    retrieval_request_id=f"retrieval:{category.value}",
                    query=raw_query,
                    source_categories=(category,),
                    allowed_domains=request.allowed_domains,
                    max_results=request.retrieval_max_results,
                    max_pages=request.retrieval_max_pages,
                    timeout_seconds=request.retrieval_timeout_seconds,
                )
            )
        return requests

    @staticmethod
    def _state(
        criteria: list[QueryCriterion],
        retrievals: list[BoundedRetrievalRequest],
        unresolved: list[UnresolvedQueryPhrase],
    ) -> QueryPlanState:
        if criteria or retrievals:
            return QueryPlanState.VALIDATED
        if unresolved:
            return QueryPlanState.REJECTED
        return QueryPlanState.DRAFT


__all__ = [
    "DETERMINISTIC_QUERY_PLANNER_VERSION",
    "DeterministicQueryPlanner",
    "QueryPlanRequest",
    "QueryPlannerPort",
]
