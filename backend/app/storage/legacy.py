"""Recognising and adopting a pre-migration database.

Before Phase 5, `create_schema` built the schema with `create_all` and created
no `alembic_version` table. Such a database holds real facts and receipts and
carries no revision, so running the migrations against it fails on the first
`CREATE TABLE`.

Three states have to be told apart, and the difference matters because two of
them are safe to act on and one is not:

- an empty database, which migrates from zero;
- a database that is recognisably the Phase 2 schema, which is stamped at the
  initial revision and then migrated forward, touching no data;
- anything else, which is refused.

The third case is the reason this module inspects rather than assumes. Stamping
a database merely because two tables happen to share a name would tell Alembic
that migrations it never ran are already applied, and the next upgrade would
then build on a schema that is not what it thinks it is. That failure would
surface much later, as corrupt reads rather than as a refusal, so the check is
deliberately strict: the schema must match the Phase 2 fingerprint exactly, and
anything unexplained is a refusal rather than a best guess.

Nothing here drops, rewrites or repairs anything. A database that is not
recognised is left exactly as it was found.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import Engine, inspect

LEGACY_REVISION = "0001_initial_schema"
"""The revision whose schema a pre-migration database already has."""


@dataclass(frozen=True)
class TableShape:
    """The part of a table's definition the adoption check compares."""

    columns: frozenset[str]
    primary_key: tuple[str, ...]
    indexes: frozenset[str]


#: The Phase 2 schema, as both `create_all` and the initial migration produce it.
#:
#: Held in one place so that startup code and tests compare against the same
#: description. A second copy would be a place for the two to disagree, and the
#: disagreement would show up as a database being adopted by one path and
#: refused by the other.
LEGACY_TABLES: Mapping[str, TableShape] = {
    "source_facts": TableShape(
        columns=frozenset(
            {
                "source_record_id",
                "source_system",
                "source_record_type",
                "locator_kind",
                "locator_reference",
                "row_number",
                "provider_event_id",
                "observed_at",
                "occurred_at",
                "payload_hash",
                "canonical_payload",
            }
        ),
        primary_key=("source_record_id",),
        indexes=frozenset(
            {
                "ix_source_facts_payload_hash",
                "ix_source_facts_source_record_type",
                "ix_source_facts_source_system",
            }
        ),
    ),
    "import_receipts": TableShape(
        columns=frozenset(
            {
                "sequence",
                "receipt_id",
                "document_hash",
                "document_name",
                "source_system",
                "source_record_type",
                "parser_version",
                "received_at",
                "outcome",
                "row_count",
                "accepted_count",
                "duplicate_count",
                "conflict_count",
                "rejected_count",
                "row_outcomes",
                "failure_detail",
            }
        ),
        primary_key=("sequence",),
        indexes=frozenset(
            {
                "ix_import_receipts_document_hash",
                "ix_import_receipts_outcome",
                "ix_import_receipts_received_at",
            }
        ),
    ),
}

#: The append-only protections a Phase 2 database must already carry.
#:
#: Their absence is a refusal rather than something to add. A database whose
#: triggers were dropped is one where the append-only guarantee may already have
#: been broken, and adopting it would silently vouch for history this system
#: cannot vouch for.
LEGACY_TRIGGERS: frozenset[str] = frozenset(
    {
        "trg_source_facts_no_update",
        "trg_source_facts_no_delete",
        "trg_import_receipts_no_update",
        "trg_import_receipts_no_delete",
    }
)


class AdoptionPlan(StrEnum):
    """What to do with a database that carries no revision stamp."""

    FRESH = "FRESH"
    """Empty. Migrate from zero."""

    ADOPT_LEGACY = "ADOPT_LEGACY"
    """Recognisably Phase 2. Stamp the initial revision, then migrate forward."""


