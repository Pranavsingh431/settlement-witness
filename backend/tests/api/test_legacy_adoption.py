"""Tests for adopting a database built before migrations existed.

The real starting point in a deployment is not a database stamped at the
initial revision. It is one built by the Phase 2 `create_all` call, which left
no `alembic_version` table at all. Building the older schema with
`upgrade_to(engine, "0001_initial_schema")` stamps it, so those tests never
touch this path, and the failure it hides is total: the first migration tries
to create a table that is already there and the application cannot start.

So the databases here are built the way Phase 2 built them, with `create_all`
and raw trigger DDL, and never through the migrations.

The malformed shapes are built by reading a real Phase 2 database's own DDL out
of `sqlite_master`, editing one clause, and rebuilding the table from it. Doing
it that way keeps each variant a visibly minimal change from something genuine,
rather than a hand-written approximation that might differ in ways the test did
not intend.
"""

import re
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
    PHASE_2_SCHEMA,
    PHASE_2_TRIGGERS,
    AdoptionPlan,
    UnrecognisedSchemaError,
    describe_table,
    legacy_schema_differences,
    normalise_expression,
    normalise_sql,
    observed_triggers,
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


Edit = Callable[[str], str]


def _unchanged(sql: str) -> str:
    """Leave a statement alone."""
    return sql


def replacing(old: str, new: str = "") -> Edit:
    """Return an edit that swaps one fragment of a DDL statement."""

    def edit(sql: str) -> str:
        return sql.replace(old, new)

    return edit


def rebuild_table(
    engine: Engine,
    table: str,
    *,
    table_ddl: Edit = _unchanged,
    index_ddl: Edit = _unchanged,
    trigger_ddl: Edit = _unchanged,
) -> Engine:
    """Drop one table and build it again from its own DDL, edited.

    Reading the statements back out of `sqlite_master` keeps every malformed
    variant a visibly minimal change from something a Phase 2 database really
    contained, rather than a hand-written table that might differ in ways the
    test did not intend. Dropping the table takes its indexes and triggers with
    it, so both are recreated from their own statements.

    Raises:
        AssertionError: When no edit changed anything, which would leave a valid
            Phase 2 database and quietly test nothing.
    """
    with engine.connect() as connection:
        create_table = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).scalar_one()
        create_indexes = [
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type = 'index' "
                "AND tbl_name = ? AND sql IS NOT NULL",
                (table,),
            )
        ]
        create_triggers = [
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ?", (table,)
            )
        ]

    before = [create_table, *create_indexes, *create_triggers]
    after = [
        table_ddl(create_table),
        *(index_ddl(statement) for statement in create_indexes),
        *(trigger_ddl(statement) for statement in create_triggers),
    ]
    if before == after:
        message = f"rebuilding {table!r} changed nothing, so this variant tests nothing"
        raise AssertionError(message)

    with engine.begin() as connection:
        connection.exec_driver_sql(f"DROP TABLE {table}")
        for statement in after:
            connection.exec_driver_sql(statement)
    return engine


def variant(table: str, **edits: Edit) -> Callable[[Path], Engine]:
    """Return a builder for a Phase 2 database with one table rebuilt."""

    def build(path: Path) -> Engine:
        return rebuild_table(build_phase_2_database(path), table, **edits)

    return build


