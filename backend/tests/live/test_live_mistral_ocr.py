"""Opt-in contract test against the real Mistral OCR service.

Run explicitly with ``FOUNDERLOOKUP_RUN_LIVE_TESTS=1 uv run pytest
tests/live/test_live_mistral_ocr.py``. The generated PDF is fictional and public;
founder-submitted/private artifacts are deliberately outside this test's policy.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from hashlib import sha256
from itertools import count

import httpx
import pytest
from pydantic import SecretStr

from founderlookup.api.settings import APISettings
from founderlookup.domain.common import KnowledgeState
from founderlookup.domain.evidence import DataClassification
from founderlookup.ingestion.extraction import PdfExtractionRequest
from founderlookup.ingestion.mistral_ocr import MistralOcrExtractor, MistralOcrSettings

RUN_LIVE_TESTS = os.getenv("FOUNDERLOOKUP_RUN_LIVE_TESTS") == "1"

pytestmark = pytest.mark.live


def _fictional_public_pdf() -> bytes:
    """Build one valid, dependency-free PDF containing only synthetic text."""

    stream = (
        b"BT\n"
        b"/F1 20 Tf\n"
        b"72 730 Td\n"
        b"(FounderLookup Fictional OCR Check) Tj\n"
        b"0 -40 Td\n"
        b"/F1 12 Tf\n"
        b"(Jade Meridian Systems is a fictional demo company.) Tj\n"
        b"0 -24 Td\n"
        b"(No founder or customer data appears in this document.) Tj\n"
        b"ET\n"
    )
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(stream)).encode("ascii")
        + b" >>\nstream\n"
        + stream
        + b"endstream",
    )
    document = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_number, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document.extend(f"{object_number} 0 obj\n".encode())
        document.extend(body)
        document.extend(b"\nendobj\n")
    xref_offset = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    document.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode())
    document.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode()
    )
    return bytes(document)


@pytest.mark.skipif(
    not RUN_LIVE_TESTS,
    reason="set FOUNDERLOOKUP_RUN_LIVE_TESTS=1 to call the real Mistral API",
)
@pytest.mark.anyio
async def test_real_mistral_ocr_extracts_a_fictional_public_pdf() -> None:
    api_settings = APISettings()
    configured_key = api_settings.mistral_api_key
    if configured_key is None or not configured_key.get_secret_value().strip():
        pytest.skip("MISTRAL_API_KEY is not configured")

    pdf = _fictional_public_pdf()
    requested_at = datetime.now(UTC)
    identifiers = count(1)

    def id_factory(prefix: str) -> str:
        return f"{prefix}:live-{next(identifiers)}"

    ocr_settings = MistralOcrSettings(
        api_key=SecretStr(configured_key.get_secret_value()),
        enabled=True,
        max_pages=1,
        timeout_seconds=60.0,
    )
    request = PdfExtractionRequest(
        source_artifact_id="source-artifact:live-fictional-public-ocr",
        input_sha256=sha256(pdf).hexdigest(),
        content=pdf,
        classification=DataClassification.PUBLIC,
        requested_at=requested_at,
    )

    async with httpx.AsyncClient() as client:
        extractor = MistralOcrExtractor(
            settings=ocr_settings,
            client=client,
            clock=lambda: datetime.now(UTC),
            id_factory=id_factory,
        )
        result = await extractor.extract(request)

    assert result.source_artifact_id == request.source_artifact_id
    assert result.input_sha256 == request.input_sha256
    assert result.model_version.state is KnowledgeState.KNOWN
    assert result.model_version.value is not None
    assert result.model_version.value.startswith("mistral-ocr-4")
    assert len(result.pages) == 1
    extracted_text = result.pages[0].markdown.casefold()
    assert "founderlookup" in extracted_text or "fictional" in extracted_text
    assert configured_key.get_secret_value() not in repr(ocr_settings)
