"""Foundational immutable values shared by every VC Brain contract."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Generic, Literal, Self, TypeVar

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

CONTRACT_SCHEMA_VERSION = "vc-brain.v0"
KNOWLEDGE_VALUE_SCHEMA_VERSION = "knowledge-value.v0"


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must contain at least one non-whitespace character")
    return value


def _utc_only(value: datetime) -> datetime:
    if value.utcoffset() != timedelta(0):
        raise ValueError("must be timezone-aware and use UTC")
    return value


StableId = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
VersionId = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:+/-]*$",
    ),
]
NonBlankStr = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=4_000),
    AfterValidator(_not_blank),
]
LongText = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=100_000),
    AfterValidator(_not_blank),
]
UTCDateTime = Annotated[datetime, AwareDatetime(), AfterValidator(_utc_only)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]
Score100 = Annotated[float, Field(strict=True, ge=0.0, le=100.0)]
ScalarValue = str | int | float | bool


class DomainModel(BaseModel):
    """Strict value object base; collections in contracts use immutable tuples."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
        hide_input_in_errors=True,
    )


class EntityKind(StrEnum):
    FOUNDER = "founder"
    COMPANY = "company"
    OUTBOUND_CANDIDATE = "outbound_candidate"
    APPLICATION = "application"
    OPPORTUNITY = "opportunity"
    SCREENING_CASE = "screening_case"


class SubjectRef(DomainModel):
    """Stable reference to a canonical domain subject."""

    kind: EntityKind
    subject_id: StableId


class KnowledgeState(StrEnum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    NOT_DISCLOSED = "not_disclosed"
    NOT_APPLICABLE = "not_applicable"
    CONFLICTED = "conflicted"


T = TypeVar("T")


class KnowledgeAlternative(DomainModel, Generic[T]):
    """One source-backed alternative retained by a conflicted value."""

    value: T
    evidence_ids: Annotated[tuple[StableId, ...], Field(min_length=1)]


class KnowledgeValue(DomainModel, Generic[T]):
    """A value whose missingness semantics cannot be collapsed into null."""

    schema_version: Literal["knowledge-value.v0"] = KNOWLEDGE_VALUE_SCHEMA_VERSION
    state: KnowledgeState
    value: T | None = None
    reason: NonBlankStr | None = None
    evidence_ids: tuple[StableId, ...] = ()
    alternatives: tuple[KnowledgeAlternative[T], ...] = ()

    @model_validator(mode="after")
    def validate_state_shape(self) -> Self:
        if self.state is KnowledgeState.KNOWN:
            if self.value is None:
                raise ValueError("known values require value")
            if self.alternatives:
                raise ValueError("known values cannot carry conflict alternatives")
            return self

        if self.value is not None:
            raise ValueError("non-known values cannot carry value")
        if self.reason is None:
            raise ValueError("non-known values require reason")

        if self.state is KnowledgeState.CONFLICTED:
            if len(self.alternatives) < 2:
                raise ValueError("conflicted values require at least two alternatives")
            if self.evidence_ids:
                raise ValueError("conflicted evidence belongs to each alternative")
        elif self.alternatives:
            raise ValueError("only conflicted values can carry alternatives")
        return self

    @classmethod
    def known(cls, value: T, *, evidence_ids: tuple[StableId, ...] = ()) -> Self:
        return cls(state=KnowledgeState.KNOWN, value=value, evidence_ids=evidence_ids)

    @classmethod
    def unknown(cls, reason: NonBlankStr) -> Self:
        return cls(state=KnowledgeState.UNKNOWN, reason=reason)

    @classmethod
    def not_disclosed(cls, reason: NonBlankStr, *, evidence_ids: tuple[StableId, ...] = ()) -> Self:
        return cls(
            state=KnowledgeState.NOT_DISCLOSED,
            reason=reason,
            evidence_ids=evidence_ids,
        )

    @classmethod
    def not_applicable(cls, reason: NonBlankStr) -> Self:
        return cls(state=KnowledgeState.NOT_APPLICABLE, reason=reason)

    @classmethod
    def conflicted(
        cls,
        reason: NonBlankStr,
        alternatives: tuple[KnowledgeAlternative[T], ...],
    ) -> Self:
        return cls(
            state=KnowledgeState.CONFLICTED,
            reason=reason,
            alternatives=alternatives,
        )


class VersionComponent(StrEnum):
    THESIS = "thesis"
    DETERMINISTIC_RULES = "deterministic_rules"
    FOUNDER_SCORE = "founder_score"
    CLAIM_TRUST = "claim_trust"
    AXIS_RUBRIC = "axis_rubric"
    DECISION_READINESS_POLICY = "decision_readiness_policy"
    QUERY_PLANNER = "query_planner"
    MODEL = "model"
    PROMPT = "prompt"
    TOOL = "tool"
    MEMO = "memo"
    RECOMMENDATION = "recommendation"


class ComponentVersion(DomainModel):
    """Version of one component used to produce a record."""

    component: VersionComponent
    version_id: VersionId
    name: NonBlankStr | None = None


class VersionManifest(DomainModel):
    """Exact implementation and policy versions used by an output."""

    schema_version: Literal["vc-brain.v0"] = CONTRACT_SCHEMA_VERSION
    components: tuple[ComponentVersion, ...] = ()

    @model_validator(mode="after")
    def reject_duplicate_components(self) -> Self:
        keys = tuple((item.component, item.name) for item in self.components)
        if len(keys) != len(set(keys)):
            raise ValueError("component and name pairs must be unique")
        return self


def contract_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return the stable JSON schema surface used by shared contract tests."""

    return model.model_json_schema(mode="validation")
