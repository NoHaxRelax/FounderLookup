"""Configuration regressions for server-only credentials and fail-closed OCR defaults."""

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from founderlookup.api.settings import APISettings, RuntimeEnvironment


def test_local_dotenv_loads_unprefixed_mistral_key_without_enabling_ocr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_key = "fictional-mistral-key-for-settings-test"
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        f"MISTRAL_API_KEY={fake_key}\n",
        encoding="utf-8",
    )

    settings = APISettings()

    assert settings.mistral_api_key is not None
    assert settings.mistral_api_key.get_secret_value() == fake_key
    assert settings.mistral_ocr_enabled is False
    assert settings.demo_seed_enabled is False
    assert fake_key not in repr(settings)


def test_demo_seed_requires_explicit_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FOUNDERLOOKUP_DEMO_SEED_ENABLED", raising=False)
    disabled = APISettings(_env_file=None)  # type: ignore[call-arg]

    monkeypatch.setenv("FOUNDERLOOKUP_DEMO_SEED_ENABLED", "true")
    enabled = APISettings(_env_file=None)  # type: ignore[call-arg]

    assert disabled.demo_seed_enabled is False
    assert enabled.demo_seed_enabled is True


def test_production_demo_seed_requires_separate_explicit_acknowledgement() -> None:
    with pytest.raises(ValidationError, match="demo-only acknowledgement"):
        APISettings(  # type: ignore[call-arg]
            _env_file=None,
            environment=RuntimeEnvironment.PRODUCTION,
            demo_seed_enabled=True,
        )
    settings = APISettings(  # type: ignore[call-arg]
        _env_file=None,
        environment=RuntimeEnvironment.PRODUCTION,
        demo_seed_enabled=True,
        demo_seed_production_acknowledged=True,
    )
    assert settings.demo_seed_enabled is True


def test_tavily_is_disabled_by_default_and_enabled_mode_requires_key() -> None:
    disabled = APISettings(_env_file=None)  # type: ignore[call-arg]
    assert disabled.tavily_enabled is False
    assert disabled.tavily_api_key is None

    with pytest.raises(ValidationError, match="server-side API key"):
        APISettings(_env_file=None, tavily_enabled=True)  # type: ignore[call-arg]

    enabled = APISettings(  # type: ignore[call-arg]
        _env_file=None,
        tavily_enabled=True,
        tavily_api_key=SecretStr("fictional-settings-tavily-key"),
        tavily_allowed_domains="example.com, research.example.org ",
    )
    assert enabled.tavily_allowed_domain_list == (
        "example.com",
        "research.example.org",
    )
    assert "fictional-settings-tavily-key" not in repr(enabled)


def test_openai_is_explicitly_enabled_and_private_demo_use_is_separately_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    disabled = APISettings(_env_file=None)  # type: ignore[call-arg]
    assert disabled.openai_structured_enabled is False
    assert disabled.openai_inbound_enabled is False
    assert disabled.openai_api_key is None
    assert disabled.openai_model == "gpt-5.6-luna"

    with pytest.raises(ValidationError, match="server-side API key"):
        APISettings(_env_file=None, openai_structured_enabled=True)  # type: ignore[call-arg]
    with pytest.raises(ValidationError, match="risk acceptance"):
        APISettings(  # type: ignore[call-arg]
            _env_file=None,
            openai_allow_private=True,
        )
    with pytest.raises(ValidationError, match="server-side API key"):
        APISettings(_env_file=None, openai_inbound_enabled=True)  # type: ignore[call-arg]
    with pytest.raises(ValidationError, match="private processing"):
        APISettings(  # type: ignore[call-arg]
            _env_file=None,
            openai_inbound_enabled=True,
            openai_api_key=SecretStr("fictional-openai-settings-key"),
        )

    enabled = APISettings(  # type: ignore[call-arg]
        _env_file=None,
        openai_structured_enabled=True,
        openai_inbound_enabled=True,
        openai_api_key=SecretStr("fictional-openai-settings-key"),
        openai_allow_private=True,
        openai_hackathon_private_risk_accepted=True,
    )
    assert enabled.openai_structured_enabled is True
    assert enabled.openai_inbound_enabled is True
    assert enabled.openai_inbound_max_model_calls == 5
    assert "fictional-openai-settings-key" not in repr(enabled)


def test_public_source_adapters_are_independent_opt_ins_with_server_only_token() -> None:
    disabled = APISettings(_env_file=None)  # type: ignore[call-arg]
    assert disabled.github_enabled is False
    assert disabled.hackernews_enabled is False
    assert disabled.openalex_enabled is False
    assert disabled.semantic_scholar_enabled is False
    assert disabled.patentsview_enabled is False
    assert disabled.github_token is None

    enabled = APISettings(  # type: ignore[call-arg]
        _env_file=None,
        github_enabled=True,
        hackernews_enabled=True,
        openalex_enabled=True,
        semantic_scholar_enabled=True,
        patentsview_enabled=True,
        github_token=SecretStr("fictional-github-token"),
        sourcing_cache_ttl_seconds=1_800,
    )
    assert enabled.github_enabled is True
    assert enabled.sourcing_cache_ttl_seconds == 1_800
    assert "fictional-github-token" not in repr(enabled)


def test_sourcing_coordinator_rejects_an_incoherent_page_budget() -> None:
    with pytest.raises(ValidationError, match="sourcing max pages"):
        APISettings(  # type: ignore[call-arg]
            _env_file=None,
            sourcing_max_results=2,
            sourcing_max_pages=3,
        )


def test_environment_and_log_level_are_validated_and_normalized() -> None:
    settings = APISettings(  # type: ignore[call-arg]
        _env_file=None,
        environment=RuntimeEnvironment.TEST,
        log_level="warning",
    )
    assert settings.environment is RuntimeEnvironment.TEST
    assert settings.log_level == "WARNING"

    with pytest.raises(ValidationError, match="log level"):
        APISettings(_env_file=None, log_level="VERBOSE")  # type: ignore[call-arg]


def test_checked_env_example_keys_map_to_settings_and_example_parses() -> None:
    example = Path(__file__).parents[2] / ".env.example"
    keys = {
        line.split("=", 1)[0]
        for raw in example.read_text(encoding="utf-8").splitlines()
        if (line := raw.strip()) and not line.startswith("#") and "=" in line
    }
    accepted_keys: set[str] = set()
    for name, field in APISettings.model_fields.items():
        alias = field.validation_alias
        if isinstance(alias, str):
            accepted_keys.add(alias)
        else:
            accepted_keys.add(f"FOUNDERLOOKUP_{name.upper()}")

    assert keys <= accepted_keys
    settings = APISettings(_env_file=example)  # type: ignore[call-arg]
    assert settings.environment is RuntimeEnvironment.DEVELOPMENT
    assert settings.tavily_enabled is False
    assert settings.mistral_ocr_enabled is False
