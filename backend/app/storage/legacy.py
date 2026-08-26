"""Recognising and adopting a pre-migration database.

Before Phase 5, `create_schema` built the schema with `create_all` and created
no `alembic_version` table. Such a database holds real facts and receipts and
carries no revision, so running the migrations against it fails on the first
`CREATE TABLE`.

Three states have to be told apart, and the difference matters because two of
them are safe to act on and one is not:

- an empty database, which migrates from zero;
- a database that is exactly the Phase 2 schema, which is stamped at the initial
  revision and then migrated forward, touching no data;
- anything else, which is refused.

The third case is the reason this module inspects rather than assumes. Stamping
a database merely because two tables happen to share a name would tell Alembic
that migrations it never ran are already applied, and the next upgrade would
then build on a schema that is not what it thinks it is. That failure would
surface much later, as corrupt reads rather than as a refusal.

## What "exactly" means here

Matching table and column names is not enough, because the names are not the
guarantees. A table called `source_facts` with the right eleven column names but
no `uq_source_facts_idempotency` cannot promise that one provider event was
imported once, and a trigger called `trg_source_facts_no_update` with an empty
body refuses nothing at all. Adopting either would carry a database forward
under promises it does not keep, and every later run would cite it as evidence.

So the fingerprint compares, for both tables:

- the exact set of user tables, with nothing unexpected;
- every column, in order, with its declared SQLite type and its nullability;
- the primary key, including column order;
- every unique identity, by name and by the columns it spans;
- every named CHECK constraint, by the rule it expresses;
- every index, by name, indexed column order and uniqueness;
- every trigger, by its full definition, so timing, event, table and abort
  message are all checked and a renamed no-op cannot pass.

Each of those sets is compared for equality, not containment. An unexpected
index or trigger is a refusal in the same way an unexpected table is: it means
somebody changed the schema, this code cannot know what else they changed, and
guessing is the thing that must not happen here. Refusing costs an operator one
deliberate decision. Adopting wrongly costs the audit trail its meaning.

Nothing here drops, rewrites or repairs anything. A database that is not
recognised is left exactly as it was found.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import Engine, Inspector, inspect

LEGACY_REVISION = "0001_initial_schema"
"""The revision whose schema a pre-migration database already has."""


def normalise_sql(text: str) -> str:
    """Collapse whitespace and drop a trailing semicolon.

    Two databases can hold the same rule written with different line breaks or
    indentation. Case is deliberately left alone: SQLite stores the text as it
    was written, every Phase 2 database was written by one piece of code, and
    lowering case would also lower the text inside a quoted abort message, which
    is part of what is being checked.
    """
    return " ".join(text.split()).removesuffix(";").strip()


_PUNCTUATION = re.compile(r"(<=|>=|<>|!=|=|<|>|\(|\)|,)")
"""Operators and separators, longest first so `>=` is not read as `>` then `=`."""


def _strip_enclosing_parentheses(text: str) -> str:
    """Remove one pair of parentheses that wraps the whole expression.

    Only when it really wraps the whole of it: in `(a = 1) and (b = 2)` the
    first parenthesis closes before the end, and dropping the outer characters
    would corrupt the rule rather than tidy it.
    """
    if not (text.startswith("(") and text.endswith(")")):
        return text
    depth = 0
    for position, character in enumerate(text):
        depth += (character == "(") - (character == ")")
        if depth == 0 and position < len(text) - 1:
            return text
    return text[1:-1].strip()


def normalise_expression(text: str) -> str:
    """Normalise a CHECK expression for comparison.

    Three things are treated as formatting rather than meaning: case, because a
    rule written `LENGTH(x) = 64` is the rule written `length(x) = 64`; spacing
    around operators, brackets and commas, because `x=64` is `x = 64`; and one
    enclosing pair of parentheses, because SQLite and SQLAlchemy differ about
    whether a reflected constraint keeps it.

    Nothing else is rewritten. The operands, the operator and the literal all
    still have to match, so a check loosened from `= 64` to `>= 32` is a
    difference and not a variation.

    The one thing this cannot tell apart is two string literals differing only
    in the spacing or case inside them, since it does not parse quoted text.
    None of the Phase 2 checks contains a string literal, so there is nothing
    here for that to reach.
    """
    spaced = _PUNCTUATION.sub(r" \1 ", normalise_sql(text))
    return _strip_enclosing_parentheses(" ".join(spaced.split())).casefold()


@dataclass(frozen=True)
class ColumnShape:
    """A column's declared type and whether it may be null."""

    name: str
    type_: str
    nullable: bool


