"""Focused invariants for shared strict values."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from founderlookup.domain import (
    LIFECYCLE_SCHEMA_VERSION,
    ComponentVersion,
    DomainModel,
    KnowledgeAlternative,
    KnowledgeState,
    KnowledgeValue,
    UTCDateTime,
    VersionComponent,
    VersionManifest,
)


class _Timestamped(DomainModel):
    happened_at: UTCDateTime


def test_knowledge_value_states_are_explicit_and_immutable() -> None:
    unknown = KnowledgeValue[str].unknown("No reliable source has established geography")

    assert unknown.state is KnowledgeState.UNKNOWN
    assert unknown.value is None
    with pytest.raises(ValidationError, match="frozen"):
        unknown.reason = "changed"


def test_known_and_conflicted_shapes_reject_ambiguous_payloads() -> None:
    known = KnowledgeValue[str].known("Berlin", evidence_ids=("evidence:geo",))
    assert known.value == "Berlin"

    with pytest.raises(ValidationError, match="non-known values cannot carry value"):
        KnowledgeValue[str](
            state=KnowledgeState.UNKNOWN,
            value="Berlin",
            reason="not established",
        )

    with pytest.raises(ValidationError, match="at least two alternatives"):
        KnowledgeValue[str].conflicted(
            "sources disagree",
            (
                KnowledgeAlternative[str](
                    value="Berlin",
                    evidence_ids=("evidence:geo-a",),
                ),
            ),
        )


def test_extra_fields_and_non_utc_timestamps_fail_closed() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        KnowledgeValue[str].model_validate_json(
            '{"state":"unknown","reason":"not collected","unexpected":true}'
        )

    with pytest.raises(ValidationError, match="timezone info"):
        _Timestamped(happened_at=datetime(2026, 7, 18, 10, 0))

    with pytest.raises(ValidationError, match="use UTC"):
        _Timestamped(happened_at=datetime.fromisoformat("2026-07-18T12:00:00+02:00"))

    valid = _Timestamped(happened_at=datetime(2026, 7, 18, 10, 0, tzinfo=UTC))
    assert valid.happened_at.utcoffset() is not None


def test_version_manifest_rejects_ambiguous_duplicate_components() -> None:
    assert LIFECYCLE_SCHEMA_VERSION == "lifecycle.v0"
    version = ComponentVersion(
        component=VersionComponent.TOOL,
        name="search-adapter",
        version_id="fake.v0",
    )
    with pytest.raises(ValidationError, match="must be unique"):
        VersionManifest(components=(version, version))
