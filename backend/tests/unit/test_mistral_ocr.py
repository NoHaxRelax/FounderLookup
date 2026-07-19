"""Network-isolated tests for the policy-gated Mistral OCR adapter."""

import asyncio
import base64
import gzip
import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from hashlib import sha256
from typing import cast

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from founderlookup.domain.common import KnowledgeState
from founderlookup.domain.evidence import DataClassification
from founderlookup.ingestion.extraction import (
    PdfExtractionBlockedError,
    PdfExtractionRequest,
    PdfExtractionResult,
    PdfExtractor,
)
from founderlookup.ingestion.mistral_ocr import (
    DEFAULT_MISTRAL_OCR_MODEL,
    MISTRAL_MODELS_ENDPOINT,
    MISTRAL_OCR_ENDPOINT,
    MistralOcrConfigurationError,
    MistralOcrDisabledError,
    MistralOcrExtractor,
    MistralOcrHttpError,
    MistralOcrInputError,
    MistralOcrPolicyError,
    MistralOcrResponseError,
    MistralOcrSettings,
    MistralOcrTransportError,
    MistralPrivateDataPolicy,
    RetentionPosture,
)

FIXED_TIME = datetime(2026, 7, 19, 8, 15, tzinfo=UTC)
PDF_BYTES = b"%PDF-1.7\nfictional OCR test deck\n%%EOF\n"
TEST_API_KEY = "unit-test-mistral-key"


