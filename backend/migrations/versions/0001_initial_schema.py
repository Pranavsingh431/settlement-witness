"""Initial schema: source facts, import receipts and their append-only triggers.

This is the Phase 2 schema, written as a migration so that a database created
before migrations existed can be brought forward without losing rows. It is
deliberately identical to what `create_all` produced, and a test compares the
two so they cannot drift.

Revision ID: 0001_initial_schema
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY_TABLES: tuple[str, ...] = ("source_facts", "import_receipts")


def _create_immutability_triggers(tables: Sequence[str]) -> None:
    """Make the given tables reject every UPDATE and DELETE.

    Append-only is a property of the data, not of the code path that happens to
    reach it. The repositories have no update or delete method, and that does
    nothing about a migration script or a maintenance session.
    """
    for table in tables:
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER IF NOT EXISTS trg_{table}_no_{operation.lower()} "
                f"BEFORE {operation} ON {table} "
                "BEGIN "
                f"SELECT RAISE(ABORT, '{table} is append-only: {operation} is not permitted'); "
                "END;"
            )


def _drop_immutability_triggers(tables: Sequence[str]) -> None:
    """Remove the protections, so a downgrade can drop the tables."""
    for table in tables:
        for operation in ("update", "delete"):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_{operation}")


def upgrade() -> None:
    """Create the source fact and import receipt tables."""
    op.create_table(
        "source_facts",
        sa.Column("source_record_id", sa.String(length=200), nullable=False),
        sa.Column("source_system", sa.String(length=32), nullable=False),
        sa.Column("source_record_type", sa.String(length=32), nullable=False),
        sa.Column("locator_kind", sa.String(length=32), nullable=False),
        sa.Column("locator_reference", sa.String(length=200), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("provider_event_id", sa.String(length=200), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("canonical_payload", sa.JSON(), nullable=False),
        sa.CheckConstraint("length(payload_hash) = 64", name="ck_source_facts_hash_length"),
        sa.CheckConstraint("row_number >= 1", name="ck_source_facts_row_number"),
        sa.PrimaryKeyConstraint("source_record_id"),
        sa.UniqueConstraint(
            "source_system", "provider_event_id", name="uq_source_facts_idempotency"
        ),
    )
    op.create_index(
        op.f("ix_source_facts_payload_hash"), "source_facts", ["payload_hash"], unique=False
    )
    op.create_index(
        op.f("ix_source_facts_source_record_type"),
        "source_facts",
        ["source_record_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_source_facts_source_system"), "source_facts", ["source_system"], unique=False
    )

    op.create_table(
        "import_receipts",
        sa.Column("sequence", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("receipt_id", sa.String(length=100), nullable=False),
        sa.Column("document_hash", sa.String(length=64), nullable=False),
        sa.Column("document_name", sa.String(length=200), nullable=False),
        sa.Column("source_system", sa.String(length=32), nullable=False),
        sa.Column("source_record_type", sa.String(length=32), nullable=False),
        sa.Column("parser_version", sa.String(length=32), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("accepted_count", sa.Integer(), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), nullable=False),
        sa.Column("conflict_count", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        sa.Column("row_outcomes", sa.JSON(), nullable=False),
        sa.Column("failure_detail", sa.Text(), nullable=True),
        sa.CheckConstraint("length(document_hash) = 64", name="ck_import_receipts_hash_length"),
        sa.CheckConstraint("row_count >= 0", name="ck_import_receipts_row_count"),
        sa.PrimaryKeyConstraint("sequence"),
        sa.UniqueConstraint("receipt_id"),
    )
    op.create_index(
        op.f("ix_import_receipts_document_hash"),
        "import_receipts",
        ["document_hash"],
        unique=False,
    )
    op.create_index(
        op.f("ix_import_receipts_outcome"), "import_receipts", ["outcome"], unique=False
    )
    op.create_index(
        op.f("ix_import_receipts_received_at"), "import_receipts", ["received_at"], unique=False
    )

    _create_immutability_triggers(APPEND_ONLY_TABLES)


def downgrade() -> None:
    """Drop everything this revision created."""
    _drop_immutability_triggers(APPEND_ONLY_TABLES)
    op.drop_index(op.f("ix_import_receipts_received_at"), table_name="import_receipts")
    op.drop_index(op.f("ix_import_receipts_outcome"), table_name="import_receipts")
    op.drop_index(op.f("ix_import_receipts_document_hash"), table_name="import_receipts")
    op.drop_table("import_receipts")
    op.drop_index(op.f("ix_source_facts_source_system"), table_name="source_facts")
    op.drop_index(op.f("ix_source_facts_source_record_type"), table_name="source_facts")
    op.drop_index(op.f("ix_source_facts_payload_hash"), table_name="source_facts")
    op.drop_table("source_facts")
