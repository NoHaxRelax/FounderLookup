"""Fail-closed API configuration loaded from environment or local ``.env``."""

from __future__ import annotations

import secrets
from functools import cached_property
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class APISettings(BaseSettings):
    """Security-sensitive values stay redacted and never enter OpenAPI models."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="FOUNDERLOOKUP_",
        extra="ignore",
        frozen=True,
        populate_by_name=True,
    )

    data_dir: Path = Path(".data")
    investor_api_key: SecretStr | None = None
    founder_status_pepper: SecretStr | None = None
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    intake_rate_limit: int = Field(default=10, gt=0, le=10_000)
    status_rate_limit: int = Field(default=60, gt=0, le=10_000)
    rate_limit_window_seconds: int = Field(default=60, gt=0, le=3_600)
    maximum_deck_bytes: int = Field(default=10 * 1024 * 1024, gt=0, le=50 * 1024 * 1024)
    maximum_collection_results: int = Field(default=100, gt=0, le=1_000)
    maximum_retry_attempts: int = Field(default=3, gt=0, le=10)

    # OCR stays part of the composition root: the API never exposes these values.
    mistral_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="MISTRAL_API_KEY",
    )
    mistral_ocr_enabled: bool = False
    mistral_ocr_model: str = "mistral-ocr-latest"
    mistral_ocr_max_input_bytes: int = Field(default=20_000_000, gt=0)
    mistral_ocr_max_response_bytes: int = Field(default=20_000_000, gt=0)
    mistral_ocr_max_pages: int = Field(default=50, gt=0, le=1_000)
    mistral_ocr_timeout_seconds: float = Field(default=60.0, gt=0.0, le=300.0)
    mistral_ocr_approved_non_private_classifications: str = "public"
    mistral_ocr_allow_private: bool = False
    mistral_ocr_training_opt_out_confirmed: bool = False
    mistral_ocr_retention_posture: str = "unconfirmed"
    mistral_ocr_region: str | None = None
    mistral_ocr_region_confirmed: bool = False
    mistral_ocr_purpose: str | None = None
    mistral_ocr_purpose_confirmed: bool = False

    @field_validator("cors_origins")
    @classmethod
    def validate_origins(cls, value: str) -> str:
        origins = tuple(item.strip() for item in value.split(",") if item.strip())
        if not origins:
            raise ValueError("at least one explicit CORS origin is required")
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                origin == "*"
                or parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("CORS origins must be explicit HTTP(S) origins")
        return ",".join(origins)

    @cached_property
    def cors_allowed_origins(self) -> tuple[str, ...]:
        return tuple(self.cors_origins.split(","))

    def resolved_investor_token(self) -> str:
        """Missing configuration becomes an inaccessible ephemeral credential."""

        if self.investor_api_key is not None:
            return self.investor_api_key.get_secret_value()
        return secrets.token_urlsafe(48)

    def resolved_status_pepper(self) -> bytes:
        if self.founder_status_pepper is not None:
            return self.founder_status_pepper.get_secret_value().encode("utf-8")
        return secrets.token_bytes(32)
