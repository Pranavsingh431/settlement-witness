"""Tests for adopting a database built before migrations existed.

The real starting point in a deployment is not a database stamped at the
initial revision. It is one built by the Phase 2 `create_all` call, which left
no `alembic_version` table at all. Building the older schema with
`upgrade_to(engine, "0001_initial_schema")` stamps it, so those tests never
touch this path, and the failure it hides is total: the first migration tries
to create a table that is already there and the application cannot start.

So the databases here are built the way Phase 2 built them, with `create_all`
and raw trigger DDL, and never through the migrations.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.orm import Session

from app.domain.facts import SourceRecordType
from app.reconciliation.runs import ReconciliationRunService
from app.storage.database import (
    APPEND_ONLY_TABLES,
    create_database_engine,
    create_schema,
    database_url_for,
    session_factory,
    session_scope,
)
from app.storage.legacy import (
    LEGACY_REVISION,
    LEGACY_TRIGGERS,
    AdoptionPlan,
    UnrecognisedSchemaError,
    legacy_schema_differences,
    plan_adoption,
)
from app.storage.migrations import current_revision, head_revision, upgrade_to_head
from app.storage.models import Base
from app.storage.repository import SourceFactRepository
from tests.api.conftest import FIXTURE_DOCUMENTS, import_fixtures

PHASE_2_TABLES = ("source_facts", "import_receipts")


def build_phase_2_database(path: Path) -> Engine:
    """Return an engine on a database built the way Phase 2 built one.

    Deliberately mirrors the code that shipped in Phase 2: `create_all` for the
    two tables the models declared then, and raw DDL for the four append-only
    triggers. Nothing here goes through Alembic, so the database carries no
    revision stamp, which is the whole point.
    """
    engine = create_database_engine(database_url_for(path))
    Base.metadata.create_all(engine, tables=[Base.metadata.tables[t] for t in PHASE_2_TABLES])
    with engine.begin() as connection:
        for table in PHASE_2_TABLES:
            for operation in ("UPDATE", "DELETE"):
                connection.exec_driver_sql(
                    f"CREATE TRIGGER trg_{table}_no_{operation.lower()} "
                    f"BEFORE {operation} ON {table} BEGIN "
                    f"SELECT RAISE(ABORT, '{table} is append-only: "
                    f"{operation} is not permitted'); END;"
                )
    return engine


def build_loaded_phase_2_database(path: Path) -> Engine:
    """Return a Phase 2 database holding the imported example documents."""
    engine = build_phase_2_database(path)
    import_fixtures(engine, FIXTURE_DOCUMENTS)
    return engine


def rows_of(engine: Engine, table: str, order_by: str) -> list[dict[str, Any]]:
    """Return every column of every row, so a comparison is field-for-field."""
    with Session(engine) as session:
        result = session.execute(text(f"SELECT * FROM {table} ORDER BY {order_by}"))  # noqa: S608
        return [dict(row._mapping) for row in result]


def _describe(engine: Engine, table: str) -> tuple[Any, ...]:
    """Return the parts of a table definition two builds of it must share."""
    inspector = inspect(engine)
    return (
        sorted((column["name"], str(column["type"])) for column in inspector.get_columns(table)),
        tuple(inspector.get_pk_constraint(table)["constrained_columns"]),
        sorted(
            (index["name"], tuple(index["column_names"]), index["unique"])
            for index in inspector.get_indexes(table)
        ),
    )


def triggers_of(engine: Engine) -> set[str]:
    """Return the trigger names a database carries."""
    with engine.connect() as connection:
        return {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }


class TestThePhase2StartingPoint:
    """The builder really does produce the pre-migration shape."""

    def test_it_carries_no_revision_stamp(self, tmp_path: Path) -> None:
        """Which is what distinguishes it from a database built by migrations."""
        engine = build_phase_2_database(tmp_path / "unstamped.sqlite")

        assert current_revision(engine) is None
        assert "alembic_version" not in inspect(engine).get_table_names()
        engine.dispose()

    def test_it_has_only_the_two_original_tables(self, tmp_path: Path) -> None:
        """The run tables came later, so this is genuinely the older schema."""
        engine = build_phase_2_database(tmp_path / "two.sqlite")

        assert set(inspect(engine).get_table_names()) == set(PHASE_2_TABLES)
        engine.dispose()

    def test_it_carries_the_four_original_triggers(self, tmp_path: Path) -> None:
        """A Phase 2 database was already append-only."""
        engine = build_phase_2_database(tmp_path / "protected.sqlite")

        assert triggers_of(engine) == set(LEGACY_TRIGGERS)
        engine.dispose()

    def test_it_accepts_imports(self, tmp_path: Path) -> None:
        """So the data the upgrade must preserve is real, not hand-written rows."""
        engine = build_loaded_phase_2_database(tmp_path / "loaded.sqlite")

        with session_factory(engine)() as session:
            assert SourceFactRepository(session).count() == 10
        engine.dispose()

    def test_it_matches_the_schema_the_initial_revision_builds(self, tmp_path: Path) -> None:
        """Pins the claim that the fingerprint describes the real Phase 2 schema.

        `create_all` reads today's models, while the initial revision is the
        frozen record of what Phase 2 shipped. They must still agree, otherwise
        this file would be adopting a shape no deployment ever had.
        """
        from app.storage.migrations import upgrade_to

        built = build_phase_2_database(tmp_path / "built.sqlite")
        migrated = create_database_engine(database_url_for(tmp_path / "migrated.sqlite"))
        upgrade_to(migrated, LEGACY_REVISION)

        assert legacy_schema_differences(built) == ()
        assert legacy_schema_differences(migrated) == ()
        for table in PHASE_2_TABLES:
            assert _describe(built, table) == _describe(migrated, table)
        built.dispose()
        migrated.dispose()

    def test_upgrading_it_is_planned_as_an_adoption(self, tmp_path: Path) -> None:
        """It is recognised, rather than treated as an empty database."""
        engine = build_phase_2_database(tmp_path / "planned.sqlite")

        assert plan_adoption(engine) is AdoptionPlan.ADOPT_LEGACY
        engine.dispose()


class TestAdoptingAPhase2Database:
    """The upgrade this phase exists to make work."""

    @pytest.fixture
    def upgraded(self, tmp_path: Path) -> Engine:
        """Return a loaded Phase 2 database brought to head."""
        engine = build_loaded_phase_2_database(tmp_path / "adopt.sqlite")
        upgrade_to_head(engine)
        return engine

    def test_the_upgrade_succeeds(self, tmp_path: Path) -> None:
        """Before this phase it raised on `table source_facts already exists`."""
        engine = build_loaded_phase_2_database(tmp_path / "succeeds.sqlite")

        upgrade_to_head(engine)

        assert current_revision(engine) == head_revision(engine)
        engine.dispose()

    def test_source_facts_survive_field_for_field(self, tmp_path: Path) -> None:
        """Every column of every row, not a count and not a subset."""
        engine = build_loaded_phase_2_database(tmp_path / "facts.sqlite")
        before = rows_of(engine, "source_facts", "source_record_id")

        upgrade_to_head(engine)

        after = rows_of(engine, "source_facts", "source_record_id")
        engine.dispose()

        assert len(before) == 10
        assert after == before

    def test_import_receipts_survive_field_for_field(self, tmp_path: Path) -> None:
        """The audit trail is as much a record as the facts are."""
        engine = build_loaded_phase_2_database(tmp_path / "receipts.sqlite")
        before = rows_of(engine, "import_receipts", "sequence")

        upgrade_to_head(engine)

        after = rows_of(engine, "import_receipts", "sequence")
        engine.dispose()

        assert len(before) == 3
        assert after == before

    def test_the_original_triggers_remain(self, upgraded: Engine) -> None:
        """The upgrade adds protection. It does not replace what was there."""
        assert set(LEGACY_TRIGGERS) <= triggers_of(upgraded)
        upgraded.dispose()

    def test_every_append_only_table_is_protected_afterwards(self, upgraded: Engine) -> None:
        """Old and new tables alike, by the same two triggers each."""
        expected = {
            f"trg_{table}_no_{operation}"
            for table in APPEND_ONLY_TABLES
            for operation in ("update", "delete")
        }

        assert triggers_of(upgraded) == expected
        upgraded.dispose()

    def test_the_run_tables_appear(self, upgraded: Engine) -> None:
        """The revision the adopted database had never run."""
        present = set(inspect(upgraded).get_table_names())
        upgraded.dispose()

        assert {"reconciliation_runs", "reconciliation_decisions"} <= present

    def test_importing_still_works(self, upgraded: Engine) -> None:
        """An adopted database is a working database, not just a preserved one."""
        import_fixtures(upgraded, (("payment_events.csv", SourceRecordType.PAYMENT_EVENT),))

        with session_factory(upgraded)() as session:
            assert SourceFactRepository(session).count() == 10
        upgraded.dispose()

    def test_reconciling_reaches_the_same_conclusion_as_a_clean_database(
        self, upgraded: Engine, tmp_path: Path
    ) -> None:
        """Adoption must not change what the same facts reconcile to.

        Compared against a database migrated from nothing and loaded with the
        same documents. The run key is derived from the snapshot and the
        versions, so two databases holding the same facts must produce the same
        key, and any difference would mean the adoption altered the evidence.
        """
        clean = create_database_engine(database_url_for(tmp_path / "clean.sqlite"))
        create_schema(clean)
        import_fixtures(clean, FIXTURE_DOCUMENTS)

        runs = []
        for engine in (upgraded, clean):
            with session_scope(engine) as session:
                runs.append(
                    ReconciliationRunService(session).create_run(
                        SourceFactRepository(session).fact_index()
                    )
                )
        adopted_run, clean_run = runs
        upgraded.dispose()
        clean.dispose()

        assert adopted_run.fact_count == 10
        assert adopted_run.decision_count > 0
        assert adopted_run.run_key == clean_run.run_key
        assert adopted_run.snapshot_fingerprint == clean_run.snapshot_fingerprint
        assert adopted_run.status_counts == clean_run.status_counts
        assert adopted_run.exception_counts == clean_run.exception_counts

    def test_adopting_again_changes_nothing(self, tmp_path: Path) -> None:
        """Startup runs this every time, so the second run must be a no-op."""
        engine = build_loaded_phase_2_database(tmp_path / "twice.sqlite")
        upgrade_to_head(engine)
        before = rows_of(engine, "source_facts", "source_record_id")

        create_schema(engine)

        assert current_revision(engine) == head_revision(engine)
        assert rows_of(engine, "source_facts", "source_record_id") == before
        engine.dispose()

    def test_an_empty_version_table_does_not_block_adoption(self, tmp_path: Path) -> None:
        """Same reasoning as for an empty database: no row means unstamped."""
        engine = build_loaded_phase_2_database(tmp_path / "halfstamped.sqlite")
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32))")
        before = rows_of(engine, "source_facts", "source_record_id")

        upgrade_to_head(engine)

        assert current_revision(engine) == head_revision(engine)
        assert rows_of(engine, "source_facts", "source_record_id") == before
        engine.dispose()

    def test_the_stamp_records_the_revision_the_schema_already_matched(
        self, tmp_path: Path
    ) -> None:
        """Stamping at the wrong revision would skip or repeat a migration."""
        engine = build_phase_2_database(tmp_path / "stamped.sqlite")

        from app.storage.migrations import adopt_if_legacy

        adopt_if_legacy(engine)

        assert current_revision(engine) == LEGACY_REVISION
        engine.dispose()


class TestCreateSchemaAdoptsToo:
    """Startup goes through `create_schema`, so that is the path that matters."""

    def test_create_schema_adopts_a_phase_2_database(self, tmp_path: Path) -> None:
        """The application starting against an old database is the real case."""
        engine = build_loaded_phase_2_database(tmp_path / "startup.sqlite")

        create_schema(engine)

        assert current_revision(engine) == head_revision(engine)
        with session_factory(engine)() as session:
            assert SourceFactRepository(session).count() == 10
        engine.dispose()


class TestRefusingAnUnrecognisedDatabase:
    """Fail closed. Never stamp a database merely because tables exist.

    Stamping the wrong database would tell Alembic that migrations it never ran
    are already applied, and the damage would surface later as wrong reads
    rather than as a clear failure now.
    """

    @staticmethod
    def _missing_a_table(path: Path) -> Engine:
        """Half of Phase 2, as an interrupted setup might leave behind."""
        engine = build_phase_2_database(path)
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TRIGGER trg_import_receipts_no_update")
            connection.exec_driver_sql("DROP TRIGGER trg_import_receipts_no_delete")
            connection.exec_driver_sql("DROP TABLE import_receipts")
        return engine

    @staticmethod
    def _partial_columns(path: Path) -> Engine:
        """The right table names carrying the wrong definitions."""
        engine = create_database_engine(database_url_for(path))
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE source_facts (source_record_id TEXT PRIMARY KEY)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE import_receipts (sequence INTEGER PRIMARY KEY)"
            )
        return engine

    @staticmethod
    def _missing_a_trigger(path: Path) -> Engine:
        """Append-only may already have been broken, so it cannot be vouched for."""
        engine = build_phase_2_database(path)
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TRIGGER trg_source_facts_no_delete")
        return engine

    @staticmethod
    def _an_unrelated_table(path: Path) -> Engine:
        """Someone else's database that happens to share two table names."""
        engine = build_phase_2_database(path)
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE ledger_notes (id INTEGER PRIMARY KEY)")
        return engine

    @staticmethod
    def _a_wrong_primary_key(path: Path) -> Engine:
        """Same columns, different identity, so rows would not line up."""
        engine = build_phase_2_database(path)
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TRIGGER trg_source_facts_no_update")
            connection.exec_driver_sql("DROP TRIGGER trg_source_facts_no_delete")
            connection.exec_driver_sql("ALTER TABLE source_facts RENAME TO old_facts")
            connection.exec_driver_sql(
                "CREATE TABLE source_facts AS SELECT * FROM old_facts WHERE 0"
            )
            connection.exec_driver_sql("DROP TABLE old_facts")
            for operation in ("UPDATE", "DELETE"):
                connection.exec_driver_sql(
                    f"CREATE TRIGGER trg_source_facts_no_{operation.lower()} "
                    f"BEFORE {operation} ON source_facts BEGIN "
                    f"SELECT RAISE(ABORT, 'source_facts is append-only: "
                    f"{operation} is not permitted'); END;"
                )
        return engine

    SHAPES: tuple[tuple[str, Callable[[Path], Engine], str], ...] = (
        ("missing_table", _missing_a_table, "table 'import_receipts' is missing"),
        ("partial_columns", _partial_columns, "has the wrong columns"),
        ("missing_trigger", _missing_a_trigger, "append-only triggers are missing"),
        ("unrelated_table", _an_unrelated_table, "unexpected tables are present"),
        ("wrong_primary_key", _a_wrong_primary_key, "is missing indexes"),
    )

    @pytest.mark.parametrize(("name", "build", "expected"), SHAPES)
    def test_the_shape_is_refused(
        self, tmp_path: Path, name: str, build: Callable[[Path], Engine], expected: str
    ) -> None:
        """One clear error naming what actually differed."""
        engine = build(tmp_path / f"{name}.sqlite")

        with pytest.raises(UnrecognisedSchemaError, match=expected):
            upgrade_to_head(engine)
        engine.dispose()

    @pytest.mark.parametrize(("name", "build", "expected"), SHAPES)
    def test_the_refusal_writes_no_version_table(
        self, tmp_path: Path, name: str, build: Callable[[Path], Engine], expected: str
    ) -> None:
        """A refusal must leave the database exactly as it was found."""
        engine = build(tmp_path / f"{name}-clean.sqlite")
        before = set(inspect(engine).get_table_names())

        with pytest.raises(UnrecognisedSchemaError):
            upgrade_to_head(engine)

        after = set(inspect(engine).get_table_names())
        engine.dispose()

        assert "alembic_version" not in after
        assert after == before

    def test_the_error_lists_every_difference(self, tmp_path: Path) -> None:
        """So the reader can identify the database instead of guessing."""
        engine = self._an_unrelated_table(tmp_path / "listed.sqlite")
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TRIGGER trg_source_facts_no_delete")

        with pytest.raises(UnrecognisedSchemaError) as raised:
            upgrade_to_head(engine)
        engine.dispose()

        assert len(raised.value.differences) == 2
        assert "ledger_notes" in raised.value.differences[0]
        assert "trg_source_facts_no_delete" in raised.value.differences[1]

    def test_an_already_stamped_database_is_never_inspected(self, tmp_path: Path) -> None:
        """Adoption applies to unstamped databases only.

        A migrated database is allowed to hold tables this application does not
        own, because the stamp already says which revisions have run.
        """
        engine = create_database_engine(database_url_for(tmp_path / "stamped.sqlite"))
        upgrade_to_head(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE ledger_notes (id INTEGER PRIMARY KEY)")

        upgrade_to_head(engine)

        assert current_revision(engine) == head_revision(engine)
        engine.dispose()


class TestAnEmptyDatabaseIsNotAdopted:
    """Nothing to adopt, so it migrates from zero as it always did."""

    def test_it_is_planned_as_fresh(self, tmp_path: Path) -> None:
        """Told apart from a legacy database by having no tables at all."""
        engine = create_database_engine(database_url_for(tmp_path / "fresh.sqlite"))

        assert plan_adoption(engine) is AdoptionPlan.FRESH
        engine.dispose()

    def test_it_reaches_head(self, tmp_path: Path) -> None:
        """The behaviour that existed before this phase, unchanged."""
        engine = create_database_engine(database_url_for(tmp_path / "empty.sqlite"))

        create_schema(engine)

        assert current_revision(engine) == head_revision(engine)
        engine.dispose()

    def test_an_empty_version_table_does_not_make_it_look_legacy(self, tmp_path: Path) -> None:
        """An interrupted first migration leaves that table behind holding no row.

        The database is still empty and still unstamped, so it must migrate from
        zero rather than be refused for carrying a table.
        """
        engine = create_database_engine(database_url_for(tmp_path / "halfway.sqlite"))
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32))")

        assert current_revision(engine) is None
        assert plan_adoption(engine) is AdoptionPlan.FRESH

        create_schema(engine)

        assert current_revision(engine) == head_revision(engine)
        engine.dispose()

    def test_a_second_setup_run_leaves_it_at_head(self, tmp_path: Path) -> None:
        """Startup runs setup every time."""
        engine = create_database_engine(database_url_for(tmp_path / "again.sqlite"))
        create_schema(engine)

        create_schema(engine)

        assert current_revision(engine) == head_revision(engine)
        engine.dispose()
