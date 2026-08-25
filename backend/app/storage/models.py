"""Database tables.

Two tables, and the difference between them is the point of this phase.

``source_facts`` holds what the system believes. It is append-only: a row is
inserted once and never updated or deleted. ``import_receipts`` holds what the
system was told and what it did about it, including the attempts it refused.
An import that is rejected writes nothing to the first table and always writes
to the second, so a refusal leaves a trace rather than a silence.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
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
