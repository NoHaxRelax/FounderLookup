"""FastAPI transport for the versioned MVP command and read-model surface."""

import re
import secrets
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    Query,
    Request,
    Response,
    Security,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response as FastAPIResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, ValidationError

from founderlookup import __version__
from founderlookup.api.errors import APIProblem, ProblemDetails, install_exception_handlers
from founderlookup.api.schemas import (
    ActivationCommand,
    CapabilityRevokedResponse,
    DecisionCommand,
    OutreachCommand,
    QueryCommand,
    ThesisDraftRequest,
)
from founderlookup.api.security import (
    FixedWindowRateLimiter,
    InvestorAuthenticator,
    InvestorPrincipal,
)
from founderlookup.api.settings import APISettings
from founderlookup.application.models import (
    ApplicationReceipt,
    CandidateCollection,
    FounderStatusView,
    InvestmentThesisRevision,
    OpportunityCollection,
    OpportunityDetail,
    OutboundCandidateView,
    OutreachRecord,
    QueryResult,
    RunAccepted,
)
from founderlookup.application.ports import ApplicationIntakePort, IntakeSubmission
from founderlookup.application.service import FakeVCBrainService
from founderlookup.domain.assessment import Decision
from founderlookup.domain.lifecycles import (
    OpportunityOrigin,
    OutboundCandidateStatus,
    ScreeningCaseStatus,
)
from founderlookup.domain.runs import PipelineRun
from founderlookup.ingestion.intake import DeckTooLargeError

_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_PROBLEM_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ProblemDetails, "description": "Authentication or capability denied"},
    409: {"model": ProblemDetails, "description": "Command conflicts with current state"},
    422: {"model": ProblemDetails, "description": "Request validation failed"},
    429: {"model": ProblemDetails, "description": "Public-route rate limit exceeded"},
}


class HealthResponse(BaseModel):
    """Public process-health response."""

    model_config = ConfigDict(frozen=True)

    status: Literal["ok"] = "ok"


