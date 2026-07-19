"""Application-layer composition and acceptance contracts for inbound intelligence."""

from __future__ import annotations

import hashlib

from founderlookup.application.application_metadata import ApplicationMetadataProjection
from founderlookup.application.deck_evidence import DeckEvidenceProjection
from founderlookup.domain.scoring import CoverageSummary
from founderlookup.screening.founder_reads import GradedObservation
from founderlookup.screening.live_analyses import (
    InboundAnalysisSnapshot,
    MemoIdentity,
)


def combined_inbound_snapshot_id(
    deck: DeckEvidenceProjection | None,
    metadata: ApplicationMetadataProjection | None,
) -> str:
    """Stable identity for the exact accepted deck/metadata projection combination."""

    components = tuple(
        item
        for item in (
            deck.projection_id if deck is not None else None,
            metadata.projection_id if metadata is not None else None,
        )
        if item is not None
    )
    if not components:
        raise ValueError("an inbound snapshot requires at least one accepted projection")
    digest = hashlib.sha256("|".join(components).encode()).hexdigest()[:24]
    return f"snapshot:{digest}"


def compose_inbound_analysis_snapshot(
    *,
    deck: DeckEvidenceProjection | None,
    metadata: ApplicationMetadataProjection | None,
    coverage: CoverageSummary,
    memo_identity: MemoIdentity,
    founder_observations: tuple[GradedObservation, ...] = (),
) -> InboundAnalysisSnapshot:
    """Merge accepted provider-neutral projections without granting either extra trust."""

    claims = tuple(
        (
            *(deck.claims if deck is not None else ()),
            *(metadata.claims if metadata is not None else ()),
        )
    )
    evidence = tuple(
        (
            *(deck.evidence if deck is not None else ()),
            *(metadata.evidence if metadata is not None else ()),
        )
    )
    return InboundAnalysisSnapshot(
        input_snapshot_id=combined_inbound_snapshot_id(deck, metadata),
        claims=claims,
        evidence=evidence,
        coverage=coverage,
        memo_identity=memo_identity,
        contradictions=(() if deck is None else deck.contradictions),
        founder_observations=founder_observations,
        public_lookup_urls=(() if metadata is None else metadata.public_lookup_urls),
    )


__all__ = ["combined_inbound_snapshot_id", "compose_inbound_analysis_snapshot"]