@dataclass(frozen=True)
class IndexShape:
    """An index by the columns it covers, in order, and whether it is unique."""

    columns: tuple[str, ...]
    unique: bool


@dataclass(frozen=True)
class TableShape:
    """Everything about a table that Phase 2 guaranteed.

    Compared field by field rather than against the stored `CREATE TABLE` text,
    because SQLite keeps that text verbatim and the two Phase 2 build paths
    emitted their constraints in different orders while meaning the same thing.
    """

    columns: tuple[ColumnShape, ...]
    primary_key: tuple[str, ...]
    unique_constraints: frozenset[tuple[str | None, tuple[str, ...]]]
    checks: Mapping[str, str]
    indexes: Mapping[str, IndexShape]


def _trigger_definition(table: str, operation: str) -> str:
    """Return the trigger text Phase 2 wrote, as SQLite stores it.

    Copied from the `_immutability_triggers` helper that shipped in Phase 2 and
    from the initial migration, which write the same string. SQLite drops the
    `IF NOT EXISTS` and the trailing semicolon when it stores the definition, so
    neither appears here.
    """
    return normalise_sql(
        f"CREATE TRIGGER trg_{table}_no_{operation.lower()} "
        f"BEFORE {operation} ON {table} "
        "BEGIN "
        f"SELECT RAISE(ABORT, '{table} is append-only: {operation} is not permitted'); "
        "END"
    )


#: The Phase 2 schema, as both `create_all` and the initial migration produce it.
#:
#: Written out rather than reflected from a reference database, because a
#: fingerprint derived from the current code would agree with whatever the
#: current code happens to do. This is the frozen record of what shipped, and a
#: test builds a database both ways and requires each to match it, so the record
#: cannot drift away from the schema without that test failing.
PHASE_2_SCHEMA: Mapping[str, TableShape] = {
    "source_facts": TableShape(
        columns=(
            ColumnShape("source_record_id", "VARCHAR(200)", nullable=False),
            ColumnShape("source_system", "VARCHAR(32)", nullable=False),
            ColumnShape("source_record_type", "VARCHAR(32)", nullable=False),
            ColumnShape("locator_kind", "VARCHAR(32)", nullable=False),
            ColumnShape("locator_reference", "VARCHAR(200)", nullable=False),
            ColumnShape("row_number", "INTEGER", nullable=False),
            ColumnShape("provider_event_id", "VARCHAR(200)", nullable=False),
            ColumnShape("observed_at", "DATETIME", nullable=False),
            ColumnShape("occurred_at", "DATETIME", nullable=False),
            ColumnShape("payload_hash", "VARCHAR(64)", nullable=False),
            ColumnShape("canonical_payload", "JSON", nullable=False),
        ),
        primary_key=("source_record_id",),
        unique_constraints=frozenset(
            {("uq_source_facts_idempotency", ("source_system", "provider_event_id"))}
        ),
        checks={
            "ck_source_facts_hash_length": normalise_expression("length(payload_hash) = 64"),
            "ck_source_facts_row_number": normalise_expression("row_number >= 1"),
        },
        indexes={
            "ix_source_facts_payload_hash": IndexShape(("payload_hash",), unique=False),
            "ix_source_facts_source_record_type": IndexShape(("source_record_type",), unique=False),
            "ix_source_facts_source_system": IndexShape(("source_system",), unique=False),
        },
    ),
    "import_receipts": TableShape(
        columns=(
            ColumnShape("sequence", "INTEGER", nullable=False),
            ColumnShape("receipt_id", "VARCHAR(100)", nullable=False),
            ColumnShape("document_hash", "VARCHAR(64)", nullable=False),
            ColumnShape("document_name", "VARCHAR(200)", nullable=False),
            ColumnShape("source_system", "VARCHAR(32)", nullable=False),
            ColumnShape("source_record_type", "VARCHAR(32)", nullable=False),
            ColumnShape("parser_version", "VARCHAR(32)", nullable=False),
            ColumnShape("received_at", "DATETIME", nullable=False),
            ColumnShape("outcome", "VARCHAR(32)", nullable=False),
            ColumnShape("row_count", "INTEGER", nullable=False),
            ColumnShape("accepted_count", "INTEGER", nullable=False),
            ColumnShape("duplicate_count", "INTEGER", nullable=False),
            ColumnShape("conflict_count", "INTEGER", nullable=False),
            ColumnShape("rejected_count", "INTEGER", nullable=False),
            ColumnShape("row_outcomes", "JSON", nullable=False),
            ColumnShape("failure_detail", "TEXT", nullable=True),
        ),
        primary_key=("sequence",),
        # Phase 2 declared this one with `unique=True` on the column rather than
        # as a named constraint, so SQLite has no name to report for it. The
        # identity is the column it spans, which is what is compared.
        unique_constraints=frozenset({(None, ("receipt_id",))}),
        checks={
            "ck_import_receipts_hash_length": normalise_expression("length(document_hash) = 64"),
            "ck_import_receipts_row_count": normalise_expression("row_count >= 0"),
        },
        indexes={
            "ix_import_receipts_document_hash": IndexShape(("document_hash",), unique=False),
            "ix_import_receipts_outcome": IndexShape(("outcome",), unique=False),
            "ix_import_receipts_received_at": IndexShape(("received_at",), unique=False),
        },
    ),
}

