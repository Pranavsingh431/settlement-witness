"""Tests for the migration workflow.

The point of migrations is that an existing database can be brought forward
without losing rows. So the tests build the older schema, put data in it, and
then upgrade, because that is the case a migration exists for. Creating the
latest schema from nothing would not exercise anything.
"""

from pathlib import Path

import pytest
from sqlalchemy import Engine, Executable, delete, inspect, update
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from app.domain.facts import SourceRecordType
from app.storage.database import (
    APPEND_ONLY_TABLES,
    create_database_engine,
    database_url_for,
    session_factory,
)
from app.storage.migrations import current_revision, head_revision, upgrade_to, upgrade_to_head
from app.storage.models import Base, ReconciliationDecisionRow, ReconciliationRunRow
from app.storage.repository import ImportReceiptRepository, SourceFactRepository
from tests.api.conftest import FIXTURE_DOCUMENTS, import_fixtures

INITIAL = "0001_initial_schema"


def execute_and_commit(session: Session, statement: Executable) -> None:
    """Run one statement and commit it, so a raises block holds a single call."""
    session.execute(statement)
    session.commit()


class TestUpgradingFromNothing:
    """A clean database reaches head."""

    def test_a_new_database_is_stamped_at_head(self, tmp_path: Path) -> None:
        """Which is what makes the next migration have something to work from."""
        engine = create_database_engine(database_url_for(tmp_path / "new.sqlite"))
        upgrade_to_head(engine)

        assert current_revision(engine) == head_revision(engine)
        engine.dispose()

    def test_upgrading_again_changes_nothing(self, tmp_path: Path) -> None:
        """Safe to run on every start, like the setup command it replaced."""
        engine = create_database_engine(database_url_for(tmp_path / "twice.sqlite"))
        upgrade_to_head(engine)
        upgrade_to_head(engine)

        assert current_revision(engine) == head_revision(engine)
        engine.dispose()

    def test_every_table_the_models_declare_exists(self, tmp_path: Path) -> None:
        """The migrations and the ORM metadata must not drift apart.

        A table declared in models.py but never migrated would work in tests
        that build a schema from metadata and fail against a real database.
        """
        engine = create_database_engine(database_url_for(tmp_path / "tables.sqlite"))
        upgrade_to_head(engine)

        present = set(inspect(engine).get_table_names())
        assert set(Base.metadata.tables) <= present
        engine.dispose()

    def test_every_append_only_table_is_protected(self, tmp_path: Path) -> None:
        """Two triggers each, created by the migrations rather than separately."""
        engine = create_database_engine(database_url_for(tmp_path / "triggers.sqlite"))
        upgrade_to_head(engine)

        with engine.connect() as connection:
            triggers = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                )
            }
        engine.dispose()

        expected = {
            f"trg_{table}_no_{operation}"
            for table in APPEND_ONLY_TABLES
            for operation in ("update", "delete")
        }
        assert triggers == expected


class TestUpgradingAnExistingDatabase:
    """The case migrations exist for: data already in the older schema."""

    @staticmethod
    def _initial_with_data(path: Path) -> Engine:
        """Return an engine at the initial revision, holding imported facts."""
        engine = create_database_engine(database_url_for(path))
        upgrade_to(engine, INITIAL)
        import_fixtures(engine, FIXTURE_DOCUMENTS)
        return engine

    def test_the_initial_schema_has_no_run_tables(self, tmp_path: Path) -> None:
        """Confirms the starting point is genuinely the older one."""
        engine = create_database_engine(database_url_for(tmp_path / "old.sqlite"))
        upgrade_to(engine, INITIAL)

        present = set(inspect(engine).get_table_names())
        assert "source_facts" in present
        assert "reconciliation_runs" not in present
        engine.dispose()

    def test_source_facts_survive_the_upgrade(self, tmp_path: Path) -> None:
        """Every row, unchanged, not merely the same count."""
        engine = self._initial_with_data(tmp_path / "upgrade.sqlite")
        with session_factory(engine)() as session:
            before = SourceFactRepository(session).all_facts()

        upgrade_to_head(engine)

        with session_factory(engine)() as session:
            after = SourceFactRepository(session).all_facts()
        engine.dispose()

        assert len(before) == 10
        assert after == before

    def test_import_receipts_survive_the_upgrade(self, tmp_path: Path) -> None:
        """The audit trail is as much a record as the facts are."""
        engine = self._initial_with_data(tmp_path / "receipts.sqlite")
        with session_factory(engine)() as session:
            before = [
                (row.receipt_id, row.outcome, row.document_hash)
                for row in ImportReceiptRepository(session).all_receipts()
            ]

        upgrade_to_head(engine)

        with session_factory(engine)() as session:
            after = [
                (row.receipt_id, row.outcome, row.document_hash)
                for row in ImportReceiptRepository(session).all_receipts()
            ]
        engine.dispose()

        assert len(before) == 3
        assert after == before

    def test_the_run_tables_appear_after_the_upgrade(self, tmp_path: Path) -> None:
        """The whole reason for the second revision."""
        engine = self._initial_with_data(tmp_path / "appear.sqlite")
        upgrade_to_head(engine)

        present = set(inspect(engine).get_table_names())
        engine.dispose()

        assert {"reconciliation_runs", "reconciliation_decisions"} <= present

    def test_importing_still_works_after_the_upgrade(self, tmp_path: Path) -> None:
        """An upgraded database is a working database, not just a preserved one."""
        engine = self._initial_with_data(tmp_path / "still.sqlite")
        upgrade_to_head(engine)

        import_fixtures(engine, (("payment_events.csv", SourceRecordType.PAYMENT_EVENT),))

        with session_factory(engine)() as session:
            assert SourceFactRepository(session).count() == 10
        engine.dispose()

    def test_the_upgraded_database_is_stamped_at_head(self, tmp_path: Path) -> None:
        """So the next migration knows where it is starting from."""
        engine = self._initial_with_data(tmp_path / "stamp.sqlite")
        assert current_revision(engine) == INITIAL

        upgrade_to_head(engine)

        assert current_revision(engine) == head_revision(engine)
        engine.dispose()


