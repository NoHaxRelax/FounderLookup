"""Activation-gated outbound Application convergence into common full Screening."""

from datetime import UTC, datetime
from itertools import count

import pytest

from founderlookup.application.models import (
    OutreachMethod,
    ThesisCriterion,
    ThesisCriterionMode,
    ThesisDraft,
)
from founderlookup.application.ports import AcceptedApplication
from founderlookup.application.service import (
    FakeVCBrainService,
    OutboundApplicationLinkUnavailableError,
)
from founderlookup.domain.assessment import FullAssessmentIdentity
from founderlookup.domain.lifecycles import (
    OpportunityOrigin,
    OutboundCandidateStatus,
    ScreeningCaseStatus,
)
from founderlookup.domain.query import UnknownValuePolicy

NOW = datetime(2026, 7, 19, 12, tzinfo=UTC)


def _service() -> FakeVCBrainService:
    identifiers = count(1)
    service = FakeVCBrainService(
        clock=lambda: NOW,
        id_factory=lambda: f"convergence-id-{next(identifiers):04d}",
        capability_pepper=b"outbound-convergence-test-pepper",
    )
    no_preference = ThesisCriterion(
        mode=ThesisCriterionMode.NO_PREFERENCE,
        unknown_policy=UnknownValuePolicy.PRESERVE_AS_UNKNOWN,
    )
    service.create_thesis(
        ThesisDraft(
            sector=no_preference,
            stage=no_preference,
            geography=no_preference,
            check_size=no_preference,
            ownership_target=no_preference,
            risk_appetite=no_preference,
        ),
        actor_id="investor:convergence",
    )
    return service


def _ready_candidate(service: FakeVCBrainService, *, name: str = "Ink Robotics") -> str:
    candidate = service.seed_outbound_candidate(
        company_name=name,
        founder_id="founder:outbound",
        source_artifact_ids=(f"source:{name.casefold().replace(' ', '-')}",),
    )
    service.start_preliminary_assessment(candidate.outbound_candidate_id)
    service.activate_candidate(candidate.outbound_candidate_id)
    return candidate.outbound_candidate_id


def _accepted(
    *,
    application_id: str,
    company_id: str,
    run_id: str,
) -> AcceptedApplication:
    return AcceptedApplication(
        application_id=application_id,
        company_id=company_id,
        run_id=run_id,
        source_artifact_id=f"artifact:{application_id}",
        source_artifact_sha256="a" * 64,
        received_at=NOW,
    )


def test_activated_candidate_application_converges_without_autonomous_action() -> None:
    service = _service()
    candidate_id = _ready_candidate(service)
    candidate_before = service.list_candidates().items[0]
    preliminary = candidate_before.preliminary_assessment
    assert preliminary is not None

    company_id = service.canonical_company_for_outbound_application(candidate_id)
    accepted = _accepted(
        application_id="application:outbound",
        company_id=company_id,
        run_id="run:outbound-intake",
    )
    receipt = service.register_application(
        accepted,
        display_name="outbound-deck.pdf",
        media_type="application/pdf",
        outbound_candidate_id=candidate_id,
    )

    candidate_after = service.list_candidates().items[0]
    opportunities = service.list_opportunities()
    assert receipt.company_id == candidate_before.company_id
    assert candidate_after.status is OutboundCandidateStatus.APPLIED
    assert candidate_after.application_id == accepted.application_id
    assert candidate_after.preliminary_assessment == preliminary
    assert len(opportunities.items) == 1
    assert opportunities.items[0].origin is OpportunityOrigin.OUTBOUND

    opportunity_id = opportunities.items[0].opportunity_id
    before_screening = service.get_opportunity(opportunity_id)
    assert before_screening.application_id == accepted.application_id
    assert before_screening.outbound_candidate_id == candidate_id
    assert before_screening.company_id == candidate_before.company_id
    assert before_screening.founder_id == candidate_before.founder_id
    assert before_screening.screening_status is ScreeningCaseStatus.FIRST_PASS
    assert before_screening.latest_assessment is None
    assert before_screening.human_decisions == ()
    assert before_screening.related_run_ids == (preliminary.run_id, accepted.run_id)

    service.start_screening(opportunity_id)
    screened = service.get_opportunity(opportunity_id)
    assert screened.latest_assessment is not None
    identity = screened.latest_assessment.identity
    assert isinstance(identity, FullAssessmentIdentity)
    assert identity.origin is OpportunityOrigin.OUTBOUND
    assert identity.application_id == accepted.application_id
    assert identity.outbound_candidate_id == candidate_id
    assert identity.company_id == candidate_before.company_id
    assert screened.human_decisions == ()
    assert service.list_candidates().items[0].preliminary_assessment == preliminary


