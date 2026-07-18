"""FastAPI application factory."""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict

from founderlookup import __version__


class HealthResponse(BaseModel):
    """Public process-health response."""

    model_config = ConfigDict(frozen=True)

    status: Literal["ok"] = "ok"


def create_app() -> FastAPI:
    """Create a dependency-light API instance for runtime and tests."""

    application = FastAPI(
        title="FounderLookup VC Brain API",
        summary="Evidence-first founder sourcing and opportunity screening.",
        version=__version__,
    )

    @application.get(
        "/health",
        response_model=HealthResponse,
        tags=["system"],
        summary="Check process health",
    )
    async def health() -> HealthResponse:
        return HealthResponse()

    return application
