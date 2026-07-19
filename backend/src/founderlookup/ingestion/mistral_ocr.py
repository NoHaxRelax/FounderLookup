"""Policy-gated stateless Mistral OCR adapter for private Source Artifacts."""

from __future__ import annotations

import base64
import json
import os
import re
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Final, Protocol, Self

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    model_validator,
)

from founderlookup.domain.common import (
    DomainModel,
    KnowledgeValue,
    NonBlankStr,
    PositiveInt,
    VersionId,
)
from founderlookup.domain.evidence import DataClassification
from founderlookup.ingestion.extraction import (
    Confidence01,
    ExtractedPdfPage,
    PdfExtractionBlockedError,
    PdfExtractionError,
    PdfExtractionRequest,
    PdfExtractionResult,
    PdfExtractionUsage,
    PdfPageConfidence,
)

MISTRAL_OCR_ENDPOINT: Final = "https://api.mistral.ai/v1/ocr"
MISTRAL_MODELS_ENDPOINT: Final = "https://api.mistral.ai/v1/models"
MISTRAL_OCR_ADAPTER_VERSION: Final = "mistral-ocr-http.v0"
DEFAULT_MISTRAL_OCR_MODEL: Final = "mistral-ocr-latest"
_PDF_DATA_URL_PREFIX: Final = "data:application/pdf;base64,"
_ENV_PREFIX: Final = "FOUNDERLOOKUP_MISTRAL_OCR_"

_PositiveFloat = Annotated[float, Field(strict=True, gt=0.0, le=300.0)]
_MaxPages = Annotated[int, Field(strict=True, gt=0, le=1_000)]
_ModelName = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=128,
        pattern=r"^mistral-ocr-[A-Za-z0-9._-]+$",
    ),
]


class MistralOcrError(PdfExtractionError):
    """Base adapter failure with a deliberately non-sensitive message."""

    code = "mistral_ocr_failed"
    safe_message = "The configured OCR extraction could not be completed safely."


class _MistralOcrBlockedError(MistralOcrError, PdfExtractionBlockedError):
    """Base for safe policy/configuration blocks that made no provider request."""


class MistralOcrDisabledError(_MistralOcrBlockedError):
    code = "mistral_ocr_disabled"
    safe_message = "External OCR extraction is disabled by configuration."


class MistralOcrPolicyError(_MistralOcrBlockedError):
    code = "mistral_ocr_policy_denied"
    safe_message = "External OCR is not approved for this artifact classification."


class MistralOcrConfigurationError(_MistralOcrBlockedError):
    code = "mistral_ocr_configuration_invalid"
    safe_message = "External OCR configuration is incomplete or invalid."


class MistralOcrInputError(MistralOcrError):
    code = "mistral_ocr_input_invalid"
    safe_message = "The document cannot be sent within the configured OCR limits."


class MistralOcrTransportError(MistralOcrError):
    code = "mistral_ocr_transport_failed"
    safe_message = "The OCR service could not be reached within the configured timeout."


class MistralOcrHttpError(MistralOcrError):
    code = "mistral_ocr_http_failed"
    safe_message = "The OCR service rejected the bounded extraction request."


class MistralOcrResponseError(MistralOcrError):
    code = "mistral_ocr_response_invalid"
    safe_message = "The OCR service returned an invalid extraction response."


class RetentionPosture(StrEnum):
    UNCONFIRMED = "unconfirmed"
    ZERO_DATA_RETENTION = "zero_data_retention"
    APPROVED_RETENTION = "approved_retention"


def _environment_bool(
    source: Mapping[str, str],
    name: str,
    *,
    default: bool,
) -> bool:
    raw = source.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise MistralOcrConfigurationError


def _environment_positive_int(
    source: Mapping[str, str],
    name: str,
    *,
    default: int,
) -> int:
    raw = source.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise MistralOcrConfigurationError from None
    if value <= 0:
        raise MistralOcrConfigurationError
    return value


def _environment_positive_float(
    source: Mapping[str, str],
    name: str,
    *,
    default: float,
) -> float:
    raw = source.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise MistralOcrConfigurationError from None
    if value <= 0.0:
        raise MistralOcrConfigurationError
    return value


def _environment_classifications(
    source: Mapping[str, str],
) -> tuple[DataClassification, ...]:
    raw = source.get(f"{_ENV_PREFIX}APPROVED_NON_PRIVATE_CLASSIFICATIONS")
    if raw is None:
        return (DataClassification.PUBLIC,)
    try:
        return tuple(DataClassification(item.strip()) for item in raw.split(",") if item.strip())
    except ValueError:
        raise MistralOcrConfigurationError from None


