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
from sqlalchemy import select
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
from app.storage.models import ImportReceiptRow, SourceFactRow

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