def test_contacted_candidate_is_eligible_and_direct_application_stays_inbound() -> None:
    service = _service()
    candidate_id = _ready_candidate(service)
    service.record_outreach(
        candidate_id,
        method=OutreachMethod.EMAIL,
        status="sent_by_human",
        actor_id="investor:convergence",
    )
    company_id = service.canonical_company_for_outbound_application(candidate_id)
    service.register_application(
        _accepted(
            application_id="application:contacted",
            company_id=company_id,
            run_id="run:contacted-intake",
        ),
        display_name="contacted.pdf",
        media_type="application/pdf",
        outbound_candidate_id=candidate_id,
    )
    service.register_application(
        _accepted(
            application_id="application:direct",
            company_id="company:direct",
            run_id="run:direct-intake",
        ),
        display_name="direct.pdf",
        media_type="application/pdf",
    )

    opportunities = service.list_opportunities()
    by_origin = {item.origin: item for item in opportunities.items}
    assert set(by_origin) == {OpportunityOrigin.INBOUND, OpportunityOrigin.OUTBOUND}
    direct = service.get_opportunity(by_origin[OpportunityOrigin.INBOUND].opportunity_id)
    assert direct.outbound_candidate_id is None
    assert direct.company_id == "company:direct"


def test_link_state_and_identity_failures_share_one_non_enumerating_error() -> None:
    service = _service()
    discovered = service.seed_outbound_candidate(
        company_name="Not Activated",
        source_artifact_ids=("source:not-activated",),
    )
    service.start_preliminary_assessment(discovered.outbound_candidate_id)

    failures: list[OutboundApplicationLinkUnavailableError] = []
    for candidate_id in ("candidate:missing", discovered.outbound_candidate_id):
        with pytest.raises(OutboundApplicationLinkUnavailableError) as caught:
            service.canonical_company_for_outbound_application(candidate_id)
        failures.append(caught.value)

    assert {error.code for error in failures} == {"outbound_application_link_unavailable"}
    assert {str(error) for error in failures} == {"outbound Application link is unavailable"}


def test_replay_is_idempotent_and_link_cannot_be_reused_or_hijacked() -> None:
    service = _service()
    candidate_id = _ready_candidate(service, name="One Application")
    company_id = service.canonical_company_for_outbound_application(candidate_id)
    accepted = _accepted(
        application_id="application:one",
        company_id=company_id,
        run_id="run:one",
    )
    first = service.register_application(
        accepted,
        display_name="one.pdf",
        media_type="application/pdf",
        outbound_candidate_id=candidate_id,
    )
    replay = service.register_application(
        accepted.model_copy(update={"replayed": True}),
        display_name="one.pdf",
        media_type="application/pdf",
        outbound_candidate_id=candidate_id,
    )

    assert replay.application_id == first.application_id
    assert replay.replayed is True
    assert len(service.list_opportunities().items) == 1

    with pytest.raises(OutboundApplicationLinkUnavailableError):
        service.register_application(
            _accepted(
                application_id="application:different",
                company_id=company_id,
                run_id="run:different",
            ),
            display_name="different.pdf",
            media_type="application/pdf",
            outbound_candidate_id=candidate_id,
        )

    other_candidate_id = _ready_candidate(service, name="Hijack Attempt")
    with pytest.raises(OutboundApplicationLinkUnavailableError):
        service.register_application(
            accepted.model_copy(
                update={
                    "company_id": service.canonical_company_for_outbound_application(
                        other_candidate_id
                    )
                }
            ),
            display_name="hijack.pdf",
            media_type="application/pdf",
            outbound_candidate_id=other_candidate_id,
        )
    with pytest.raises(OutboundApplicationLinkUnavailableError):
        service.register_application(
            accepted.model_copy(update={"replayed": True}),
            display_name="one.pdf",
            media_type="application/pdf",
        )

    assert len(service.list_opportunities().items) == 1
    assert (
        service.list_candidates(status=OutboundCandidateStatus.ACTIVATED).items[0].application_id
        is None
    )
