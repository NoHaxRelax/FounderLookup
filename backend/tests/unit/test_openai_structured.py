"""Deterministic contracts for bounded OpenAI public-page Structured Outputs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256

import httpx
import pytest
from pydantic import SecretStr

from founderlookup.domain.common import EntityKind, KnowledgeState, KnowledgeValue, SubjectRef
from founderlookup.domain.evidence import (
    DataClassification,
    SourceArtifact,
    SourceArtifactKind,
    SourceCategory,
)
from founderlookup.ingestion.hackathons import IdentityReviewState
from founderlookup.ingestion.openai_structured import (
    OpenAIPublicPageExtraction,
    OpenAIStructuredPageExtractor,
    OpenAIStructuredPolicy,
    PublicPageStructuredRequest,
    StructuredExtractionStatus,
    project_public_contact_evidence,
)

NOW = datetime(2026, 7, 19, 16, tzinfo=UTC)
PUBLIC_MARKDOWN = b"""# Signal Forge
Event: Alpine AI Hack 2026
Project: Signal Forge
Participants: [Ada Demo](https://showcase.example/people/ada)
Repository: [GitHub](https://github.com/example/signal-forge)
Demo: [Try it](https://signal-forge.example/)
Pitch deck: [Public slides](https://showcase.example/decks/signal-forge.pdf)
Website: [Official site](https://signal-forge.example/)
Contact: hello@signal-forge.example
"""


def _artifact(
    *,
    classification: DataClassification = DataClassification.PUBLIC,
) -> SourceArtifact:
    return SourceArtifact(
        source_artifact_id="source-artifact:openai-test",
        artifact_series_id="source-series:openai-test",
        artifact_version_id="source-version:openai-test",
        version_number=1,
        kind=SourceArtifactKind.WEB_SNAPSHOT,
        source_category=SourceCategory.HACKATHON,
        classification=classification,
        origin_locator="https://showcase.example/projects/signal-forge",
        display_name="Signal Forge public showcase",
        media_type="text/markdown; charset=utf-8",
        content_sha256=sha256(PUBLIC_MARKDOWN).hexdigest(),
        retrieved_at=NOW,
        source_event_time=KnowledgeValue[datetime].unknown(
            "The showcase did not establish a source event time"
        ),
    )


def _extraction_payload() -> dict[str, object]:
    return {
        "schema_version": "openai-public-page-extraction.v0",
        "event": {
            "state": "known",
            "value": "Alpine AI Hack 2026",
            "gap_reason": None,
            "evidence": {"line_number": 2, "excerpt": "Event: Alpine AI Hack 2026"},
        },
        "project": {
            "state": "known",
            "value": "Signal Forge",
            "gap_reason": None,
            "evidence": {"line_number": 3, "excerpt": "Project: Signal Forge"},
        },
        "participants": [
            {
                "display_name": "Ada Demo",
                "public_profile_url": "https://showcase.example/people/ada",
                "evidence": {
                    "line_number": 4,
                    "excerpt": ("Participants: [Ada Demo](https://showcase.example/people/ada)"),
                },
            }
        ],
        "participant_gap_reason": None,
        "links": [
            {
                "kind": "repository",
                "label": "GitHub",
                "url": "https://github.com/example/signal-forge",
                "evidence": {
                    "line_number": 5,
                    "excerpt": ("Repository: [GitHub](https://github.com/example/signal-forge)"),
                },
            },
            {
                "kind": "demo",
                "label": "Try it",
                "url": "https://signal-forge.example/",
                "evidence": {
                    "line_number": 6,
                    "excerpt": "Demo: [Try it](https://signal-forge.example/)",
                },
            },
            {
                "kind": "pitch_deck",
                "label": "Public slides",
                "url": "https://showcase.example/decks/signal-forge.pdf",
                "evidence": {
                    "line_number": 7,
                    "excerpt": (
                        "Pitch deck: [Public slides]"
                        "(https://showcase.example/decks/signal-forge.pdf)"
                    ),
                },
            },
        ],
        "public_deck_gap_reason": None,
        "public_contacts": [
            {
                "kind": "website",
                "label": "Official site",
                "value": "https://signal-forge.example/",
                "evidence": {
                    "line_number": 8,
                    "excerpt": "Website: [Official site](https://signal-forge.example/)",
                },
            },
            {
                "kind": "public_email",
                "label": "Contact",
                "value": "hello@signal-forge.example",
                "evidence": {
                    "line_number": 9,
                    "excerpt": "Contact: hello@signal-forge.example",
                },
            },
        ],
        "public_contact_gap_reason": None,
        "ambiguous_or_unsupported": [],
        "identity_verification": "not_performed",
    }


def _response(payload: dict[str, object]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "resp_public_test",
            "model": "gpt-5.6-luna",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": json.dumps(payload)}],
                }
            ],
            "usage": {"input_tokens": 400, "output_tokens": 250, "total_tokens": 650},
        },
    )


@pytest.mark.anyio
async def test_responses_request_is_strict_bounded_stateless_and_projects_exact_source() -> None:
    captured: dict[str, object] = {}
    secret = "fictional-openai-unit-key"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.openai.com/v1/responses"
        assert request.headers["authorization"] == f"Bearer {secret}"
        captured.update(json.loads(request.content))
        return _response(_extraction_payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        extractor = OpenAIStructuredPageExtractor(
            api_key=SecretStr(secret),
            policy=OpenAIStructuredPolicy(),
            now=lambda: NOW,
            client=client,
        )
        result = await extractor.extract(
            PublicPageStructuredRequest(
                request_id="structured-request:001",
                source_artifact=_artifact(),
                content=PUBLIC_MARKDOWN,
            )
        )

    assert captured["model"] == "gpt-5.6-luna"
    assert captured["store"] is False
    assert captured["max_output_tokens"] == 2_000
    text = captured["text"]
    assert isinstance(text, dict)
    output_format = text["format"]
    assert isinstance(output_format, dict)
    assert output_format["type"] == "json_schema"
    assert output_format["strict"] is True
    schema = output_format["schema"]
    assert isinstance(schema, dict)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])

    assert result.status is StructuredExtractionStatus.SUCCEEDED
    assert result.failure is None
    assert result.model_version.value == "gpt-5.6-luna"
    assert result.provider_response_id.value == "resp_public_test"
    assert result.usage.total_tokens == 650
    assert result.projection is not None
    assert result.projection.event_name.value == "Alpine AI Hack 2026"
    assert result.projection.event_locator is not None
    assert result.projection.event_locator.locator == "line:2"
    assert result.projection.participants[0].identity_state is IdentityReviewState.NEEDS_REVIEW
    assert result.contact_projection is not None
    assert [item.value for item in result.contact_projection.routes] == [
        "https://signal-forge.example/",
        "hello@signal-forge.example",
    ]
    assert all(item.identity_assertion == "none" for item in result.contact_projection.routes)
    assert secret not in result.model_dump_json()


@pytest.mark.anyio
async def test_hallucinated_url_is_rejected_after_schema_validation() -> None:
    payload = _extraction_payload()
    links = payload["links"]
    assert isinstance(links, list)
    first = links[0]
    assert isinstance(first, dict)
    first["url"] = "https://attacker.example/hallucinated"

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: _response(payload))
    ) as client:
        result = await OpenAIStructuredPageExtractor(
            api_key=SecretStr("fictional-key"),
            policy=OpenAIStructuredPolicy(),
            now=lambda: NOW,
            client=client,
        ).extract(
            PublicPageStructuredRequest(
                request_id="structured-request:hallucination",
                source_artifact=_artifact(),
                content=PUBLIC_MARKDOWN,
            )
        )

    assert result.status is StructuredExtractionStatus.FAILED
    assert result.projection is None
    assert result.failure is not None
    assert result.failure.safe_code == "structured_output_invalid"


@pytest.mark.anyio
async def test_non_public_artifact_is_blocked_before_network_even_with_private_flag_elsewhere() -> (
    None
):
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        raise AssertionError("non-public sourcing content must not be sent")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await OpenAIStructuredPageExtractor(
            api_key=SecretStr("fictional-key"),
            policy=OpenAIStructuredPolicy(),
            now=lambda: NOW,
            client=client,
        ).extract(
            PublicPageStructuredRequest(
                request_id="structured-request:private",
                source_artifact=_artifact(classification=DataClassification.FOUNDER_PRIVATE),
                content=PUBLIC_MARKDOWN,
            )
        )

    assert called is False
    assert result.status is StructuredExtractionStatus.FAILED
    assert result.failure is not None
    assert result.failure.safe_code == "non_public_source_blocked"


@pytest.mark.anyio
async def test_refusal_is_safe_and_does_not_parse_provider_text() -> None:
    response = httpx.Response(
        200,
        json={
            "id": "resp_refusal",
            "model": "gpt-5.6-luna",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "refusal",
                            "refusal": "private provider explanation must not escape",
                        }
                    ],
                }
            ],
            "usage": {},
        },
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: response)
    ) as client:
        result = await OpenAIStructuredPageExtractor(
            api_key=SecretStr("fictional-key"),
            policy=OpenAIStructuredPolicy(),
            now=lambda: NOW,
            client=client,
        ).extract(
            PublicPageStructuredRequest(
                request_id="structured-request:refusal",
                source_artifact=_artifact(),
                content=PUBLIC_MARKDOWN,
            )
        )

    assert result.failure is not None
    assert result.failure.safe_code == "model_refusal"
    assert "private provider explanation" not in result.model_dump_json()


def test_validated_public_contacts_become_candidate_claim_evidence() -> None:
    extraction = OpenAIPublicPageExtraction.model_validate_json(json.dumps(_extraction_payload()))
    from founderlookup.ingestion.openai_structured import (
        project_validated_openai_extraction,
    )

    _showcase, contacts = project_validated_openai_extraction(
        source_artifact=_artifact(),
        content=PUBLIC_MARKDOWN,
        extraction=extraction,
        model_version="gpt-5.6-luna",
    )
    projected = project_public_contact_evidence(
        source_artifact=_artifact(),
        contacts=contacts,
        subject=SubjectRef(
            kind=EntityKind.OUTBOUND_CANDIDATE,
            subject_id="outbound-candidate:public-contact-test",
        ),
    )

    assert projected is not None
    assert [item.predicate for item in projected.claims] == [
        "public_contact.website",
        "public_contact.public_email",
    ]
    assert all(item.status == "asserted_unverified" for item in projected.claims)
    assert all(item.trust.state.value == "unscored" for item in projected.claims)
    assert all(item.observed_value.state is KnowledgeState.KNOWN for item in projected.observations)
