"""Bank finality audits.

Adds the two tables that hold a second conclusion about the same facts: whether
a bank says a payout arrived. Separate from the reconciliation tables because
they answer a separate question from separate evidence, and nothing in them can
change a decision.

Append-only for the same reason as everything else here. An audit that said
"no statement has been imported" is a true statement about a moment, and it stays
true afterwards. Importing the statement makes a new snapshot and a new audit
beside it rather than rewriting what was known before.

Revision ID: 0004_bank_finality_audits
Revises: 0003_review_events
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_bank_finality_audits"
down_revision: str | None = "0003_review_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY_TABLES: tuple[str, ...] = (
    "bank_finality_audits",
    "bank_finality_certificates",
)


def _create_immutability_triggers(tables: Sequence[str]) -> None:
    """Make the given tables reject every UPDATE and DELETE.

    The same wording as every earlier revision. The abort message is part of
    what the legacy fingerprint compares, and a table protected by a differently
    worded trigger would be a second dialect of the same rule.
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
    """Create the bank finality audit and certificate tables."""
    op.create_table(
        "bank_finality_audits",
        sa.Column("audit_id", sa.String(length=100), nullable=False),
        sa.Column("audit_key", sa.String(length=64), nullable=False),
        sa.Column("snapshot_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("bank_finality_version", sa.String(length=32), nullable=False),
        sa.Column("bank_statement_schema_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fact_count", sa.Integer(), nullable=False),
        sa.Column("payout_count", sa.Integer(), nullable=False),
        sa.Column("bank_transaction_count", sa.Integer(), nullable=False),
        sa.Column("outcome_counts", sa.JSON(), nullable=False),
        sa.CheckConstraint("fact_count >= 0", name="ck_bank_finality_audits_fact_count"),
        sa.CheckConstraint(
            "length(snapshot_fingerprint) = 64", name="ck_bank_finality_audits_fingerprint"
        ),
        sa.PrimaryKeyConstraint("audit_id"),
        sa.UniqueConstraint("audit_key", name="uq_bank_finality_audits_audit_key"),
    )
    op.create_index(
        op.f("ix_bank_finality_audits_audit_key"),
        "bank_finality_audits",
        ["audit_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_bank_finality_audits_created_at"),
        "bank_finality_audits",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_bank_finality_audits_snapshot_fingerprint"),
        "bank_finality_audits",
        ["snapshot_fingerprint"],
        unique=False,
    )

    op.create_table(
        "bank_finality_certificates",
        sa.Column("sequence", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("audit_id", sa.String(length=100), nullable=False),
        sa.Column("payout_id", sa.String(length=200), nullable=False),
        sa.Column("outcome", sa.String(length=40), nullable=False),
        sa.Column("bank_reference", sa.String(length=200), nullable=True),
        sa.Column("matched_bank_transaction_ids", sa.JSON(), nullable=False),
        sa.Column("certificate_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["audit_id"],
            ["bank_finality_audits.audit_id"],
            name="fk_bank_finality_certificates_audit",
        ),
        sa.PrimaryKeyConstraint("sequence"),
        sa.UniqueConstraint("audit_id", "payout_id", name="uq_bank_finality_certificates_payout"),
    )
    op.create_index(
        op.f("ix_bank_finality_certificates_audit_id"),
        "bank_finality_certificates",
        ["audit_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_bank_finality_certificates_outcome"),
        "bank_finality_certificates",
        ["outcome"],
        unique=False,
    )
    op.create_index(
        op.f("ix_bank_finality_certificates_payout_id"),
        "bank_finality_certificates",
        ["payout_id"],
        unique=False,
    )

    _create_immutability_triggers(APPEND_ONLY_TABLES)


def downgrade() -> None:
    """Drop everything this revision created."""
    _drop_immutability_triggers(APPEND_ONLY_TABLES)
    op.drop_index(
        op.f("ix_bank_finality_certificates_payout_id"), table_name="bank_finality_certificates"
    )
    op.drop_index(
        op.f("ix_bank_finality_certificates_outcome"), table_name="bank_finality_certificates"
    )
    op.drop_index(
        op.f("ix_bank_finality_certificates_audit_id"), table_name="bank_finality_certificates"
    )
    op.drop_table("bank_finality_certificates")
    op.drop_index(
        op.f("ix_bank_finality_audits_snapshot_fingerprint"), table_name="bank_finality_audits"
    )
    op.drop_index(op.f("ix_bank_finality_audits_created_at"), table_name="bank_finality_audits")
    op.drop_index(op.f("ix_bank_finality_audits_audit_key"), table_name="bank_finality_audits")
    op.drop_table("bank_finality_audits")
