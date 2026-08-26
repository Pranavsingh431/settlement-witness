"""Reconciliation run persistence.

Adds the two tables that hold what the system concluded, alongside the two that
hold what it observed. Both are append-only for the same reason: a conclusion
that can be revised in place is not an audit trail, and a decision whose
evidence has moved underneath it cannot be replayed.

Revision ID: 0002_reconciliation_runs
Revises: 0001_initial_schema
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_reconciliation_runs"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY_TABLES: tuple[str, ...] = ("reconciliation_runs", "reconciliation_decisions")


def _create_immutability_triggers(tables: Sequence[str]) -> None:
    """Make the given tables reject every UPDATE and DELETE."""
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
    """Create the reconciliation run and decision tables."""
    op.create_table(
        "reconciliation_runs",
        sa.Column("run_id", sa.String(length=100), nullable=False),
        sa.Column("run_key", sa.String(length=64), nullable=False),
        sa.Column("snapshot_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("baseline_version", sa.String(length=32), nullable=False),
        sa.Column("domain_schema_version", sa.String(length=32), nullable=False),
        sa.Column("parser_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fact_count", sa.Integer(), nullable=False),
        sa.Column("settlement_line_count", sa.Integer(), nullable=False),
        sa.Column("decision_count", sa.Integer(), nullable=False),
        sa.Column("status_counts", sa.JSON(), nullable=False),
        sa.Column("exception_counts", sa.JSON(), nullable=False),
        sa.CheckConstraint("fact_count >= 0", name="ck_reconciliation_runs_fact_count"),
        sa.CheckConstraint(
            "length(snapshot_fingerprint) = 64", name="ck_reconciliation_runs_fingerprint"
        ),
        sa.PrimaryKeyConstraint("run_id"),
        sa.UniqueConstraint("run_key", name="uq_reconciliation_runs_run_key"),
    )
    op.create_index(
        op.f("ix_reconciliation_runs_created_at"),
        "reconciliation_runs",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_reconciliation_runs_run_key"), "reconciliation_runs", ["run_key"], unique=False
    )
    op.create_index(
        op.f("ix_reconciliation_runs_snapshot_fingerprint"),
        "reconciliation_runs",
        ["snapshot_fingerprint"],
        unique=False,
    )

    op.create_table(
        "reconciliation_decisions",
        sa.Column("sequence", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=100), nullable=False),
        sa.Column("decision_id", sa.String(length=200), nullable=False),
        sa.Column("subject_settlement_line_id", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("exception_codes", sa.JSON(), nullable=False),
        sa.Column("reason_codes", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("evidence_verification", sa.JSON(), nullable=False),
        sa.Column("invariant_results", sa.JSON(), nullable=False),
        sa.Column("decision_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["reconciliation_runs.run_id"], name="fk_reconciliation_decisions_run"
        ),
        sa.PrimaryKeyConstraint("sequence"),
        sa.UniqueConstraint("run_id", "decision_id", name="uq_reconciliation_decisions_identity"),
        sa.UniqueConstraint(
            "run_id", "subject_settlement_line_id", name="uq_reconciliation_decisions_subject"
        ),
    )
    op.create_index(
        op.f("ix_reconciliation_decisions_decision_id"),
        "reconciliation_decisions",
        ["decision_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_reconciliation_decisions_run_id"),
        "reconciliation_decisions",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_reconciliation_decisions_status"),
        "reconciliation_decisions",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_reconciliation_decisions_subject_settlement_line_id"),
        "reconciliation_decisions",
        ["subject_settlement_line_id"],
        unique=False,
    )

    _create_immutability_triggers(APPEND_ONLY_TABLES)


def downgrade() -> None:
    """Drop everything this revision created."""
    _drop_immutability_triggers(APPEND_ONLY_TABLES)
    op.drop_index(
        op.f("ix_reconciliation_decisions_subject_settlement_line_id"),
        table_name="reconciliation_decisions",
    )
    op.drop_index(op.f("ix_reconciliation_decisions_status"), table_name="reconciliation_decisions")
    op.drop_index(op.f("ix_reconciliation_decisions_run_id"), table_name="reconciliation_decisions")
    op.drop_index(
        op.f("ix_reconciliation_decisions_decision_id"), table_name="reconciliation_decisions"
    )
    op.drop_table("reconciliation_decisions")
    op.drop_index(
        op.f("ix_reconciliation_runs_snapshot_fingerprint"), table_name="reconciliation_runs"
    )
    op.drop_index(op.f("ix_reconciliation_runs_run_key"), table_name="reconciliation_runs")
    op.drop_index(op.f("ix_reconciliation_runs_created_at"), table_name="reconciliation_runs")
    op.drop_table("reconciliation_runs")