class TestRunTablesAreAppendOnly:
    """The new tables get the same protection as the old ones.

    Issued as SQL against the tables, so a refusal comes from the database and
    not from a repository method being absent.
    """

    @pytest.fixture
    def engine_with_a_run(self, tmp_path: Path) -> Engine:
        """Return an engine holding one persisted run."""
        from app.reconciliation.runs import ReconciliationRunService
        from app.storage.database import session_scope

        engine = create_database_engine(database_url_for(tmp_path / "runs.sqlite"))
        upgrade_to_head(engine)
        import_fixtures(engine, FIXTURE_DOCUMENTS)
        with session_scope(engine) as session:
            ReconciliationRunService(session).create_run(SourceFactRepository(session).fact_index())
        return engine

    def test_updating_a_run_is_refused(self, engine_with_a_run: Engine) -> None:
        """A conclusion that can be edited in place is not an audit trail."""
        with session_factory(engine_with_a_run)() as session:
            with pytest.raises(DatabaseError, match="reconciliation_runs is append-only"):
                execute_and_commit(session, update(ReconciliationRunRow).values(fact_count=0))
            session.rollback()

    def test_deleting_a_run_is_refused(self, engine_with_a_run: Engine) -> None:
        """A run cannot be made to disappear."""
        with session_factory(engine_with_a_run)() as session:
            with pytest.raises(DatabaseError, match="reconciliation_runs is append-only"):
                execute_and_commit(session, delete(ReconciliationRunRow))
            session.rollback()

    def test_updating_a_decision_is_refused(self, engine_with_a_run: Engine) -> None:
        """Editing a stored status would rewrite what was concluded."""
        with session_factory(engine_with_a_run)() as session:
            with pytest.raises(DatabaseError, match="reconciliation_decisions is append-only"):
                execute_and_commit(
                    session, update(ReconciliationDecisionRow).values(status="RESOLVED")
                )
            session.rollback()

    def test_deleting_a_decision_is_refused(self, engine_with_a_run: Engine) -> None:
        """Nor removed."""
        with session_factory(engine_with_a_run)() as session:
            with pytest.raises(DatabaseError, match="reconciliation_decisions is append-only"):
                execute_and_commit(session, delete(ReconciliationDecisionRow))
            session.rollback()

    def test_a_refused_update_changes_nothing(self, engine_with_a_run: Engine) -> None:
        """The refusal is not partial."""
        from app.reconciliation.runs import ReconciliationRunRepository

        with session_factory(engine_with_a_run)() as session:
            before = ReconciliationRunRepository(session).list_runs(limit=10, offset=0)

        with session_factory(engine_with_a_run)() as session:
            with pytest.raises(DatabaseError):
                execute_and_commit(session, update(ReconciliationRunRow).values(fact_count=0))
            session.rollback()

        with session_factory(engine_with_a_run)() as session:
            after = ReconciliationRunRepository(session).list_runs(limit=10, offset=0)

        assert after == before
