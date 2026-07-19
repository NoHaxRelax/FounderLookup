"""Uniform policy metadata attached to every persisted public source artifact."""

from __future__ import annotations

from hashlib import sha256
from typing import Final, Literal

from founderlookup.domain.common import DomainModel, KnowledgeValue, NonBlankStr, StableId
from founderlookup.domain.evidence import DataClassification, SourceArtifact

PUBLIC_SOURCE_POLICY_VERSION: Final = "public-source-policy.v0"


class PublicSourceCollectionPolicy(DomainModel):
    """Composition-time policy facts; Unknown is explicit when source terms are unresolved."""

    policy_version: Literal["public-source-policy.v0"] = PUBLIC_SOURCE_POLICY_VERSION
    collection_purpose: NonBlankStr
    lawful_basis: NonBlankStr
    source_terms: KnowledgeValue[str]
    robots_policy: KnowledgeValue[str]
    classification: Literal[DataClassification.PUBLIC] = DataClassification.PUBLIC
    contact_details_collected: Literal[False] = False
    silent_identity_merge_allowed: Literal[False] = False


class PublicSourcePolicyRecord(DomainModel):
    """Immutable policy sidecar for one acquired public Source Artifact version."""

    record_type: Literal["public_source_policy"] = "public_source_policy"
    record_id: StableId
    record_version_id: StableId
    source_artifact_id: StableId
    artifact_version_id: StableId
    adapter_id: StableId
    origin_locator: NonBlankStr
    policy: PublicSourceCollectionPolicy


def project_public_source_policy(
    *,
    source_artifact: SourceArtifact,
    adapter_id: str,
    policy: PublicSourceCollectionPolicy,
) -> PublicSourcePolicyRecord:
    """Build a deterministic sidecar without claiming unknown source-policy facts."""

    if source_artifact.classification is not DataClassification.PUBLIC:
        raise ValueError("public-source policy metadata requires a public Source Artifact")
    material = "\x1f".join(
        (
            source_artifact.source_artifact_id,
            source_artifact.artifact_version_id,
            adapter_id,
            policy.model_dump_json(),
        )
    )
    digest = sha256(material.encode("utf-8")).hexdigest()[:32]
    return PublicSourcePolicyRecord(
        record_id=f"source-policy:{digest}",
        record_version_id=f"source-policy-version:{digest}",
        source_artifact_id=source_artifact.source_artifact_id,
        artifact_version_id=source_artifact.artifact_version_id,
        adapter_id=adapter_id,
        origin_locator=source_artifact.origin_locator,
        policy=policy,
    )


__all__ = [
    "PUBLIC_SOURCE_POLICY_VERSION",
    "PublicSourceCollectionPolicy",
    "PublicSourcePolicyRecord",
    "project_public_source_policy",
]
