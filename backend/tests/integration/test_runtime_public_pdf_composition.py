"""Composition contract for the separately allowlisted outbound public-PDF path."""

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from founderlookup.api.settings import APISettings
from founderlookup.runtime import create_runtime_app


def _settings(data_dir: Path, **overrides: object) -> APISettings:
    values: dict[str, object] = {
        "_env_file": None,
        "data_dir": data_dir,
        "tavily_enabled": True,
        "tavily_api_key": SecretStr("fixture-tavily-key"),
    }
    values.update(overrides)
    return APISettings(**values)  # type: ignore[arg-type]


def test_public_pdf_policy_is_independent_from_broad_tavily_search(tmp_path: Path) -> None:
    broad_search = create_runtime_app(settings=_settings(tmp_path / "broad"))
    assert broad_search.state.enabled_sourcing_adapters == ("tavily-web-v0",)
    assert broad_search.state.public_pdf_acquisition_enabled is False
    assert broad_search.state.public_pdf_ocr_enabled is False

    allowlisted = create_runtime_app(
        settings=_settings(
            tmp_path / "allowlisted",
            public_pdf_allowed_domains="docs.google.com, googleusercontent.com",
            public_pdf_excluded_domains="drive.google.com",
            public_pdf_max_bytes=5_000_000,
            mistral_api_key=SecretStr("fixture-mistral-key"),
            mistral_ocr_enabled=True,
        )
    )
    assert allowlisted.state.public_pdf_acquisition_enabled is True
    assert allowlisted.state.public_pdf_ocr_enabled is True


def test_public_pdf_settings_normalize_domains_and_reject_overlap(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        public_pdf_allowed_domains="docs.google.com, GOOGLEUSERCONTENT.COM ",
        public_pdf_excluded_domains="evil.example",
    )
    assert settings.public_pdf_allowed_domain_list == (
        "docs.google.com",
        "googleusercontent.com",
    )
    assert settings.public_pdf_excluded_domain_list == ("evil.example",)

    with pytest.raises(ValidationError, match="public PDF domain"):
        _settings(
            tmp_path / "invalid",
            public_pdf_allowed_domains="docs.google.com",
            public_pdf_excluded_domains="docs.google.com",
        )