class MistralPrivateDataPolicy(DomainModel):
    """Every non-public-data control is explicit and disabled by default."""

    allow_private: bool = False
    training_opt_out_confirmed: bool = False
    retention_posture: RetentionPosture = RetentionPosture.UNCONFIRMED
    region: NonBlankStr | None = None
    region_confirmed: bool = False
    purpose: NonBlankStr | None = None
    purpose_confirmed: bool = False

    def permits_private_transfer(self) -> bool:
        return (
            self.allow_private
            and self.training_opt_out_confirmed
            and self.retention_posture is not RetentionPosture.UNCONFIRMED
            and self.region is not None
            and self.region_confirmed
            and self.purpose is not None
            and self.purpose_confirmed
        )


class MistralOcrSettings(DomainModel):
    """Server-owned OCR settings; SecretStr keeps the credential out of repr/logs."""

    api_key: SecretStr
    enabled: bool = False
    model_alias: _ModelName = DEFAULT_MISTRAL_OCR_MODEL
    max_input_bytes: PositiveInt = 20_000_000
    max_response_bytes: PositiveInt = 20_000_000
    max_pages: _MaxPages = 50
    timeout_seconds: _PositiveFloat = 60.0
    approved_non_private_classifications: tuple[DataClassification, ...] = (
        DataClassification.PUBLIC,
    )
    private_policy: MistralPrivateDataPolicy = Field(default_factory=MistralPrivateDataPolicy)

    @model_validator(mode="after")
    def validate_secret_and_classifications(self) -> Self:
        if not self.api_key.get_secret_value().strip():
            raise ValueError("api_key must be non-blank")
        if self.model_alias != DEFAULT_MISTRAL_OCR_MODEL and not self.model_alias.startswith(
            "mistral-ocr-4"
        ):
            raise ValueError("model_alias must select Mistral OCR 4")
        if any(
            classification is not DataClassification.PUBLIC
            for classification in self.approved_non_private_classifications
        ):
            raise ValueError("only public data may use the non-private allowlist")
        return self

    @classmethod
    def from_environment(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        **overrides: object,
    ) -> Self:
        """Read only the process environment; this never loads a dotenv file."""

        source = os.environ if environ is None else environ
        api_key = source.get("MISTRAL_API_KEY")
        if api_key is None or not api_key.strip():
            raise MistralOcrConfigurationError
        try:
            retention_posture = RetentionPosture(
                source.get(
                    f"{_ENV_PREFIX}RETENTION_POSTURE",
                    RetentionPosture.UNCONFIRMED.value,
                ).strip()
            )
            private_policy = MistralPrivateDataPolicy(
                allow_private=_environment_bool(
                    source,
                    f"{_ENV_PREFIX}ALLOW_PRIVATE",
                    default=False,
                ),
                training_opt_out_confirmed=_environment_bool(
                    source,
                    f"{_ENV_PREFIX}TRAINING_OPT_OUT_CONFIRMED",
                    default=False,
                ),
                retention_posture=retention_posture,
                region=source.get(f"{_ENV_PREFIX}REGION") or None,
                region_confirmed=_environment_bool(
                    source,
                    f"{_ENV_PREFIX}REGION_CONFIRMED",
                    default=False,
                ),
                purpose=source.get(f"{_ENV_PREFIX}PURPOSE") or None,
                purpose_confirmed=_environment_bool(
                    source,
                    f"{_ENV_PREFIX}PURPOSE_CONFIRMED",
                    default=False,
                ),
            )
            values: dict[str, object] = {
                "api_key": api_key,
                "enabled": _environment_bool(
                    source,
                    f"{_ENV_PREFIX}ENABLED",
                    default=False,
                ),
                "model_alias": source.get(
                    f"{_ENV_PREFIX}MODEL",
                    DEFAULT_MISTRAL_OCR_MODEL,
                ),
                "max_input_bytes": _environment_positive_int(
                    source,
                    f"{_ENV_PREFIX}MAX_INPUT_BYTES",
                    default=20_000_000,
                ),
                "max_response_bytes": _environment_positive_int(
                    source,
                    f"{_ENV_PREFIX}MAX_RESPONSE_BYTES",
                    default=20_000_000,
                ),
                "max_pages": _environment_positive_int(
                    source,
                    f"{_ENV_PREFIX}MAX_PAGES",
                    default=50,
                ),
                "timeout_seconds": _environment_positive_float(
                    source,
                    f"{_ENV_PREFIX}TIMEOUT_SECONDS",
                    default=60.0,
                ),
                "approved_non_private_classifications": _environment_classifications(source),
                "private_policy": private_policy,
            }
            values.update(overrides)
            return cls.model_validate(values)
        except (TypeError, ValueError):
            raise MistralOcrConfigurationError from None