#: The append-only protections a Phase 2 database must already carry, in full.
#:
#: Names alone would accept a trigger whose body does nothing, which is the one
#: way a database can look protected and not be. The whole definition is
#: compared, so the timing, the event, the table and the abort message are all
#: checked together.
#:
#: Their absence is a refusal rather than something to add. A database whose
#: triggers were dropped is one where the append-only guarantee may already have
#: been broken, and adopting it would vouch for history this system cannot
#: vouch for.
PHASE_2_TRIGGERS: Mapping[str, str] = {
    f"trg_{table}_no_{operation.lower()}": _trigger_definition(table, operation)
    for table in PHASE_2_SCHEMA
    for operation in ("UPDATE", "DELETE")
}

#: Tables that say nothing about which schema a database holds.
#:
#: `alembic_version` is Alembic's own bookkeeping. It can exist while holding no
#: row, after an interrupted first migration, and a database in that state is
#: still unstamped. Counting it as user data would refuse an otherwise empty
#: database that had once been half migrated, which is the opposite of helpful.
IGNORED_TABLES: frozenset[str] = frozenset({"alembic_version"})


class AdoptionPlan(StrEnum):
    """What to do with a database that carries no revision stamp."""

    FRESH = "FRESH"
    """Empty. Migrate from zero."""

    ADOPT_LEGACY = "ADOPT_LEGACY"
    """Exactly Phase 2. Stamp the initial revision, then migrate forward."""


class UnrecognisedSchemaError(RuntimeError):
    """Raised when an unstamped database is neither empty nor exactly Phase 2.

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


def user_tables(engine: Engine) -> frozenset[str]:
    """Return the tables a database holds, excluding SQLite's and Alembic's own."""
    return frozenset(
        name
        for name in inspect(engine).get_table_names()
        if not name.startswith("sqlite_") and name not in IGNORED_TABLES
    )


def observed_triggers(engine: Engine) -> Mapping[str, str]:
    """Return every trigger in a database, by name, with its normalised definition."""
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT name, sql FROM sqlite_master WHERE type = 'trigger'"
        ).all()
    return {str(name): normalise_sql(str(sql or "")) for name, sql in rows}


def describe_table(inspector: Inspector, table: str) -> TableShape:
    """Return the shape of one live table, in the form the fingerprint compares."""
    return TableShape(
        columns=tuple(
            ColumnShape(
                name=column["name"],
                type_=normalise_sql(str(column["type"])),
                nullable=bool(column["nullable"]),
            )
            for column in inspector.get_columns(table)
        ),
        primary_key=tuple(inspector.get_pk_constraint(table)["constrained_columns"]),
        unique_constraints=frozenset(
            (constraint["name"], tuple(constraint["column_names"]))
            for constraint in inspector.get_unique_constraints(table)
        ),
        checks={
            str(constraint["name"]): normalise_expression(constraint["sqltext"])
            for constraint in inspector.get_check_constraints(table)
        },
        indexes={
            str(index["name"]): IndexShape(
                columns=tuple(str(column) for column in index["column_names"]),
                unique=bool(index["unique"]),
            )
            for index in inspector.get_indexes(table)
        },
    )


def _column_differences(table: str, observed: TableShape, expected: TableShape) -> list[str]:
    """Report how one table's columns differ from what Phase 2 declared."""
    if observed.columns == expected.columns:
        return []

    observed_names = [column.name for column in observed.columns]
    expected_names = [column.name for column in expected.columns]
    if sorted(observed_names) != sorted(expected_names):
        missing = sorted(set(expected_names) - set(observed_names))
        unexpected = sorted(set(observed_names) - set(expected_names))
        return [
            f"table {table!r} has the wrong columns; missing {missing}, unexpected {unexpected}"
        ]
    if observed_names != expected_names:
        return [f"table {table!r} declares its columns in a different order"]

    by_name = {column.name: column for column in expected.columns}
    return [
        f"table {table!r} column {column.name!r} is "
        f"{column.type_} {'NULL' if column.nullable else 'NOT NULL'}, expected "
        f"{by_name[column.name].type_} "
        f"{'NULL' if by_name[column.name].nullable else 'NOT NULL'}"
        for column in observed.columns
        if column != by_name[column.name]
    ]


