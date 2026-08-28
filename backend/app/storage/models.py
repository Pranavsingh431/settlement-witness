"""Database tables.

Five tables, all append-only.

``source_facts`` holds what the system believes. It is append-only: a row is
inserted once and never updated or deleted. ``import_receipts`` holds what the
system was told and what it did about it, including the attempts it refused.
An import that is rejected writes nothing to the first table and always writes
to the second, so a refusal leaves a trace rather than a silence.

``reconciliation_runs`` and ``reconciliation_decisions`` hold what the system
concluded. A run is a statement about one snapshot of facts under one set of
rule versions, and it is never revised. New facts, or a new rule version,
produce a new run beside the old one, so the history of what was concluded and
on what evidence stays intact.

``review_events`` holds what people did about those conclusions. It is the only
table that is not about the ledger, and it changes nothing in the four above.

Every table here is protected by triggers that abort UPDATE and DELETE.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for every table in this schema."""


class SourceFactRow(Base):
    """One immutable observation, exactly as the domain contract defines it.

    Nothing in this application updates a row of this table. A correction is a
    new fact from a later document, which is what append-only means in practice.
    """

    __tablename__ = "source_facts"
    __table_args__ = (
        # The idempotency identity from the domain contract, enforced by the
        # database rather than only by the code that happens to write to it.
        UniqueConstraint("source_system", "provider_event_id", name="uq_source_facts_idempotency"),
        CheckConstraint("length(payload_hash) = 64", name="ck_source_facts_hash_length"),
        CheckConstraint("row_number >= 1", name="ck_source_facts_row_number"),
    )

    source_record_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    source_system: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_record_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    locator_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    locator_reference: Mapped[str] = mapped_column(String(200), nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    canonical_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


class ImportReceiptRow(Base):
    """One import attempt, accepted or not.

    Append-only in the same way. A later import never edits an earlier receipt,
    so the history of what was tried stays intact and a rejected import is as
    visible as a successful one.
    """

    __tablename__ = "import_receipts"
    __table_args__ = (
        CheckConstraint("length(document_hash) = 64", name="ck_import_receipts_hash_length"),
        CheckConstraint("row_count >= 0", name="ck_import_receipts_row_count"),
    )

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    """Insertion order, assigned by the database.

    An audit trail has to be readable in the order things happened. The receipt
    identifier is a random uuid, so sorting by it would give a stable but
    meaningless order, and two attempts in the same second would appear to have
    happened in the wrong sequence."""

    receipt_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    document_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    document_name: Mapped[str] = mapped_column(String(200), nullable=False)
    """A label for people, such as a file name. Never used as an identifier."""

    source_system: Mapped[str] = mapped_column(String(32), nullable=False)
    source_record_type: Mapped[str] = mapped_column(String(32), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    conflict_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False)
    row_outcomes: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    """One entry per row: its number, what happened to it, and any code."""

    failure_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Why a whole document was refused, when it was."""


class ReconciliationRunRow(Base):
    """One complete reconciliation over one snapshot of facts.

    Immutable. A run records what the baseline concluded about a specific set of
    source facts under a specific set of rule versions, and nothing revises it.
    When the facts change or a rule version moves, the next reconciliation is a
    new run, and the old one remains readable exactly as it was.
    """

    __tablename__ = "reconciliation_runs"
    __table_args__ = (
        # The idempotency identity of a run. Two reconciliations over the same
        # snapshot under the same rules are the same run, and the database
        # enforces that rather than trusting the code that writes to it.
        UniqueConstraint("run_key", name="uq_reconciliation_runs_run_key"),
        CheckConstraint(
            "length(snapshot_fingerprint) = 64", name="ck_reconciliation_runs_fingerprint"
        ),
        CheckConstraint("fact_count >= 0", name="ck_reconciliation_runs_fact_count"),
    )

    run_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    run_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    """Digest of the snapshot fingerprint and every rule version behind the run.

    A changed fact set changes it, and so does a changed baseline, parser or
    domain contract version. That is deliberate: the same facts under different
    rules are a different conclusion and deserve a separate record."""

    snapshot_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    baseline_version: Mapped[str] = mapped_column(String(32), nullable=False)
    domain_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    """When the run was persisted. Wall clock, unlike as_of."""

    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    """The snapshot time the decisions describe, from the latest observed fact."""

    fact_count: Mapped[int] = mapped_column(Integer, nullable=False)
    settlement_line_count: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status_counts: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False)
    exception_counts: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False)


class ReconciliationDecisionRow(Base):
    """One decision within one run.

    The columns that are queried are stored as columns, and the complete
    decision is stored as canonical JSON beside them. The JSON is the record:
    it round-trips through the domain model exactly, so a stored decision can be
    replayed and re-verified rather than merely summarised.
    """

    __tablename__ = "reconciliation_decisions"
    __table_args__ = (
        UniqueConstraint("run_id", "decision_id", name="uq_reconciliation_decisions_identity"),
        UniqueConstraint(
            "run_id", "subject_settlement_line_id", name="uq_reconciliation_decisions_subject"
        ),
        ForeignKeyConstraint(
            ["run_id"], ["reconciliation_runs.run_id"], name="fk_reconciliation_decisions_run"
        ),
    )

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    decision_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    subject_settlement_line_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    exception_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    evidence: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    evidence_verification: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    invariant_results: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    decision_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    """The complete decision, exactly as the domain model serialises it.

    Kept whole rather than reassembled from the columns above, so replay uses
    what was decided rather than a reconstruction of it."""


class ReviewEventRow(Base):
    """One recorded human review action against one recorded decision.

    Append-only like everything else here, and for a sharper reason: an editable
    workflow history would let somebody rewrite what was known and when, beside
    a decision that cannot be rewritten at all. The asymmetry would be the worst
    possible one.

    There is no status column. The workflow state is derived from the events
    every time it is asked for, because a stored status and an event log can
    disagree and then somebody has to decide which one is true.

    There is no actor column either. This application has no authentication, so
    there is nobody to record. A column filled with a name typed into a box
    would look like accountability and provide none.
    """

    __tablename__ = "review_events"
    __table_args__ = (
        # A retry carries the same key and returns the original event. The
        # database enforces that rather than trusting the service to look first.
        UniqueConstraint("idempotency_key", name="uq_review_events_idempotency_key"),
        UniqueConstraint("event_id", name="uq_review_events_event_id"),
        CheckConstraint(
            "action IN ('ACKNOWLEDGED', 'REQUEST_EVIDENCE', 'ESCALATED', "
            "'CLOSED_WITHOUT_OVERRIDE')",
            name="ck_review_events_action",
        ),
        CheckConstraint(
            "length(decision_fingerprint) = 64", name="ck_review_events_fingerprint_length"
        ),
        CheckConstraint("length(command_fingerprint) = 64", name="ck_review_events_command_length"),
        ForeignKeyConstraint(
            ["run_id"], ["reconciliation_runs.run_id"], name="fk_review_events_run"
        ),
    )

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    """Insertion order, assigned by the database.

    The only ordering this workflow uses. Timestamps are recorded and never
    sorted on: two events in the same millisecond still have an order, and a
    clock that steps backwards cannot reorder a history."""

    event_id: Mapped[str] = mapped_column(String(100), nullable=False)
    run_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    decision_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    subject_settlement_line_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    decision_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    """A digest of the decision the reviewer was looking at when they acted."""

    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    """A sentence from a person, stored and served as plain text."""

    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    command_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    """What the command asked for, so a retry can be told from a reuse."""

    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
