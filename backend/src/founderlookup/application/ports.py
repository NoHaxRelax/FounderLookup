"""True external seams consumed by the application and HTTP layers."""

from __future__ import annotations

import ipaddress
import re
from typing import Annotated, Protocol, Self, runtime_checkable
from urllib.parse import urlsplit

from pydantic import Field, StringConstraints, field_validator, model_validator

from founderlookup.domain.common import DomainModel, NonBlankStr, StableId, UTCDateTime

_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_GITHUB_PATH = re.compile(r"^/[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/?$")
_LINKEDIN_PATH = re.compile(r"^/in/[A-Za-z0-9._%-]{1,100}/?$")

ShortOptionalText = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=500),
]
LongOptionalText = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=4_000),
]
SubmittedEmail = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=3, max_length=320),
]
SubmittedUrl = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=9, max_length=2_048),
]


def _validate_email(value: str) -> str:
    if _EMAIL.fullmatch(value) is None:
        raise ValueError("email must have a valid bounded address shape")
    return value


def _public_https_url(value: str, *, profile: str | None = None) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("submitted URLs must be credential-free HTTPS URLs without fragments")
    hostname = parsed.hostname.casefold().rstrip(".")
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        raise ValueError("submitted URLs must use a public host")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if "." not in hostname:
            raise ValueError("submitted URLs must use a public host") from None
    else:
        if not address.is_global:
            raise ValueError("submitted URLs must not target a private or local address")
    if profile == "github" and (
        hostname != "github.com" or parsed.query or _GITHUB_PATH.fullmatch(parsed.path) is None
    ):
        raise ValueError("GitHub profiles must use https://github.com/<username>")
    if profile == "linkedin" and (
        hostname not in {"linkedin.com", "www.linkedin.com"}
        or parsed.query
        or _LINKEDIN_PATH.fullmatch(parsed.path) is None
    ):
        raise ValueError("LinkedIn profiles must use an https://linkedin.com/in/... path")
    return value


class ApplicationFounderProfile(DomainModel):
    """Founder-provided identity lead; never proof of a canonical person identity."""

    full_name: Annotated[
        str,
        StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200),
    ]
    role_title: ShortOptionalText | None = None
    email: SubmittedEmail | None = None
    linkedin_url: SubmittedUrl | None = None
    github_url: SubmittedUrl | None = None
    previous_companies: Annotated[
        tuple[ShortOptionalText, ...],
        Field(max_length=20),
    ] = ()
    background: LongOptionalText | None = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        return None if value is None else _validate_email(value)

    @field_validator("linkedin_url")
    @classmethod
    def validate_linkedin(cls, value: str | None) -> str | None:
        return None if value is None else _public_https_url(value, profile="linkedin")

    @field_validator("github_url")
    @classmethod
    def validate_github(cls, value: str | None) -> str | None:
        return None if value is None else _public_https_url(value, profile="github")

    @model_validator(mode="after")
    def reject_duplicate_previous_companies(self) -> Self:
        normalized = tuple(item.casefold() for item in self.previous_companies)
        if len(normalized) != len(set(normalized)):
            raise ValueError("previous companies must be unique per founder")
        return self


class ApplicationSubmittedMetadata(DomainModel):
    """Optional structured Application context, all asserted by the submitter."""

    website: SubmittedUrl | None = None
    one_line_pitch: (
        Annotated[
            str,
            StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=1_000),
        ]
        | None
    ) = None
    location: ShortOptionalText | None = None
    stage: ShortOptionalText | None = None
    contact_email: SubmittedEmail | None = None
    founders: Annotated[
        tuple[ApplicationFounderProfile, ...],
        Field(max_length=10),
    ] = ()

    @field_validator("website")
    @classmethod
    def validate_website(cls, value: str | None) -> str | None:
        return None if value is None else _public_https_url(value)

    @field_validator("contact_email")
    @classmethod
    def validate_contact_email(cls, value: str | None) -> str | None:
        return None if value is None else _validate_email(value)

    @model_validator(mode="after")
    def reject_duplicate_founder_rows(self) -> Self:
        keys = tuple(
            (
                item.full_name.casefold(),
                None if item.email is None else item.email.casefold(),
            )
            for item in self.founders
        )
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate founder rows are not allowed")
        return self


class IntakeSubmission(DomainModel):
    company_name: NonBlankStr
    display_name: NonBlankStr
    media_type: NonBlankStr
    deck_content: bytes
    idempotency_key: NonBlankStr
    canonical_company_id: StableId | None = None
    metadata: ApplicationSubmittedMetadata = Field(
        default_factory=lambda: ApplicationSubmittedMetadata()
    )


class AcceptedApplication(DomainModel):
    application_id: StableId
    company_id: StableId
    run_id: StableId
    source_artifact_id: StableId
    source_artifact_sha256: NonBlankStr
    received_at: UTCDateTime
    company_name: NonBlankStr | None = None
    metadata: ApplicationSubmittedMetadata = Field(
        default_factory=lambda: ApplicationSubmittedMetadata()
    )
    replayed: bool = False


@runtime_checkable
class ApplicationIntakePort(Protocol):
    async def submit(self, submission: IntakeSubmission) -> AcceptedApplication:
        """Accept or idempotently replay one validated minimum Application."""
        ...


@runtime_checkable
class PrivateArtifactReadPort(Protocol):
    def read(
        self,
        artifact_id: str,
        *,
        principal_id: str,
        expected_sha256: str,
    ) -> bytes:
        """Return content-verified bytes after server-side authorization."""
        ...
