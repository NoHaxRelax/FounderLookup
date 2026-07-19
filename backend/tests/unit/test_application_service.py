"""Use-case tests for deterministic screening, workflow, and retry services."""

from datetime import UTC, datetime
from itertools import count
from pathlib import Path

import pytest

from founderlookup.application.models import (
    CriterionMatchOutcome,
    OutreachMethod,
    ThesisCriterion,
    ThesisCriterionMode,
    ThesisDraft,
)
from founderlookup.application.ports import AcceptedApplication
from founderlookup.application.service import (
    ApplicationDeckTooLargeError,
    ApplicationExtractionOutcome,
    CapabilityDeniedError,
    ConflictError,
    FakeVCBrainService,
    InvalidApplicationDeckError,
    RetryLimitError,
)
from founderlookup.domain import (
    CriterionStrength,
    DecisionReadinessStatus,
    HumanDecisionDisposition,
    KnowledgeState,
    OpportunityQueryPlan,
    OutboundCandidateStatus,
    PipelineRunKind,
    PipelineRunStatus,
    QueryCriterion,
    QueryCriterionField,
    QueryOperator,
    QueryPlanningMode,
    QueryPlanState,
    RecommendationAction,
    RuleOutcome,
    RuleOverride,
    UnknownValuePolicy,
)
from founderlookup.infrastructure import SQLiteMemory, SQLiteRuleOverrideLedger
from founderlookup.screening import RuleOverrideLedgerPort

NOW = datetime(2026, 7, 18, 14, tzinfo=UTC)


def _service(
    *,
    max_retry_attempts: int = 3,
    max_fake_pdf_bytes: int = 10 * 1024 * 1024,
    rule_override_ledger: RuleOverrideLedgerPort | None = None,
) -> FakeVCBrainService:
    identifiers = count(1)
    return FakeVCBrainService(
        clock=lambda: NOW,
        id_factory=lambda: f"id{next(identifiers):04d}",
        capability_pepper=b"test-only-pepper" * 2,
        max_retry_attempts=max_retry_attempts,
        max_fake_pdf_bytes=max_fake_pdf_bytes,
        rule_override_ledger=rule_override_ledger,
    )


def _criterion(
    mode: ThesisCriterionMode,
    *,
    operator: QueryOperator | None = None,
    values: tuple[str | int | float | bool, ...] = (),
) -> ThesisCriterion:
    return ThesisCriterion(
        mode=mode,
        operator=operator,
        values=values,
        unknown_policy=UnknownValuePolicy.MANUAL_REVIEW,
    )


def _draft() -> ThesisDraft:
    no_preference = _criterion(ThesisCriterionMode.NO_PREFERENCE)
    return ThesisDraft(
        sector=_criterion(
            ThesisCriterionMode.SCORED_PREFERENCE,
            operator=QueryOperator.CONTAINS,
            values=("AI infrastructure",),
        ),
        stage=_criterion(
            ThesisCriterionMode.HARD_CONSTRAINT,
            operator=QueryOperator.ANY_OF,
            values=("pre_seed", "seed"),
        ),
        geography=no_preference,
        check_size=_criterion(
            ThesisCriterionMode.HARD_CONSTRAINT,
            operator=QueryOperator.BETWEEN,
            values=(50_000, 250_000),
        ),
        ownership_target=no_preference,
        risk_appetite=_criterion(
            ThesisCriterionMode.SCORED_PREFERENCE,
            operator=QueryOperator.EQUALS,
            values=("high",),
        ),
    )


def _accept_application(service: FakeVCBrainService) -> str:
    service.accept_application(
        company_name="Jade Systems",
        display_name="../../jade-deck.pdf",
        media_type="application/pdf",
        deck_content=b"%PDF-1.7 fictional",
        idempotency_key="application-attempt-01",
    )
    return service.list_opportunities().items[0].opportunity_id


def _register_queued_application(service: FakeVCBrainService) -> AcceptedApplication:
    accepted = AcceptedApplication(
        application_id="application-ingestion-01",
        company_id="company-ingestion-01",
        run_id="run-ingestion-01",
        source_artifact_id="source-artifact-ingestion-01",
        source_artifact_sha256="a" * 64,
        received_at=NOW,
    )
    service.register_application(
        accepted,
        display_name="deck.pdf",
        media_type="application/pdf",
    )
    return accepted


