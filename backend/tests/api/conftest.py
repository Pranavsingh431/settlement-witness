"""Fixtures for API and run persistence tests."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.config import Settings
from app.domain.facts import SourceRecordType, SourceSystem
from app.ingestion.service import ImportService
from app.main import create_app
from app.storage.database import (
    create_database_engine,
    create_schema,
    database_url_for,
    session_scope,
)
from tests.ingestion.conftest import FIXED_NOW, read_fixture

FIXTURE_DOCUMENTS: tuple[tuple[str, SourceRecordType], ...] = (
    ("payment_events.csv", SourceRecordType.PAYMENT_EVENT),
    ("settlement_lines.csv", SourceRecordType.SETTLEMENT_LINE),
    ("payouts.csv", SourceRecordType.PAYOUT),
)


def import_fixtures(engine: Engine, documents: tuple[tuple[str, SourceRecordType], ...]) -> None:
    """Import the given example documents into a database."""
    with session_scope(engine) as session:
        service = ImportService(session, now=FIXED_NOW)
        for file_name, record_type in documents:
            service.import_document(
                read_fixture(file_name),
                source_system=SourceSystem.PSP_API,
                record_type=record_type,
                document_name=file_name,
            )


@pytest.fixture
def api_engine(tmp_path: Path) -> Iterator[Engine]:
    """Return an engine on a fresh migrated database, with no facts yet."""
    engine = create_database_engine(database_url_for(tmp_path / "api.sqlite"))
    create_schema(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def loaded_engine(api_engine: Engine) -> Engine:
    """Return an engine with the three example documents imported."""
    import_fixtures(api_engine, FIXTURE_DOCUMENTS)
    return api_engine


@pytest.fixture
def client(loaded_engine: Engine) -> Iterator[TestClient]:
    """Return a client bound to a database holding the example facts."""
    with TestClient(create_app(Settings(app_env="test"), engine=loaded_engine)) as opened:
        yield opened


@pytest.fixture
def empty_client(api_engine: Engine) -> Iterator[TestClient]:
    """Return a client bound to a migrated database with no facts."""
    with TestClient(create_app(Settings(app_env="test"), engine=api_engine)) as opened:
        yield opened
