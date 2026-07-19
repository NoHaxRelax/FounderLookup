"""Configuration regressions for server-only credentials and fail-closed OCR defaults."""

from pathlib import Path

import pytest

from founderlookup.api.settings import APISettings


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
    assert fake_key not in repr(settings)
