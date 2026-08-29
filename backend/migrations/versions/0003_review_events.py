"""Human review events.

Adds the one table in this schema that is not about the ledger. It records what
people did about a conclusion, beside the conclusion, and changes nothing in it.

Append-only for the same reason as everything else here, and for a sharper one:
an editable workflow history sitting next to an immutable decision would be the
worst possible asymmetry. Somebody could rewrite what was known and when, while
the thing it was known about stayed fixed.

There is no status column and no actor column. The workflow state is derived
from the events, and there is no authentication in this application so there is
nobody to attribute an event to.

Revision ID: 0003_review_events
Revises: 0002_reconciliation_runs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_review_events"
down_revision: str | None = "0002_reconciliation_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY_TABLES: tuple[str, ...] = ("review_events",)


def _create_immutability_triggers(tables: Sequence[str]) -> None:
    """Make the given tables reject every UPDATE and DELETE.

    The same wording as the earlier revisions, deliberately. The abort message
    is part of what the legacy fingerprint compares, and a table protected by a
    differently worded trigger would be a second dialect of the same rule.
    """
    if op.get_bind().dialect.name == "postgresql":
        for table in tables:
            function = f"fn_{table}_append_only"
            op.execute(
                f"CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN "
                f"RAISE EXCEPTION '{table} is append-only: % is not permitted', TG_OP; "
                "END; "
                "$$;"
            )
            for operation in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER trg_{table}_no_{operation.lower()} "
                    f"BEFORE {operation} ON {table} FOR EACH ROW "
                    f"EXECUTE FUNCTION {function}();"
                )
        return

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
    """Remove the protections, so a downgrade can drop the table."""
    if op.get_bind().dialect.name == "postgresql":
        for table in tables:
            for operation in ("update", "delete"):
                op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_{operation} ON {table}")
            op.execute(f"DROP FUNCTION IF EXISTS fn_{table}_append_only()")
        return

    for table in tables:
        for operation in ("update", "delete"):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_{operation}")


def upgrade() -> None:
    """Create the review event table."""
    op.create_table(
        "review_events",
        sa.Column("sequence", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=100), nullable=False),
        sa.Column("run_id", sa.String(length=100), nullable=False),
        sa.Column("decision_id", sa.String(length=200), nullable=False),
        sa.Column("subject_settlement_line_id", sa.String(length=200), nullable=False),
        sa.Column("decision_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("command_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('ACKNOWLEDGED', 'REQUEST_EVIDENCE', 'ESCALATED', "
            "'CLOSED_WITHOUT_OVERRIDE')",
            name="ck_review_events_action",
        ),
        sa.CheckConstraint(
            "length(command_fingerprint) = 64", name="ck_review_events_command_length"
        ),
        sa.CheckConstraint(
            "length(decision_fingerprint) = 64", name="ck_review_events_fingerprint_length"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["reconciliation_runs.run_id"], name="fk_review_events_run"
        ),
        sa.PrimaryKeyConstraint("sequence"),
        sa.UniqueConstraint("event_id", name="uq_review_events_event_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_review_events_idempotency_key"),
    )
    op.create_index(op.f("ix_review_events_action"), "review_events", ["action"], unique=False)
    op.create_index(
        op.f("ix_review_events_decision_id"), "review_events", ["decision_id"], unique=False
    )
    op.create_index(op.f("ix_review_events_run_id"), "review_events", ["run_id"], unique=False)
    op.create_index(
        op.f("ix_review_events_subject_settlement_line_id"),
        "review_events",
        ["subject_settlement_line_id"],
        unique=False,
    )

    _create_immutability_triggers(APPEND_ONLY_TABLES)


def downgrade() -> None:
    """Drop everything this revision created."""
    _drop_immutability_triggers(APPEND_ONLY_TABLES)
    op.drop_index(op.f("ix_review_events_subject_settlement_line_id"), table_name="review_events")
    op.drop_index(op.f("ix_review_events_run_id"), table_name="review_events")
    op.drop_index(op.f("ix_review_events_decision_id"), table_name="review_events")
    op.drop_index(op.f("ix_review_events_action"), table_name="review_events")
    op.drop_table("review_events")
