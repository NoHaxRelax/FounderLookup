"""Conservative projection of public hackathon showcase pages.

The live provider remains responsible for finding and acquiring an approved public page.
This module only interprets explicit Markdown labels and links from the immutable acquired
artifact.  It never treats a participant as a verified Founder, never collects contact
details, and never turns a missing participant or deck into a negative signal.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from enum import StrEnum
from typing import Annotated, Final, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, model_validator

from founderlookup.domain.common import (
    DomainModel,
    KnowledgeValue,
    NonBlankStr,
    StableId,
    UTCDateTime,
)
from founderlookup.domain.evidence import (
    ArtifactAvailability,
    DataClassification,
    SourceArtifact,
    SourceCategory,
    SourceLocator,
    SourceLocatorKind,
)

HACKATHON_PROJECTION_VERSION: Final = "hackathon-showcase-projection.v0"
PUBLIC_HACKATHON_DECK_RELATIONSHIP_VERSION: Final = "public-hackathon-deck-relationship.v0"

_HEADING = re.compile(r"^\s{0,3}(?P<marks>#{1,6})\s+(?P<value>.+?)\s*$")
_BULLET = re.compile(r"^\s*[-+*]\s+(?P<value>.+?)\s*$")
_LABEL = re.compile(r"^\s*(?:[-+*]\s+)?(?P<label>[^:]{1,48})\s*:\s*(?P<value>.+?)\s*$")
_MARKDOWN_LINK = re.compile(r"\[(?P<label>[^\]]{1,160})\]\((?P<url>[^)\s]+)\)")
_BARE_URL = re.compile(r"https?://[^\s<>\])}]+", re.IGNORECASE)
_MARKDOWN_DECORATION = re.compile(r"(?:\*\*|__|`)")
_WHITESPACE = re.compile(r"\s+")
_MAX_SOURCE_LINES: Final = 2_000
_MAX_PARTICIPANTS: Final = 24
_MAX_LINKS: Final = 32
_MAX_EXCERPT_CHARS: Final = 600
_MAX_NAME_CHARS: Final = 160

_EVENT_LABELS: Final = frozenset({"event", "hackathon", "competition", "cohort"})
_PROJECT_LABELS: Final = frozenset({"project", "project name", "startup", "team project"})
_PARTICIPANT_LABELS: Final = frozenset(
    {"team", "team members", "participants", "makers", "built by", "created by"}
)
_DECK_LABEL_TOKENS: Final = ("pitch deck", "deck", "slides", "presentation")
_REPOSITORY_LABEL_TOKENS: Final = ("github", "gitlab", "repository", "source code", "repo")
_DEMO_LABEL_TOKENS: Final = (
    "demo",
    "prototype",
    "try it",
    "product",
    "website",
    "live site",
)
_PARTICIPANT_HEADINGS: Final = frozenset(
    {"team", "team members", "participants", "makers", "created by", "built by"}
)
_EVENT_SECTION_HEADINGS: Final = frozenset({"submitted to"})
_PARTICIPANT_SECTION_STOP = re.compile(r"^\s*\d+\s+people\s+like\s+this\s*:", re.IGNORECASE)


class HackathonLinkKind(StrEnum):
    PITCH_DECK = "pitch_deck"
    REPOSITORY = "repository"
    DEMO = "demo"


class IdentityReviewState(StrEnum):
    NEEDS_REVIEW = "needs_review"


class PublicHackathonLink(DomainModel):
    kind: HackathonLinkKind
    label: NonBlankStr
    url: NonBlankStr
    locator: SourceLocator


class PublicHackathonParticipant(DomainModel):
    """One public display name, not a verified Founder identity."""

    display_name: NonBlankStr
    public_profile_url: KnowledgeValue[str]
    locator: SourceLocator
    identity_state: Literal[IdentityReviewState.NEEDS_REVIEW] = IdentityReviewState.NEEDS_REVIEW


class HackathonShowcaseProjection(DomainModel):
    projection_version: Literal["hackathon-showcase-projection.v0"] = HACKATHON_PROJECTION_VERSION
    projection_id: StableId
    source_artifact_id: StableId
    source_url: NonBlankStr
    event_name: KnowledgeValue[str]
    event_locator: SourceLocator | None = None
    project_name: KnowledgeValue[str]
    project_locator: SourceLocator | None = None
    participants: Annotated[tuple[PublicHackathonParticipant, ...], Field(max_length=24)] = ()
    participant_gap_reason: NonBlankStr | None = None
    links: Annotated[tuple[PublicHackathonLink, ...], Field(max_length=32)] = ()
    pitch_deck_gap_reason: NonBlankStr | None = None
    truncated: bool = False

    @model_validator(mode="after")
    def preserve_explicit_gaps(self) -> HackathonShowcaseProjection:
        if bool(self.participants) == (self.participant_gap_reason is not None):
            raise ValueError(
                "participant gap reason is required exactly when participants are absent"
            )
        has_deck = any(link.kind is HackathonLinkKind.PITCH_DECK for link in self.links)
        if has_deck == (self.pitch_deck_gap_reason is not None):
            raise ValueError("pitch-deck gap reason is required exactly when no deck is linked")
        if (self.event_name.value is None) != (self.event_locator is None):
            raise ValueError("event locator is required exactly when the event name is known")
        if (self.project_name.value is None) != (self.project_locator is None):
            raise ValueError("project locator is required exactly when the project name is known")
        return self

    @property
    def follow_up_urls(self) -> tuple[str, ...]:
        """Bounded public URLs suitable for a separately policy-checked acquisition."""

        return tuple(link.url for link in self.links)


class PublicHackathonDeckRelationship(DomainModel):
    """Exact public showcase link to a separately acquired immutable deck artifact."""

    record_type: Literal["public_hackathon_deck_relationship"] = (
        "public_hackathon_deck_relationship"
    )
    relationship_version: Literal["public-hackathon-deck-relationship.v0"] = (
        PUBLIC_HACKATHON_DECK_RELATIONSHIP_VERSION
    )
    relationship_id: StableId
    relationship_version_id: StableId
    projection_id: StableId
    candidate_id: StableId
    showcase_source_artifact_id: StableId
    deck_source_artifact_id: StableId
    # Exact link published by the showcase; a normalized fetch target never replaces it.
    deck_original_url: NonBlankStr
    deck_acquisition_url: NonBlankStr
    deck_url_normalization: Literal["direct_pdf", "google_slides_export_pdf"]
    showcase_locator: SourceLocator
    acquired_at: UTCDateTime
    classification: Literal[DataClassification.PUBLIC] = DataClassification.PUBLIC
    identity_assertion: Literal["none"] = "none"


def link_public_hackathon_deck(
    *,
    projection: HackathonShowcaseProjection,
    link: PublicHackathonLink,
    deck_source_artifact: SourceArtifact,
    candidate_id: str,
    acquisition_url: str | None = None,
    url_normalization: Literal["direct_pdf", "google_slides_export_pdf"] = "direct_pdf",
) -> PublicHackathonDeckRelationship:
    """Link only the exact explicitly labelled deck URL; never infer a nearby document."""

    if link.kind is not HackathonLinkKind.PITCH_DECK:
        raise ValueError("public deck relationship requires an explicit pitch-deck link")
    if deck_source_artifact.classification is not DataClassification.PUBLIC:
        raise ValueError("public deck relationship requires a public deck Source Artifact")
    resolved_acquisition_url = acquisition_url or link.url
    if deck_source_artifact.origin_locator != resolved_acquisition_url:
        raise ValueError("acquired deck origin must exactly match the acquisition URL")
    if deck_source_artifact.source_artifact_id == projection.source_artifact_id:
        raise ValueError("showcase page and separately acquired deck must be distinct artifacts")
    material = "\x1f".join(
        (
            projection.projection_id,
            candidate_id,
            link.url,
            resolved_acquisition_url,
            url_normalization,
            deck_source_artifact.source_artifact_id,
            deck_source_artifact.artifact_version_id,
            PUBLIC_HACKATHON_DECK_RELATIONSHIP_VERSION,
        )
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    return PublicHackathonDeckRelationship(
        relationship_id=f"hackathon-deck-link:{digest}",
        relationship_version_id=f"hackathon-deck-link-version:{digest}",
        projection_id=projection.projection_id,
        candidate_id=candidate_id,
        showcase_source_artifact_id=projection.source_artifact_id,
        deck_source_artifact_id=deck_source_artifact.source_artifact_id,
        deck_original_url=link.url,
        deck_acquisition_url=resolved_acquisition_url,
        deck_url_normalization=url_normalization,
        showcase_locator=link.locator,
        acquired_at=deck_source_artifact.retrieved_at,
    )


def _clean_text(value: str) -> str:
    return _WHITESPACE.sub(" ", _MARKDOWN_DECORATION.sub("", value)).strip()


def _normalized_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _clean_text(value).casefold()).strip()


def _locator(line_number: int, excerpt: str) -> SourceLocator:
    return SourceLocator(
        kind=SourceLocatorKind.URL_EXCERPT,
        locator=f"line:{line_number}",
        excerpt=excerpt[:_MAX_EXCERPT_CHARS],
    )


def _public_url(value: str) -> str | None:
    candidate = value.strip().rstrip(".,;:")
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 80, 443}
        or parsed.fragment
    ):
        return None
    hostname = parsed.hostname.casefold().rstrip(".")
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        return None
    try:
        address = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        pass
    else:
        if not address.is_global:
            return None
    netloc = hostname
    if port is not None and not (
        (parsed.scheme.casefold() == "http" and port == 80)
        or (parsed.scheme.casefold() == "https" and port == 443)
    ):
        netloc = f"{hostname}:{port}"
    return urlunsplit((parsed.scheme.casefold(), netloc, parsed.path or "/", parsed.query, ""))


def _links_from_line(line: str) -> tuple[tuple[str, str], ...]:
    links: list[tuple[str, str]] = []
    consumed: set[str] = set()
    for match in _MARKDOWN_LINK.finditer(line):
        safe_url = _public_url(match.group("url"))
        if safe_url is None or safe_url in consumed:
            continue
        links.append((_clean_text(match.group("label")), safe_url))
        consumed.add(safe_url)
    for match in _BARE_URL.finditer(line):
        safe_url = _public_url(match.group(0))
        if safe_url is None or safe_url in consumed:
            continue
        links.append((safe_url, safe_url))
        consumed.add(safe_url)
    return tuple(links)


def _link_kind(label: str, url: str) -> HackathonLinkKind | None:
    normalized = _normalized_label(label)
    path = urlsplit(url).path.casefold()
    if any(token in normalized for token in _DECK_LABEL_TOKENS) or path.endswith(
        (".pdf", ".ppt", ".pptx", ".key")
    ):
        return HackathonLinkKind.PITCH_DECK
    hostname = (urlsplit(url).hostname or "").casefold()
    if any(token in normalized for token in _REPOSITORY_LABEL_TOKENS) or hostname in {
        "github.com",
        "gitlab.com",
        "codeberg.org",
    }:
        return HackathonLinkKind.REPOSITORY
    if any(token in normalized for token in _DEMO_LABEL_TOKENS):
        return HackathonLinkKind.DEMO
    return None


def _participant_values(value: str) -> tuple[str, ...]:
    without_links = _MARKDOWN_LINK.sub(lambda match: match.group("label"), value)
    candidates = re.split(r"\s*(?:,|;|\||\band\b)\s*", without_links)
    accepted: list[str] = []
    for candidate in candidates:
        name = _clean_text(candidate).strip("-\u2013\u2014")
        if (
            1 < len(name) <= _MAX_NAME_CHARS
            and "@" not in name
            and not name.casefold().startswith(("http://", "https://"))
        ):
            accepted.append(name)
    return tuple(dict.fromkeys(accepted))


def project_hackathon_showcase(
    *,
    source_artifact: SourceArtifact,
    content: bytes,
) -> HackathonShowcaseProjection:
    """Project explicit public showcase fields without network access or identity guesses."""

    if source_artifact.source_category is not SourceCategory.HACKATHON:
        raise ValueError("hackathon projection requires a hackathon Source Artifact")
    if source_artifact.classification is not DataClassification.PUBLIC:
        raise ValueError("hackathon projection accepts only public Source Artifacts")
    if source_artifact.availability is not ArtifactAvailability.AVAILABLE:
        raise ValueError("hackathon Source Artifact must be available")
    if hashlib.sha256(content).hexdigest() != source_artifact.content_sha256:
        raise ValueError("hackathon source bytes do not match the immutable artifact hash")
    try:
        markdown = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("hackathon source content must be UTF-8 text") from error

    all_lines = markdown.splitlines()
    lines = all_lines[:_MAX_SOURCE_LINES]
    truncated = len(lines) != len(all_lines)
    event_name: tuple[str, SourceLocator] | None = None
    project_name: tuple[str, SourceLocator] | None = None
    first_heading: tuple[str, SourceLocator] | None = None
    event_section_level: int | None = None
    event_section_active = False
    participant_section_level: int | None = None
    participant_section_active = False
    participants: list[PublicHackathonParticipant] = []
    links: list[PublicHackathonLink] = []
    seen_participants: set[tuple[str, str | None]] = set()
    seen_links: set[tuple[HackathonLinkKind, str]] = set()

    for index, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        locator = _locator(index, raw_line)
        heading = _HEADING.fullmatch(raw_line)
        if heading is not None:
            level = len(heading.group("marks"))
            heading_value = _clean_text(heading.group("value"))[:_MAX_NAME_CHARS]
            if first_heading is None and heading_value:
                first_heading = (heading_value, locator)
            normalized_heading = _normalized_label(heading_value)
            if normalized_heading in _EVENT_SECTION_HEADINGS:
                event_section_active = True
                event_section_level = level
                continue
            if normalized_heading in _PARTICIPANT_HEADINGS:
                participant_section_active = True
                participant_section_level = level
                continue
            if (
                event_section_active
                and event_section_level is not None
                and level <= event_section_level
            ):
                event_section_active = False
            if (
                participant_section_active
                and participant_section_level is not None
                and level <= participant_section_level
            ):
                participant_section_active = False

        if event_section_active and event_name is None:
            event_links = tuple(
                (label, url)
                for label, url in _links_from_line(raw_line)
                if not _normalized_label(label).startswith("image")
            )
            if event_links:
                event_name = (event_links[0][0][:_MAX_NAME_CHARS], locator)
                event_section_active = False

        if participant_section_active and _PARTICIPANT_SECTION_STOP.match(raw_line):
            participant_section_active = False

        label_match = _LABEL.fullmatch(raw_line)
        if label_match is not None:
            label = _normalized_label(label_match.group("label"))
            value = _clean_text(label_match.group("value"))
            if label in _EVENT_LABELS and value and event_name is None:
                event_name = (value[:_MAX_NAME_CHARS], locator)
            if label in _PROJECT_LABELS and value and project_name is None:
                project_name = (value[:_MAX_NAME_CHARS], locator)
            if label in _PARTICIPANT_LABELS:
                profile_links = _links_from_line(label_match.group("value"))
                link_by_name = {_clean_text(text): url for text, url in profile_links}
                for name in _participant_values(label_match.group("value")):
                    profile_url = link_by_name.get(name)
                    key = (name.casefold(), profile_url)
                    if key in seen_participants or len(participants) >= _MAX_PARTICIPANTS:
                        truncated = truncated or len(participants) >= _MAX_PARTICIPANTS
                        continue
                    participants.append(
                        PublicHackathonParticipant(
                            display_name=name,
                            public_profile_url=(
                                KnowledgeValue[str].known(profile_url)
                                if profile_url is not None
                                else KnowledgeValue[str].unknown(
                                    "No public profile URL is explicitly linked for this "
                                    "display name."
                                )
                            ),
                            locator=locator,
                        )
                    )
                    seen_participants.add(key)

        if participant_section_active:
            bullet = _BULLET.fullmatch(raw_line)
            if bullet is not None:
                profile_links = _links_from_line(bullet.group("value"))
                named_profiles = tuple(
                    (name, url)
                    for name, url in profile_links
                    if not _normalized_label(name).startswith("image")
                )
                names_and_profiles = (
                    named_profiles
                    if named_profiles
                    else tuple((name, None) for name in _participant_values(bullet.group("value")))
                )
                for name, profile_url in names_and_profiles:
                    key = (name.casefold(), profile_url)
                    if key not in seen_participants and len(participants) < _MAX_PARTICIPANTS:
                        participants.append(
                            PublicHackathonParticipant(
                                display_name=name,
                                public_profile_url=(
                                    KnowledgeValue[str].known(profile_url)
                                    if profile_url is not None
                                    else KnowledgeValue[str].unknown(
                                        "No public profile URL is explicitly linked for this "
                                        "display name."
                                    )
                                ),
                                locator=locator,
                            )
                        )
                        seen_participants.add(key)

        link_context = label_match.group("label") if label_match is not None else raw_line
        for link_label, url in _links_from_line(raw_line):
            kind = _link_kind(f"{link_context} {link_label}", url)
            if kind is None:
                continue
            key = (kind, url)
            if key in seen_links or len(links) >= _MAX_LINKS:
                truncated = truncated or len(links) >= _MAX_LINKS
                continue
            links.append(
                PublicHackathonLink(
                    kind=kind,
                    label=link_label[:_MAX_NAME_CHARS],
                    url=url,
                    locator=locator,
                )
            )
            seen_links.add(key)

    if project_name is None:
        project_name = first_heading

    evidence_ids = (source_artifact.source_artifact_id,)
    projection_material = "\x1f".join(
        (
            source_artifact.source_artifact_id,
            source_artifact.artifact_version_id,
            HACKATHON_PROJECTION_VERSION,
        )
    )
    projection_id = (
        f"hackathon-projection:{hashlib.sha256(projection_material.encode()).hexdigest()[:32]}"
    )
    return HackathonShowcaseProjection(
        projection_id=projection_id,
        source_artifact_id=source_artifact.source_artifact_id,
        source_url=source_artifact.origin_locator,
        event_name=(
            KnowledgeValue[str].known(event_name[0], evidence_ids=evidence_ids)
            if event_name is not None
            else KnowledgeValue[str].unknown(
                "No explicit event label is present on the source page."
            )
        ),
        event_locator=(None if event_name is None else event_name[1]),
        project_name=(
            KnowledgeValue[str].known(project_name[0], evidence_ids=evidence_ids)
            if project_name is not None
            else KnowledgeValue[str].unknown(
                "No explicit project label or page heading is present on the source page."
            )
        ),
        project_locator=(None if project_name is None else project_name[1]),
        participants=tuple(participants),
        participant_gap_reason=(
            None
            if participants
            else "No public participant display names were explicitly labeled on the source page."
        ),
        links=tuple(links),
        pitch_deck_gap_reason=(
            None
            if any(link.kind is HackathonLinkKind.PITCH_DECK for link in links)
            else "No public pitch-deck link was explicitly labeled on the source page."
        ),
        truncated=truncated,
    )


__all__ = [
    "HACKATHON_PROJECTION_VERSION",
    "PUBLIC_HACKATHON_DECK_RELATIONSHIP_VERSION",
    "HackathonLinkKind",
    "HackathonShowcaseProjection",
    "IdentityReviewState",
    "PublicHackathonDeckRelationship",
    "PublicHackathonLink",
    "PublicHackathonParticipant",
    "link_public_hackathon_deck",
    "project_hackathon_showcase",
]