def create_app(
    *,
    settings: APISettings | None = None,
    service: FakeVCBrainService | None = None,
    intake_service: ApplicationIntakePort | None = None,
    application_extraction: Callable[[str], Awaitable[None]] | None = None,
) -> FastAPI:
    """Compose the transport; intake fails closed unless the safe service is wired."""

    configured = settings or APISettings()
    vc_brain = service or FakeVCBrainService(
        capability_pepper=configured.resolved_status_pepper(),
        max_retry_attempts=configured.maximum_retry_attempts,
    )
    authenticator = InvestorAuthenticator(configured.resolved_investor_token())
    rate_limiter = FixedWindowRateLimiter(window_seconds=configured.rate_limit_window_seconds)
    bearer = HTTPBearer(
        auto_error=False,
        scheme_name="InvestorBearer",
        description="Configured single-investor bearer credential for the P0 MVP.",
    )

    application = FastAPI(
        title="FounderLookup VC Brain API",
        summary="Evidence-first founder sourcing and opportunity screening.",
        version=__version__,
    )
    application.state.vc_brain_service = vc_brain
    application.state.intake_service = intake_service
    application.state.application_extraction = application_extraction
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(configured.cors_allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Founder-Status-Capability",
            "X-Request-ID",
        ],
        expose_headers=["Location", "Retry-After", "X-Request-ID"],
        max_age=600,
    )
    install_exception_handlers(application)

    @application.middleware("http")
    async def add_request_id(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        candidate = request.headers.get("X-Request-ID", "")
        request.state.request_id = (
            candidate if _REQUEST_ID.fullmatch(candidate) else secrets.token_hex(16)
        )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    async def require_investor(
        credentials: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(bearer),
        ],
    ) -> InvestorPrincipal:
        authorization = (
            None if credentials is None else f"{credentials.scheme} {credentials.credentials}"
        )
        return authenticator.authenticate(authorization)

    def rate_key(request: Request) -> str:
        return request.client.host if request.client is not None else "unknown-client"

    @application.get(
        "/health",
        response_model=HealthResponse,
        tags=["system"],
        summary="Check process health",
    )
    async def health() -> HealthResponse:
        return HealthResponse()

    @application.post(
        "/api/v1/applications",
        response_model=ApplicationReceipt,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["applications"],
        summary="Submit the minimum founder Application",
        responses={
            **_PROBLEM_RESPONSES,
            413: {"model": ProblemDetails, "description": "Deck exceeds the size limit"},
            415: {"model": ProblemDetails, "description": "Deck is not a supported PDF"},
            503: {"model": ProblemDetails, "description": "Safe intake is not configured"},
        },
    )
    async def submit_application(
        request: Request,
        response: Response,
        background_tasks: BackgroundTasks,
        company_name: Annotated[str, Form(min_length=1, max_length=300)],
        deck: Annotated[UploadFile, File()],
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=255),
        ],
    ) -> ApplicationReceipt:
        rate_limiter.check(
            bucket="application-intake",
            key=rate_key(request),
            limit=configured.intake_rate_limit,
        )
        if intake_service is None:
            raise APIProblem(
                status=503,
                code="safe_intake_unavailable",
                title="Application intake is temporarily unavailable",
            )
        content = await deck.read(configured.maximum_deck_bytes + 1)
        if len(content) > configured.maximum_deck_bytes:
            raise DeckTooLargeError
        try:
            submission = IntakeSubmission(
                company_name=company_name,
                display_name=deck.filename or "deck.pdf",
                media_type=deck.content_type or "application/octet-stream",
                deck_content=content,
                idempotency_key=idempotency_key,
            )
        except ValidationError as error:
            raise APIProblem(
                status=422,
                code="invalid_application_submission",
                title="Request validation failed",
            ) from error
        accepted = await intake_service.submit(submission)
        receipt = vc_brain.register_application(
            accepted,
            display_name=submission.display_name,
            media_type=submission.media_type,
        )
        if application_extraction is not None:
            # Extraction is idempotent after success and may safely resume an interrupted replay.
            background_tasks.add_task(application_extraction, accepted.application_id)
        response.headers["Location"] = f"/api/v1/runs/{receipt.run_id}"
        return receipt

    @application.get(
        "/api/v1/founder-status",
        response_model=FounderStatusView,
        tags=["applications"],
        summary="Read the capability-scoped founder status projection",
        responses=_PROBLEM_RESPONSES,
    )
    async def founder_status(
        request: Request,
        capability: Annotated[
            str | None,
            Header(alias="X-Founder-Status-Capability"),
        ] = None,
    ) -> FounderStatusView:
        rate_limiter.check(
            bucket="founder-status",
            key=rate_key(request),
            limit=configured.status_rate_limit,
        )
        if capability is None:
            raise APIProblem(
                status=401,
                code="founder_status_denied",
                title="Access denied",
            )
        return vc_brain.founder_status(capability)

    @application.delete(
        "/api/v1/applications/{application_id}/status-capability",
        response_model=CapabilityRevokedResponse,
        tags=["applications"],
        summary="Revoke founder status access",
        responses=_PROBLEM_RESPONSES,
    )
    async def revoke_status_capability(
        application_id: str,
        _principal: Annotated[InvestorPrincipal, Depends(require_investor)],
    ) -> CapabilityRevokedResponse:
        vc_brain.revoke_founder_status(application_id)
        return CapabilityRevokedResponse()

    @application.get(
        "/api/v1/theses",
        response_model=tuple[InvestmentThesisRevision, ...],
        tags=["theses"],
        summary="List immutable thesis revisions",
        responses=_PROBLEM_RESPONSES,
    )
    async def list_theses(
        _principal: Annotated[InvestorPrincipal, Depends(require_investor)],
    ) -> tuple[InvestmentThesisRevision, ...]:
        return vc_brain.thesis_history()

    @application.get(
        "/api/v1/theses/active",
        response_model=InvestmentThesisRevision,
        tags=["theses"],
        summary="Get the active thesis revision",
        responses=_PROBLEM_RESPONSES,
    )
    async def active_thesis(
        _principal: Annotated[InvestorPrincipal, Depends(require_investor)],
    ) -> InvestmentThesisRevision:
        return vc_brain.active_thesis()

    @application.post(
        "/api/v1/theses",
        response_model=InvestmentThesisRevision,
        status_code=status.HTTP_201_CREATED,
        tags=["theses"],
        summary="Append a new thesis revision",
        responses=_PROBLEM_RESPONSES,
    )
    async def create_thesis(
        command: ThesisDraftRequest,
        principal: Annotated[InvestorPrincipal, Depends(require_investor)],
    ) -> InvestmentThesisRevision:
        return vc_brain.create_thesis(command.to_domain(), actor_id=principal.principal_id)

    @application.post(
        "/api/v1/sourcing-runs",
        response_model=RunAccepted,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["sourcing"],
        summary="Start a bounded fake-backed sourcing run",
        responses=_PROBLEM_RESPONSES,
    )
    async def start_sourcing(
        response: Response,
        _principal: Annotated[InvestorPrincipal, Depends(require_investor)],
    ) -> RunAccepted:
        accepted = vc_brain.start_sourcing()
        response.headers["Location"] = accepted.status_url
        return accepted

    @application.get(
        "/api/v1/outbound-candidates",
        response_model=CandidateCollection,
        tags=["sourcing"],
        summary="List bounded outbound candidates",
        responses=_PROBLEM_RESPONSES,
    )
    async def list_candidates(
        _principal: Annotated[InvestorPrincipal, Depends(require_investor)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        workflow_state: Annotated[OutboundCandidateStatus | None, Query()] = None,
    ) -> CandidateCollection:
        return vc_brain.list_candidates(limit=limit, status=workflow_state)

    @application.post(
        "/api/v1/outbound-candidates/{candidate_id}/preliminary-assessment",
        response_model=RunAccepted,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["sourcing"],
        summary="Run the shared preliminary assessment contract",
        responses=_PROBLEM_RESPONSES,
    )
    async def preliminary_assessment(
        candidate_id: str,
        response: Response,
        _principal: Annotated[InvestorPrincipal, Depends(require_investor)],
    ) -> RunAccepted:
        accepted = vc_brain.start_preliminary_assessment(candidate_id)
        response.headers["Location"] = accepted.status_url
        return accepted

    @application.post(
        "/api/v1/outbound-candidates/{candidate_id}/activate",
        response_model=OutboundCandidateView,
        tags=["sourcing"],
        summary="Explicitly activate an outbound candidate",
        responses=_PROBLEM_RESPONSES,
    )
    async def activate_candidate(
        candidate_id: str,
        _principal: Annotated[InvestorPrincipal, Depends(require_investor)],
        command: ActivationCommand | None = None,
    ) -> OutboundCandidateView:
        return vc_brain.activate_candidate(
            candidate_id,
            outreach_draft=(command.outreach_draft if command is not None else None),
        )

    @application.post(
        "/api/v1/outbound-candidates/{candidate_id}/outreach",
        response_model=OutreachRecord,
        status_code=status.HTTP_201_CREATED,
        tags=["sourcing"],
        summary="Record a human-controlled outreach event",
        responses=_PROBLEM_RESPONSES,
    )
    async def record_outreach(
        candidate_id: str,
        command: OutreachCommand,
        principal: Annotated[InvestorPrincipal, Depends(require_investor)],
    ) -> OutreachRecord:
        return vc_brain.record_outreach(
            candidate_id,
            method=command.method,
            status=command.status,
            actor_id=principal.principal_id,
        )

    @application.post(
        "/api/v1/queries",
        response_model=QueryResult,
        tags=["opportunities"],
        summary="Execute a validated typed Opportunity Query Plan",
        responses=_PROBLEM_RESPONSES,
    )
    async def query_opportunities(
        command: QueryCommand,
        _principal: Annotated[InvestorPrincipal, Depends(require_investor)],
    ) -> QueryResult:
        return vc_brain.query_opportunities(command.plan.to_domain())

    @application.get(
        "/api/v1/opportunities",
        response_model=OpportunityCollection,
        tags=["opportunities"],
        summary="List bounded Opportunity summaries",
        responses=_PROBLEM_RESPONSES,
    )
    async def list_opportunities(
        _principal: Annotated[InvestorPrincipal, Depends(require_investor)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        origin: Annotated[OpportunityOrigin | None, Query()] = None,
        workflow_state: Annotated[ScreeningCaseStatus | None, Query()] = None,
    ) -> OpportunityCollection:
        return vc_brain.list_opportunities(
            limit=limit,
            origin=origin,
            screening_status=workflow_state,
        )

    @application.get(
        "/api/v1/opportunities/{opportunity_id}",
        response_model=OpportunityDetail,
        tags=["opportunities"],
        summary="Get the nested evidence-first Opportunity detail",
        responses=_PROBLEM_RESPONSES,
    )
    async def opportunity_detail(
        opportunity_id: str,
        _principal: Annotated[InvestorPrincipal, Depends(require_investor)],
        expand: Annotated[str | None, Query()] = None,
    ) -> OpportunityDetail:
        requested = set(expand.split(",")) if expand else set()
        if not requested.issubset({"claims", "evidence"}):
            raise APIProblem(
                status=400,
                code="unsupported_expansion",
                title="Unsupported Opportunity expansion",
            )
        return vc_brain.get_opportunity(opportunity_id)

    @application.post(
        "/api/v1/opportunities/{opportunity_id}/screen",
        response_model=RunAccepted,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["opportunities"],
        summary="Start common full Screening",
        responses=_PROBLEM_RESPONSES,
    )
    async def screen_opportunity(
        opportunity_id: str,
        response: Response,
        _principal: Annotated[InvestorPrincipal, Depends(require_investor)],
    ) -> RunAccepted:
        accepted = vc_brain.start_screening(opportunity_id)
        response.headers["Location"] = accepted.status_url
        return accepted

    @application.post(
        "/api/v1/opportunities/{opportunity_id}/decisions",
        response_model=Decision,
        status_code=status.HTTP_201_CREATED,
        tags=["decisions"],
        summary="Append an immutable human Decision",
        responses=_PROBLEM_RESPONSES,
    )
    async def record_decision(
        opportunity_id: str,
        command: DecisionCommand,
        principal: Annotated[InvestorPrincipal, Depends(require_investor)],
    ) -> Decision:
        return vc_brain.record_decision(
            opportunity_id,
            assessment_id=command.assessment_id,
            memo_id=command.memo_id,
            recommendation_id=command.recommendation_id,
            disposition=command.disposition,
            rationale=command.rationale,
            actor_id=principal.principal_id,
        )

    @application.get(
        "/api/v1/runs/{run_id}",
        response_model=PipelineRun,
        tags=["runs"],
        summary="Get observable pipeline-run state",
        responses=_PROBLEM_RESPONSES,
    )
    async def run_status(
        run_id: str,
        _principal: Annotated[InvestorPrincipal, Depends(require_investor)],
    ) -> PipelineRun:
        return vc_brain.get_run(run_id)

    @application.post(
        "/api/v1/runs/{run_id}/retry",
        response_model=RunAccepted,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["runs"],
        summary="Retry from the last safe stage boundary",
        responses=_PROBLEM_RESPONSES,
    )
    async def retry_run(
        run_id: str,
        response: Response,
        _principal: Annotated[InvestorPrincipal, Depends(require_investor)],
    ) -> RunAccepted:
        accepted = vc_brain.retry_run(run_id)
        response.headers["Location"] = accepted.status_url
        return accepted

    @application.get(
        "/api/v1/artifacts/{artifact_id}",
        response_class=FastAPIResponse,
        tags=["evidence"],
        summary="Read an authorized private Source Artifact",
        responses=_PROBLEM_RESPONSES,
    )
    async def read_artifact(
        artifact_id: str,
        principal: Annotated[InvestorPrincipal, Depends(require_investor)],
    ) -> FastAPIResponse:
        content, descriptor = vc_brain.read_artifact(
            artifact_id,
            principal_id=principal.principal_id,
        )
        encoded_name = quote(descriptor.display_name, safe="")
        return FastAPIResponse(
            content=content,
            media_type=descriptor.media_type,
            headers={
                "Content-Disposition": f"inline; filename*=UTF-8''{encoded_name}",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return application
