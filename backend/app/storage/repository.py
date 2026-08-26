"""Repositories over the two tables.

These are the only place that turns database rows into domain objects and back.
Nothing outside this module writes SQL, and nothing inside it decides anything
about reconciliation.

Both repositories are append-only by construction: there is no update method and
no delete method, so a caller cannot rewrite history by mistake.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from pydantic import TypeAdapter
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.domain.evidence import SourceFactIndex, build_fact_index
from app.domain.facts import (
    IdempotencyKey,
    SourceFact,
    SourceLocator,
    SourceLocatorKind,
    SourceRecordType,
    SourceSystem,
)
from app.domain.primitives import CanonicalPayload
from app.ingestion.receipts import ImportOutcome, ImportReceipt, RowResult
from app.storage.models import ImportReceiptRow, SourceFactRow

_ROW_OUTCOMES_ADAPTER: TypeAdapter[tuple[RowResult, ...]] = TypeAdapter(tuple[RowResult, ...])
"""Revalidates the row outcomes read back from a receipt.

Same reasoning as the payload adapter below. A stored outcome that is no longer
a member of the enum, or a row result that has grown a field, fails here rather
than being served as though the audit trail still meant what it says."""

_PAYLOAD_ADAPTER: TypeAdapter[CanonicalPayload] = TypeAdapter(CanonicalPayload)
"""Revalidates a payload read back from the database.

Stored JSON is untrusted input like any other. Running it through the
contract's own type on the way out means a payload that acquired a float,
by hand or by a future migration, is refused here rather than reaching a
reconciliation check."""


def _to_domain(row: SourceFactRow) -> SourceFact:
    """Rebuild a source fact from its stored row.

    The fact is revalidated by the domain model on the way out, including its
    payload hash. A row that was corrupted in the database therefore fails here
    rather than being handed to a verifier as though it were sound.
    """
    payload = _PAYLOAD_ADAPTER.validate_python(row.canonical_payload)
    return SourceFact(
        source_record_id=row.source_record_id,
        source_system=SourceSystem(row.source_system),
        source_record_type=SourceRecordType(row.source_record_type),
        source_locator=SourceLocator(
            kind=SourceLocatorKind(row.locator_kind),
            reference=row.locator_reference,
            row_number=row.row_number,
        ),
        provider_event_id=row.provider_event_id,
        observed_at=_as_utc(row.observed_at),
        occurred_at=_as_utc(row.occurred_at),
        payload_hash=row.payload_hash,
        canonical_payload=payload,
    )


def _receipt_to_domain(row: ImportReceiptRow) -> ImportReceipt:
    """Rebuild an import receipt from its stored row.

    Every stored value is put back through the model that wrote it: the two
    enums, the outcome, and the row results held as JSON. A receipt that no
    longer satisfies the contract therefore fails here rather than being served
    as though it were sound, which is the same rule the fact and decision read
    paths follow.
    """
    return ImportReceipt(
        receipt_id=row.receipt_id,
        document_hash=row.document_hash,
        document_name=row.document_name,
        source_system=SourceSystem(row.source_system),
        source_record_type=SourceRecordType(row.source_record_type),
        parser_version=row.parser_version,
        received_at=_as_utc(row.received_at),
        outcome=ImportOutcome(row.outcome),
        row_count=row.row_count,
        accepted_count=row.accepted_count,
        duplicate_count=row.duplicate_count,
        conflict_count=row.conflict_count,
        rejected_count=row.rejected_count,
        row_results=_ROW_OUTCOMES_ADAPTER.validate_python(row.row_outcomes),
        failure_detail=row.failure_detail,
    )


def _as_utc(value: datetime) -> datetime:
    """Return a stored timestamp as an aware UTC datetime.

    SQLite has no timestamp type, so a datetime comes back without its offset.
    Everything written here was converted to UTC before storage, which is what
    makes reattaching UTC correct rather than a guess.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _to_row(fact: SourceFact) -> SourceFactRow:
    """Flatten a source fact into its stored row."""
    return SourceFactRow(
        source_record_id=fact.source_record_id,
        source_system=fact.source_system.value,
        source_record_type=fact.source_record_type.value,
        locator_kind=fact.source_locator.kind.value,
        locator_reference=fact.source_locator.reference,
        row_number=fact.source_locator.row_number or 1,
        provider_event_id=fact.provider_event_id,
        observed_at=fact.observed_at,
        occurred_at=fact.occurred_at,
        payload_hash=fact.payload_hash,
        canonical_payload=dict(fact.canonical_payload),
    )


