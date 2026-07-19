"""Public hackathon showcase projection stays explicit, bounded, and reversible."""

from datetime import UTC, datetime
from hashlib import sha256

import pytest

from founderlookup.domain.common import KnowledgeState, KnowledgeValue
from founderlookup.domain.evidence import (
    DataClassification,
    SourceArtifact,
    SourceArtifactKind,
    SourceCategory,
)
from founderlookup.ingestion.hackathons import (
    HackathonLinkKind,
    IdentityReviewState,
    project_hackathon_showcase,
)


def _artifact(content: bytes, **updates: object) -> SourceArtifact:
    artifact = SourceArtifact(
        source_artifact_id="artifact:hackathon:1",
        artifact_series_id="series:hackathon:1",
        artifact_version_id="version:hackathon:1",
        version_number=1,
        kind=SourceArtifactKind.WEB_SNAPSHOT,
        source_category=SourceCategory.HACKATHON,
        classification=DataClassification.PUBLIC,
        origin_locator="https://showcase.example/projects/cold-start",
        display_name="Public project showcase",
        media_type="text/markdown",
        content_sha256=sha256(content).hexdigest(),
        retrieved_at=datetime(2026, 7, 19, tzinfo=UTC),
        source_event_time=KnowledgeValue[datetime].unknown("event date unavailable"),
    )
    return artifact.model_copy(update=updates)


def test_projects_explicit_people_and_public_deck_links_without_verifying_identity() -> None:
    content = b"""# Quiet Systems

Event: Open Builders Demo Day
Project: Quiet Systems
Team: [Ada Example](https://example.com/ada), Lin Example
Pitch deck: [Investor slides](https://cdn.example.com/quiet-systems.pdf)
Repository: [Source](https://github.com/example/quiet-systems)
Demo: [Try it](https://quiet.example/)
"""

    projected = project_hackathon_showcase(source_artifact=_artifact(content), content=content)

    assert projected.event_name.state is KnowledgeState.KNOWN
    assert projected.event_name.value == "Open Builders Demo Day"
    assert projected.project_name.value == "Quiet Systems"
    assert tuple(item.display_name for item in projected.participants) == (
        "Ada Example",
        "Lin Example",
    )
    assert all(
        item.identity_state is IdentityReviewState.NEEDS_REVIEW for item in projected.participants
    )
    assert projected.participants[0].public_profile_url.value == "https://example.com/ada"
    assert projected.participants[1].public_profile_url.state is KnowledgeState.UNKNOWN
    assert projected.participants[0].locator.locator == "line:5"
    assert tuple(item.kind for item in projected.links) == (
        HackathonLinkKind.PITCH_DECK,
        HackathonLinkKind.REPOSITORY,
        HackathonLinkKind.DEMO,
    )
    assert projected.links[0].url == "https://cdn.example.com/quiet-systems.pdf"
    assert projected.pitch_deck_gap_reason is None
    assert projected.follow_up_urls == tuple(item.url for item in projected.links)


def test_missing_people_and_deck_remain_neutral_explicit_gaps() -> None:
    content = b"# A public event page\n\nProject: Signal Garden\n"

    projected = project_hackathon_showcase(source_artifact=_artifact(content), content=content)

    assert projected.participants == ()
    assert "No public participant" in (projected.participant_gap_reason or "")
    assert projected.links == ()
    assert "No public pitch-deck" in (projected.pitch_deck_gap_reason or "")
    assert projected.event_name.state is KnowledgeState.UNKNOWN


def test_participant_section_accepts_public_screen_names_but_not_contact_details() -> None:
    content = b"""# Project Pine
## Team
- [maker-one](https://profiles.example/maker-one)
- person@example.com
- Second Maker
## Product
Description only.
"""

    projected = project_hackathon_showcase(source_artifact=_artifact(content), content=content)

    assert tuple(item.display_name for item in projected.participants) == (
        "maker-one",
        "Second Maker",
    )


@pytest.mark.parametrize(
    "url",
    (
        "http://127.0.0.1/deck.pdf",
        "http://169.254.169.254/latest/meta-data",
        "https://user:password@example.com/deck.pdf",
        "https://localhost/deck.pdf",
        "https://example.com:8443/deck.pdf",
    ),
)
def test_blocks_non_public_or_credentialed_follow_up_links(url: str) -> None:
    content = f"# Project\nPitch deck: [Deck]({url})\n".encode()

    projected = project_hackathon_showcase(source_artifact=_artifact(content), content=content)

    assert projected.links == ()
    assert projected.pitch_deck_gap_reason is not None


def test_rejects_wrong_classification_category_hash_and_encoding() -> None:
    content = b"# Project\n"
    artifact = _artifact(content)

    with pytest.raises(ValueError, match="hackathon Source Artifact"):
        project_hackathon_showcase(
            source_artifact=artifact.model_copy(update={"source_category": SourceCategory.OTHER}),
            content=content,
        )
    with pytest.raises(ValueError, match="only public"):
        project_hackathon_showcase(
            source_artifact=artifact.model_copy(
                update={"classification": DataClassification.FOUNDER_PRIVATE}
            ),
            content=content,
        )
    with pytest.raises(ValueError, match="hash"):
        project_hackathon_showcase(source_artifact=artifact, content=b"# Changed\n")
    invalid_utf8 = b"\xff\xfe"
    with pytest.raises(ValueError, match="UTF-8"):
        project_hackathon_showcase(
            source_artifact=_artifact(invalid_utf8),
            content=invalid_utf8,
        )


def test_projection_is_stable_and_bounded() -> None:
    people = ", ".join(f"Person {index}" for index in range(40))
    links = "\n".join(
        f"Pitch deck: [Deck {index}](https://cdn.example.com/{index}.pdf)" for index in range(40)
    )
    content = f"# Massive Project\nTeam: {people}\n{links}\n".encode()
    artifact = _artifact(content)

    first = project_hackathon_showcase(source_artifact=artifact, content=content)
    second = project_hackathon_showcase(source_artifact=artifact, content=content)

    assert first == second
    assert len(first.participants) == 24
    assert len(first.links) == 32
    assert first.truncated is True


def test_devpost_style_sections_preserve_event_creators_and_public_links() -> None:
    content = b"""# Pala

## Links
[Live site](https://pala.example/redirect) | [Pitch deck](https://decks.example/pala.pdf)

## Try it out
* [github.com](https://github.com/example/pala)

#### Submitted to
* [NexHacks](https://nexhacks.devpost.com/)

#### Created by
* [Image: Ada Demo](https://devpost.com/users/ada-image) [Ada Demo](https://devpost.com/ada)
* [Bo Demo](https://devpost.com/bo)

8 people like this:
* [Not A Participant](https://devpost.com/liker)
"""

    projected = project_hackathon_showcase(source_artifact=_artifact(content), content=content)

    assert projected.event_name.value == "NexHacks"
    assert projected.event_locator is not None
    assert projected.event_locator.locator == "line:10"
    assert [item.display_name for item in projected.participants] == ["Ada Demo", "Bo Demo"]
    assert [item.public_profile_url.value for item in projected.participants] == [
        "https://devpost.com/ada",
        "https://devpost.com/bo",
    ]
    assert {item.kind for item in projected.links} == {
        HackathonLinkKind.PITCH_DECK,
        HackathonLinkKind.REPOSITORY,
        HackathonLinkKind.DEMO,
    }