class _ChunkedStream(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self._chunks = chunks
        self.yielded = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            self.yielded += 1
            yield chunk

    async def aclose(self) -> None:
        return None


def test_policy_and_configuration_failures_are_blocked_extractions() -> None:
    assert issubclass(MistralOcrDisabledError, PdfExtractionBlockedError)
    assert issubclass(MistralOcrPolicyError, PdfExtractionBlockedError)
    assert issubclass(MistralOcrConfigurationError, PdfExtractionBlockedError)
    assert not issubclass(MistralOcrInputError, PdfExtractionBlockedError)
    assert not issubclass(MistralOcrTransportError, PdfExtractionBlockedError)
    assert not issubclass(MistralOcrHttpError, PdfExtractionBlockedError)
    assert not issubclass(MistralOcrResponseError, PdfExtractionBlockedError)


def _clock() -> datetime:
    return FIXED_TIME


def _id_factory(prefix: str) -> str:
    return f"{prefix}:fixed"


def _request(
    *,
    classification: DataClassification = DataClassification.PUBLIC,
    content: bytes = PDF_BYTES,
) -> PdfExtractionRequest:
    return PdfExtractionRequest(
        source_artifact_id="source-artifact:ocr-test",
        input_sha256=sha256(content).hexdigest(),
        content=content,
        classification=classification,
        requested_at=FIXED_TIME,
    )


def _settings(
    *,
    enabled: bool = True,
    private_policy: MistralPrivateDataPolicy | None = None,
    approved_non_private_classifications: tuple[DataClassification, ...] = (
        DataClassification.PUBLIC,
    ),
    max_input_bytes: int = 1_000,
    max_response_bytes: int = 20_000_000,
    max_pages: int = 50,
    model_alias: str = DEFAULT_MISTRAL_OCR_MODEL,
) -> MistralOcrSettings:
    return MistralOcrSettings(
        api_key=SecretStr(TEST_API_KEY),
        enabled=enabled,
        private_policy=private_policy or MistralPrivateDataPolicy(),
        approved_non_private_classifications=approved_non_private_classifications,
        max_input_bytes=max_input_bytes,
        max_response_bytes=max_response_bytes,
        max_pages=max_pages,
        model_alias=model_alias,
    )


def _response() -> dict[str, object]:
    return {
        "model": "mistral-ocr-4-0",
        "pages": [
            {
                "index": 0,
                "markdown": "# Fictional Company",
                "confidence_scores": {
                    "average_page_confidence_score": 0.97,
                    "minimum_page_confidence_score": 0.91,
                },
                "dimensions": {"dpi": 200, "height": 1200, "width": 900},
                "images": [],
            },
            {
                "index": 1,
                "markdown": "Enterprise traction is founder asserted.",
                "confidence_scores": None,
                "images": [],
            },
        ],
        "usage_info": {
            "pages_processed": 2,
            "doc_size_bytes": len(PDF_BYTES),
        },
    }


async def _extract(
    *,
    settings: MistralOcrSettings,
    request: PdfExtractionRequest,
    handler: Callable[[httpx.Request], httpx.Response],
) -> PdfExtractionResult:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        extractor = MistralOcrExtractor(
            settings=settings,
            client=client,
            clock=_clock,
            id_factory=_id_factory,
        )
        assert isinstance(extractor, PdfExtractor)
        return await extractor.extract(request)


def test_direct_stateless_ocr_request_and_structured_response() -> None:
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        assert request.method == "POST"
        assert str(request.url) == MISTRAL_OCR_ENDPOINT
        assert request.url.path == "/v1/ocr"
        assert request.headers["Authorization"] == f"Bearer {TEST_API_KEY}"
        payload = cast(dict[str, object], json.loads(request.content))
        assert set(payload) == {
            "model",
            "document",
            "include_image_base64",
            "confidence_scores_granularity",
            "pages",
        }
        assert payload["model"] == DEFAULT_MISTRAL_OCR_MODEL
        assert payload["include_image_base64"] is False
        assert payload["confidence_scores_granularity"] == "page"
        assert payload["pages"] == "0-49"
        document = cast(dict[str, object], payload["document"])
        assert document["type"] == "document_url"
        document_url = cast(str, document["document_url"])
        assert document_url.startswith("data:application/pdf;base64,")
        encoded = document_url.removeprefix("data:application/pdf;base64,")
        assert base64.b64decode(encoded, validate=True) == PDF_BYTES
        assert "files" not in request.url.path
        assert "batch" not in request.url.path
        return httpx.Response(200, json=_response())

    result = asyncio.run(_extract(settings=_settings(), request=_request(), handler=handler))

    assert len(seen_requests) == 1
    assert result.model_version.state is KnowledgeState.KNOWN
    assert result.model_version.value == "mistral-ocr-4-0"
    assert tuple(page.locator for page in result.pages) == ("page:0", "page:1")
    assert result.pages[0].markdown == "# Fictional Company"
    assert result.pages[0].confidence.average.value == 0.97
    assert result.pages[0].confidence.minimum.value == 0.91
    assert result.pages[1].confidence.average.state is KnowledgeState.UNKNOWN
    assert result.pages[1].confidence.minimum.state is KnowledgeState.UNKNOWN
    assert result.usage.pages_processed.value == 2
    assert result.usage.document_size_bytes.value == len(PDF_BYTES)


def test_latest_response_alias_is_resolved_to_one_concrete_ocr_4_model() -> None:
    requests: list[httpx.Request] = []
    response_payload = _response()
    response_payload["model"] = DEFAULT_MISTRAL_OCR_MODEL

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            assert str(request.url) == MISTRAL_OCR_ENDPOINT
            return httpx.Response(200, json=response_payload)
        assert request.method == "GET"
        assert str(request.url) == f"{MISTRAL_MODELS_ENDPOINT}/{DEFAULT_MISTRAL_OCR_MODEL}"
        assert request.content == b""
        return httpx.Response(
            200,
            json={
                "id": DEFAULT_MISTRAL_OCR_MODEL,
                "aliases": ["mistral-ocr-4-0", "mistral-ocr-4"],
            },
        )

    result = asyncio.run(_extract(settings=_settings(), request=_request(), handler=handler))

    assert [request.method for request in requests] == ["POST", "GET"]
    assert result.model_version.state is KnowledgeState.KNOWN
    assert result.model_version.value == "mistral-ocr-4-0"


def test_environment_loading_is_explicit_and_secret_is_redacted() -> None:
    settings = MistralOcrSettings.from_environment(environ={"MISTRAL_API_KEY": TEST_API_KEY})

    assert settings.enabled is False
    assert settings.max_pages == 50
    assert settings.api_key.get_secret_value() == TEST_API_KEY
    assert TEST_API_KEY not in repr(settings)
    with pytest.raises(MistralOcrConfigurationError):
        MistralOcrSettings.from_environment(environ={})

    confirmed = MistralOcrSettings.from_environment(
        environ={
            "MISTRAL_API_KEY": TEST_API_KEY,
            "FOUNDERLOOKUP_MISTRAL_OCR_ENABLED": "true",
            "FOUNDERLOOKUP_MISTRAL_OCR_ALLOW_PRIVATE": "true",
            "FOUNDERLOOKUP_MISTRAL_OCR_TRAINING_OPT_OUT_CONFIRMED": "true",
            "FOUNDERLOOKUP_MISTRAL_OCR_RETENTION_POSTURE": "zero_data_retention",
            "FOUNDERLOOKUP_MISTRAL_OCR_REGION": "EU",
            "FOUNDERLOOKUP_MISTRAL_OCR_REGION_CONFIRMED": "true",
            "FOUNDERLOOKUP_MISTRAL_OCR_PURPOSE": "Pitch deck OCR extraction only",
            "FOUNDERLOOKUP_MISTRAL_OCR_PURPOSE_CONFIRMED": "true",
            "FOUNDERLOOKUP_MISTRAL_OCR_MAX_PAGES": "25",
        }
    )
    assert confirmed.enabled is True
    assert confirmed.max_pages == 25
    assert confirmed.private_policy.permits_private_transfer() is True

    with pytest.raises(MistralOcrConfigurationError):
        MistralOcrSettings.from_environment(
            environ={
                "MISTRAL_API_KEY": TEST_API_KEY,
                "FOUNDERLOOKUP_MISTRAL_OCR_ENABLED": "not-a-boolean",
            }
        )


def test_public_ocr_is_disabled_by_default_without_sending_a_request() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_response())

    with pytest.raises(MistralOcrDisabledError):
        asyncio.run(
            _extract(
                settings=_settings(enabled=False),
                request=_request(),
                handler=handler,
            )
        )
    assert calls == 0


