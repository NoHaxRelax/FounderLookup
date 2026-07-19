"""API-to-graph-to-canonical-Assessment integration on synthetic inputs."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel, SecretStr

from founderlookup.api.app import create_app
from founderlookup.api.settings import APISettings, RuntimeEnvironment
from founderlookup.application.live_screening import LiveScreeningCoordinator
from founderlookup.application.ports import (
    AcceptedApplication,
    ApplicationFounderProfile,
    ApplicationSubmittedMetadata,
)
from founderlookup.application.service import FakeVCBrainService
from founderlookup.demo.bootstrap import seed_local_demo
from founderlookup.domain.assessment import REQUIRED_MEMO_SECTIONS
from founderlookup.domain.lifecycles import OpportunityOrigin
from founderlookup.infrastructure.live_screening import SQLiteLiveScreeningStore
from founderlookup.infrastructure.persistence import RecordCategory, SQLiteMemory
from founderlookup.screening.inbound_graph import InboundGraphLimits
from founderlookup.screening.inbound_runtime import RuntimeInboundIntelligence

NOW = datetime(2026, 7, 19, 13, tzinfo=UTC)
TOKEN = "synthetic-live-screening-investor-token"


class _UnknownReasoner:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.calls: list[str] = []
        self.saw_metadata = False

    async def extract[SchemaT: BaseModel](
        self,
        *,
        schema: type[SchemaT],
        instructions: str,
        content: str,
    ) -> SchemaT:
        self.calls.append(schema.__name__)
        self.saw_metadata = self.saw_metadata or (
            "submitted_one_line_pitch" in content
            and "https://signal-forge.example" in content
            and "founder_provided_unverified_not_evidence" in content
        )
        if self._fail:
            raise RuntimeError("private provider detail must be masked")
        if schema.__name__ == "_MarketProposal":
            payload: object = {
                "rating": "unknown",
                "trend": "unknown",
                "confidence": None,
                "supporting_claim_ids": [],
                "counter_claim_ids": [],
                "open_questions": ["Which source independently supports market direction?"],
            }
        elif schema.__name__ == "_IdeaProposal":
            payload = {
                "rating": "unknown",
                "trend": "unknown",
                "confidence": None,
                "supporting_claim_ids": [],
                "counter_claim_ids": [],
                "open_questions": ["Which evidence establishes novelty and quality?"],
            }
        elif schema.__name__ == "_FounderProposal":
            payload = {
                "rating": "unknown",
                "trend": "unknown",
                "confidence": None,
                "supporting_claim_ids": [],
                "counter_claim_ids": [],
                "open_questions": ["Which evidence demonstrates shipped builder substance?"],
            }
        elif schema.__name__ == "_AdversarialProposal":
            payload = {
                "contradiction_ids": [],
                "confidence": None,
                "open_questions": ["Which applicant assertions need corroboration?"],
            }
        else:
            payload = {
                "sections": [
                    {
                        "kind": kind.value,
                        "content": None,
                        "material_claim_ids": [],
                        "gap_reason": "Only unverified applicant assertions are available.",
                        "requested_evidence": "Acquire one independent public source.",
                    }
                    for kind in sorted(REQUIRED_MEMO_SECTIONS, key=lambda item: item.value)
                ],
                "recommendation": {
                    "action": "needs_information",
                    "reasons": [
                        {
                            "summary": "Applicant assertions need independent corroboration.",
                            "claim_ids": [],
                        }
                    ],
                    "next_actions": ["A human reviewer should request corroboration."],
                },
                "contradiction_ids": [],
                "confidence": None,
                "open_questions": ["Which source should be acquired first?"],
            }
        return schema.model_validate(payload)


def _service() -> tuple[FakeVCBrainService, str]:
    identifiers = count(1)
    service = FakeVCBrainService(
        clock=lambda: NOW,
        id_factory=lambda: f"liveid{next(identifiers):04d}",
        capability_pepper=b"synthetic-live-screening-pepper",
    )
    seed_local_demo(service)
    before = {item.opportunity_id for item in service.list_opportunities().items}
    metadata = ApplicationSubmittedMetadata(
        website="https://signal-forge.example",
        one_line_pitch="A fictional applicant assertion with no outside corroboration.",
        founders=(
            ApplicationFounderProfile(
                full_name="Avery Example",
                github_url="https://github.com/avery-example",
            ),
        ),
    )
    service.register_application(
        AcceptedApplication(
            application_id="application:live-synthetic",
            company_id="company:live-synthetic",
            run_id="run:intake-live-synthetic",
            source_artifact_id="artifact:live-synthetic-deck",
            source_artifact_sha256="a" * 64,
            received_at=NOW,
            company_name="Signal Forge",
            metadata=metadata,
        ),
        display_name="synthetic-deck.pdf",
        media_type="application/pdf",
    )
    inbound = {
        item.opportunity_id
        for item in service.list_opportunities(origin=OpportunityOrigin.INBOUND).items
    }
    (opportunity_id,) = inbound - before
    return service, opportunity_id


def _settings() -> APISettings:
    return APISettings(
        _env_file=None,  # type: ignore[call-arg]
        environment=RuntimeEnvironment.TEST,
        investor_api_key=SecretStr(TOKEN),
        founder_status_pepper=SecretStr("synthetic-live-screening-pepper"),
    )


def _coordinator(
    service: FakeVCBrainService,
    reasoner: _UnknownReasoner,
    memory: SQLiteMemory,
) -> LiveScreeningCoordinator:
    intelligence = RuntimeInboundIntelligence(
        reasoner,
        clock=lambda: NOW,
        max_input_bytes=100_000,
        limits=InboundGraphLimits(
            max_model_calls=5,
            stage_timeout_seconds=1,
            total_timeout_seconds=3,
        ),
    )
    return LiveScreeningCoordinator(
        service=service,
        intelligence=intelligence,
        on_accepted=SQLiteLiveScreeningStore(memory).persist,
    )


@pytest.mark.anyio
async def test_screen_api_backgrounds_graph_accepts_canonical_outputs_and_persists(
    tmp_path: Path,
) -> None:
    service, opportunity_id = _service()
    reasoner = _UnknownReasoner()
    memory = SQLiteMemory((tmp_path / "memory.sqlite3").resolve())
    coordinator = _coordinator(service, reasoner, memory)
    app = create_app(
        settings=_settings(),
        service=service,
        live_screening_coordinator=coordinator,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        queued = await client.post(
            f"/api/v1/opportunities/{opportunity_id}/screen",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        run_id = queued.json()["run_id"]
        polled = await client.get(
            f"/api/v1/runs/{run_id}",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        detail = await client.get(
            f"/api/v1/opportunities/{opportunity_id}",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

    assert queued.status_code == 202
    assert polled.json()["status"] == "succeeded"
    assert reasoner.saw_metadata is True
    assert set(reasoner.calls) == {
        "_MarketProposal",
        "_IdeaProposal",
        "_FounderProposal",
        "_AdversarialProposal",
        "_MemoProposal",
    }
    accepted = detail.json()["latest_assessment"]
    assert accepted["run_id"] == run_id
    assert accepted["axes"]["founder"]["rating"] == "unknown"
    assert accepted["axes"]["market"]["rating"] == "unknown"
    assert accepted["axes"]["idea_vs_market"]["rating"] == "unknown"
    assert len(accepted["memo"]["sections"]) == 5
    assert accepted["recommendation"]["action"] == "needs_information"
    assert detail.json()["human_decisions"] == []
    assert memory.latest(RecordCategory.ASSESSMENT, accepted["assessment_id"])
    audit = memory.list_records(
        RecordCategory.INBOUND_ANALYSIS_AUDIT,
        subject_id=opportunity_id,
    )
    assert len(audit) == 1
    assert audit[0].payload["contains_human_decision"] is False


@pytest.mark.anyio
async def test_provider_failure_accepts_deterministic_fallback_with_safe_partial_run(
    tmp_path: Path,
) -> None:
    service, opportunity_id = _service()
    reasoner = _UnknownReasoner(fail=True)
    memory = SQLiteMemory((tmp_path / "memory.sqlite3").resolve())
    app = create_app(
        settings=_settings(),
        service=service,
        live_screening_coordinator=_coordinator(service, reasoner, memory),
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        queued = await client.post(
            f"/api/v1/opportunities/{opportunity_id}/screen",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        run = await client.get(
            f"/api/v1/runs/{queued.json()['run_id']}",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        detail = await client.get(
            f"/api/v1/opportunities/{opportunity_id}",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

    assert run.json()["status"] == "partially_succeeded"
    assert len(run.json()["failures"]) == 5
    assert all(
        item["safe_code"] == "invalid_or_unavailable_live_output" for item in run.json()["failures"]
    )
    assert "private provider detail" not in run.text
    assert detail.json()["latest_assessment"]["recommendation"]["action"] == ("needs_information")
    assert detail.json()["human_decisions"] == []
