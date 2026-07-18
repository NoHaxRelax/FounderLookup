"""Baseline cross-source identity resolution (deduplication).

Task 3.2. A pure, reversible function over provider-neutral identity signals that
clusters them into candidate entities:

- signals from the same source record (same ``source_ref``) are one entity;
- records that share a strong identifier (handle, profile URL, external id, email)
  are linked with high confidence;
- records that share only a display name across two or more independent source
  categories are surfaced as one entity but flagged ``NEEDS_REVIEW``, because a name
  alone is not proof of identity;
- a single-source record with no cross-source match is resolved at lower confidence.

No canonical persistence lives here. Merging resolved entities into canonical Memory
(the SubjectRef / Founder / Company store) is the SWE Memory layer and a later paired
step. Confidences are heuristic and uncalibrated by design.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from founderlookup.domain.evidence import SourceCategory


class IdentitySignalKind(StrEnum):
    HANDLE = "handle"
    PROFILE_URL = "profile_url"
    EXTERNAL_ID = "external_id"
    EMAIL = "email"
    NAME = "name"


@dataclass(frozen=True)
class IdentitySignal:
    """One identity clue extracted from a single source record."""

    kind: IdentitySignalKind
    value: str
    source_category: SourceCategory
    source_ref: str


class ResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True)
class MatchReason:
    rule: str
    detail: str


@dataclass(frozen=True)
class ResolvedEntity:
    """A candidate entity: a cluster of signals with a status and match reasons."""

    signals: tuple[IdentitySignal, ...]
    status: ResolutionStatus
    confidence: float
    reasons: tuple[MatchReason, ...]

    @property
    def source_categories(self) -> frozenset[SourceCategory]:
        return frozenset(signal.source_category for signal in self.signals)

    @property
    def source_refs(self) -> frozenset[str]:
        return frozenset(signal.source_ref for signal in self.signals)


_STRONG_KINDS = frozenset(
    {
        IdentitySignalKind.HANDLE,
        IdentitySignalKind.PROFILE_URL,
        IdentitySignalKind.EXTERNAL_ID,
        IdentitySignalKind.EMAIL,
    }
)


def _norm(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _strong_key(signal: IdentitySignal) -> str | None:
    if signal.kind not in _STRONG_KINDS:
        return None
    if signal.kind is IdentitySignalKind.PROFILE_URL:
        parsed = urllib.parse.urlparse(signal.value)
        return f"url:{parsed.netloc.lower()}{parsed.path.rstrip('/').lower()}"
    return f"{signal.kind.value}:{_norm(signal.value)}"


class _DisjointSet:
    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, item: int) -> int:
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, left: int, right: int) -> None:
        root_left, root_right = self.find(left), self.find(right)
        if root_left != root_right:
            self._parent[max(root_left, root_right)] = min(root_left, root_right)


def resolve_identities(signals: Sequence[IdentitySignal]) -> tuple[ResolvedEntity, ...]:
    """Cluster identity signals into candidate entities. Deterministic and pure."""
    count = len(signals)
    if count == 0:
        return ()
    dsu = _DisjointSet(count)

    # 1. Signals from the same source record describe one entity.
    first_by_ref: dict[str, int] = {}
    for index, signal in enumerate(signals):
        anchor = first_by_ref.setdefault(signal.source_ref, index)
        dsu.union(anchor, index)

    # 2. A shared strong identifier links records across sources.
    first_by_strong: dict[str, int] = {}
    for index, signal in enumerate(signals):
        key = _strong_key(signal)
        if key is None:
            continue
        anchor = first_by_strong.setdefault(key, index)
        dsu.union(anchor, index)

    strong_clusters: dict[int, list[int]] = {}
    for index in range(count):
        strong_clusters.setdefault(dsu.find(index), []).append(index)

    # 3. A display name shared across independent source categories is a candidate
    #    merge, but only a NEEDS_REVIEW one: a name alone is not proof.
    roots_by_name: dict[str, set[int]] = {}
    for root, members in strong_clusters.items():
        for index in members:
            if signals[index].kind is IdentitySignalKind.NAME:
                roots_by_name.setdefault(_norm(signals[index].value), set()).add(root)

    name_merged: set[int] = set()
    for roots in roots_by_name.values():
        if len(roots) < 2:
            continue
        categories = {
            signals[index].source_category
            for root in roots
            for index in strong_clusters[root]
        }
        if len(categories) < 2:
            continue
        ordered = sorted(roots)
        for root in ordered[1:]:
            dsu.union(ordered[0], root)
        for root in roots:
            name_merged.update(strong_clusters[root])

    final_clusters: dict[int, list[int]] = {}
    for index in range(count):
        final_clusters.setdefault(dsu.find(index), []).append(index)

    entities: list[ResolvedEntity] = []
    for members in final_clusters.values():
        member_signals = tuple(signals[index] for index in members)
        categories = {signals[index].source_category for index in members}
        refs = {signals[index].source_ref for index in members}
        if any(index in name_merged for index in members):
            entities.append(
                ResolvedEntity(
                    signals=member_signals,
                    status=ResolutionStatus.NEEDS_REVIEW,
                    confidence=0.4,
                    reasons=(
                        MatchReason(
                            "corroborated_name",
                            "same display name across independent sources; confirm before merging",
                        ),
                    ),
                )
            )
        elif len(refs) >= 2:
            entities.append(
                ResolvedEntity(
                    signals=member_signals,
                    status=ResolutionStatus.RESOLVED,
                    confidence=0.9 if len(categories) >= 2 else 0.8,
                    reasons=(
                        MatchReason(
                            "shared_identifier",
                            "records linked by a shared strong identifier",
                        ),
                    ),
                )
            )
        else:
            entities.append(
                ResolvedEntity(
                    signals=member_signals,
                    status=ResolutionStatus.RESOLVED,
                    confidence=0.6,
                    reasons=(
                        MatchReason(
                            "single_source",
                            "single source record with no cross-source match",
                        ),
                    ),
                )
            )

    entities.sort(
        key=lambda entity: (
            min(signal.source_ref for signal in entity.signals),
            min(signal.value for signal in entity.signals),
        )
    )
    return tuple(entities)


__all__ = [
    "IdentitySignal",
    "IdentitySignalKind",
    "MatchReason",
    "ResolutionStatus",
    "ResolvedEntity",
    "resolve_identities",
]
