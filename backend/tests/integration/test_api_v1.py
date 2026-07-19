"""Security and workflow acceptance tests for the minimum versioned REST API."""

import asyncio
import hashlib
from datetime import UTC, datetime
from itertools import count

import httpx
import pytest
from pydantic import SecretStr

from founderlookup.api import create_app
from founderlookup.api.settings import APISettings
from founderlookup.application.models import (
    InvestmentThesisRevision,
    ThesisCriterionMode,
    ThesisDraft,
)
from founderlookup.application.ports import AcceptedApplication, IntakeSubmission
from founderlookup.application.service import FakeVCBrainService
from founderlookup.domain import QueryOperator

NOW = datetime(2026, 7, 18, 15, tzinfo=UTC)
INVESTOR_TOKEN = "investor-test-token-never-log"
PDF = b"%PDF-1.7 fictional private deck"


class StubIntakeService:
    def __init__(self) -> None:
        self.submissions: list[IntakeSubmission] = []

    async def submit(self, submission: IntakeSubmission) -> AcceptedApplication:
        self.submissions.append(submission)
        return AcceptedApplication(
            application_id="application01",
            company_id=submission.canonical_company_id or "company01",
            run_id="ingestionrun01",
            source_artifact_id="artifact01",
            source_artifact_sha256=hashlib.sha256(submission.deck_content).hexdigest(),
            received_at=NOW,
            replayed=len(self.submissions) > 1,
        )


class ConcurrentStubIntakeService:
    """Release two identical submissions together to exercise replay registration."""

    def __init__(self) -> None:
        self._arrivals = 0
        self._both_arrived = asyncio.Event()

    async def submit(self, submission: IntakeSubmission) -> AcceptedApplication:
        self._arrivals += 1
        call_number = self._arrivals
        if self._arrivals == 2:
            self._both_arrived.set()
        await self._both_arrived.wait()
        return AcceptedApplication(
            application_id="concurrent-application01",
            company_id=submission.canonical_company_id or "concurrent-company01",
            run_id="concurrent-ingestionrun01",
            source_artifact_id="concurrent-artifact01",
            source_artifact_sha256=hashlib.sha256(submission.deck_content).hexdigest(),
            received_at=NOW,
            replayed=call_number > 1,
        )


class StubArtifactReader:
    def read(
        self,
        artifact_id: str,
        *,
        principal_id: str,
        expected_sha256: str,
    ) -> bytes:
        assert principal_id == "investor"
        assert artifact_id == "artifact01"
        assert expected_sha256 == hashlib.sha256(PDF).hexdigest()
        return PDF


class BrokenService(FakeVCBrainService):
    def thesis_history(self) -> tuple[InvestmentThesisRevision, ...]:
        raise RuntimeError("private /Users/example/path and provider-secret")


def _settings(
    *,
    intake_rate_limit: int = 10,
    status_rate_limit: int = 60,
) -> APISettings:
    return APISettings(
        _env_file=None,  # type: ignore[call-arg]  # pydantic-settings runtime override
        investor_api_key=SecretStr(INVESTOR_TOKEN),
        founder_status_pepper=SecretStr("founder-status-test-pepper"),
        cors_origins="https://vc.example",
        intake_rate_limit=intake_rate_limit,
        status_rate_limit=status_rate_limit,
        rate_limit_window_seconds=60,
        maximum_deck_bytes=1_000_000,
    )


def _service(*, with_artifacts: bool = False) -> FakeVCBrainService:
    identifiers = count(1)
    return FakeVCBrainService(
        clock=lambda: NOW,
        id_factory=lambda: f"apiid{next(identifiers):04d}",
        capability_pepper=b"founder-status-test-pepper",
        artifact_reader=StubArtifactReader() if with_artifacts else None,
        max_retry_attempts=2,
    )


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {INVESTOR_TOKEN}"}


def _thesis_payload() -> dict[str, object]:
    no_preference: dict[str, object] = {
        "mode": ThesisCriterionMode.NO_PREFERENCE.value,
        "operator": None,
        "values": [],
        "unknown_policy": "preserve_as_unknown",
    }
    return {
        "sector": {
            "mode": "scored_preference",
            "operator": QueryOperator.CONTAINS.value,
            "values": ["AI infrastructure"],
            "unknown_policy": "manual_review",
        },
        "stage": {
            "mode": "hard_constraint",
            "operator": QueryOperator.ANY_OF.value,
            "values": ["pre_seed", "seed"],
            "unknown_policy": "needs_information",
        },
        "geography": no_preference,
        "check_size": {
            "mode": "hard_constraint",
            "operator": QueryOperator.BETWEEN.value,
            "values": [50_000, 250_000],
            "unknown_policy": "manual_review",
        },
        "ownership_target": no_preference,
        "risk_appetite": {
            "mode": "scored_preference",
            "operator": QueryOperator.EQUALS.value,
            "values": ["high"],
            "unknown_policy": "manual_review",
        },
    }