def _approved_private_policy() -> MistralPrivateDataPolicy:
    return MistralPrivateDataPolicy(
        allow_private=True,
        training_opt_out_confirmed=True,
        retention_posture=RetentionPosture.ZERO_DATA_RETENTION,
        region="EU",
        region_confirmed=True,
        purpose="Pitch deck OCR extraction only",
        purpose_confirmed=True,
    )


@pytest.mark.parametrize(
    "policy",
    [
        _approved_private_policy().model_copy(update={"allow_private": False}),
        _approved_private_policy().model_copy(update={"training_opt_out_confirmed": False}),
        _approved_private_policy().model_copy(
            update={"retention_posture": RetentionPosture.UNCONFIRMED}
        ),
        _approved_private_policy().model_copy(update={"region": None}),
        _approved_private_policy().model_copy(update={"region_confirmed": False}),
        _approved_private_policy().model_copy(update={"purpose": None}),
        _approved_private_policy().model_copy(update={"purpose_confirmed": False}),
    ],
)
def test_founder_private_requires_every_explicit_confirmation(
    policy: MistralPrivateDataPolicy,
) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_response())

    with pytest.raises(MistralOcrPolicyError):
        asyncio.run(
            _extract(
                settings=_settings(private_policy=policy),
                request=_request(classification=DataClassification.FOUNDER_PRIVATE),
                handler=handler,
            )
        )
    assert calls == 0


