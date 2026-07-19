"""RFC 9457-style safe problem responses with request correlation."""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

from founderlookup.application.service import (
    ApplicationServiceError,
    ArtifactUnavailableError,
    CapabilityDeniedError,
    ConflictError,
    NotFoundError,
    RetryLimitError,
)
from founderlookup.ingestion.intake import (
    DeckTooLargeError,
    IdempotencyConflictError,
    IntakeServiceError,
    InvalidPdfSignatureError,
    UnsupportedDeckMediaTypeError,
)


class ProblemField(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str
    code: str
    message: str


class ProblemDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str
    title: str
    status: int
    code: str
    request_id: str
    detail: str | None = None
    fields: tuple[ProblemField, ...] = ()


class APIProblem(RuntimeError):
    def __init__(
        self,
        *,
        status: int,
        code: str,
        title: str,
        detail: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(title)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.headers = dict(headers or {})


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else "request-unavailable"


def problem_response(
    request: Request,
    *,
    status: int,
    code: str,
    title: str,
    detail: str | None = None,
    fields: tuple[ProblemField, ...] = (),
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    problem = ProblemDetails(
        type=f"urn:founderlookup:problem:{code}",
        title=title,
        status=status,
        code=code,
        request_id=_request_id(request),
        detail=detail,
        fields=fields,
    )
    response_headers = {"X-Request-ID": problem.request_id, **dict(headers or {})}
    return JSONResponse(
        status_code=status,
        content=problem.model_dump(mode="json"),
        media_type="application/problem+json",
        headers=response_headers,
    )


def install_exception_handlers(application: FastAPI) -> None:
    @application.exception_handler(APIProblem)
    async def handle_api_problem(request: Request, error: APIProblem) -> JSONResponse:
        return problem_response(
            request,
            status=error.status,
            code=error.code,
            title=error.title,
            detail=error.detail,
            headers=error.headers,
        )

    @application.exception_handler(RequestValidationError)
    async def handle_validation(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        fields = tuple(
            ProblemField(
                field=".".join(str(part) for part in item["loc"]),
                code=str(item["type"]),
                message=str(item["msg"]),
            )
            for item in error.errors()
        )
        return problem_response(
            request,
            status=422,
            code="request_validation_failed",
            title="Request validation failed",
            fields=fields,
        )

    @application.exception_handler(IntakeServiceError)
    async def handle_intake_error(
        request: Request,
        error: IntakeServiceError,
    ) -> JSONResponse:
        if isinstance(error, DeckTooLargeError):
            status = 413
        elif isinstance(error, UnsupportedDeckMediaTypeError | InvalidPdfSignatureError):
            status = 415
        elif isinstance(error, IdempotencyConflictError):
            status = 409
        else:
            status = 422
        return problem_response(
            request,
            status=status,
            code=error.code,
            title=error.safe_message,
        )

    @application.exception_handler(ApplicationServiceError)
    async def handle_service_error(
        request: Request,
        error: ApplicationServiceError,
    ) -> JSONResponse:
        if isinstance(error, CapabilityDeniedError):
            status, title = 401, "Access denied"
        elif isinstance(error, NotFoundError | ArtifactUnavailableError):
            status, title = 404, "Resource not found"
        elif isinstance(error, ConflictError | RetryLimitError):
            status, title = 409, "Command conflicts with current state"
        else:
            status, title = 400, "Command could not be completed"
        return problem_response(
            request,
            status=status,
            code=error.code,
            title=title,
        )

    @application.exception_handler(StarletteHTTPException)
    async def handle_http_error(
        request: Request,
        error: StarletteHTTPException,
    ) -> JSONResponse:
        title = "Resource not found" if error.status_code == 404 else "HTTP request failed"
        return problem_response(
            request,
            status=error.status_code,
            code="route_not_found" if error.status_code == 404 else "http_error",
            title=title,
        )

    @application.exception_handler(Exception)
    async def handle_unexpected(request: Request, _error: Exception) -> JSONResponse:
        # Never serialize exception messages: they may contain a path, secret, or private data.
        return problem_response(
            request,
            status=500,
            code="internal_error",
            title="An unexpected error occurred",
        )
