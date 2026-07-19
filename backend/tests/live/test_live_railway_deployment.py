"""Opt-in smoke test against the deployed Railway frontend and backend.

This test is deliberately excluded from normal runs. It verifies the public landing and
founder path, the same-origin frontend API proxy, the investor gate, and a fictional
minimum Application against the real deployment without sending any private data.
"""

from __future__ import annotations

import os
import secrets

import httpx
import pytest

from founderlookup.api.settings import APISettings

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("FOUNDERLOOKUP_RUN_LIVE_TESTS") != "1",
        reason="set FOUNDERLOOKUP_RUN_LIVE_TESTS=1 to test a deployed environment",
    ),
]


def _fictional_pdf() -> bytes:
    stream = (
        b"BT\n/F1 18 Tf\n72 730 Td\n"
        b"(FounderLookup fictional Railway smoke test) Tj\n"
        b"0 -30 Td\n/F1 11 Tf\n"
        b"(No founder customer or confidential data is present.) Tj\nET\n"
    )
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
        + stream
        + b"endstream",
    )
    document = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document.extend(f"{number} 0 obj\n".encode())
        document.extend(body)
        document.extend(b"\nendobj\n")
    xref = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    document.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode())
    document.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    return bytes(document)


def _required_url(name: str) -> str:
    value = os.getenv(name, "").strip().rstrip("/")
    if not value:
        pytest.skip(f"{name} is required for the opt-in deployment test")
    if not value.startswith("https://"):
        pytest.fail(f"{name} must be an HTTPS origin")
    return value


@pytest.mark.anyio
async def test_live_railway_frontend_backend_gate_and_founder_application() -> None:
    frontend = _required_url("FOUNDERLOOKUP_LIVE_FRONTEND_URL")
    backend = _required_url("FOUNDERLOOKUP_LIVE_BACKEND_URL")
    configured_key = APISettings().investor_api_key
    if configured_key is None or not configured_key.get_secret_value().strip():
        pytest.skip("FOUNDERLOOKUP_INVESTOR_API_KEY is required for the live gate test")
    investor_key = configured_key.get_secret_value()
    auth = {"Authorization": f"Bearer {investor_key}"}

    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        frontend_health = await client.get(f"{frontend}/healthz")
        assert frontend_health.status_code == 200

        landing = await client.get(frontend)
        assert landing.status_code == 200
        assert '<div id="root"></div>' in landing.text
        assert "FounderLookup" in landing.text

        backend_health = await client.get(f"{backend}/health")
        assert backend_health.status_code == 200
        assert backend_health.json()["status"] == "ok"

        denied = await client.get(f"{frontend}/api/v1/theses")
        assert denied.status_code == 401
        assert investor_key not in denied.text

        theses = await client.get(f"{frontend}/api/v1/theses", headers=auth)
        assert theses.status_code == 200
        assert isinstance(theses.json(), list)
        assert investor_key not in theses.text

        candidates = await client.get(
            f"{frontend}/api/v1/outbound-candidates",
            headers=auth,
        )
        assert candidates.status_code == 200
        assert isinstance(candidates.json()["items"], list)

        idempotency_key = f"railway-live-{secrets.token_hex(12)}"
        application = await client.post(
            f"{frontend}/api/v1/applications",
            headers={"Idempotency-Key": idempotency_key},
            data={"company_name": "Fictional Railway Smoke Check"},
            files={
                "deck": (
                    "fictional-railway-smoke.pdf",
                    _fictional_pdf(),
                    "application/pdf",
                )
            },
        )
        assert application.status_code == 202, application.text
        receipt = application.json()
        assert receipt["status"] in {"received", "processing"}
        capability = receipt["founder_status_capability"]
        assert capability
        assert capability != investor_key

        status = await client.get(
            f"{frontend}/api/v1/founder-status",
            headers={"X-Founder-Status-Capability": capability},
        )
        assert status.status_code == 200, status.text
        projection = status.json()
        assert projection["application_id"] == receipt["application_id"]
        assert "founder_status_capability" not in projection
        assert investor_key not in status.text