def executing(*statements: str) -> Callable[[Path], Engine]:
    """Return a builder for a Phase 2 database with extra statements applied."""

    def build(path: Path) -> Engine:
        engine = build_phase_2_database(path)
        with engine.begin() as connection:
            for statement in statements:
                connection.exec_driver_sql(statement)
        return engine

    return build


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

        assert triggers_of(engine) == set(PHASE_2_TRIGGERS)
        engine.dispose()

    def test_it_accepts_imports(self, tmp_path: Path) -> None:
        """So the data the upgrade must preserve is real, not hand-written rows."""
        engine = build_loaded_phase_2_database(tmp_path / "loaded.sqlite")

        with session_factory(engine)() as session:
            assert SourceFactRepository(session).count() == 10
        engine.dispose()

    def test_it_matches_the_frozen_fingerprint(self, tmp_path: Path) -> None:
        """The fingerprint is a written record, so something must hold it to reality.

        `PHASE_2_SCHEMA` is written out rather than reflected, because a
        fingerprint derived from the current code would agree with whatever the
        current code happens to do. This is what stops the written record and
        the shipped schema drifting apart: it compares the record against a
        database built the way Phase 2 built one, field by field.
        """
        engine = build_phase_2_database(tmp_path / "pinned.sqlite")

        inspector = inspect(engine)
        for table in PHASE_2_TABLES:
            assert describe_table(inspector, table) == PHASE_2_SCHEMA[table]
        assert observed_triggers(engine) == PHASE_2_TRIGGERS
        engine.dispose()

    def test_the_initial_revision_builds_the_same_schema(self, tmp_path: Path) -> None:
        """`create_all` reads today's models, the revision is what Phase 2 shipped.

        They must still agree, otherwise the fingerprint would describe a shape
        no deployment ever had. Compared through the same description the
        adoption check uses, rather than through the stored `CREATE TABLE` text,
        because SQLite keeps that text verbatim and the two paths emit their
        constraints in different orders while meaning the same thing.
        """
        from app.storage.migrations import upgrade_to

        built = build_phase_2_database(tmp_path / "built.sqlite")
        migrated = create_database_engine(database_url_for(tmp_path / "migrated.sqlite"))
        upgrade_to(migrated, LEGACY_REVISION)

        assert legacy_schema_differences(built) == ()
        assert legacy_schema_differences(migrated) == ()
        for table in PHASE_2_TABLES:
            assert describe_table(inspect(built), table) == describe_table(inspect(migrated), table)
        assert observed_triggers(built) == observed_triggers(migrated)
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
        assert set(PHASE_2_TRIGGERS) <= triggers_of(upgraded)
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

    #: One malformed database per guarantee Phase 2 shipped.
    #:
    #: Each entry is a name, a builder, and the exact sentence the refusal must
    #: contain. Both tests below run over all of them, so every guarantee is
    #: checked for being refused, and for being refused without writing
    #: anything. The expected text is compared literally, not as a pattern.
    SHAPES: tuple[tuple[str, Callable[[Path], Engine], str], ...] = (
        # Tables.
        ("missing_table", _missing_a_table, "table 'import_receipts' is missing"),
        ("partial_columns", _partial_columns, "has the wrong columns"),
        (
            "unrelated_table",
            executing("CREATE TABLE ledger_notes (id INTEGER PRIMARY KEY)"),
            "unexpected tables are present: ['ledger_notes']",
        ),
        # Columns.
        (
            "nullability_loosened",
            variant(
                "source_facts",
                table_ddl=replacing(
                    "payload_hash VARCHAR(64) NOT NULL", "payload_hash VARCHAR(64)"
                ),
            ),
            "column 'payload_hash' is VARCHAR(64) NULL, expected VARCHAR(64) NOT NULL",
        ),
        (
            "type_changed",
            variant(
                "import_receipts",
                table_ddl=replacing("receipt_id VARCHAR(100)", "receipt_id TEXT"),
            ),
            "column 'receipt_id' is TEXT NOT NULL, expected VARCHAR(100) NOT NULL",
        ),
        (
            "columns_reordered",
            variant(
                "source_facts",
                table_ddl=replacing(
                    "locator_kind VARCHAR(32) NOT NULL, \n\t"
                    "locator_reference VARCHAR(200) NOT NULL",
                    "locator_reference VARCHAR(200) NOT NULL, \n\t"
                    "locator_kind VARCHAR(32) NOT NULL",
                ),
            ),
            "table 'source_facts' declares its columns in a different order",
        ),
        (
            "wrong_primary_key",
            variant(
                "source_facts",
                table_ddl=replacing(
                    "PRIMARY KEY (source_record_id)", "PRIMARY KEY (provider_event_id)"
                ),
            ),
            "has primary key ['provider_event_id'], expected ['source_record_id']",
        ),
        # Unique identities.
        (
            "no_fact_idempotency",
            variant(
                "source_facts",
                table_ddl=replacing(
                    "CONSTRAINT uq_source_facts_idempotency "
                    "UNIQUE (source_system, provider_event_id), \n\t"
                ),
            ),
            "missing [\"uq_source_facts_idempotency over ['source_system', 'provider_event_id']\"]",
        ),
        (
            "no_unique_receipt_id",
            variant("import_receipts", table_ddl=replacing(", \n\tUNIQUE (receipt_id)")),
            "missing [\"unnamed over ['receipt_id']\"]",
        ),
        # Checks.
        (
            "check_weakened",
            variant(
                "source_facts",
                table_ddl=replacing("CHECK (row_number >= 1)", "CHECK (row_number >= 0)"),
            ),
            "check 'ck_source_facts_row_number' is 'row_number >= 0', expected 'row_number >= 1'",
        ),
        (
            "check_removed",
            variant(
                "import_receipts",
                table_ddl=replacing(
                    ", \n\tCONSTRAINT ck_import_receipts_hash_length "
                    "CHECK (length(document_hash) = 64)"
                ),
            ),
            "is missing check 'ck_import_receipts_hash_length'",
        ),
        # Indexes.
        (
            "index_columns_changed",
            variant(
                "source_facts",
                index_ddl=replacing(
                    "ix_source_facts_payload_hash ON source_facts (payload_hash)",
                    "ix_source_facts_payload_hash ON source_facts (row_number)",
                ),
            ),
            "index 'ix_source_facts_payload_hash' covers ['row_number']",
        ),
        (
            "index_made_unique",
            variant(
                "import_receipts",
                index_ddl=replacing(
                    "CREATE INDEX ix_import_receipts_outcome",
                    "CREATE UNIQUE INDEX ix_import_receipts_outcome",
                ),
            ),
            "index 'ix_import_receipts_outcome' covers ['outcome'] and is unique",
        ),
        (
            "index_renamed",
            variant(
                "source_facts",
                index_ddl=replacing(
                    "CREATE INDEX ix_source_facts_source_system", "CREATE INDEX ix_renamed"
                ),
            ),
            "is missing index 'ix_source_facts_source_system'",
        ),
        (
            "extra_index",
            executing("CREATE INDEX ix_extra ON source_facts (occurred_at)"),
            "has an unexpected index 'ix_extra'",
        ),
        # Triggers.
        (
            "missing_trigger",
            executing("DROP TRIGGER trg_source_facts_no_delete"),
            "append-only trigger 'trg_source_facts_no_delete' is missing",
        ),
        (
            "noop_trigger",
            variant(
                "source_facts",
                trigger_ddl=replacing(
                    "SELECT RAISE(ABORT, 'source_facts is append-only: UPDATE is not permitted');",
                    "SELECT 1;",
                ),
            ),
            "trigger 'trg_source_facts_no_update' is not the Phase 2 append-only trigger",
        ),
        (
            "trigger_message_changed",
            variant(
                "source_facts",
                trigger_ddl=replacing("is append-only: DELETE", "is read-only: DELETE"),
            ),
            "trigger 'trg_source_facts_no_delete' is not the Phase 2 append-only trigger",
        ),
        (
            "trigger_timing_changed",
            variant(
                "source_facts",
                trigger_ddl=replacing(
                    "BEFORE UPDATE ON source_facts", "AFTER UPDATE ON source_facts"
                ),
            ),
            "trigger 'trg_source_facts_no_update' is not the Phase 2 append-only trigger",
        ),
        (
            "extra_trigger",
            executing("CREATE TRIGGER trg_extra AFTER INSERT ON source_facts BEGIN SELECT 1; END"),
            "unexpected trigger 'trg_extra' is present",
        ),
    )

    @pytest.mark.parametrize(("name", "build", "expected"), SHAPES)
    def test_the_shape_is_refused(
        self, tmp_path: Path, name: str, build: Callable[[Path], Engine], expected: str
    ) -> None:
        """One clear error naming what actually differed."""
        engine = build(tmp_path / f"{name}.sqlite")

        with pytest.raises(UnrecognisedSchemaError, match=re.escape(expected)):
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
        engine = executing(
            "CREATE TABLE ledger_notes (id INTEGER PRIMARY KEY)",
            "DROP TRIGGER trg_source_facts_no_delete",
        )(tmp_path / "listed.sqlite")

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


