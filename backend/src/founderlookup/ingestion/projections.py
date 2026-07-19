"""Conservative deterministic projections from authoritative public source records.

Only explicitly structured scalar fields become source assertions. Free-form profile text,
contact details, provider snippets, relevance scores, and inferred identities are excluded.
Every accepted assertion produces an Observation plus precise Evidence and an explicitly
unscored, source-asserted Claim; cross-source Trust and identity resolution remain later,
reversible review steps.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from hashlib import sha256
from typing import Annotated, Final, Literal

from pydantic import Field

from founderlookup.domain.common import (
    DomainModel,
    KnowledgeValue,
    ScalarValue,
    StableId,
    SubjectRef,
)
from founderlookup.domain.evidence import (
    ArtifactAvailability,
    Claim,
    ClaimOrigin,
    ClaimStatus,
    DataClassification,
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
from founderlookup.domain.scoring import ClaimTrustScore, TrustScoreState
from founderlookup.ingestion.hackathons import (
    HackathonLinkKind,
    HackathonShowcaseProjection,
)

PUBLIC_SOURCE_EVIDENCE_PROJECTION_VERSION: Final = "public-source-evidence-projection.v0"
_GITHUB_SNAPSHOT_VERSION: Final = "github-developer-activity-snapshot.v0"
_MAX_ASSERTIONS: Final = 64


@dataclass(frozen=True, slots=True)
class _AssertionDraft:
    predicate: str
    value: ScalarValue
    locator: SourceLocator
    statement: str


class PublicSourceEvidenceProjection(DomainModel):
    """Identifiers for one atomic source-assertion projection."""

    projection_version: Literal["public-source-evidence-projection.v0"] = (
        PUBLIC_SOURCE_EVIDENCE_PROJECTION_VERSION
    )
    projection_id: StableId
    projection_version_id: StableId
    source_artifact_id: StableId
    subject: SubjectRef
    observations: Annotated[tuple[Observation, ...], Field(max_length=_MAX_ASSERTIONS)] = ()
    evidence: Annotated[tuple[Evidence, ...], Field(max_length=_MAX_ASSERTIONS)] = ()
    claims: Annotated[tuple[Claim, ...], Field(max_length=_MAX_ASSERTIONS)] = ()


def project_public_source_evidence(
    *,
    source_artifact: SourceArtifact,
    content: bytes,
    subject: SubjectRef,
    hackathon: HackathonShowcaseProjection | None = None,
) -> PublicSourceEvidenceProjection | None:
    """Project only explicit structured assertions, or return None for opaque content."""

    _validate_source(source_artifact, content)
    if hackathon is not None:
        if hackathon.source_artifact_id != source_artifact.source_artifact_id:
            raise ValueError("hackathon projection does not reference the source artifact")
        drafts = _hackathon_drafts(hackathon)
    elif source_artifact.media_type.split(";", 1)[0].strip().casefold() == "application/json":
        drafts = _json_drafts(source_artifact.source_category, content)
    else:
        drafts = ()
    if not drafts:
        return None

    material = "\x1f".join(
        (
            source_artifact.source_artifact_id,
            source_artifact.artifact_version_id,
            subject.kind.value,
            subject.subject_id,
            PUBLIC_SOURCE_EVIDENCE_PROJECTION_VERSION,
            *(f"{item.predicate}:{item.locator.locator}:{item.value!r}" for item in drafts),
        )
    )
    projection_digest = sha256(material.encode("utf-8")).hexdigest()[:32]
    projection_id = f"source-evidence-projection:{projection_digest}"
    observations: list[Observation] = []
    evidence_items: list[Evidence] = []
    claims: list[Claim] = []
    as_of = source_artifact.source_event_time.value or source_artifact.retrieved_at

    for index, draft in enumerate(drafts[:_MAX_ASSERTIONS], start=1):
        record_material = "\x1f".join(
            (
                projection_id,
                str(index),
                draft.predicate,
                draft.locator.kind.value,
                draft.locator.locator,
                repr(draft.value),
            )
        )
        digest = sha256(record_material.encode("utf-8")).hexdigest()[:32]
        observation_id = f"observation:{digest}"
        evidence_id = f"evidence:{digest}"
        claim_id = f"claim:{digest}"
        observation = Observation(
            observation_id=observation_id,
            observation_version_id=f"observation-version:{digest}",
            source_artifact_id=source_artifact.source_artifact_id,
            subject=subject,
            predicate=draft.predicate,
            observed_value=KnowledgeValue[ScalarValue].known(draft.value),
            locator=draft.locator,
            retrieved_at=source_artifact.retrieved_at,
            source_event_time=source_artifact.source_event_time,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            extraction_version=PUBLIC_SOURCE_EVIDENCE_PROJECTION_VERSION,
            verification_state=VerificationState.SOURCE_ASSERTED,
        )
        evidence = Evidence(
            evidence_id=evidence_id,
            claim_id=claim_id,
            source_artifact_id=source_artifact.source_artifact_id,
            observation_id=observation_id,
            stance=EvidenceStance.SUPPORTS,
            locator=draft.locator,
            collected_at=source_artifact.retrieved_at,
            source_event_time=source_artifact.source_event_time,
        )
        claim = Claim(
            claim_id=claim_id,
            claim_version_id=f"claim-version:{digest}",
            subject=subject,
            predicate=draft.predicate,
            statement=draft.statement,
            status=ClaimStatus.ASSERTED_UNVERIFIED,
            origin=ClaimOrigin.SOURCE_ASSERTION,
            as_of=as_of,
            created_at=source_artifact.retrieved_at,
            supporting_evidence_ids=(evidence_id,),
            trust=ClaimTrustScore(
                state=TrustScoreState.UNSCORED,
                trust_policy_version="claim-trust-rubric.v0",
                reason=(
                    "The source assertion has precise Evidence but has not undergone "
                    "cross-source corroboration and contradiction scoring."
                ),
            ),
        )
        observations.append(observation)
        evidence_items.append(evidence)
        claims.append(claim)

    return PublicSourceEvidenceProjection(
        projection_id=projection_id,
        projection_version_id=f"source-evidence-version:{projection_digest}",
        source_artifact_id=source_artifact.source_artifact_id,
        subject=subject,
        observations=tuple(observations),
        evidence=tuple(evidence_items),
        claims=tuple(claims),
    )


def _validate_source(source_artifact: SourceArtifact, content: bytes) -> None:
    if source_artifact.classification is not DataClassification.PUBLIC:
        raise ValueError("public source projection accepts only public Source Artifacts")
    if source_artifact.availability is not ArtifactAvailability.AVAILABLE:
        raise ValueError("public source projection requires available source content")
    if sha256(content).hexdigest() != source_artifact.content_sha256:
        raise ValueError("public source bytes do not match the immutable artifact hash")


def _source_record(locator: str, value: object) -> SourceLocator:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return SourceLocator(
        kind=SourceLocatorKind.SOURCE_RECORD,
        locator=locator,
        excerpt=rendered[:600],
    )


def _scalar(value: object) -> ScalarValue | None:
    if isinstance(value, bool | str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return None


def _draft(
    predicate: str,
    value: object,
    locator: str,
    statement: str,
) -> _AssertionDraft | None:
    accepted = _scalar(value)
    if accepted is None or (isinstance(accepted, str) and not accepted.strip()):
        return None
    return _AssertionDraft(
        predicate=predicate,
        value=accepted,
        locator=_source_record(locator, accepted),
        statement=statement,
    )


def _json_drafts(category: SourceCategory, content: bytes) -> tuple[_AssertionDraft, ...]:
    try:
        value = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ()
    if not isinstance(value, dict):
        return ()
    if value.get("schema_version") == _GITHUB_SNAPSHOT_VERSION:
        return _github_drafts(value)
    if category is SourceCategory.RESEARCH:
        return _research_drafts(value)
    if category is SourceCategory.PATENT:
        return _patent_drafts(value)
    if category is SourceCategory.PUBLIC_SOCIAL:
        return _public_social_drafts(value)
    return ()


def _github_drafts(value: dict[str, object]) -> tuple[_AssertionDraft, ...]:
    drafts: list[_AssertionDraft | None] = []
    subject = value.get("subject")
    records = value.get("records")
    if not isinstance(subject, dict) or not isinstance(records, dict):
        return ()
    owner = subject.get("owner")
    drafts.append(
        _draft(
            "developer_activity.github_handle",
            owner,
            "/subject/owner",
            f"GitHub publishes the handle {owner!s}; identity remains unverified.",
        )
    )
    repositories = records.get("repositories")
    if isinstance(repositories, list):
        drafts.append(
            _draft(
                "developer_activity.public_repository_count_in_snapshot",
                len(repositories),
                "/records/repositories",
                f"The bounded GitHub snapshot contains {len(repositories)} public repositories.",
            )
        )
        for index, repository in enumerate(repositories[:20]):
            if not isinstance(repository, dict):
                continue
            full_name = repository.get("full_name")
            drafts.append(
                _draft(
                    "developer_activity.public_repository",
                    full_name,
                    f"/records/repositories/{index}/full_name",
                    f"GitHub publishes the public repository {full_name!s}.",
                )
            )
    repository = records.get("repository")
    if isinstance(repository, dict):
        full_name = repository.get("full_name")
        drafts.append(
            _draft(
                "developer_activity.public_repository",
                full_name,
                "/records/repository/full_name",
                f"GitHub publishes the public repository {full_name!s}.",
            )
        )
    events = records.get("public_events")
    if isinstance(events, list):
        drafts.append(
            _draft(
                "developer_activity.recent_public_event_count_in_snapshot",
                len(events),
                "/records/public_events",
                (
                    f"The bounded GitHub snapshot contains {len(events)} recent public events; "
                    "this is not a complete activity history or quality score."
                ),
            )
        )
    return tuple(item for item in drafts if item is not None)


def _research_drafts(value: dict[str, object]) -> tuple[_AssertionDraft, ...]:
    fields = (
        ("research.public_author_identifier", ("authorId", "id")),
        ("research.display_name", ("name", "display_name")),
        ("research.publication_count", ("paperCount", "works_count")),
        ("research.citation_count", ("citationCount", "cited_by_count")),
        ("research.h_index", ("hIndex",)),
    )
    drafts: list[_AssertionDraft] = []
    for predicate, alternatives in fields:
        for field in alternatives:
            if field not in value:
                continue
            draft = _draft(
                predicate,
                value[field],
                f"/{field}",
                (
                    f"The scholarly source publishes {field}={value[field]!s}; the metric is "
                    "source context and not a Founder Score."
                ),
            )
            if draft is not None:
                drafts.append(draft)
            break
    return tuple(drafts)


def _patent_drafts(value: dict[str, object]) -> tuple[_AssertionDraft, ...]:
    patents = value.get("patents")
    if not isinstance(patents, list) or not patents or not isinstance(patents[0], dict):
        return ()
    patent = patents[0]
    drafts = (
        _draft(
            "patent.public_identifier",
            patent.get("patent_id"),
            "/patents/0/patent_id",
            f"The patent source publishes patent identifier {patent.get('patent_id')!s}.",
        ),
        _draft(
            "patent.title",
            patent.get("patent_title"),
            "/patents/0/patent_title",
            f"The patent source publishes the title {patent.get('patent_title')!s}.",
        ),
    )
    return tuple(item for item in drafts if item is not None)


def _public_social_drafts(value: dict[str, object]) -> tuple[_AssertionDraft, ...]:
    drafts = (
        _draft(
            "public_social.display_handle",
            value.get("username"),
            "/username",
            (
                f"The public technical-community source publishes handle "
                f"{value.get('username')!s}; identity remains unverified."
            ),
        ),
        _draft(
            "public_social.context_karma",
            value.get("karma"),
            "/karma",
            (
                f"The public source publishes karma={value.get('karma')!s}; this gameable metric "
                "is context only and not founder quality."
            ),
        ),
    )
    return tuple(item for item in drafts if item is not None)


def _hackathon_drafts(
    projection: HackathonShowcaseProjection,
) -> tuple[_AssertionDraft, ...]:
    drafts: list[_AssertionDraft] = []
    if projection.event_name.value is not None:
        assert projection.event_locator is not None
        drafts.append(
            _AssertionDraft(
                predicate="hackathon.event_name",
                value=projection.event_name.value,
                locator=projection.event_locator,
                statement=f"The public showcase names the event {projection.event_name.value}.",
            )
        )
    if projection.project_name.value is not None:
        assert projection.project_locator is not None
        drafts.append(
            _AssertionDraft(
                predicate="hackathon.project_name",
                value=projection.project_name.value,
                locator=projection.project_locator,
                statement=f"The public showcase names the project {projection.project_name.value}.",
            )
        )
    for participant in projection.participants:
        drafts.append(
            _AssertionDraft(
                predicate="hackathon.participant_display_name",
                value=participant.display_name,
                locator=participant.locator,
                statement=(
                    f"The public showcase publishes participant display name "
                    f"{participant.display_name}; identity remains unverified."
                ),
            )
        )
    for link in projection.links:
        predicate = {
            HackathonLinkKind.PITCH_DECK: "hackathon.public_pitch_deck_url",
            HackathonLinkKind.REPOSITORY: "hackathon.public_repository_url",
            HackathonLinkKind.DEMO: "hackathon.public_demo_url",
        }[link.kind]
        drafts.append(
            _AssertionDraft(
                predicate=predicate,
                value=link.url,
                locator=link.locator,
                statement=(
                    f"The public showcase explicitly links {link.kind.value} at {link.url}."
                ),
            )
        )
    return tuple(drafts[:_MAX_ASSERTIONS])


__all__ = [
    "PUBLIC_SOURCE_EVIDENCE_PROJECTION_VERSION",
    "PublicSourceEvidenceProjection",
    "project_public_source_evidence",
]
