"""Deterministic tests for baseline cross-source identity resolution."""

from datetime import UTC, datetime
from itertools import count

import pytest

from founderlookup.domain.evidence import SourceCategory
from founderlookup.ingestion.identity import (
    IDENTITY_RESOLUTION_VERSION,
    IdentityLinkAction,
    IdentityLinkLedger,
    IdentitySignal,
    IdentitySignalKind,
    ResolutionStatus,
    resolve_identities,
)

DEV = SourceCategory.DEVELOPER_ACTIVITY
SOCIAL = SourceCategory.PUBLIC_SOCIAL
RESEARCH = SourceCategory.RESEARCH


def _sig(kind: IdentitySignalKind, value: str, cat: SourceCategory, ref: str) -> IdentitySignal:
    return IdentitySignal(kind=kind, value=value, source_category=cat, source_ref=ref)


def test_empty_input_returns_empty() -> None:
    assert resolve_identities([]) == ()


def test_same_source_record_is_one_entity() -> None:
    signals = [
        _sig(IdentitySignalKind.HANDLE, "octocat", DEV, "gh:1"),
        _sig(IdentitySignalKind.NAME, "The Octocat", DEV, "gh:1"),
    ]
    entities = resolve_identities(signals)
    assert len(entities) == 1
    assert entities[0].status is ResolutionStatus.RESOLVED
    assert entities[0].source_refs == frozenset({"gh:1"})


def test_shared_handle_links_records_across_sources() -> None:
    signals = [
        _sig(IdentitySignalKind.HANDLE, "ada", DEV, "gh:1"),
        _sig(IdentitySignalKind.NAME, "Ada L", DEV, "gh:1"),
        _sig(IdentitySignalKind.HANDLE, "ada", SOCIAL, "hn:1"),
    ]
    entities = resolve_identities(signals)
    assert len(entities) == 1
    entity = entities[0]
    assert entity.status is ResolutionStatus.RESOLVED
    assert entity.confidence == 0.9
    assert entity.source_categories == frozenset({DEV, SOCIAL})
    assert entity.reasons[0].rule == "shared_identifier"
    assert entity.resolution_version == IDENTITY_RESOLUTION_VERSION
    assert entity.resolution_id.startswith("identity-resolution:")


def test_profile_url_normalization_links_records() -> None:
    signals = [
        _sig(IdentitySignalKind.PROFILE_URL, "https://github.com/ada", DEV, "gh:8"),
        _sig(IdentitySignalKind.PROFILE_URL, "https://github.com/ada/", SOCIAL, "hn:8"),
    ]
    entities = resolve_identities(signals)
    assert len(entities) == 1
    assert entities[0].confidence == 0.9


def test_name_only_match_across_sources_is_needs_review() -> None:
    signals = [
        _sig(IdentitySignalKind.HANDLE, "adalove", DEV, "gh:2"),
        _sig(IdentitySignalKind.NAME, "Ada Lovelace", DEV, "gh:2"),
        _sig(IdentitySignalKind.EXTERNAL_ID, "A555", RESEARCH, "oa:2"),
        _sig(IdentitySignalKind.NAME, "Ada Lovelace", RESEARCH, "oa:2"),
    ]
    entities = resolve_identities(signals)
    assert len(entities) == 1
    entity = entities[0]
    assert entity.status is ResolutionStatus.NEEDS_REVIEW
    assert entity.confidence == 0.4
    assert entity.reasons[0].rule == "corroborated_name"
    assert entity.source_categories == frozenset({DEV, RESEARCH})


def test_same_name_same_source_stays_separate() -> None:
    signals = [
        _sig(IdentitySignalKind.HANDLE, "js1", DEV, "gh:3"),
        _sig(IdentitySignalKind.NAME, "John Smith", DEV, "gh:3"),
        _sig(IdentitySignalKind.HANDLE, "js2", DEV, "gh:4"),
        _sig(IdentitySignalKind.NAME, "John Smith", DEV, "gh:4"),
    ]
    entities = resolve_identities(signals)
    assert len(entities) == 2
    assert all(e.status is ResolutionStatus.RESOLVED for e in entities)


def test_distinct_people_stay_separate() -> None:
    signals = [
        _sig(IdentitySignalKind.HANDLE, "alice", DEV, "gh:5"),
        _sig(IdentitySignalKind.NAME, "Alice", DEV, "gh:5"),
        _sig(IdentitySignalKind.HANDLE, "bob", SOCIAL, "hn:5"),
        _sig(IdentitySignalKind.NAME, "Bob", SOCIAL, "hn:5"),
    ]
    entities = resolve_identities(signals)
    assert len(entities) == 2


def test_single_source_name_resolves_at_low_confidence() -> None:
    signals = [_sig(IdentitySignalKind.NAME, "Solo Founder", DEV, "x:1")]
    entities = resolve_identities(signals)
    assert len(entities) == 1
    assert entities[0].status is ResolutionStatus.RESOLVED
    assert entities[0].confidence == 0.6
    assert entities[0].reasons[0].rule == "single_source"


def test_aliases_and_match_evidence_are_preserved_without_choosing_one_name() -> None:
    signals = [
        IdentitySignal(
            kind=IdentitySignalKind.HANDLE,
            value="ada",
            source_category=DEV,
            source_ref="gh:ada",
            evidence_ids=("evidence:github-handle",),
        ),
        IdentitySignal(
            kind=IdentitySignalKind.NAME,
            value="Ada L.",
            source_category=DEV,
            source_ref="gh:ada",
            evidence_ids=("evidence:github-name",),
        ),
        IdentitySignal(
            kind=IdentitySignalKind.HANDLE,
            value="ada",
            source_category=SOCIAL,
            source_ref="hn:ada",
            evidence_ids=("evidence:hn-handle",),
        ),
    ]

    first = resolve_identities(signals)[0]
    second = resolve_identities(tuple(reversed(signals)))[0]

    assert first.aliases == ("ada", "Ada L.")
    assert first.match_evidence_ids == (
        "evidence:github-handle",
        "evidence:github-name",
        "evidence:hn-handle",
    )
    assert first.resolution_id == second.resolution_id


def test_human_approved_identity_link_is_append_only_and_reversible() -> None:
    identifiers = count(1)
    ledger = IdentityLinkLedger(
        clock=lambda: datetime(2026, 7, 19, 12, tzinfo=UTC),
        id_factory=lambda prefix: f"{prefix}:{next(identifiers)}",
    )

    approval = ledger.approve(
        left_entity_id="founder:b",
        right_entity_id="founder:a",
        actor_id="investor:reviewer",
        rationale="Public repository links the two source records.",
        evidence_ids=("evidence:repo-profile",),
    )
    reversal = ledger.reverse(
        approval.link_id,
        actor_id="investor:reviewer",
        rationale="Founder correction showed the records belong to different people.",
    )

    assert approval.action is IdentityLinkAction.APPROVED
    assert (approval.left_entity_id, approval.right_entity_id) == (
        "founder:a",
        "founder:b",
    )
    assert reversal.action is IdentityLinkAction.REVERSED
    assert reversal.reverses_event_id == approval.event_id
    assert ledger.events == (approval, reversal)
    assert ledger.active_links == ()
    with pytest.raises(ValueError, match="not active"):
        ledger.reverse(
            approval.link_id,
            actor_id="investor:reviewer",
            rationale="Duplicate reversal must fail.",
        )