def test_fully_confirmed_private_policy_allows_one_bounded_call() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_response())

    result = asyncio.run(
        _extract(
            settings=_settings(private_policy=_approved_private_policy()),
            request=_request(classification=DataClassification.FOUNDER_PRIVATE),
            handler=handler,
        )
    )

    assert calls == 1
    assert result.input_sha256 == sha256(PDF_BYTES).hexdigest()


def test_investor_internal_requires_the_full_private_policy() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_response())

    with pytest.raises(ValidationError):
        _settings(
            approved_non_private_classifications=(
                DataClassification.PUBLIC,
                DataClassification.INVESTOR_INTERNAL,
            )
        )
    with pytest.raises(MistralOcrPolicyError):
        asyncio.run(
            _extract(
                settings=_settings(),
                request=_request(classification=DataClassification.INVESTOR_INTERNAL),
                handler=handler,
            )
        )
    assert calls == 0

    asyncio.run(
        _extract(
            settings=_settings(private_policy=_approved_private_policy()),
            request=_request(classification=DataClassification.INVESTOR_INTERNAL),
            handler=handler,
        )
    )
    assert calls == 1


def test_restricted_classification_is_denied_even_with_private_policy() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_response())

    with pytest.raises(MistralOcrPolicyError):
        asyncio.run(
            _extract(
                settings=_settings(private_policy=_approved_private_policy()),
                request=_request(classification=DataClassification.RESTRICTED),
                handler=handler,
            )
        )
    assert calls == 0


def test_input_is_bounded_before_http() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_response())

    with pytest.raises(MistralOcrInputError):
        asyncio.run(
            _extract(
                settings=_settings(max_input_bytes=len(PDF_BYTES) - 1),
                request=_request(),
                handler=handler,
            )
        )
    invalid_pdf = b"not-a-pdf"
    with pytest.raises(MistralOcrInputError):
        asyncio.run(
            _extract(
                settings=_settings(),
                request=_request(content=invalid_pdf),
                handler=handler,
            )
        )
    assert calls == 0


def test_page_budget_is_sent_and_enforced_against_response_and_usage() -> None:
    seen_pages: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = cast(dict[str, object], json.loads(request.content))
        seen_pages.append(payload["pages"])
        return httpx.Response(200, json=_response())

    with pytest.raises(MistralOcrResponseError):
        asyncio.run(
            _extract(
                settings=_settings(max_pages=1),
                request=_request(),
                handler=handler,
            )
        )

    usage_over_budget = _response()
    usage_over_budget["pages"] = cast(list[object], usage_over_budget["pages"])[:1]
    usage_over_budget["usage_info"] = {
        "pages_processed": 2,
        "doc_size_bytes": len(PDF_BYTES),
    }

    def usage_handler(request: httpx.Request) -> httpx.Response:
        payload = cast(dict[str, object], json.loads(request.content))
        seen_pages.append(payload["pages"])
        return httpx.Response(200, json=usage_over_budget)

    with pytest.raises(MistralOcrResponseError):
        asyncio.run(
            _extract(
                settings=_settings(max_pages=1),
                request=_request(),
                handler=usage_handler,
            )
        )

    assert seen_pages == ["0", "0"]

    with pytest.raises(ValidationError):
        _settings(max_pages=0)
    with pytest.raises(ValidationError):
        _settings(max_pages=1_001)


@pytest.mark.parametrize(
    "response_payload",
    [
        {"pages": _response()["pages"], "usage_info": _response()["usage_info"]},
        {"model": "mistral-ocr-3-0", "pages": _response()["pages"]},
        {"model": "mistral-ocr-4-0", "pages": []},
        {
            "model": "mistral-ocr-4-0",
            "pages": [
                {"index": 1, "markdown": "wrong first index"},
                {"index": 0, "markdown": "out of order"},
            ],
        },
        {
            "model": "mistral-ocr-4-0",
            "pages": _response()["pages"],
            "usage_info": {"pages_processed": 9},
        },
        {
            "model": "mistral-ocr-4-0",
            "pages": _response()["pages"],
            "usage_info": {"doc_size_bytes": 9},
        },
    ],
)
def test_invalid_or_mismatched_response_fails_closed(
    response_payload: dict[str, object],
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_payload)

    with pytest.raises(MistralOcrResponseError):
        asyncio.run(_extract(settings=_settings(), request=_request(), handler=handler))