async def _post_application(client: httpx.AsyncClient) -> httpx.Response:
    return await client.post(
        "/api/v1/applications",
        data={"company_name": "Jade Systems"},
        files={"deck": ("../private-deck.pdf", PDF, "application/pdf")},
        headers={"Idempotency-Key": "submission-attempt-01"},
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_investor_auth_is_generic_safe_and_never_echoes_credentials() -> None:
    app = create_app(settings=_settings(), service=_service())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.get("/api/v1/theses")
        invalid = await client.get(
            "/api/v1/theses",
            headers={"Authorization": "Bearer a-secret-invalid-token"},
        )

    for response in (missing, invalid):
        assert response.status_code == 401
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["code"] == "investor_authentication_required"
        assert response.json()["title"] == "Access denied"
        assert response.json()["request_id"] == response.headers["X-Request-ID"]
        assert "token" not in response.text.casefold()
        assert "/Users/" not in response.text


@pytest.mark.anyio
async def test_cors_uses_only_the_explicit_allowlist() -> None:
    app = create_app(settings=_settings(), service=_service())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        allowed = await client.options(
            "/api/v1/theses",
            headers={
                "Origin": "https://vc.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        denied = await client.options(
            "/api/v1/theses",
            headers={
                "Origin": "https://attacker.example",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://vc.example"
    assert "access-control-allow-origin" not in denied.headers


@pytest.mark.anyio
async def test_intake_fails_closed_without_safe_service_and_is_rate_limited() -> None:
    closed_app = create_app(settings=_settings(), service=_service())
    closed_transport = httpx.ASGITransport(app=closed_app)
    async with httpx.AsyncClient(transport=closed_transport, base_url="http://test") as client:
        unavailable = await _post_application(client)
    assert unavailable.status_code == 503
    assert unavailable.json()["code"] == "safe_intake_unavailable"

    intake = StubIntakeService()
    limited_app = create_app(
        settings=_settings(intake_rate_limit=1),
        service=_service(),
        intake_service=intake,
    )
    limited_transport = httpx.ASGITransport(app=limited_app)
    async with httpx.AsyncClient(transport=limited_transport, base_url="http://test") as client:
        accepted = await _post_application(client)
        limited = await _post_application(client)

    assert accepted.status_code == 202
    assert accepted.headers["Location"] == "/api/v1/runs/ingestionrun01"
    assert len(intake.submissions) == 1
    assert limited.status_code == 429
    assert limited.json()["code"] == "rate_limit_exceeded"
    assert int(limited.headers["Retry-After"]) >= 1


@pytest.mark.anyio
async def test_founder_capability_is_scoped_generic_revocable_and_rate_limited() -> None:
    service = _service()
    app = create_app(
        settings=_settings(status_rate_limit=3),
        service=service,
        intake_service=StubIntakeService(),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        accepted = await _post_application(client)
        body = accepted.json()
        capability = body["founder_status_capability"]
        status_response = await client.get(
            "/api/v1/founder-status",
            headers={"X-Founder-Status-Capability": capability},
        )
        invalid = await client.get(
            "/api/v1/founder-status",
            headers={"X-Founder-Status-Capability": "invalid-capability"},
        )
        revoked = await client.delete(
            f"/api/v1/applications/{body['application_id']}/status-capability",
            headers=_auth(),
        )
        after_revoke = await client.get(
            "/api/v1/founder-status",
            headers={"X-Founder-Status-Capability": capability},
        )
        rate_limited = await client.get(
            "/api/v1/founder-status",
            headers={"X-Founder-Status-Capability": capability},
        )

    assert capability not in service.capability_digests
    assert status_response.status_code == 200
    assert set(status_response.json()) == {
        "application_id",
        "received_at",
        "stage",
        "last_updated_at",
        "target_state",
        "information_requests",
        "outcome",
        "next_action",
        "outcome_at",
    }
    assert revoked.status_code == 200
    assert invalid.status_code == after_revoke.status_code == 401
    assert invalid.json()["title"] == after_revoke.json()["title"] == "Access denied"
    assert rate_limited.status_code == 429


@pytest.mark.anyio
async def test_concurrent_idempotent_application_responses_share_valid_capability() -> None:
    service = _service()
    app = create_app(
        settings=_settings(),
        service=service,
        intake_service=ConcurrentStubIntakeService(),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first, second = await asyncio.gather(
            _post_application(client),
            _post_application(client),
        )
        capabilities = [
            first.json()["founder_status_capability"],
            second.json()["founder_status_capability"],
        ]
        status_responses = await asyncio.gather(
            *(
                client.get(
                    "/api/v1/founder-status",
                    headers={"X-Founder-Status-Capability": capability},
                )
                for capability in capabilities
            )
        )
        revoked = await client.delete(
            "/api/v1/applications/concurrent-application01/status-capability",
            headers=_auth(),
        )
        revoked_responses = await asyncio.gather(
            *(
                client.get(
                    "/api/v1/founder-status",
                    headers={"X-Founder-Status-Capability": capability},
                )
                for capability in capabilities
            )
        )

    assert first.status_code == second.status_code == 202
    assert capabilities[0] == capabilities[1]
    assert capabilities[0] not in service.capability_digests
    assert len(service.capability_digests) == 1
    assert [response.status_code for response in status_responses] == [200, 200]
    assert revoked.status_code == 200
    assert [response.status_code for response in revoked_responses] == [401, 401]


@pytest.mark.anyio
async def test_validation_and_unsupported_version_use_safe_problem_documents() -> None:
    app = create_app(settings=_settings(), service=_service())
    transport = httpx.ASGITransport(app=app)
    invalid_payload = _thesis_payload()
    invalid_payload["geography"] = {
        "mode": "no_preference",
        "operator": "equals",
        "values": ["Berlin"],
        "unknown_policy": "manual_review",
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        invalid = await client.post(
            "/api/v1/theses",
            json=invalid_payload,
            headers={**_auth(), "X-Request-ID": "request-validation-01"},
        )
        unsupported = await client.get("/api/v2/opportunities", headers=_auth())

    assert invalid.status_code == 422
    assert invalid.headers["content-type"].startswith("application/problem+json")
    assert invalid.json()["request_id"] == "request-validation-01"
    assert invalid.json()["fields"]
    assert "Berlin" not in invalid.text
    assert unsupported.status_code == 404
    assert unsupported.json()["code"] == "route_not_found"


@pytest.mark.anyio
async def test_unexpected_failure_is_generic_problem_json() -> None:
    app = create_app(settings=_settings(), service=BrokenService())
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/theses", headers=_auth())

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "internal_error"
    assert "provider-secret" not in response.text
    assert "/Users/" not in response.text


@pytest.mark.anyio
async def test_thesis_and_query_commands_accept_json_and_expose_interpretation() -> None:
    service = _service()
    service.accept_application(
        company_name="Jade Systems",
        display_name="jade.pdf",
        media_type="application/pdf",
        deck_content=PDF,
        idempotency_key="query-test-application",
    )
    app = create_app(settings=_settings(), service=service)
    transport = httpx.ASGITransport(app=app)
    query_payload = {
        "plan": {
            "query_plan_id": "queryplan01",
            "query_plan_version_id": "queryplanversion01",
            "raw_query": "Inbound companies in Berlin",
            "planning_mode": "deterministic",
            "planner_version": "fake-planner.v0",
            "state": "validated",
            "criteria": [
                {
                    "criterion_id": "origincriterion01",
                    "field": "origin",
                    "operator": "equals",
                    "operands": ["inbound"],
                    "strength": "hard_constraint",
                    "unknown_policy": "manual_review",
                    "source_text": "Inbound",
                },
                {
                    "criterion_id": "geographycriterion01",
                    "field": "geography",
                    "operator": "equals",
                    "operands": ["Berlin"],
                    "strength": "scored_preference",
                    "unknown_policy": "preserve_as_unknown",
                    "source_text": "in Berlin",
                },
            ],
            "max_results": 20,
            "created_at": NOW.isoformat(),
        }
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        thesis = await client.post("/api/v1/theses", json=_thesis_payload(), headers=_auth())
        query = await client.post("/api/v1/queries", json=query_payload, headers=_auth())
        collection = await client.get(
            "/api/v1/opportunities?origin=inbound&workflow_state=first_pass",
            headers=_auth(),
        )

    assert thesis.status_code == 201
    assert thesis.json()["geography"]["configured_outcome"] == "not_evaluated"
    assert query.status_code == 200
    assert query.json()["ordering"] == "matched_preferences_desc,opportunity_id_asc"
    criteria = query.json()["results"][0]["criteria"]
    assert [item["outcome"] for item in criteria] == ["match", "unknown"]
    assert criteria[1]["knowledge_state"] == "unknown"
    assert "rationale" in criteria[1]
    assert collection.status_code == 200
    assert collection.json()["applied_filters"] == [
        "origin=inbound",
        "screening_status=first_pass",
    ]
    assert collection.json()["ordering"] == "updated_at_desc,opportunity_id_asc"


@pytest.mark.anyio
async def test_activation_preserves_edited_draft_without_recording_contact() -> None:
    service = _service()
    service.create_thesis(
        ThesisDraft.model_validate_json(httpx.Response(200, json=_thesis_payload()).content),
        actor_id="investor",
    )
    candidate = service.seed_outbound_candidate(
        company_name="Ink Robotics",
        source_artifact_ids=("source-artifact-01",),
    )
    app = create_app(settings=_settings(), service=service)
    transport = httpx.ASGITransport(app=app)
    draft = "Custom evidence-reviewed invitation; a human decides whether to send it."
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        preliminary = await client.post(
            f"/api/v1/outbound-candidates/{candidate.outbound_candidate_id}/preliminary-assessment",
            headers=_auth(),
        )
        activated = await client.post(
            f"/api/v1/outbound-candidates/{candidate.outbound_candidate_id}/activate",
            json={"outreach_draft": draft},
            headers=_auth(),
        )

    assert preliminary.status_code == 202
    assert activated.status_code == 200
    assert activated.json()["status"] == "activated"
    assert activated.json()["status"] != "contacted"
    assert activated.json()["outreach_draft"] == draft


@pytest.mark.anyio
async def test_activated_outbound_application_converges_on_common_screening_api() -> None:
    service = _service()
    service.create_thesis(
        ThesisDraft.model_validate_json(httpx.Response(200, json=_thesis_payload()).content),
        actor_id="investor",
    )
    candidate = service.seed_outbound_candidate(
        company_name="Ink Robotics",
        founder_id="founder:ink",
        source_artifact_ids=("source-artifact-01",),
    )
    preliminary = service.start_preliminary_assessment(candidate.outbound_candidate_id)
    service.activate_candidate(candidate.outbound_candidate_id)
    intake = StubIntakeService()
    app = create_app(settings=_settings(), service=service, intake_service=intake)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        accepted = await client.post(
            "/api/v1/applications",
            data={
                "company_name": "Ink Robotics",
                "outbound_candidate_id": candidate.outbound_candidate_id,
            },
            files={"deck": ("ink-deck.pdf", PDF, "application/pdf")},
            headers={"Idempotency-Key": "outbound-application-attempt-01"},
        )
        replay = await client.post(
            "/api/v1/applications",
            data={
                "company_name": "Ink Robotics",
                "outbound_candidate_id": candidate.outbound_candidate_id,
            },
            files={"deck": ("ink-deck.pdf", PDF, "application/pdf")},
            headers={"Idempotency-Key": "outbound-application-attempt-01"},
        )
        candidates = await client.get("/api/v1/outbound-candidates", headers=_auth())
        opportunities = await client.get("/api/v1/opportunities", headers=_auth())
        opportunity_id = opportunities.json()["items"][0]["opportunity_id"]
        before_screening = await client.get(
            f"/api/v1/opportunities/{opportunity_id}", headers=_auth()
        )
        screening = await client.post(
            f"/api/v1/opportunities/{opportunity_id}/screen", headers=_auth()
        )
        screened = await client.get(f"/api/v1/opportunities/{opportunity_id}", headers=_auth())

    assert accepted.status_code == 202
    assert replay.status_code == 202
    assert replay.json()["application_id"] == accepted.json()["application_id"]
    assert replay.json()["replayed"] is True
    assert len(intake.submissions) == 2
    assert all(
        submission.canonical_company_id == candidate.company_id for submission in intake.submissions
    )
    candidate_body = candidates.json()["items"][0]
    assert candidate_body["status"] == "applied"
    assert candidate_body["application_id"] == accepted.json()["application_id"]
    assert candidate_body["preliminary_assessment"]["run_id"] == preliminary.run_id
    opportunity_body = opportunities.json()["items"]
    assert len(opportunity_body) == 1
    assert opportunity_body[0]["origin"] == "outbound"
    assert before_screening.json()["outbound_candidate_id"] == candidate.outbound_candidate_id
    assert before_screening.json()["company_id"] == candidate.company_id
    assert before_screening.json()["latest_assessment"] is None
    assert before_screening.json()["human_decisions"] == []
    assert before_screening.json()["related_run_ids"] == [
        preliminary.run_id,
        accepted.json()["run_id"],
    ]
    assert screening.status_code == 202
    identity = screened.json()["latest_assessment"]["identity"]
    assert identity["mode"] == "full"
    assert identity["origin"] == "outbound"
    assert identity["application_id"] == accepted.json()["application_id"]
    assert identity["outbound_candidate_id"] == candidate.outbound_candidate_id
    assert identity["company_id"] == candidate.company_id
    assert screened.json()["human_decisions"] == []


@pytest.mark.anyio
async def test_outbound_application_gate_is_generic_and_precedes_intake() -> None:
    service = _service()
    service.create_thesis(
        ThesisDraft.model_validate_json(httpx.Response(200, json=_thesis_payload()).content),
        actor_id="investor",
    )
    candidate = service.seed_outbound_candidate(
        company_name="Not Activated",
        source_artifact_ids=("source-artifact-not-activated",),
    )
    service.start_preliminary_assessment(candidate.outbound_candidate_id)
    intake = StubIntakeService()
    app = create_app(settings=_settings(), service=service, intake_service=intake)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.post(
            "/api/v1/applications",
            data={
                "company_name": "Missing",
                "outbound_candidate_id": "candidate:missing",
            },
            files={"deck": ("deck.pdf", PDF, "application/pdf")},
            headers={"Idempotency-Key": "missing-candidate-attempt"},
        )
        not_activated = await client.post(
            "/api/v1/applications",
            data={
                "company_name": "Not Activated",
                "outbound_candidate_id": candidate.outbound_candidate_id,
            },
            files={"deck": ("deck.pdf", PDF, "application/pdf")},
            headers={"Idempotency-Key": "not-activated-attempt"},
        )

    assert missing.status_code == not_activated.status_code == 409
    assert missing.json()["code"] == not_activated.json()["code"]
    assert missing.json()["title"] == not_activated.json()["title"]
    assert missing.json()["code"] == "outbound_application_link_unavailable"
    assert "candidate:missing" not in missing.text
    assert candidate.outbound_candidate_id not in not_activated.text
    assert intake.submissions == []


@pytest.mark.anyio
async def test_public_intake_cannot_supply_a_canonical_company_id() -> None:
    service = _service()
    intake = StubIntakeService()
    app = create_app(settings=_settings(), service=service, intake_service=intake)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/applications",
            data={
                "company_name": "Direct Inbound",
                "canonical_company_id": "company:attacker-controlled",
            },
            files={"deck": ("deck.pdf", PDF, "application/pdf")},
            headers={"Idempotency-Key": "direct-inbound-attempt"},
        )

    assert response.status_code == 202
    assert response.json()["company_id"] == "company01"
    assert intake.submissions[0].canonical_company_id is None
    opportunity = service.get_opportunity(service.list_opportunities().items[0].opportunity_id)
    assert opportunity.origin.value == "inbound"
    assert opportunity.outbound_candidate_id is None


@pytest.mark.anyio
async def test_screen_decision_and_retry_routes_preserve_immutable_history() -> None:
    service = _service()
    service.create_thesis(
        # JSON round-trip gives the same strict validation path used by FastAPI.
        ThesisDraft.model_validate_json(httpx.Response(200, json=_thesis_payload()).content),
        actor_id="investor",
    )
    service.accept_application(
        company_name="Jade Systems",
        display_name="jade.pdf",
        media_type="application/pdf",
        deck_content=PDF,
        idempotency_key="internal-test-application",
    )
    opportunity_id = service.list_opportunities().items[0].opportunity_id
    failed = service.seed_failed_run(accepted_output_ids=("accepted-evidence-01",))
    app = create_app(settings=_settings(), service=service)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        screening = await client.post(
            f"/api/v1/opportunities/{opportunity_id}/screen",
            headers=_auth(),
        )
        detail = await client.get(
            f"/api/v1/opportunities/{opportunity_id}?expand=claims,evidence",
            headers=_auth(),
        )
        assessment = detail.json()["latest_assessment"]
        decision_payload = {
            "assessment_id": assessment["assessment_id"],
            "memo_id": assessment["memo"]["memo_id"],
            "recommendation_id": assessment["recommendation"]["recommendation_id"],
            "disposition": "hold",
            "rationale": "Resolve the material founder identity gap.",
        }
        first_decision = await client.post(
            f"/api/v1/opportunities/{opportunity_id}/decisions",
            json=decision_payload,
            headers=_auth(),
        )
        second_decision = await client.post(
            f"/api/v1/opportunities/{opportunity_id}/decisions",
            json={**decision_payload, "disposition": "request_more_information"},
            headers=_auth(),
        )
        retry = await client.post(f"/api/v1/runs/{failed.run_id}/retry", headers=_auth())
        repeated_retry = await client.post(
            f"/api/v1/runs/{failed.run_id}/retry",
            headers=_auth(),
        )
        final_detail = await client.get(
            f"/api/v1/opportunities/{opportunity_id}",
            headers=_auth(),
        )

    assert screening.status_code == 202
    assert first_decision.status_code == second_decision.status_code == 201
    assert first_decision.json()["decision_id"] != second_decision.json()["decision_id"]
    assert len(final_detail.json()["human_decisions"]) == 2
    assert retry.status_code == repeated_retry.status_code == 202
    assert retry.json()["run_id"] == repeated_retry.json()["run_id"]
    accepted_output_ids = retry.json()["run"]["accepted_output_ids"]
    assert accepted_output_ids[0] == "accepted-evidence-01"
    assert len(accepted_output_ids) == 2
    assert retry.json()["run"]["stages"][1]["accepted_output_ids"] == [accepted_output_ids[1]]
    assert retry.json()["run"]["retry_of_run_id"] == failed.run_id


@pytest.mark.anyio
async def test_private_artifact_requires_investor_and_never_exposes_local_path() -> None:
    app = create_app(
        settings=_settings(),
        service=_service(with_artifacts=True),
        intake_service=StubIntakeService(),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _post_application(client)
        anonymous = await client.get("/api/v1/artifacts/artifact01")
        missing_anonymous = await client.get("/api/v1/artifacts/not-real")
        authorized = await client.get("/api/v1/artifacts/artifact01", headers=_auth())

    assert anonymous.status_code == missing_anonymous.status_code == 401
    assert anonymous.json()["title"] == missing_anonymous.json()["title"] == "Access denied"
    assert authorized.status_code == 200
    assert authorized.content == PDF
    assert authorized.headers["X-Content-Type-Options"] == "nosniff"
    assert "../" not in authorized.headers["Content-Disposition"]


@pytest.mark.anyio
async def test_openapi_contains_the_complete_minimum_route_surface() -> None:
    app = create_app(settings=_settings(), service=_service())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    expected_paths = {
        "/api/v1/applications",
        "/api/v1/founder-status",
        "/api/v1/applications/{application_id}/status-capability",
        "/api/v1/theses",
        "/api/v1/theses/active",
        "/api/v1/sourcing-runs",
        "/api/v1/query-plans",
        "/api/v1/outbound-candidates",
        "/api/v1/outbound-candidates/{candidate_id}/preliminary-assessment",
        "/api/v1/outbound-candidates/{candidate_id}/activate",
        "/api/v1/outbound-candidates/{candidate_id}/outreach",
        "/api/v1/queries",
        "/api/v1/opportunities",
        "/api/v1/opportunities/{opportunity_id}",
        "/api/v1/opportunities/{opportunity_id}/screen",
        "/api/v1/opportunities/{opportunity_id}/decisions",
        "/api/v1/runs/{run_id}",
        "/api/v1/runs/{run_id}/retry",
        "/api/v1/artifacts/{artifact_id}",
    }
    assert expected_paths.issubset(schema["paths"])
    assert "InvestorBearer" in schema["components"]["securitySchemes"]
    intake_body = schema["paths"]["/api/v1/applications"]["post"]["requestBody"]
    assert "multipart/form-data" in intake_body["content"]


@pytest.mark.anyio
async def test_query_planning_fails_closed_when_factory_has_no_planner() -> None:
    app = create_app(settings=_settings(), service=_service())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/query-plans",
            json={"raw_query": "technical founder in Berlin"},
            headers=_auth(),
        )

    assert response.status_code == 503
    assert response.json()["code"] == "query_planner_unavailable"
