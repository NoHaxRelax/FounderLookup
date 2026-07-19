"""Provider-contract tests for the screening OpenAI reasoner."""

from __future__ import annotations

import json

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict

from founderlookup.screening.openai_client import OpenAIReasoner


class _FounderSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_name: str
    evidence_excerpt: str


def _parsed_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "resp_reasoner_unit",
            "object": "response",
            "created_at": 1_753_000_000,
            "status": "completed",
            "completed_at": 1_753_000_001,
            "error": None,
            "incomplete_details": None,
            "instructions": "Extract only explicit founder signals.",
            "max_output_tokens": None,
            "model": "gpt-5.6-luna",
            "output": [
                {
                    "id": "msg_reasoner_unit",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "company_name": "Signal Forge",
                                    "evidence_excerpt": "Signal Forge builds public tooling.",
                                }
                            ),
                            "annotations": [],
                            "logprobs": [],
                        }
                    ],
                }
            ],
            "parallel_tool_calls": True,
            "previous_response_id": None,
            "reasoning": {"effort": "low", "summary": None},
            "store": False,
            "temperature": None,
            "text": {"format": {"type": "text"}},
            "tool_choice": "auto",
            "tools": [],
            "top_p": None,
            "truncation": "disabled",
            "usage": {
                "input_tokens": 20,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": 10,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 30,
            },
            "metadata": {},
        },
    )


@pytest.mark.anyio
async def test_extract_uses_strict_structured_outputs_without_provider_storage() -> None:
    captured: dict[str, object] = {}
    api_key = "fictional-openai-reasoner-key"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.openai.com/v1/responses"
        assert request.headers["authorization"] == f"Bearer {api_key}"
        captured.update(json.loads(request.content))
        return _parsed_response()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        provider = AsyncOpenAI(api_key=api_key, http_client=http_client)
        reasoner = OpenAIReasoner(provider)
        result = await reasoner.extract(
            schema=_FounderSignal,
            instructions="Extract only explicit founder signals.",
            content="Signal Forge builds public tooling.",
        )

    assert result == _FounderSignal(
        company_name="Signal Forge",
        evidence_excerpt="Signal Forge builds public tooling.",
    )
    assert captured["model"] == "gpt-5.6-luna"
    assert captured["store"] is False
    assert captured["reasoning"] == {"effort": "low"}
    text = captured["text"]
    assert isinstance(text, dict)
    output_format = text["format"]
    assert isinstance(output_format, dict)
    assert output_format["type"] == "json_schema"
    assert output_format["strict"] is True
    schema = output_format["schema"]
    assert isinstance(schema, dict)
    assert schema["additionalProperties"] is False