def test_thesis_revisions_and_fake_assessments_preserve_no_preference_semantics() -> None:
    service = _service()
    first = service.create_thesis(_draft(), actor_id="investor01")
    second = service.create_thesis(_draft(), actor_id="investor01")
    opportunity_id = _accept_application(service)

    accepted = service.start_screening(opportunity_id)
    detail = service.get_opportunity(opportunity_id)

    assert first.thesis_id == second.thesis_id
    assert first.thesis_version_id != second.thesis_version_id
    assert second.geography.configured_outcome is RuleOutcome.NOT_EVALUATED
    assert second.geography.values == ()
    assert accepted.run.status is PipelineRunStatus.SUCCEEDED
    assert detail.latest_assessment is not None
    results = {
        result.inputs[0].field: result.outcome
        for result in detail.latest_assessment.deterministic_results
    }
    assert results["geography"] is RuleOutcome.NOT_EVALUATED
    assert results["ownership_target"] is RuleOutcome.NOT_EVALUATED
    assert results["sector"] is RuleOutcome.INDETERMINATE
    assert detail.latest_assessment.decision_readiness is not None
    assert detail.latest_assessment.memo is not None
    assert detail.latest_assessment.recommendation is not None


def test_preliminary_activation_and_outreach_remain_explicit_human_steps() -> None:
    service = _service()
    service.create_thesis(_draft(), actor_id="investor01")
    candidate = service.seed_outbound_candidate(
        company_name="Ink Robotics",
        source_artifact_ids=("source-artifact-01",),
    )

    preliminary_run = service.start_preliminary_assessment(candidate.outbound_candidate_id)
    assessed = service.list_candidates().items[0]

    assert preliminary_run.run.kind is PipelineRunKind.INTELLIGENCE
    assert assessed.preliminary_assessment is not None
    assert assessed.preliminary_assessment.identity.mode == "preliminary"
    assert assessed.preliminary_assessment.memo is None
    assert assessed.preliminary_assessment.decision_readiness is None
    assert assessed.preliminary_assessment.coverage.source_count == 1
    assert assessed.preliminary_assessment.coverage.artifact_count == 1
    assert assessed.preliminary_assessment.recommendation is not None
    assert assessed.preliminary_assessment.recommendation.action is RecommendationAction.ACTIVATE

    activated = service.activate_candidate(
        candidate.outbound_candidate_id,
        outreach_draft="Custom evidence-reviewed draft; a human must send it.",
    )
    assert activated.status.value == "activated"
    assert activated.outreach_draft == "Custom evidence-reviewed draft; a human must send it."
    outreach = service.record_outreach(
        candidate.outbound_candidate_id,
        method=OutreachMethod.EMAIL,
        status="sent_by_human",
        actor_id="investor01",
    )
    assert outreach.status == "sent_by_human"
    assert service.list_candidates().items[0].status.value == "contacted"


def test_preliminary_candidate_without_source_evidence_never_activates() -> None:
    service = _service()
    service.create_thesis(_draft(), actor_id="investor01")
    candidate = service.seed_outbound_candidate(company_name="Unverified Systems")

    service.start_preliminary_assessment(candidate.outbound_candidate_id)
    assessed = service.list_candidates().items[0]
    assessment = assessed.preliminary_assessment

    assert assessed.status is OutboundCandidateStatus.PRELIMINARY_ASSESSMENT
    assert assessment is not None
    assert assessment.coverage.source_count == 0
    assert assessment.coverage.artifact_count == 0
    assert assessment.coverage.freshest_evidence_at.state is KnowledgeState.UNKNOWN
    assert "source_evidence" in assessment.coverage.missing_fields
    assert assessment.recommendation is not None
    assert assessment.recommendation.action is RecommendationAction.NEEDS_INFORMATION
    with pytest.raises(ConflictError, match="preliminary assessment"):
        service.activate_candidate(candidate.outbound_candidate_id)