class MistralOcrClock(Protocol):
    def __call__(self) -> datetime: ...


class MistralOcrIdFactory(Protocol):
    def __call__(self, prefix: str) -> str: ...


class _MistralResponseModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)


class _MistralConfidenceScores(_MistralResponseModel):
    average_page_confidence_score: Confidence01 | None = None
    minimum_page_confidence_score: Confidence01 | None = None


class _MistralPage(_MistralResponseModel):
    index: int
    markdown: str
    confidence_scores: _MistralConfidenceScores | None = None


class _MistralUsage(_MistralResponseModel):
    pages_processed: int | None = None
    doc_size_bytes: int | None = None


class _MistralResponse(_MistralResponseModel):
    model: _ModelName
    pages: list[_MistralPage]
    usage_info: _MistralUsage = Field(default_factory=_MistralUsage)


class _MistralModelCard(_MistralResponseModel):
    id: _ModelName
    aliases: list[_ModelName] = Field(default_factory=list)


def _known_or_unknown_confidence(
    value: float | None,
    *,
    field: str,
) -> KnowledgeValue[Confidence01]:
    if value is None:
        return KnowledgeValue[Confidence01].unknown(f"OCR response omitted {field}")
    return KnowledgeValue[Confidence01].known(value)


def _known_or_unknown_usage(value: int | None, *, field: str) -> KnowledgeValue[int]:
    if value is None:
        return KnowledgeValue[int].unknown(f"OCR response omitted {field}")
    return KnowledgeValue[int].known(value)


