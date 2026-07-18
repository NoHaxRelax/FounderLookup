"""Shared, provider-neutral helpers for source-specific ingestion adapters.

These keep each source adapter thin and consistent: identifier slugging, the
mapping from (leads, failures) to a valid ``CollectionResultStatus``, safe JSON
decoding, retrieval-relevance wrapping, and a shared discovery failure mapping.
Adapter-specific result identifiers and messages stay in each adapter.
"""

from __future__ import annotations

import json

from founderlookup.domain.common import KnowledgeValue
from founderlookup.domain.discovery import CollectionFailure, CollectionResultStatus


def slug(value: str) -> str:
    """Reduce an arbitrary token to a StableId-safe fragment."""
    cleaned = "".join(c if (c.isalnum() or c in "._-") else "-" for c in value).strip("-")
    return cleaned or "x"


def result_status(has_leads: bool, has_failures: bool) -> CollectionResultStatus:
    """Map presence of leads and failures to a valid discovery status."""
    if has_leads and has_failures:
        return CollectionResultStatus.PARTIALLY_SUCCEEDED
    if not has_leads and has_failures:
        return CollectionResultStatus.FAILED
    return CollectionResultStatus.SUCCEEDED


def decode_json(body: bytes) -> dict[str, object]:
    """Parse a JSON object body, returning an empty mapping on any failure."""
    try:
        parsed = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def relevance(score: object) -> KnowledgeValue[float]:
    """Wrap a provider relevance score; it is retrieval metadata, not evidence."""
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        return KnowledgeValue[float].known(float(score))
    return KnowledgeValue[float].unknown("the source did not return a relevance score")


def discovery_failure_for_status(status: int, operation_id: str) -> CollectionFailure | None:
    """Translate a non-200 discovery status into a safe CollectionFailure."""
    if status == 200:
        return None
    if status == 403 or status == 429:
        return CollectionFailure(
            operation_id=operation_id,
            safe_code="rate_limited",
            safe_message="source rate limit or access restriction",
            retryable=True,
        )
    return CollectionFailure(
        operation_id=operation_id,
        safe_code="upstream_status",
        safe_message="unexpected upstream status",
        retryable=status >= 500,
    )