def test_status_capability_is_hashed_scoped_replay_safe_and_revocable() -> None:
    service = _service()
    receipt = service.accept_application(
        company_name="Jade Systems",
        display_name="jade.pdf",
        media_type="application/pdf",
        deck_content=b"%PDF-1.7 fictional",
        idempotency_key="application-attempt-01",
    )

    assert receipt.founder_status_capability not in service.capability_digests
    status = service.founder_status(receipt.founder_status_capability)
    assert status.application_id == receipt.application_id
    assert "memo" not in type(status).model_fields
    assert "recommendation" not in type(status).model_fields

    replay = service.accept_application(
        company_name=" Jade   Systems ",
        display_name="jade.pdf",
        media_type="application/pdf",
        deck_content=b"%PDF-1.7 fictional",
        idempotency_key="application-attempt-01",
    )
    assert replay.replayed is True
    assert replay.application_id == receipt.application_id
    assert replay.founder_status_capability == receipt.founder_status_capability
    assert service.founder_status(receipt.founder_status_capability) == status
    assert len(service.capability_digests) == 1

    service.revoke_founder_status(receipt.application_id)
    with pytest.raises(CapabilityDeniedError):
        service.founder_status(replay.founder_status_capability)

    replay_after_revocation = service.accept_application(
        company_name="Jade Systems",
        display_name="jade.pdf",
        media_type="application/pdf",
        deck_content=b"%PDF-1.7 fictional",
        idempotency_key="application-attempt-01",
    )
    assert replay_after_revocation.founder_status_capability == receipt.founder_status_capability
    with pytest.raises(CapabilityDeniedError):
        service.founder_status(replay_after_revocation.founder_status_capability)


def test_application_extraction_outcome_is_observable_and_retry_preserves_artifact() -> None:
    service = _service()
    accepted = _register_queued_application(service)

    blocked = service.record_application_extraction_outcome(
        accepted.application_id,
        outcome=ApplicationExtractionOutcome.BLOCKED,
        safe_code="mistral_ocr_policy_denied",
    )

    assert blocked.status is PipelineRunStatus.PARTIALLY_SUCCEEDED
    assert blocked.accepted_output_ids == (accepted.source_artifact_id,)
    assert blocked.stages[0].status.value == "failed"
    assert blocked.failures[0].safe_code == "mistral_ocr_policy_denied"
    assert blocked.failures[0].retryable is True

    retry = service.retry_run(blocked.run_id).run
    assert retry.status is PipelineRunStatus.SUCCEEDED
    assert retry.retry_of_run_id == blocked.run_id
    assert retry.accepted_output_ids[0] == accepted.source_artifact_id


def test_successful_application_extraction_accepts_output_and_clears_failure() -> None:
    service = _service()
    accepted = _register_queued_application(service)
    service.record_application_extraction_outcome(
        accepted.application_id,
        outcome=ApplicationExtractionOutcome.FAILED,
        safe_code="mistral_ocr_transport_failed",
    )

    recovered = service.record_application_extraction_outcome(
        accepted.application_id,
        outcome=ApplicationExtractionOutcome.SUCCEEDED,
        accepted_output_id="pdf-extraction-01",
    )

    assert recovered.status is PipelineRunStatus.SUCCEEDED
    assert recovered.accepted_output_ids == (
        accepted.source_artifact_id,
        "pdf-extraction-01",
    )
    assert recovered.stages[0].accepted_output_ids == ("pdf-extraction-01",)
    assert recovered.failures == ()


def test_idempotency_key_conflict_preserves_original_application() -> None:
    service = _service()
    receipt = service.accept_application(
        company_name="Jade Systems",
        display_name="jade.pdf",
        media_type="application/pdf",
        deck_content=b"%PDF-1.7 original",
        idempotency_key="application-attempt-01",
    )

    with pytest.raises(ConflictError, match="different content"):
        service.accept_application(
            company_name="Jade Systems",
            display_name="jade.pdf",
            media_type="application/pdf",
            deck_content=b"%PDF-1.7 replacement",
            idempotency_key="application-attempt-01",
        )

    assert service.list_opportunities().items[0].company_id == receipt.company_id


