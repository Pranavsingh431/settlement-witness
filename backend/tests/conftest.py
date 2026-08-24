"""Shared pytest fixtures for the backend test suite."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from the developer machine.

    Two things would otherwise leak in: ``SW_`` variables already exported in the
    shell, and the repository root ``.env`` file that ``make setup`` creates.
    Both are removed here, so the suite behaves the same on a laptop and in CI.
    """
    for name in list(os.environ):
        if name.startswith("SW_"):
            monkeypatch.delenv(name, raising=False)

    monkeypatch.setitem(Settings.model_config, "env_file", tmp_path / "no-such.env")


@pytest.fixture
def settings() -> Settings:
    """Return fixed settings so that tests never depend on the local machine."""
    return Settings(
        app_env="test",
        log_level="INFO",
        api_host="127.0.0.1",
        api_port=8000,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """Return a test client bound to a freshly built application."""
    with TestClient(create_app(settings)) as test_client:
        yield test_client
