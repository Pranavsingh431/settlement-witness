"""Tests for settings loading and validation."""

import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings


def test_settings_use_documented_defaults() -> None:
    """With no overrides the settings match the values documented in .env.example."""
    settings = Settings()

    assert settings.app_env == "local"
    assert settings.log_level == "INFO"
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8000
    assert settings.max_upload_bytes == 8 * 1024 * 1024


def test_prefixed_environment_variables_override_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Variables prefixed with SW_ take priority over the defaults."""
    monkeypatch.setenv("SW_APP_ENV", "ci")
    monkeypatch.setenv("SW_API_PORT", "9001")

    settings = Settings()

    assert settings.app_env == "ci"
    assert settings.api_port == 9001


def test_the_upload_limit_can_be_lowered(monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator on a small machine can shrink what one request may cost."""
    monkeypatch.setenv("SW_MAX_UPLOAD_BYTES", "65536")

    assert Settings().max_upload_bytes == 65536


def test_an_upload_limit_of_zero_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A limit no document can meet would refuse every import at startup instead."""
    monkeypatch.setenv("SW_MAX_UPLOAD_BYTES", "0")

    with pytest.raises(ValidationError, match="max_upload_bytes"):
        Settings()


def test_an_out_of_range_port_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """An impossible port fails at startup instead of failing when the socket binds."""
    monkeypatch.setenv("SW_API_PORT", "70000")

    with pytest.raises(ValidationError):
        Settings()


def test_an_unknown_environment_name_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the four named environments are accepted."""
    monkeypatch.setenv("SW_APP_ENV", "staging")

    with pytest.raises(ValidationError):
        Settings()


def test_unrelated_environment_variables_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Variables that belong to other tools do not break settings loading."""
    monkeypatch.setenv("SW_SOMETHING_A_LATER_PHASE_ADDS", "value")

    assert Settings().app_env == "local"


def test_get_settings_resolves_once_per_process() -> None:
    """Settings are cached so that every caller sees the same object."""
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()