def test_retry_preserves_accepted_outputs_and_resumes_after_safe_stage() -> None:
    service = _service(max_retry_attempts=2)
    failed = service.seed_failed_run(accepted_output_ids=("accepted-evidence-01",))

    retry = service.retry_run(failed.run_id)
    repeated = service.retry_run(failed.run_id)

    assert retry.run_id == repeated.run_id
    assert retry.run.retry_of_run_id == failed.run_id
    assert retry.run.attempt == 2
    assert retry.run.accepted_output_ids[: len(failed.accepted_output_ids)] == (
        failed.accepted_output_ids
    )
    assert len(retry.run.accepted_output_ids) == len(failed.accepted_output_ids) + 1
    recovered_output_id = retry.run.accepted_output_ids[-1]
    assert retry.run.stages[0].status.value == "skipped"
    assert retry.run.stages[0].accepted_output_ids == failed.accepted_output_ids
    assert retry.run.stages[1].status.value == "succeeded"
    assert retry.run.stages[1].accepted_output_ids == (recovered_output_id,)
    assert retry.run.status is PipelineRunStatus.SUCCEEDED

    recovered_from_empty = service.retry_run(service.seed_failed_run().run_id)
    assert recovered_from_empty.run.status is PipelineRunStatus.SUCCEEDED
    assert len(recovered_from_empty.run.accepted_output_ids) == 1
    assert recovered_from_empty.run.stages[1].accepted_output_ids == (
        recovered_from_empty.run.accepted_output_ids[0],
    )

    exhausted = service.seed_failed_run(attempt=2)
    with pytest.raises(RetryLimitError):
        service.retry_run(exhausted.run_id)


@pytest.mark.parametrize(
    ("media_type", "deck_content", "max_bytes", "error_type"),
    (
        ("text/plain", b"%PDF-1.7 valid", 1024, InvalidApplicationDeckError),
        ("application/pdf", b"not-a-pdf", 1024, InvalidApplicationDeckError),
        ("application/pdf", b"%PDF-1.7 too-large", 8, ApplicationDeckTooLargeError),
    ),
)
def test_fake_application_intake_rejects_unsafe_decks_before_mutation(
    media_type: str,
    deck_content: bytes,
    max_bytes: int,
    error_type: type[Exception],
) -> None:
    service = _service(max_fake_pdf_bytes=max_bytes)

    with pytest.raises(error_type):
        service.accept_application(
            company_name="Jade Systems",
            display_name="jade.pdf",
            media_type=media_type,
            deck_content=deck_content,
            idempotency_key="unsafe-application",
        )

    assert service.list_opportunities().items == ()


def test_decisions_are_append_only_and_survive_a_new_system_assessment() -> None:
    service = _service()
    service.create_thesis(_draft(), actor_id="investor01")
    opportunity_id = _accept_application(service)
    service.start_screening(opportunity_id)
    first_detail = service.get_opportunity(opportunity_id)
    assessment = first_detail.latest_assessment
    assert assessment is not None
    assert assessment.memo is not None
    assert assessment.recommendation is not None

    decision = service.record_decision(
        opportunity_id,
        assessment_id=assessment.assessment_id,
        memo_id=assessment.memo.memo_id,
        recommendation_id=assessment.recommendation.recommendation_id,
        disposition=HumanDecisionDisposition.HOLD,
        rationale="Resolve the founder identity first.",
        actor_id="investor01",
    )
    service.start_screening(opportunity_id)
    updated = service.get_opportunity(opportunity_id)

    assert updated.human_decisions == (decision,)
    assert len(updated.assessment_history) == 2
    assert len(updated.memo_revisions) == 2
    assert updated.latest_recommendation is not None
    assert decision.reviewed_recommendation_id != updated.latest_recommendation.recommendation_id
    latest = updated.latest_assessment
    assert latest is not None and latest.memo is not None and latest.recommendation is not None
    second_decision = service.record_decision(
        opportunity_id,
        assessment_id=latest.assessment_id,
        memo_id=latest.memo.memo_id,
        recommendation_id=latest.recommendation.recommendation_id,
        disposition=HumanDecisionDisposition.REQUEST_MORE_INFORMATION,
        rationale="Request one focused founder-identification response.",
        actor_id="investor01",
    )
    final = service.get_opportunity(opportunity_id)
    assert final.human_decisions == (decision, second_decision)
    assert final.human_decisions[0] == decision