class TestNormalisingBeforeComparing:
    """Formatting differences are tolerated. Differences in meaning are not.

    The line matters: normalising too little would refuse a database over a line
    break, and normalising too much would accept one whose rules had been
    loosened.
    """

    def test_whitespace_is_collapsed(self) -> None:
        """The same statement written across lines is the same statement."""
        assert normalise_sql("CREATE  TRIGGER\n\tt  BEFORE") == "CREATE TRIGGER t BEFORE"

    def test_a_trailing_semicolon_is_dropped(self) -> None:
        """SQLite stores a definition without it, callers often write it."""
        assert normalise_sql("SELECT 1;") == "SELECT 1"

    def test_trigger_case_is_preserved(self) -> None:
        """Deliberate: the abort message is part of what a trigger promises.

        Lowering case would also lower the text inside the quoted message, so
        the check would stop distinguishing one message from another. Every
        genuine Phase 2 trigger was written by one piece of code, so none of
        them can differ in case.
        """
        assert normalise_sql("Raise(Abort, 'X')") == "Raise(Abort, 'X')"

    def test_check_case_and_spacing_do_not_matter(self) -> None:
        """A rule written loudly is the same rule."""
        assert normalise_expression("LENGTH(payload_hash)  =  64") == normalise_expression(
            "length(payload_hash) = 64"
        )

    def test_one_enclosing_pair_of_parentheses_is_removed(self) -> None:
        """SQLite and SQLAlchemy disagree about keeping it."""
        assert normalise_expression("(row_number >= 1)") == normalise_expression("row_number >= 1")

    def test_spacing_around_operators_and_brackets_does_not_matter(self) -> None:
        """A rule written tightly is the same rule."""
        assert normalise_expression("row_number>=1") == normalise_expression("row_number >= 1")

    def test_a_two_character_operator_stays_whole(self) -> None:
        """`>=` must not be read as `>` followed by `=`."""
        assert normalise_expression("row_number >= 1") != normalise_expression("row_number > 1")

    def test_inner_parentheses_are_kept(self) -> None:
        """Stripping a pair that does not enclose the whole rule would corrupt it.

        The brackets survive, spaced out like every other separator.
        """
        assert normalise_expression("(a = 1) and (b = 2)") == "( a = 1 ) and ( b = 2 )"

    def test_a_changed_operator_is_not_a_formatting_difference(self) -> None:
        """The point of the whole exercise."""
        assert normalise_expression("row_number >= 1") != normalise_expression("row_number >= 0")

    def test_a_check_written_differently_still_adopts(self, tmp_path: Path) -> None:
        """End to end, so the normalisation is load bearing and not decorative."""
        engine = variant(
            "source_facts",
            table_ddl=replacing(
                "CHECK (length(payload_hash) = 64)", "CHECK(LENGTH(payload_hash)=64)"
            ),
        )(tmp_path / "shouty.sqlite")

        assert legacy_schema_differences(engine) == ()

        upgrade_to_head(engine)

        assert current_revision(engine) == head_revision(engine)
        engine.dispose()

    def test_a_check_written_loosely_is_refused(self, tmp_path: Path) -> None:
        """The same edit, changing the rule rather than the formatting."""
        engine = variant(
            "source_facts",
            table_ddl=replacing(
                "CHECK (length(payload_hash) = 64)", "CHECK(LENGTH(payload_hash)>=32)"
            ),
        )(tmp_path / "loose.sqlite")

        with pytest.raises(UnrecognisedSchemaError, match="ck_source_facts_hash_length"):
            upgrade_to_head(engine)
        engine.dispose()