class UnrecognisedSchemaError(RuntimeError):
    """Raised when an unstamped database is neither empty nor recognisably Phase 2.

    Carries the specific differences, because the useful question when this
    happens is which database it is, and a message saying only that something
    was wrong sends a person looking through the schema by hand.
    """

    def __init__(self, differences: Sequence[str]) -> None:
        self.differences = tuple(differences)
        listed = "\n".join(f"  - {difference}" for difference in self.differences)
        super().__init__(
            "this database carries no migration stamp and is not the Phase 2 schema, "
            "so it cannot be adopted safely:\n"
            f"{listed}\n"
            "Nothing has been changed. Stamping it would tell the migrations that "
            "revisions they never ran are already applied. If this database really "
            "should be brought forward, inspect it and decide deliberately."
        )


#: Tables that say nothing about which schema a database holds.
#:
#: `alembic_version` is Alembic's own bookkeeping. It can exist while holding no
#: row, after an interrupted first migration, and a database in that state is
#: still unstamped. Counting it as user data would refuse an otherwise empty
#: database that had once been half migrated, which is the opposite of helpful.
IGNORED_TABLES: frozenset[str] = frozenset({"alembic_version"})


def user_tables(engine: Engine) -> frozenset[str]:
    """Return the tables a database holds, excluding SQLite's and Alembic's own."""
    return frozenset(
        name
        for name in inspect(engine).get_table_names()
        if not name.startswith("sqlite_") and name not in IGNORED_TABLES
    )


def triggers(engine: Engine) -> frozenset[str]:
    """Return the trigger names defined in a database."""
    with engine.connect() as connection:
        return frozenset(
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        )


def legacy_schema_differences(engine: Engine) -> tuple[str, ...]:
    """Return every way a database differs from the Phase 2 schema.

    Empty means it is recognisably Phase 2 and can be adopted. Anything listed
    is a reason not to.

    Args:
        engine: The database to inspect.

    Returns:
        One sentence per difference, in a fixed order so two runs report the
        same thing.
    """
    differences: list[str] = []
    present = user_tables(engine)

    unexpected = sorted(present - set(LEGACY_TABLES))
    if unexpected:
        differences.append(f"unexpected tables are present: {unexpected}")

    inspector = inspect(engine)
    for name in sorted(LEGACY_TABLES):
        expected = LEGACY_TABLES[name]
        if name not in present:
            differences.append(f"table {name!r} is missing")
            continue

        columns = frozenset(column["name"] for column in inspector.get_columns(name))
        if columns != expected.columns:
            missing = sorted(expected.columns - columns)
            extra = sorted(columns - expected.columns)
            differences.append(
                f"table {name!r} has the wrong columns; missing {missing}, unexpected {extra}"
            )

        primary_key = tuple(inspector.get_pk_constraint(name)["constrained_columns"])
        if primary_key != expected.primary_key:
            differences.append(
                f"table {name!r} has primary key {list(primary_key)}, "
                f"expected {list(expected.primary_key)}"
            )

        indexes = frozenset(index["name"] for index in inspector.get_indexes(name) if index["name"])
        if not expected.indexes <= indexes:
            differences.append(
                f"table {name!r} is missing indexes: {sorted(expected.indexes - indexes)}"
            )

    missing_triggers = sorted(LEGACY_TRIGGERS - triggers(engine))
    if missing_triggers:
        differences.append(f"append-only triggers are missing: {missing_triggers}")

    return tuple(differences)


def plan_adoption(engine: Engine) -> AdoptionPlan:
    """Decide what to do with a database that carries no revision stamp.

    Args:
        engine: The unstamped database.

    Returns:
        `FRESH` when it is empty, `ADOPT_LEGACY` when it is recognisably the
        Phase 2 schema.

    Raises:
        UnrecognisedSchemaError: When it is neither. Nothing is modified.
    """
    if not user_tables(engine):
        return AdoptionPlan.FRESH

    differences = legacy_schema_differences(engine)
    if differences:
        raise UnrecognisedSchemaError(differences)
    return AdoptionPlan.ADOPT_LEGACY
