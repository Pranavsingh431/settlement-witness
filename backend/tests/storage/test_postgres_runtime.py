"""Tests for the managed PostgreSQL runtime path.

These tests never connect to a database. They hold the branch selection and
the advisory-lock protocol independently of a particular hosted provider; the
real provider URL stays only in Vercel's environment store.
"""

from typing import Self, cast

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

from app.storage.database import create_database_engine
from app.storage.migrations import adopt_if_legacy, upgrade_to_head


class _PostgresDialect:
    name = "postgresql"


class _RecordingConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.commit_count = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        return None

    def execute(self, statement: object) -> None:
        self.statements.append(str(statement))

    def commit(self) -> None:
        self.commit_count += 1


class _PostgresEngine:
    dialect = _PostgresDialect()

    def __init__(self) -> None:
        self.connection = _RecordingConnection()

    def connect(self) -> _RecordingConnection:
        return self.connection


def test_a_postgres_url_builds_a_postgres_engine_without_sqlite_hooks() -> None:
    """The driver is importable before any hosted database connection is opened."""
    engine = create_database_engine("postgresql+psycopg://operator@example.test/neondb")
    try:
        assert engine.dialect.name == "postgresql"
    finally:
        engine.dispose()


def test_postgres_is_never_considered_a_legacy_sqlite_database() -> None:
    """A remote database can migrate fresh, but must never be guessed and stamped."""
    adopt_if_legacy(cast(Engine, _PostgresEngine()))


def test_postgres_migrations_hold_one_advisory_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent cold starts wait for one schema upgrade rather than racing it."""
    engine = _PostgresEngine()
    seen: list[Config] = []

    def record_upgrade(config: Config, revision: str) -> None:
        assert revision == "head"
        seen.append(config)

    monkeypatch.setattr(command, "upgrade", record_upgrade)

    upgrade_to_head(cast(Engine, engine))

    assert len(seen) == 1
    assert engine.connection.commit_count == 2
    assert "pg_advisory_lock" in engine.connection.statements[0]
    assert "pg_advisory_unlock" in engine.connection.statements[1]