def test_advance_is_rejected_while_readiness_is_blocked() -> None:
    service = _service()
    service.create_thesis(_draft(), actor_id="investor01")
    opportunity_id = _accept_application(service)
    service.start_screening(opportunity_id)
    assessment = service.get_opportunity(opportunity_id).latest_assessment

    assert assessment is not None
    assert assessment.memo is not None
    assert assessment.recommendation is not None
    assert assessment.decision_readiness is not None
    assert assessment.decision_readiness.status is DecisionReadinessStatus.BLOCKED

    with pytest.raises(ConflictError, match="Advance decision"):
        service.record_decision(
            opportunity_id,
            assessment_id=assessment.assessment_id,
            memo_id=assessment.memo.memo_id,
            recommendation_id=assessment.recommendation.recommendation_id,
            disposition=HumanDecisionDisposition.ADVANCE,
            rationale="Advance despite unresolved blocker.",
            actor_id="investor01",
        )

    unchanged = service.get_opportunity(opportunity_id)
    assert unchanged.human_decisions == ()
    assert unchanged.latest_assessment == assessment


def test_service_rule_override_history_survives_ledger_restart(tmp_path: Path) -> None:
    database_path = (tmp_path / "memory.sqlite3").resolve()
    service = _service(rule_override_ledger=SQLiteRuleOverrideLedger(SQLiteMemory(database_path)))
    service.create_thesis(_draft(), actor_id="investor01")
    opportunity_id = _accept_application(service)
    service.start_screening(opportunity_id)
    assessment = service.get_opportunity(opportunity_id).latest_assessment
    assert assessment is not None
    result = assessment.deterministic_results[0]
    original_outcome = result.outcome
    override = RuleOverride(
        override_id="override-service-001",
        replacement_outcome=RuleOutcome.PASS,
        actor_id="investor01",
        recorded_at=NOW,
        rationale="Accept the documented gap for this deterministic result.",
    )

    event = service.record_rule_override(
        opportunity_id,
        result_id=result.result_id,
        override=override,
    )
    restarted_service = _service(
        rule_override_ledger=SQLiteRuleOverrideLedger(SQLiteMemory(database_path))
    )

    assert result.outcome is original_outcome
    assert result.override is None
    assert restarted_service.rule_override_history(result.result_id) == (event,)


def test_query_uses_typed_executor_and_exposes_match_and_unknown_per_criterion() -> None:
    service = _service()
    opportunity_id = _accept_application(service)
    plan = OpportunityQueryPlan(
        query_plan_id="queryplan01",
        query_plan_version_id="queryplanversion01",
        raw_query="Inbound AI infrastructure companies in Berlin",
        planning_mode=QueryPlanningMode.DETERMINISTIC,
        planner_version="fake-planner.v0",
        state=QueryPlanState.VALIDATED,
        criteria=(
            QueryCriterion(
                criterion_id="criterionorigin",
                field=QueryCriterionField.ORIGIN,
                operator=QueryOperator.EQUALS,
                operands=("inbound",),
                strength=CriterionStrength.HARD_CONSTRAINT,
                unknown_policy=UnknownValuePolicy.MANUAL_REVIEW,
                source_text="inbound",
            ),
            QueryCriterion(
                criterion_id="criteriongeography",
                field=QueryCriterionField.GEOGRAPHY,
                operator=QueryOperator.EQUALS,
                operands=("Berlin",),
                strength=CriterionStrength.SCORED_PREFERENCE,
                unknown_policy=UnknownValuePolicy.PRESERVE_AS_UNKNOWN,
                source_text="in Berlin",
            ),
        ),
        max_results=20,
        created_at=NOW,
    )

    result = service.query_opportunities(plan)

    assert result.results[0].opportunity_id == opportunity_id
    outcomes = {item.field: item.outcome for item in result.results[0].criteria}
    assert outcomes[QueryCriterionField.ORIGIN] is CriterionMatchOutcome.MATCH
    assert outcomes[QueryCriterionField.GEOGRAPHY] is CriterionMatchOutcome.UNKNOWN
