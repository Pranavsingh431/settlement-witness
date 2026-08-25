"""Fixtures for ingestion and storage tests."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.storage.database import create_database_engine, create_schema, database_url_for

FIXED_NOW = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)
"""One observed-at time for every test, so a comparison never fails on the clock."""

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "data" / "fixtures" / "ingestion"
"""The documented example documents, shared with `make import-fixtures`."""


def read_fixture(name: str) -> bytes:
    """Return one fixture document as raw bytes."""
    return (FIXTURE_DIR / name).read_bytes()


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    """Return a path for a SQLite file that this test alone uses."""
    return tmp_path / "settlement.sqlite"


@pytest.fixture
def engine(database_path: Path) -> Iterator[Engine]:
    """Return an engine on a fresh file backed database with its schema created."""
    created = create_database_engine(database_url_for(database_path))
    create_schema(created)
    try:
        yield created
    finally:
        created.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """Return a session that commits at the end of a successful test."""
    from app.storage.database import session_factory

    opened = session_factory(engine)()
    try:
        yield opened
        opened.commit()
    finally:
        opened.close()
