"""Runtime composition proof for the optional OpenAI structured sourcing adapter."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from founderlookup.api.settings import APISettings
from founderlookup.infrastructure.persistence import RecordCategory
from founderlookup.runtime import create_runtime_app

NOW = datetime(2026, 7, 19, 17, tzinfo=UTC)
PUBLIC_SHOWCASE = """# Signal Forge
Event: Alpine AI Hack 2026
Project: Signal Forge
Participant: Ada Demo
Contact: hello@signal-forge.example
"""


def _thesis_payload() -> dict[str, object]:
    no_preference: dict[str, object] = {
        "mode": "no_preference",
        "operator": None,
        "values": [],
        "unknown_policy": "preserve_as_unknown",
    }
    return {
        "sector": no_preference,
        "stage": no_preference,
        "geography": no_preference,
        "check_size": no_preference,
        "ownership_target": no_preference,
        "risk_appetite": no_preference,
    }


def _structured_payload() -> dict[str, object]:
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
                "public_profile_url": None,
                "evidence": {"line_number": 4, "excerpt": "Participant: Ada Demo"},
            }
        ],
        "participant_gap_reason": None,
        "links": [],
        "public_deck_gap_reason": "No public pitch deck link appears in the source.",
        "public_contacts": [
            {
                "kind": "public_email",
                "label": "Contact",
                "value": "hello@signal-forge.example",
                "evidence": {
                    "line_number": 5,
                    "excerpt": "Contact: hello@signal-forge.example",
                },
            }
        ],
        "public_contact_gap_reason": None,
        "ambiguous_or_unsupported": [],
        "identity_verification": "not_performed",
    }


@pytest.mark.anyio
async def test_runtime_routes_acquired_showcase_through_openai_structured_outputs(
    tmp_path: Path,
) -> None:
    tavily_key = "fictional-runtime-tavily-key"
    openai_key = "fictional-runtime-openai-key"
    showcase_url = "https://showcase.example/signal-forge"
    captured_openai: dict[str, object] = {}

    def tavily_handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.headers["authorization"] == f"Bearer {tavily_key}"
        if request.url.path == "/search":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Signal Forge public showcase",
                            "url": showcase_url,
                            "content": "Provider discovery snippet",
                            "score": 0.99,
                        }
                    ]
                },
            )
        if request.url.path == "/extract":
            assert payload["urls"] == [showcase_url]
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": showcase_url,
                            "raw_content": PUBLIC_SHOWCASE,
                        }
                    ]
                },
            )
        raise AssertionError("unexpected Tavily endpoint")

    def openai_handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.openai.com/v1/responses"
        assert request.headers["authorization"] == f"Bearer {openai_key}"
        captured_openai.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "resp_runtime_structured",
                "model": "gpt-5.6-luna",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(_structured_payload()),
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 200, "output_tokens": 100, "total_tokens": 300},
            },
        )

    settings = APISettings(
        _env_file=None,  # type: ignore[call-arg]  # pydantic-settings runtime override
        data_dir=tmp_path,
        investor_api_key=SecretStr("runtime-investor-token"),
        founder_status_pepper=SecretStr("runtime-founder-status-pepper"),
        cors_origins="https://vc.example",
        tavily_api_key=SecretStr(tavily_key),
        tavily_enabled=True,
        tavily_allowed_domains="showcase.example",
        openai_api_key=SecretStr(openai_key),
        openai_structured_enabled=True,
        openai_model="gpt-5.6-luna",
    )
    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(tavily_handler)) as tavily_client,
        httpx.AsyncClient(transport=httpx.MockTransport(openai_handler)) as openai_client,
    ):
        application = create_runtime_app(
            settings=settings,
            tavily_client=tavily_client,
            openai_client=openai_client,
            clock=lambda: NOW,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url="https://test.example",
            headers={"Authorization": "Bearer runtime-investor-token"},
        ) as client:
            thesis = await client.post("/api/v1/theses", json=_thesis_payload())
            accepted = await client.post(
                "/api/v1/sourcing-runs",
                json={
                    "query": "Signal Forge Alpine AI Hack",
                    "source_categories": ["hackathon"],
                    "allowed_domains": ["showcase.example"],
                    "max_results": 1,
                    "max_pages": 1,
                    "max_bytes": 50_000,
                    "timeout_seconds": 10,
                },
            )
            run = await client.get(accepted.headers["Location"])
            candidates = await client.get("/api/v1/outbound-candidates")

    assert thesis.status_code == 201
    assert accepted.status_code == 202
    assert run.status_code == 200
    assert run.json()["status"] == "succeeded"
    assert candidates.json()["items"][0]["company_name"] == "Showcase project: Signal Forge"
    assert application.state.openai_structured_enabled is True
    assert captured_openai["model"] == "gpt-5.6-luna"
    assert captured_openai["store"] is False
    text = captured_openai["text"]
    assert isinstance(text, dict)
    output_format = text["format"]
    assert isinstance(output_format, dict)
    assert output_format["type"] == "json_schema"
    assert output_format["strict"] is True

    telemetry = application.state.sqlite_memory.list_records(
        RecordCategory.COLLECTION_TELEMETRY,
        subject_id=run.json()["run_id"],
    )
    structured = next(
        item.payload
        for item in telemetry
        if item.payload.get("operation") == "structured_page_extraction"
    )
    assert structured["status"] == "succeeded"
    assert structured["model_version"]["value"] == "gpt-5.6-luna"
    assert tavily_key not in run.text
    assert openai_key not in run.text