def test_exact_model_configuration_rejects_a_different_concrete_model() -> None:
    response_payload = _response()
    response_payload["model"] = "mistral-ocr-4-1"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_payload)

    with pytest.raises(MistralOcrResponseError):
        asyncio.run(
            _extract(
                settings=_settings(model_alias="mistral-ocr-4-0"),
                request=_request(),
                handler=handler,
            )
        )
    with pytest.raises(ValidationError):
        _settings(model_alias="mistral-ocr-3-0")


def test_redirects_are_disabled_even_when_the_injected_client_enables_them() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(307, headers={"Location": "https://unapproved.invalid/collect"})

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
        ) as client:
            extractor = MistralOcrExtractor(
                settings=_settings(),
                client=client,
                clock=_clock,
                id_factory=_id_factory,
            )
            with pytest.raises(MistralOcrHttpError):
                await extractor.extract(_request())

    asyncio.run(run())
    assert len(requests) == 1
    assert str(requests[0].url) == MISTRAL_OCR_ENDPOINT


def test_content_length_rejects_before_streaming() -> None:
    stream = _ChunkedStream(b"not read")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Length": "9"},
            stream=stream,
        )

    with pytest.raises(MistralOcrResponseError):
        asyncio.run(
            _extract(
                settings=_settings(max_response_bytes=8),
                request=_request(),
                handler=handler,
            )
        )
    assert stream.yielded == 0


@pytest.mark.parametrize("status_code", [200, 500])
def test_decompressed_response_is_incrementally_bounded_for_every_status(
    status_code: int,
) -> None:
    stream = _ChunkedStream(b"12345", b"67890", b"must-not-be-read")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, stream=stream)

    with pytest.raises(MistralOcrResponseError):
        asyncio.run(
            _extract(
                settings=_settings(max_response_bytes=8),
                request=_request(),
                handler=handler,
            )
        )
    assert stream.yielded == 2


def test_compressed_response_is_capped_after_decompression() -> None:
    compressed = gzip.compress(b"A" * 10_000)
    assert len(compressed) < 100
    stream = _ChunkedStream(compressed)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "Content-Encoding": "gzip",
                "Content-Length": str(len(compressed)),
            },
            stream=stream,
        )

    with pytest.raises(MistralOcrResponseError):
        asyncio.run(
            _extract(
                settings=_settings(max_response_bytes=100),
                request=_request(),
                handler=handler,
            )
        )
    assert stream.yielded == 1


def test_http_timeout_invalid_json_and_error_body_are_safely_mapped() -> None:
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("sensitive upstream detail", request=request)

    with pytest.raises(MistralOcrTransportError) as timeout_failure:
        asyncio.run(_extract(settings=_settings(), request=_request(), handler=timeout_handler))
    assert timeout_failure.value.__cause__ is None

    def http_error_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            text=f"secret={TEST_API_KEY}; pdf={PDF_BYTES!r}; url={MISTRAL_OCR_ENDPOINT}",
        )

    with pytest.raises(MistralOcrHttpError) as http_failure:
        asyncio.run(_extract(settings=_settings(), request=_request(), handler=http_error_handler))
    assert TEST_API_KEY not in str(http_failure.value)
    assert "fictional OCR test deck" not in str(http_failure.value)
    assert MISTRAL_OCR_ENDPOINT not in str(http_failure.value)

    def invalid_json_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    with pytest.raises(MistralOcrResponseError):
        asyncio.run(
            _extract(settings=_settings(), request=_request(), handler=invalid_json_handler)
        )
