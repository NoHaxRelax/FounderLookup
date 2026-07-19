"""Project founder-submitted Application metadata into private, unverified provenance."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Final, Literal, Self

from pydantic import model_validator

from founderlookup.application.ports import ApplicationSubmittedMetadata
from founderlookup.domain.common import (
    DomainModel,
    EntityKind,
    KnowledgeValue,
    SubjectRef,
    UTCDateTime,
)
from founderlookup.domain.evidence import (
    Claim,
    ClaimOrigin,
    ClaimStatus,
    DataClassification,
    Evidence,
    EvidenceStance,
    SourceArtifact,
    SourceArtifactKind,
    SourceCategory,
    SourceLocator,
    SourceLocatorKind,
)
from founderlookup.domain.scoring import ClaimTrustScore, TrustScoreState

APPLICATION_METADATA_PROJECTION_VERSION: Final = "application-metadata-projection.v1"


class ApplicationMetadataProjection(DomainModel):
    """Private source record plus assertions that remain explicitly unverified."""

    schema_version: Literal["application-metadata-projection.v1"] = (
        APPLICATION_METADATA_PROJECTION_VERSION
    )
    projection_id: str
    projection_version: Literal["application-metadata-projection.v1"] = (
        APPLICATION_METADATA_PROJECTION_VERSION
    )
    application_id: str
    company_id: str
    source_artifact: SourceArtifact
    claims: tuple[Claim, ...]
    evidence: tuple[Evidence, ...]
    public_lookup_urls: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_closed_projection(self) -> Self:
        claim_ids = {item.claim_id for item in self.claims}
        evidence_ids = {item.evidence_id for item in self.evidence}
        if len(claim_ids) != len(self.claims) or len(evidence_ids) != len(self.evidence):
            raise ValueError("metadata projection identifiers must be unique")
        if any(item.claim_id not in claim_ids for item in self.evidence):
            raise ValueError("metadata Evidence must attach to a projected Claim")
        if any(
            item.status is not ClaimStatus.ASSERTED_UNVERIFIED
            or not set(item.supporting_evidence_ids).issubset(evidence_ids)
            for item in self.claims
        ):
            raise ValueError("metadata Claims must remain source-asserted and unverified")
        return self


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:24]


def _stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}:{_digest(*parts)}"


def _metadata_json(company_name: str, metadata: ApplicationSubmittedMetadata) -> bytes:
    return json.dumps(
        {
            "company_name": company_name,
            "metadata": metadata.model_dump(mode="json"),
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _assertions(
    company_name: str, metadata: ApplicationSubmittedMetadata
) -> tuple[tuple[str, str], ...]:
    values: list[tuple[str, str]] = [("submitted_company_name", company_name)]
    values.extend(
        (predicate, value)
        for predicate, value in (
            ("submitted_company_website", metadata.website),
            ("submitted_one_line_pitch", metadata.one_line_pitch),
            ("submitted_location", metadata.location),
            ("submitted_stage", metadata.stage),
            ("submitted_contact_email", metadata.contact_email),
        )
        if value is not None
    )
    for index, founder in enumerate(metadata.founders):
        prefix = f"submitted_founder_{index}"
        values.append((f"{prefix}_full_name", founder.full_name))
        values.extend(
            (predicate, value)
            for predicate, value in (
                (f"{prefix}_role_title", founder.role_title),
                (f"{prefix}_email", founder.email),
                (f"{prefix}_linkedin_url", founder.linkedin_url),
                (f"{prefix}_github_url", founder.github_url),
                (f"{prefix}_background", founder.background),
            )
            if value is not None
        )
        values.extend(
            (f"{prefix}_previous_company_{company_index}", company)
            for company_index, company in enumerate(founder.previous_companies)
        )
    return tuple(values)


def _public_urls(metadata: ApplicationSubmittedMetadata) -> tuple[str, ...]:
    values: Iterable[str | None] = (
        metadata.website,
        *(
            value
            for founder in metadata.founders
            for value in (founder.linkedin_url, founder.github_url)
        ),
    )
    return tuple(dict.fromkeys(value for value in values if value is not None))


def project_application_metadata(
    *,
    application_id: str,
    company_id: str,
    company_name: str,
    metadata: ApplicationSubmittedMetadata,
    received_at: UTCDateTime,
) -> ApplicationMetadataProjection:
    """Create deterministic private provenance; never verify the submitted assertions."""

    canonical = _metadata_json(company_name, metadata)
    content_sha256 = hashlib.sha256(canonical).hexdigest()
    source_id = _stable_id("application-metadata-artifact", application_id, content_sha256)
    source = SourceArtifact(
        source_artifact_id=source_id,
        artifact_series_id=_stable_id("application-metadata-series", application_id),
        artifact_version_id=_stable_id(
            "application-metadata-version", application_id, content_sha256
        ),
        version_number=1,
        kind=SourceArtifactKind.STRUCTURED_IMPORT,
        source_category=SourceCategory.IMPORT,
        classification=DataClassification.FOUNDER_PRIVATE,
        origin_locator=f"private-application:{application_id}#submitted-metadata",
        display_name="Founder-provided Application metadata",
        media_type="application/json",
        content_sha256=content_sha256,
        retrieved_at=received_at,
        source_event_time=KnowledgeValue.unknown(
            "submitted metadata has no separate source event time"
        ),
    )
    subject = SubjectRef(kind=EntityKind.APPLICATION, subject_id=application_id)
    claims: list[Claim] = []
    evidence: list[Evidence] = []
    for predicate, value in _assertions(company_name, metadata):
        claim_id = _stable_id("application-claim", application_id, predicate, value)
        evidence_id = _stable_id("application-evidence", claim_id, source_id)
        statement = f"The applicant submitted {predicate}: {value}"
        evidence.append(
            Evidence(
                evidence_id=evidence_id,
                claim_id=claim_id,
                source_artifact_id=source_id,
                stance=EvidenceStance.SUPPORTS,
                locator=SourceLocator(
                    kind=SourceLocatorKind.SOURCE_RECORD,
                    locator=f"private-application:{application_id}#{predicate}",
                    excerpt=statement,
                ),
                collected_at=received_at,
                source_event_time=KnowledgeValue.unknown(
                    "submitted metadata has no separate source event time"
                ),
            )
        )
        claims.append(
            Claim(
                claim_id=claim_id,
                claim_version_id=_stable_id("application-claim-version", claim_id),
                subject=subject,
                predicate=predicate,
                statement=statement,
                status=ClaimStatus.ASSERTED_UNVERIFIED,
                origin=ClaimOrigin.SOURCE_ASSERTION,
                as_of=received_at,
                created_at=received_at,
                supporting_evidence_ids=(evidence_id,),
                trust=ClaimTrustScore(
                    state=TrustScoreState.UNSCORED,
                    trust_policy_version="founder-submitted-unverified.v1",
                    reason=(
                        "The source proves only what the applicant submitted; the underlying "
                        "identity or company fact is not independently verified."
                    ),
                ),
            )
        )
    return ApplicationMetadataProjection(
        projection_id=_stable_id("application-metadata-projection", application_id, content_sha256),
        application_id=application_id,
        company_id=company_id,
        source_artifact=source,
        claims=tuple(claims),
        evidence=tuple(evidence),
        public_lookup_urls=_public_urls(metadata),
    )


__all__ = [
    "APPLICATION_METADATA_PROJECTION_VERSION",
    "ApplicationMetadataProjection",
    "project_application_metadata",
]