class TestTheExactSchemaPolicy:
    """Unexpected indexes and triggers are refused, not tolerated.

    There was a choice here. An extra index takes no guarantee away, so it could
    have been allowed, and the fingerprint described as a set of required
    guarantees rather than an exact schema.

    Refusing is the choice made, for two reasons. It keeps one rule across the
    whole check, the same rule that already refuses an unexpected table: this
    code recognises the schema Phase 2 shipped, and anything else is somebody
    else's database or a modified one. And an object this code did not put there
    means somebody changed the schema, which says nothing about what else they
    changed. The cost of refusing is one deliberate decision by an operator. The
    cost of adopting wrongly is an audit trail that cites a database nobody
    checked.
    """

    def test_an_extra_index_is_refused_although_no_guarantee_is_lost(self, tmp_path: Path) -> None:
        """Every Phase 2 guarantee is present. It is still refused."""
        engine = executing("CREATE INDEX ix_extra ON source_facts (occurred_at)")(
            tmp_path / "extra-index.sqlite"
        )

        differences = legacy_schema_differences(engine)
        engine.dispose()

        assert differences == ("table 'source_facts' has an unexpected index 'ix_extra'",)

    def test_an_extra_trigger_is_refused_although_no_guarantee_is_lost(
        self, tmp_path: Path
    ) -> None:
        """Same policy, and a trigger can do far more than an index."""
        engine = executing(
            "CREATE TRIGGER trg_extra AFTER INSERT ON source_facts BEGIN SELECT 1; END"
        )(tmp_path / "extra-trigger.sqlite")

        differences = legacy_schema_differences(engine)
        engine.dispose()

        assert differences == ("unexpected trigger 'trg_extra' is present",)

    def test_an_extra_check_is_refused(self, tmp_path: Path) -> None:
        """A constraint Phase 2 never wrote could reject rows this system writes."""
        engine = variant(
            "import_receipts",
            table_ddl=replacing(
                "CONSTRAINT ck_import_receipts_row_count CHECK (row_count >= 0)",
                "CONSTRAINT ck_import_receipts_row_count CHECK (row_count >= 0), "
                "CONSTRAINT ck_extra CHECK (accepted_count >= 0)",
            ),
        )(tmp_path / "extra-check.sqlite")

        differences = legacy_schema_differences(engine)
        engine.dispose()

        assert differences == ("table 'import_receipts' has an unexpected check 'ck_extra'",)

    def test_a_genuine_phase_2_database_has_nothing_unexpected(self, tmp_path: Path) -> None:
        """The policy is only workable because the real shape matches it exactly."""
        engine = build_loaded_phase_2_database(tmp_path / "genuine.sqlite")

        assert legacy_schema_differences(engine) == ()
        engine.dispose()