def _table_differences(table: str, observed: TableShape, expected: TableShape) -> list[str]:
    """Report every way one live table differs from what Phase 2 guaranteed."""
    differences = _column_differences(table, observed, expected)

    if observed.primary_key != expected.primary_key:
        differences.append(
            f"table {table!r} has primary key {list(observed.primary_key)}, "
            f"expected {list(expected.primary_key)}"
        )

    if observed.unique_constraints != expected.unique_constraints:
        missing = sorted(
            f"{name or 'unnamed'} over {list(columns)}"
            for name, columns in expected.unique_constraints - observed.unique_constraints
        )
        unexpected = sorted(
            f"{name or 'unnamed'} over {list(columns)}"
            for name, columns in observed.unique_constraints - expected.unique_constraints
        )
        differences.append(
            f"table {table!r} has the wrong unique constraints; "
            f"missing {missing}, unexpected {unexpected}"
        )

    for name in sorted(set(expected.checks) | set(observed.checks)):
        if name not in observed.checks:
            differences.append(f"table {table!r} is missing check {name!r}")
        elif name not in expected.checks:
            differences.append(f"table {table!r} has an unexpected check {name!r}")
        elif observed.checks[name] != expected.checks[name]:
            differences.append(
                f"table {table!r} check {name!r} is "
                f"{observed.checks[name]!r}, expected {expected.checks[name]!r}"
            )

    for name in sorted(set(expected.indexes) | set(observed.indexes)):
        if name not in observed.indexes:
            differences.append(f"table {table!r} is missing index {name!r}")
        elif name not in expected.indexes:
            differences.append(f"table {table!r} has an unexpected index {name!r}")
        elif observed.indexes[name] != expected.indexes[name]:
            found, wanted = observed.indexes[name], expected.indexes[name]
            differences.append(
                f"table {table!r} index {name!r} covers {list(found.columns)} "
                f"and is {'unique' if found.unique else 'not unique'}, expected "
                f"{list(wanted.columns)} and {'unique' if wanted.unique else 'not unique'}"
            )

    return differences


def _trigger_differences(engine: Engine) -> list[str]:
    """Report every way the append-only protections differ from Phase 2."""
    observed = observed_triggers(engine)
    differences: list[str] = []
    for name in sorted(set(PHASE_2_TRIGGERS) | set(observed)):
        if name not in observed:
            differences.append(f"append-only trigger {name!r} is missing")
        elif name not in PHASE_2_TRIGGERS:
            differences.append(f"unexpected trigger {name!r} is present")
        elif observed[name] != PHASE_2_TRIGGERS[name]:
            differences.append(
                f"trigger {name!r} is not the Phase 2 append-only trigger; "
                f"its definition is {observed[name]!r}"
            )
    return differences


def legacy_schema_differences(engine: Engine) -> tuple[str, ...]:
    """Return every way a database differs from the Phase 2 schema.

    Empty means it is exactly Phase 2 and can be adopted. Anything listed is a
    reason not to.

    Args:
        engine: The database to inspect.

    Returns:
        One sentence per difference, in a fixed order so two runs report the
        same thing.
    """
    differences: list[str] = []
    present = user_tables(engine)

    unexpected = sorted(present - set(PHASE_2_SCHEMA))
    if unexpected:
        differences.append(f"unexpected tables are present: {unexpected}")

    inspector = inspect(engine)
    for table in sorted(PHASE_2_SCHEMA):
        if table not in present:
            differences.append(f"table {table!r} is missing")
            continue
        differences.extend(
            _table_differences(table, describe_table(inspector, table), PHASE_2_SCHEMA[table])
        )

    differences.extend(_trigger_differences(engine))
    return tuple(differences)


def plan_adoption(engine: Engine) -> AdoptionPlan:
    """Decide what to do with a database that carries no revision stamp.

    Args:
        engine: The unstamped database.

    Returns:
        `FRESH` when it is empty, `ADOPT_LEGACY` when it is exactly the Phase 2
        schema.

    Raises:
        UnrecognisedSchemaError: When it is neither. Nothing is modified.
    """
    if not user_tables(engine):
        return AdoptionPlan.FRESH

    differences = legacy_schema_differences(engine)
    if differences:
        raise UnrecognisedSchemaError(differences)
    return AdoptionPlan.ADOPT_LEGACY
