"""Safety contract for direct public PDF acquisition used before outbound OCR."""

from datetime import UTC, datetime

import httpx
import pytest

from founderlookup.application.sourcing import (
    BoundedPublicPdfAcquisition,
    PublicPdfAcquisitionPolicy,
    resolve_public_deck_pdf_url,
)
from founderlookup.domain.discovery import AcquisitionRequest, AcquisitionStatus
from founderlookup.domain.evidence import DataClassification

NOW = datetime(2026, 7, 19, 12, tzinfo=UTC)
SLIDES_EDIT = (
    "https://docs.google.com/presentation/d/"
    "1tsx9EmV3Mx0U4Hcv9Hhew0FdMP4ybgdOQPDxFbFZ_DA/edit?usp=sharing"
)
SLIDES_EXPORT = (
    "https://docs.google.com/presentation/d/1tsx9EmV3Mx0U4Hcv9Hhew0FdMP4ybgdOQPDxFbFZ_DA/export/pdf"
)


def _request(url: str, *, max_bytes: int = 2_000_000) -> AcquisitionRequest:
    return AcquisitionRequest(
        acquisition_request_id="request:public-pdf",
        discovery_lead_id="lead:public-deck",
        original_url=url,
        requested_at=NOW,
        classification=DataClassification.PUBLIC,
        allowed_media_types=("application/pdf",),
        max_bytes=max_bytes,
        timeout_seconds=20,
    )


def test_public_deck_url_policy_accepts_only_https_pdf_or_google_slides() -> None:
    direct = resolve_public_deck_pdf_url("https://cdn.example.test/deck.PDF?version=2")
    assert direct.acquisition_url == "https://cdn.example.test/deck.PDF?version=2"
    assert direct.normalization == "direct_pdf"

    slides = resolve_public_deck_pdf_url(SLIDES_EDIT)
    assert slides.source_url == SLIDES_EDIT
    assert slides.acquisition_url == SLIDES_EXPORT
    assert slides.normalization == "google_slides_export_pdf"

    for rejected in (
        "http://cdn.example.test/deck.pdf",
        "https://cdn.example.test/deck.html",
        "https://docs.google.com/document/d/not-a-presentation/edit",
        "file:///tmp/deck.pdf",
    ):
        with pytest.raises(ValueError):
            resolve_public_deck_pdf_url(rejected)


@pytest.mark.anyio
async def test_public_pdf_acquisition_bounds_redirects_and_accepts_only_pdf_bytes() -> None:
    redirected = "https://download.googleusercontent.com/deck.pdf"
    pdf = b"%PDF-1.4\n" + b"x" * 1_700_000

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == SLIDES_EXPORT:
            return httpx.Response(302, headers={"location": redirected})
        assert str(request.url) == redirected
        return httpx.Response(
            200,
            headers={
                "content-type": "application/pdf",
                "content-length": str(len(pdf)),
            },
            content=pdf,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = BoundedPublicPdfAcquisition(
            policy=PublicPdfAcquisitionPolicy(
                allowed_domains=("docs.google.com", "googleusercontent.com"),
                max_bytes=2_000_000,
                max_redirects=5,
            ),
            now=lambda: NOW,
            client=client,
        )
        result = await source.acquire(_request(SLIDES_EXPORT))

    assert result.status is AcquisitionStatus.ACQUIRED
    assert result.original_url == SLIDES_EXPORT
    assert result.media_type == "application/pdf"
    assert result.content == pdf


@pytest.mark.anyio
async def test_public_pdf_acquisition_rejects_html_and_byte_overruns() -> None:
    responses = (
        httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html />"),
        httpx.Response(
            200,
            headers={"content-type": "application/pdf", "content-length": "101"},
            content=b"%PDF-1.4\n" + b"x" * 91,
        ),
    )
    for response, safe_code in zip(
        responses,
        ("public_deck_not_pdf", "content_budget_exceeded"),
        strict=True,
    ):

        def handler(_request: httpx.Request, canned: httpx.Response = response) -> httpx.Response:
            return canned

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source = BoundedPublicPdfAcquisition(
                policy=PublicPdfAcquisitionPolicy(
                    allowed_domains=("cdn.example.test",),
                    max_bytes=100,
                ),
                now=lambda: NOW,
                client=client,
            )
            result = await source.acquire(
                _request("https://cdn.example.test/deck.pdf", max_bytes=100)
            )

        assert result.status is AcquisitionStatus.BLOCKED
        assert result.content is None
        assert result.failure is not None
        assert result.failure.safe_code == safe_code
