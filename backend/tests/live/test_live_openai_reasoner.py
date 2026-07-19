"""Opt-in GPT-5.6 Luna Structured Outputs acceptance for the screening reasoner.

Run explicitly with ``FOUNDERLOOKUP_RUN_LIVE_TESTS=1 uv run pytest
tests/live/test_live_openai_reasoner.py``. The prompt is synthetic and public; no founder,
investor, customer, or other private material is sent by this test.
"""

from __future__ import annotations

import os

import pytest
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict

from founderlookup.api.settings import APISettings
from founderlookup.screening.openai_client import DEFAULT_MODEL, OpenAIReasoner

pytestmark = pytest.mark.live


class _FictionalPublicSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_name: str
    public_contact: str
    founder_identity_disclosed: bool


@pytest.mark.skipif(
    os.getenv("FOUNDERLOOKUP_RUN_LIVE_TESTS") != "1",
    reason="set FOUNDERLOOKUP_RUN_LIVE_TESTS=1 to call the real OpenAI API",
)
@pytest.mark.anyio
async def test_real_gpt_5_6_luna_parses_fictional_public_signals() -> None:
    configured_key = APISettings().openai_api_key
    if configured_key is None or not configured_key.get_secret_value().strip():
        pytest.skip("OPENAI_API_KEY is not configured")

    async with AsyncOpenAI(api_key=configured_key.get_secret_value()) as provider:
        reasoner = OpenAIReasoner(provider, model=DEFAULT_MODEL, effort="low")
        result = await reasoner.extract(
            schema=_FictionalPublicSignal,
            instructions=(
                "Extract only values explicitly stated in the fictional public source. "
                "Do not infer a founder identity."
            ),
            content=(
                "FICTIONAL PUBLIC SHOWCASE\n"
                "Project: Signal Forge\n"
                "Public contact: hello@signal-forge.example\n"
                "Founder identity: not disclosed\n"
            ),
        )

    assert result.project_name == "Signal Forge"
    assert result.public_contact == "hello@signal-forge.example"
    assert result.founder_identity_disclosed is False
    assert configured_key.get_secret_value() not in repr(result)

