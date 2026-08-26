"""Tests for the module entry point that starts the server."""

from pathlib import Path
from typing import Any

import pytest
import uvicorn

from app.__main__ import main
from app.config import get_settings


def test_main_starts_the_server_on_the_configured_address(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The entry point passes the settings through to the server instead of hard coding them."""
    recorded: dict[str, Any] = {}

    def fake_run(target: str, **kwargs: Any) -> None:
        recorded["target"] = target
        recorded.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setenv("SW_DATABASE_PATH", str(tmp_path / "entrypoint.sqlite"))
    monkeypatch.setenv("SW_API_HOST", "0.0.0.0")  # noqa: S104
    monkeypatch.setenv("SW_API_PORT", "9100")
    monkeypatch.setenv("SW_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("SW_APP_ENV", "production")

    get_settings.cache_clear()
    try:
        main()
    finally:
        get_settings.cache_clear()

    assert recorded["target"] == "app.main:app"
    assert recorded["host"] == "0.0.0.0"  # noqa: S104
    assert recorded["port"] == 9100
    assert recorded["log_level"] == "warning"
    assert recorded["reload"] is False


def test_main_enables_reload_in_the_local_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reload is a local convenience, so it must not switch on anywhere else."""
    recorded: dict[str, Any] = {}

    def fake_run(target: str, **kwargs: Any) -> None:
        recorded["target"] = target
        recorded.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setenv("SW_DATABASE_PATH", str(tmp_path / "entrypoint.sqlite"))
    monkeypatch.setenv("SW_APP_ENV", "local")

    get_settings.cache_clear()
    try:
        main()
    finally:
        get_settings.cache_clear()

    assert recorded["reload"] is True


def test_main_migrates_the_database_before_binding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A service that started against an out of date schema would fail on its
    first real request rather than on start, which is harder to diagnose."""
    from app.storage.database import create_database_engine, database_url_for
    from app.storage.migrations import current_revision, head_revision

    database = tmp_path / "migrated.sqlite"
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    monkeypatch.setenv("SW_DATABASE_PATH", str(database))

    get_settings.cache_clear()
    try:
        main()
    finally:
        get_settings.cache_clear()

    engine = create_database_engine(database_url_for(database))
    try:
        assert current_revision(engine) == head_revision(engine)
    finally:
        engine.dispose()
