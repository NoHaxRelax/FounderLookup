"""Fail-closed API configuration loaded from environment or local ``.env``."""

from __future__ import annotations

import secrets
from enum import StrEnum
from functools import cached_property
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeEnvironment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


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

    environment: RuntimeEnvironment = Field(
        default=RuntimeEnvironment.DEVELOPMENT,
        validation_alias="FOUNDERLOOKUP_ENV",
    )
    log_level: str = Field(default="INFO", validation_alias="FOUNDERLOOKUP_LOG_LEVEL")
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
    demo_seed_enabled: bool = False
    demo_seed_production_acknowledged: bool = False

    # Tavily is an explicit public-web sourcing opt-in; a key alone never enables it.
    tavily_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="TAVILY_API_KEY",
    )
    tavily_enabled: bool = False
    tavily_max_queries: int = Field(default=1, gt=0, le=4)
    tavily_max_results: int = Field(default=10, gt=0, le=20)
    tavily_max_pages: int = Field(default=5, gt=0, le=20)
    tavily_max_content_bytes: int = Field(default=500_000, gt=0, le=5_000_000)
    tavily_max_response_bytes: int = Field(default=2_000_000, gt=0, le=10_000_000)
    tavily_timeout_seconds: float = Field(default=20.0, ge=1.0, le=60.0)
    tavily_search_depth: Literal["advanced", "basic", "fast", "ultra-fast"] = "advanced"
    tavily_extract_depth: Literal["advanced", "basic"] = "advanced"
    tavily_allowed_domains: str = ""
    tavily_excluded_domains: str = "linkedin.com,facebook.com,instagram.com,x.com,twitter.com"

    # The coordinator applies these ceilings across every enabled public-source adapter.
    sourcing_max_results: int = Field(default=10, gt=0, le=20)
    sourcing_max_pages: int = Field(default=5, gt=0, le=20)
    sourcing_max_content_bytes: int = Field(default=500_000, gt=0, le=5_000_000)
    sourcing_timeout_seconds: float = Field(default=20.0, ge=1.0, le=60.0)
    sourcing_cache_ttl_seconds: int = Field(default=900, ge=0, le=86_400)
    sourcing_max_follow_up_rounds: int = Field(default=1, ge=0, le=3)
    sourcing_max_discovery_calls: int = Field(default=12, ge=1, le=32)

    # Direct public pitch-deck bytes use a separate, explicit host policy. Broad Tavily
    # search never implicitly authorizes a linked URL for PDF download or OCR.
    public_pdf_allowed_domains: str = ""
    public_pdf_excluded_domains: str = ""
    public_pdf_max_bytes: int = Field(default=5_000_000, gt=0, le=10 * 1024 * 1024)
    public_pdf_timeout_seconds: float = Field(default=30.0, ge=1.0, le=60.0)
    public_pdf_max_redirects: int = Field(default=5, ge=0, le=5)

    # OpenAI is an explicit structured-intelligence opt-in; a key alone never enables it.
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="OPENAI_API_KEY",
    )
    openai_structured_enabled: bool = False
    openai_model: str = "gpt-5.6-luna"
    openai_max_input_bytes: int = Field(default=200_000, gt=0, le=500_000)
    openai_max_output_tokens: int = Field(default=2_000, gt=0, le=10_000)
    openai_max_response_bytes: int = Field(default=1_000_000, gt=0, le=5_000_000)
    openai_timeout_seconds: float = Field(default=30.0, ge=1.0, le=120.0)
    openai_allow_private: bool = False
    openai_hackathon_private_risk_accepted: bool = False
    # Full inbound intelligence is a separate founder-private opt-in. It never follows
    # from a key or public structured-extraction enablement alone.
    openai_inbound_enabled: bool = False
    openai_inbound_effort: Literal["minimal", "low", "medium", "high"] = "low"
    openai_inbound_max_model_calls: int = Field(default=5, ge=1, le=5)
    openai_inbound_stage_timeout_seconds: float = Field(default=30.0, ge=1.0, le=120.0)
    openai_inbound_total_timeout_seconds: float = Field(default=100.0, ge=1.0, le=300.0)

    # Source-specific adapters are independently opt-in. GitHub authentication is optional
    # and remains server-side; the other P0 public APIs are keyless.
    github_enabled: bool = False
    github_token: SecretStr | None = Field(default=None, validation_alias="GITHUB_TOKEN")
    hackernews_enabled: bool = False
    openalex_enabled: bool = False
    semantic_scholar_enabled: bool = False
    patentsview_enabled: bool = False

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
    mistral_ocr_hackathon_private_risk_accepted: bool = False

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("log level must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        return normalized

    @field_validator("github_token", mode="before")
    @classmethod
    def normalize_optional_github_token(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "tavily_allowed_domains",
        "tavily_excluded_domains",
        "public_pdf_allowed_domains",
        "public_pdf_excluded_domains",
    )
    @classmethod
    def validate_domain_csv(cls, value: str) -> str:
        domains = tuple(item.strip().casefold().rstrip(".") for item in value.split(","))
        normalized = tuple(item for item in domains if item)
        if any("://" in item or "/" in item or "@" in item or ":" in item for item in normalized):
            raise ValueError("Tavily domain policy entries must be bare hostnames")
        return ",".join(dict.fromkeys(normalized))

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

    @model_validator(mode="after")
    def validate_runtime_policy(self) -> Self:
        if (
            self.environment is RuntimeEnvironment.PRODUCTION
            and self.demo_seed_enabled
            and not self.demo_seed_production_acknowledged
        ):
            raise ValueError(
                "production demo seeding requires an explicit demo-only acknowledgement"
            )
        if self.tavily_enabled and (
            self.tavily_api_key is None or not self.tavily_api_key.get_secret_value().strip()
        ):
            raise ValueError("Tavily must have a server-side API key when enabled")
        if self.tavily_max_pages > self.tavily_max_results:
            raise ValueError("Tavily max pages cannot exceed max results")
        if self.sourcing_max_pages > self.sourcing_max_results:
            raise ValueError("sourcing max pages cannot exceed max results")
        if self.openai_structured_enabled and (
            self.openai_api_key is None or not self.openai_api_key.get_secret_value().strip()
        ):
            raise ValueError("OpenAI must have a server-side API key when enabled")
        if self.openai_inbound_enabled and (
            self.openai_api_key is None or not self.openai_api_key.get_secret_value().strip()
        ):
            raise ValueError("OpenAI inbound intelligence requires a server-side API key")
        if not self.openai_model.strip():
            raise ValueError("OpenAI model must be non-blank")
        if self.openai_allow_private and not self.openai_hackathon_private_risk_accepted:
            raise ValueError(
                "OpenAI private processing requires explicit hackathon risk acceptance"
            )
        if self.openai_inbound_enabled and not (
            self.openai_allow_private and self.openai_hackathon_private_risk_accepted
        ):
            raise ValueError(
                "OpenAI inbound intelligence requires explicit private processing and "
                "hackathon risk acceptance"
            )
        if self.openai_inbound_total_timeout_seconds < self.openai_inbound_stage_timeout_seconds:
            raise ValueError("OpenAI inbound total timeout cannot be shorter than one stage")
        if self.github_token is not None and not self.github_token.get_secret_value().strip():
            raise ValueError("GitHub token must be non-blank when supplied")
        allowed = set(self.tavily_allowed_domain_list)
        excluded = set(self.tavily_excluded_domain_list)
        if allowed & excluded:
            raise ValueError("a Tavily domain cannot be both allowed and excluded")
        public_pdf_allowed = set(self.public_pdf_allowed_domain_list)
        public_pdf_excluded = set(self.public_pdf_excluded_domain_list)
        if public_pdf_allowed & public_pdf_excluded:
            raise ValueError("a public PDF domain cannot be both allowed and excluded")
        return self

    @cached_property
    def cors_allowed_origins(self) -> tuple[str, ...]:
        return tuple(self.cors_origins.split(","))

    @cached_property
    def tavily_allowed_domain_list(self) -> tuple[str, ...]:
        return tuple(item for item in self.tavily_allowed_domains.split(",") if item)

    @cached_property
    def tavily_excluded_domain_list(self) -> tuple[str, ...]:
        return tuple(item for item in self.tavily_excluded_domains.split(",") if item)

    @cached_property
    def public_pdf_allowed_domain_list(self) -> tuple[str, ...]:
        return tuple(item for item in self.public_pdf_allowed_domains.split(",") if item)

    @cached_property
    def public_pdf_excluded_domain_list(self) -> tuple[str, ...]:
        return tuple(item for item in self.public_pdf_excluded_domains.split(",") if item)

    def resolved_investor_token(self) -> str:
        """Missing configuration becomes an inaccessible ephemeral credential."""

        if self.investor_api_key is not None:
            return self.investor_api_key.get_secret_value()
        return secrets.token_urlsafe(48)

    def resolved_status_pepper(self) -> bytes:
        if self.founder_status_pepper is not None:
            return self.founder_status_pepper.get_secret_value().encode("utf-8")
        return secrets.token_bytes(32)
