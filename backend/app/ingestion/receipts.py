"""The result of one import attempt, as a typed record.

Held apart from the service that produces it because it is also what the
storage layer rebuilds when a receipt is read back, and what the API serves. A
receipt outlives the import that made it: it is the audit trail, so its shape is
a contract of its own rather than an implementation detail of the service.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.domain.facts import SourceRecordType, SourceSystem


class ImportOutcome(StrEnum):
    """What happened to a whole document."""

    ACCEPTED = "ACCEPTED"
    """Every row was new. All of them were stored."""

    DUPLICATE_NO_OP = "DUPLICATE_NO_OP"
    """Every row was already stored with the same payload. Nothing changed, and
    that is the correct result rather than an error. Re-running an import must
    be safe."""

    REJECTED_CONFLICT = "REJECTED_CONFLICT"
    """At least one row contradicts a fact already stored. Nothing was written."""

    REJECTED_INVALID = "REJECTED_INVALID"
    """At least one row could not be read, or the document itself could not be.
    Nothing was written."""


class RowOutcome(StrEnum):
    """What happened to one row."""

    ACCEPTED = "ACCEPTED"
    """The row was new and was stored."""

    DUPLICATE_NO_OP = "DUPLICATE_NO_OP"
    """The row was already stored with the same payload. Nothing to do."""

    DUPLICATE_CONFLICT = "DUPLICATE_CONFLICT"
    """The row contradicts a stored fact with the same identity."""

    REJECTED = "REJECTED"
    """The row could not be read."""

    NOT_APPLIED = "NOT_APPLIED"
    """The row was fine, and the document it belonged to was not.

    A rejected import writes nothing, so a row that would have been accepted was
    still not accepted. Recording it as ACCEPTED on a rejected receipt would
    claim a fact exists that does not. This says the row was acceptable and
    explains why it is not in the store, which is what a person re-reading the
    audit trail needs to know."""


class RowResult(BaseModel):
    """The recorded result for one row, as it appears on the receipt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    row_number: int
    outcome: RowOutcome
    source_record_id: str | None = None
    code: str | None = None
    detail: str | None = None


class ImportReceipt(BaseModel):
    """The complete, auditable result of one import attempt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str
    document_hash: str
    document_name: str
    source_system: SourceSystem
    source_record_type: SourceRecordType
    parser_version: str
    received_at: datetime
    outcome: ImportOutcome
    row_count: int
    accepted_count: int
    duplicate_count: int
    conflict_count: int
    rejected_count: int
    row_results: tuple[RowResult, ...]
    failure_detail: str | None = None

    @property
    def wrote_facts(self) -> bool:
        """Return True when this import added at least one fact."""
        return self.accepted_count > 0