class MistralOcrExtractor:
    """Direct, stateless `/v1/ocr` implementation of the PdfExtractor seam."""

    def __init__(
        self,
        *,
        settings: MistralOcrSettings,
        client: httpx.AsyncClient,
        clock: MistralOcrClock,
        id_factory: MistralOcrIdFactory,
    ) -> None:
        self._settings = settings
        self._client = client
        self._clock = clock
        self._id_factory = id_factory

    def _authorize(self, classification: DataClassification) -> None:
        if not self._settings.enabled:
            raise MistralOcrDisabledError
        if classification is DataClassification.RESTRICTED:
            raise MistralOcrPolicyError
        if classification is not DataClassification.PUBLIC:
            if not self._settings.private_policy.permits_private_transfer():
                raise MistralOcrPolicyError
            return
        if classification not in self._settings.approved_non_private_classifications:
            raise MistralOcrPolicyError

    def _validate_input(self, request: PdfExtractionRequest) -> None:
        if not request.content.startswith(b"%PDF-"):
            raise MistralOcrInputError
        if len(request.content) > self._settings.max_input_bytes:
            raise MistralOcrInputError

    def _model_matches_request(self, concrete_model: str) -> bool:
        requested = self._settings.model_alias
        if requested == DEFAULT_MISTRAL_OCR_MODEL:
            return concrete_model == requested or concrete_model.startswith("mistral-ocr-4")
        return concrete_model == requested

    async def _resolve_concrete_model(self, reported_model: str) -> str:
        """Resolve a returned ``latest`` alias without transferring document content again."""

        if not self._model_matches_request(reported_model):
            raise MistralOcrResponseError
        if reported_model != DEFAULT_MISTRAL_OCR_MODEL:
            return reported_model

        try:
            async with self._client.stream(
                "GET",
                f"{MISTRAL_MODELS_ENDPOINT}/{reported_model}",
                headers={"Authorization": f"Bearer {self._settings.api_key.get_secret_value()}"},
                timeout=self._settings.timeout_seconds,
                follow_redirects=False,
            ) as response:
                response_content = await self._read_bounded_response(response)
                status_code = response.status_code
        except (httpx.TimeoutException, httpx.RequestError):
            raise MistralOcrTransportError from None

        if status_code < 200 or status_code >= 300:
            raise MistralOcrHttpError
        try:
            model_card = _MistralModelCard.model_validate_json(response_content)
        except ValueError:
            raise MistralOcrResponseError from None
        concrete_aliases = tuple(
            alias
            for alias in model_card.aliases
            if re.fullmatch(r"mistral-ocr-\d+-\d+", alias) is not None
        )
        if model_card.id != reported_model or len(concrete_aliases) != 1:
            raise MistralOcrResponseError
        concrete_model = concrete_aliases[0]
        if not concrete_model.startswith("mistral-ocr-4"):
            raise MistralOcrResponseError
        return concrete_model

    def _parse_response(
        self,
        payload: object,
        *,
        request: PdfExtractionRequest,
    ) -> PdfExtractionResult:
        try:
            response = _MistralResponse.model_validate(payload)
        except ValueError:
            raise MistralOcrResponseError from None

        if (
            not self._model_matches_request(response.model)
            or not response.pages
            or len(response.pages) > self._settings.max_pages
        ):
            raise MistralOcrResponseError
        indexes = tuple(page.index for page in response.pages)
        if indexes != tuple(range(len(response.pages))):
            raise MistralOcrResponseError
        usage = response.usage_info
        if usage.pages_processed is not None and (
            usage.pages_processed != len(response.pages)
            or usage.pages_processed > self._settings.max_pages
        ):
            raise MistralOcrResponseError
        if usage.doc_size_bytes is not None and usage.doc_size_bytes != len(request.content):
            raise MistralOcrResponseError

        try:
            pages = tuple(
                ExtractedPdfPage(
                    page_index=page.index,
                    locator=f"page:{page.index}",
                    markdown=page.markdown,
                    confidence=PdfPageConfidence(
                        average=_known_or_unknown_confidence(
                            (
                                None
                                if page.confidence_scores is None
                                else page.confidence_scores.average_page_confidence_score
                            ),
                            field="average_page_confidence_score",
                        ),
                        minimum=_known_or_unknown_confidence(
                            (
                                None
                                if page.confidence_scores is None
                                else page.confidence_scores.minimum_page_confidence_score
                            ),
                            field="minimum_page_confidence_score",
                        ),
                    ),
                )
                for page in response.pages
            )
            return PdfExtractionResult(
                extraction_id=self._id_factory("pdf-extraction"),
                source_artifact_id=request.source_artifact_id,
                input_sha256=request.input_sha256,
                extractor_version=MISTRAL_OCR_ADAPTER_VERSION,
                model_version=KnowledgeValue[VersionId].known(response.model),
                extracted_at=self._clock(),
                pages=pages,
                usage=PdfExtractionUsage(
                    pages_processed=_known_or_unknown_usage(
                        usage.pages_processed,
                        field="pages_processed",
                    ),
                    document_size_bytes=_known_or_unknown_usage(
                        usage.doc_size_bytes,
                        field="doc_size_bytes",
                    ),
                ),
            )
        except ValueError:
            raise MistralOcrResponseError from None

    async def _read_bounded_response(self, response: httpx.Response) -> bytes:
        declared_length = response.headers.get("Content-Length")
        if declared_length is not None:
            try:
                declared_bytes = int(declared_length)
            except ValueError:
                raise MistralOcrResponseError from None
            if declared_bytes < 0 or declared_bytes > self._settings.max_response_bytes:
                raise MistralOcrResponseError

        content = bytearray()
        async for chunk in response.aiter_bytes():
            remaining = self._settings.max_response_bytes - len(content)
            if len(chunk) > remaining:
                raise MistralOcrResponseError
            content.extend(chunk)
        return bytes(content)

    async def extract(self, request: PdfExtractionRequest) -> PdfExtractionResult:
        """Authorize, bound, send once, and validate a direct OCR response."""

        self._authorize(request.classification)
        self._validate_input(request)
        encoded_pdf = base64.b64encode(request.content).decode("ascii")
        payload = {
            "model": self._settings.model_alias,
            "document": {
                "type": "document_url",
                "document_url": f"{_PDF_DATA_URL_PREFIX}{encoded_pdf}",
            },
            "include_image_base64": False,
            "confidence_scores_granularity": "page",
            "pages": (
                "0" if self._settings.max_pages == 1 else f"0-{self._settings.max_pages - 1}"
            ),
        }
        try:
            async with self._client.stream(
                "POST",
                MISTRAL_OCR_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {self._settings.api_key.get_secret_value()}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self._settings.timeout_seconds,
                follow_redirects=False,
            ) as response:
                response_content = await self._read_bounded_response(response)
                status_code = response.status_code
        except (httpx.TimeoutException, httpx.RequestError):
            raise MistralOcrTransportError from None

        if status_code < 200 or status_code >= 300:
            raise MistralOcrHttpError
        try:
            response_payload = json.loads(response_content)
        except ValueError:
            raise MistralOcrResponseError from None
        try:
            reported_response = _MistralResponse.model_validate(response_payload)
        except ValueError:
            raise MistralOcrResponseError from None
        concrete_model = await self._resolve_concrete_model(reported_response.model)
        if concrete_model != reported_response.model:
            if not isinstance(response_payload, dict):
                raise MistralOcrResponseError
            response_payload = {**response_payload, "model": concrete_model}
        return self._parse_response(response_payload, request=request)