class SourceFactRepository:
    """Read and append source facts. There is no way to change one."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, source_record_id: str) -> SourceFact | None:
        """Return the fact stored under a source record ID, if any."""
        row = self._session.get(SourceFactRow, source_record_id)
        return _to_domain(row) if row is not None else None

    def find_by_idempotency_key(self, key: IdempotencyKey) -> SourceFact | None:
        """Return the fact already holding this identity, if any.

        This is the lookup that makes duplicate detection possible. It uses the
        identity the domain contract defines, which is the source system and the
        provider event ID together.
        """
        statement = select(SourceFactRow).where(
            SourceFactRow.source_system == key.source_system.value,
            SourceFactRow.provider_event_id == key.provider_event_id,
        )
        row = self._session.scalars(statement).one_or_none()
        return _to_domain(row) if row is not None else None

    def add(self, fact: SourceFact) -> None:
        """Append one fact. Never call this for a record ID that already exists."""
        self._session.add(_to_row(fact))

    def count(self) -> int:
        """Return how many facts are stored."""
        return len(list(self._session.scalars(select(SourceFactRow.source_record_id))))

    def all_facts(self) -> tuple[SourceFact, ...]:
        """Return every accepted fact, ordered by source record ID.

        The order is fixed so that two reads of the same database produce the
        same sequence, which matters when a result is hashed or compared.
        """
        statement = select(SourceFactRow).order_by(SourceFactRow.source_record_id)
        return tuple(_to_domain(row) for row in self._session.scalars(statement))

    def fact_index(self) -> SourceFactIndex:
        """Return the complete accepted fact index, for `verify_decision`.

        This is the read path Phase 1.1 was designed around. It returns every
        accepted fact, not a filtered view, because completeness is what makes a
        verification result meaningful.

        A partial index is safe but not useful: a citation whose fact was left
        out resolves to nothing, so the decision comes back
        `INSUFFICIENT_EVIDENCE` rather than a wrong resolution. Safe is not the
        same as correct, which is why this method exists instead of leaving each
        caller to assemble its own set.
        """
        return build_fact_index(self.all_facts())


class ImportReceiptRepository:
    """Read and append import receipts. There is no way to change one."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, receipt: ImportReceiptRow) -> None:
        """Append one receipt."""
        self._session.add(receipt)

    def get(self, receipt_id: str) -> ImportReceiptRow | None:
        """Return one receipt by its identifier.

        Looked up on ``receipt_id`` rather than the primary key: the key is the
        database assigned sequence, which callers neither see nor hold.
        """
        statement = select(ImportReceiptRow).where(ImportReceiptRow.receipt_id == receipt_id)
        return self._session.scalars(statement).one_or_none()

    def all_receipts(self) -> Sequence[ImportReceiptRow]:
        """Return every receipt in the order the attempts were made.

        Ordering is by the database assigned sequence, not by received-at. Two
        attempts can share a received-at time, and an audit trail that reorders
        them is not an audit trail.
        """
        statement = select(ImportReceiptRow).order_by(ImportReceiptRow.sequence)
        return list(self._session.scalars(statement))

    def for_document(self, document_hash: str) -> Sequence[ImportReceiptRow]:
        """Return every attempt to import one document, in order."""
        statement = (
            select(ImportReceiptRow)
            .where(ImportReceiptRow.document_hash == document_hash)
            .order_by(ImportReceiptRow.sequence)
        )
        return list(self._session.scalars(statement))

    def find(self, receipt_id: str) -> ImportReceipt | None:
        """Return one receipt as a typed record, revalidated on the way out."""
        row = self.get(receipt_id)
        return _receipt_to_domain(row) if row is not None else None

    def page(
        self,
        *,
        limit: int,
        offset: int,
        outcome: ImportOutcome | None = None,
        source_system: SourceSystem | None = None,
        record_type: SourceRecordType | None = None,
    ) -> tuple[ImportReceipt, ...]:
        """Return one page of receipts, newest attempt first.

        Ordered by the database assigned sequence descending. The sequence is
        the primary key, so it is already a total order and no tie-breaker is
        needed: two receipts cannot share one. Ordering by received-at would
        need one, because two attempts can share a timestamp, and an audit trail
        that reorders attempts between two identical calls is not an audit
        trail.

        Args:
            limit: How many receipts to return.
            offset: How many to skip.
            outcome: Return only receipts with this document level outcome.
            source_system: Return only receipts for this declared system.
            record_type: Return only receipts read as this record type.

        Returns:
            The receipts on that page, revalidated on the way out.
        """
        statement = (
            self._filtered(
                select(ImportReceiptRow),
                outcome=outcome,
                source_system=source_system,
                record_type=record_type,
            )
            .order_by(ImportReceiptRow.sequence.desc())
            .limit(limit)
            .offset(offset)
        )
        return tuple(_receipt_to_domain(row) for row in self._session.scalars(statement))

    def count(
        self,
        *,
        outcome: ImportOutcome | None = None,
        source_system: SourceSystem | None = None,
        record_type: SourceRecordType | None = None,
    ) -> int:
        """Return how many receipts match these filters.

        Counts the filtered query rather than the whole table, so a caller
        paging through a filtered list is told how many pages that list has
        rather than how many the unfiltered one would have had.
        """
        statement = self._filtered(
            select(func.count()).select_from(ImportReceiptRow),
            outcome=outcome,
            source_system=source_system,
            record_type=record_type,
        )
        return self._session.scalars(statement).one()

    @staticmethod
    def _filtered[T](
        statement: Select[tuple[T]],
        *,
        outcome: ImportOutcome | None,
        source_system: SourceSystem | None,
        record_type: SourceRecordType | None,
    ) -> Select[tuple[T]]:
        """Apply the receipt filters to a select.

        Shared by the page and the count so the two cannot drift apart, which
        would show up as a total that does not match the list it describes.
        """
        if outcome is not None:
            statement = statement.where(ImportReceiptRow.outcome == outcome.value)
        if source_system is not None:
            statement = statement.where(ImportReceiptRow.source_system == source_system.value)
        if record_type is not None:
            statement = statement.where(ImportReceiptRow.source_record_type == record_type.value)
        return statement
