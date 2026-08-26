"""The import service.

One import is one document, read as one record type, from one source system.

Two rules govern everything here.

**A document is accepted whole or not at all.** One malformed row, or one
conflict with a fact already stored, rejects the entire import. A half-loaded
file is worse than a rejected one, because the gap is invisible: a later
reconciliation would report a missing settlement that was never missing, only
never loaded.

**Every attempt leaves a receipt.** Facts are written inside a savepoint that is
rolled back when the import is refused, and the receipt is written outside it.
So a rejection writes no facts and still records what was tried, what was wrong
with it, and when.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.facts import (
    IdempotencyKey,
    IngestionOutcome,
    SourceFact,
    SourceRecordType,
    SourceSystem,
    classify_ingestion,
)
from app.ingestion.errors import DocumentError, RowError
from app.ingestion.parsing import (
    build_source_fact,
    compute_document_hash,
    iter_document_rows,
    parse_row,
)
from app.ingestion.receipts import ImportOutcome, ImportReceipt, RowOutcome, RowResult
from app.ingestion.schemas import PARSER_VERSION
from app.storage.models import ImportReceiptRow
from app.storage.repository import ImportReceiptRepository, SourceFactRepository


class ImportService:
    """Reads a document, decides row by row, and commits all or nothing."""

    def __init__(self, session: Session, *, now: datetime | None = None) -> None:
        """Create a service bound to one session.

        Args:
            session: The unit of work this import runs in.
            now: The observed-at time to stamp on every fact. Injected so a test
                can freeze it, and so every fact from one document shares one
                observation time rather than drifting across a large file.
        """
        self._session = session
        self._facts = SourceFactRepository(session)
        self._receipts = ImportReceiptRepository(session)
        self._now = now or datetime.now(UTC)

    def import_document(
        self,
        content: bytes,
        *,
        source_system: SourceSystem,
        record_type: SourceRecordType,
        document_name: str,
    ) -> ImportReceipt:
        """Import one document and return its receipt.

        The receipt is committed whatever the outcome. Facts are committed only
        when every row was acceptable.

        Args:
            content: The raw document bytes. Hashed as-is.
            source_system: Which system this document came from. Declared by the
                caller, never guessed from the file.
            record_type: Which schema to read it as, also declared.
            document_name: A label for people, such as a file name. It is stored
                for readability and is never used as an identifier.

        Returns:
            The receipt describing what happened.
        """
        document_hash = compute_document_hash(content)
        receipt_id = uuid4().hex

        try:
            rows = list(iter_document_rows(content, record_type))
        except DocumentError as error:
            return self._record(
                self._document_failure(
                    receipt_id, document_hash, document_name, source_system, record_type, error
                )
            )

        results, pending, row_errors, conflicts = self._examine(
            rows, source_system, record_type, document_hash
        )

        receipt = self._build_receipt(
            receipt_id=receipt_id,
            document_hash=document_hash,
            document_name=document_name,
            source_system=source_system,
            record_type=record_type,
            results=results,
            row_errors=row_errors,
            conflicts=conflicts,
        )

        if receipt.outcome in (ImportOutcome.ACCEPTED, ImportOutcome.DUPLICATE_NO_OP):
            receipt = self._append_facts(receipt, pending)

        return self._record(receipt)

    def _examine(
        self,
        rows: Sequence[tuple[int, list[str]]],
        source_system: SourceSystem,
        record_type: SourceRecordType,
        document_hash: str,
    ) -> tuple[list[RowResult], list[SourceFact], list[RowError], list[str]]:
        """Decide every row without writing anything.

        Every row is examined even after the first failure, so one import tells a
        person everything wrong with their file instead of one problem per run.
        """
        results: list[RowResult] = []
        pending: list[SourceFact] = []
        row_errors: list[RowError] = []
        conflicts: list[str] = []

        for row_number, cells in rows:
            parsed, errors = parse_row(cells, record_type, row_number, document_hash, source_system)
            if parsed is None:
                row_errors.extend(errors)
                first = errors[0]
                results.append(
                    RowResult(
                        row_number=row_number,
                        outcome=RowOutcome.REJECTED,
                        code=first.code.value,
                        detail=first.message,
                    )
                )
                continue

            built = build_source_fact(parsed, source_system, record_type, document_hash, self._now)
            if isinstance(built, RowError):
                row_errors.append(built)
                results.append(
                    RowResult(
                        row_number=row_number,
                        outcome=RowOutcome.REJECTED,
                        source_record_id=parsed.source_record_id,
                        code=built.code.value,
                        detail=built.message,
                    )
                )
                continue

            results.append(self._classify(built, pending, conflicts))

        return results, pending, row_errors, conflicts

    def _classify(
        self, fact: SourceFact, pending: list[SourceFact], conflicts: list[str]
    ) -> RowResult:
        """Decide one well formed row against what is stored and what is pending.

        A row is compared against the database and against the rows earlier in
        the same document, because a file can contradict itself and that has to
        be caught before anything is written.
        """
        row_number = fact.source_locator.row_number or 1

        stored_by_id = self._facts.get(fact.source_record_id)
        existing = stored_by_id or self._facts.find_by_idempotency_key(fact.idempotency_key)

        if existing is None:
            existing = next(
                (
                    candidate
                    for candidate in pending
                    if candidate.idempotency_key == fact.idempotency_key
                    or candidate.source_record_id == fact.source_record_id
                ),
                None,
            )

        outcome = classify_ingestion(fact, existing) if existing else IngestionOutcome.NEW

        if outcome is IngestionOutcome.DUPLICATE_CONFLICT:
            conflicts.append(fact.source_record_id)
            return RowResult(
                row_number=row_number,
                outcome=RowOutcome.DUPLICATE_CONFLICT,
                source_record_id=fact.source_record_id,
                code=IngestionOutcome.DUPLICATE_CONFLICT.value,
                detail=(
                    "a fact with this identity is already stored with a different "
                    f"payload hash; stored {existing.payload_hash if existing else ''}, "
                    f"incoming {fact.payload_hash}"
                ),
            )

        if outcome is IngestionOutcome.DUPLICATE_NO_OP:
            return RowResult(
                row_number=row_number,
                outcome=RowOutcome.DUPLICATE_NO_OP,
                source_record_id=fact.source_record_id,
                code=IngestionOutcome.DUPLICATE_NO_OP.value,
            )

        pending.append(fact)
        return RowResult(
            row_number=row_number,
            outcome=RowOutcome.ACCEPTED,
            source_record_id=fact.source_record_id,
        )

    def _append_facts(self, receipt: ImportReceipt, pending: Sequence[SourceFact]) -> ImportReceipt:
        """Append every new fact inside a savepoint, or none of them.

        The row by row examination has already decided the import is acceptable.
        This is the second line of defence: the database also enforces the
        idempotency identity, and if it disagrees with what the examination
        concluded, the savepoint is rolled back and the import becomes a
        conflict. Rolling back only the savepoint is what lets the receipt still
        be written, so a rejection is recorded rather than lost.

        Args:
            receipt: The receipt built from the examination.
            pending: The facts to append.

        Returns:
            The original receipt, or a conflict receipt if the database refused.
        """
        savepoint = self._session.begin_nested()
        try:
            for fact in pending:
                self._facts.add(fact)
            self._session.flush()
        except IntegrityError as error:
            savepoint.rollback()
            return self._conflict_receipt(receipt, pending, error)
        savepoint.commit()
        return receipt

    def _conflict_receipt(
        self,
        receipt: ImportReceipt,
        pending: Sequence[SourceFact],
        error: IntegrityError,
    ) -> ImportReceipt:
        """Rewrite a receipt to say what the rolled-back import actually did.

        Nothing was written, so no row may still claim to have been accepted.
        Each pending row is re-examined against the rolled-back database and
        against the rest of the import, so a row that genuinely collided is
        reported as a conflict and a row that was merely caught up in the
        rejection is reported as not applied.

        Args:
            receipt: The receipt the preflight examination produced.
            pending: The facts that were attempted.
            error: The database error that refused them.

        Returns:
            A receipt whose counts and row results agree with each other and
            with the empty result of the import.
        """
        conflicting = self._identify_conflicts(pending)

        rewritten = tuple(self._rewrite_row(result, conflicting) for result in receipt.row_results)
        counts = dict.fromkeys(RowOutcome, 0)
        for result in rewritten:
            counts[result.outcome] += 1

        named = sorted(conflicting) or ["none identifiable"]
        return receipt.model_copy(
            update={
                "outcome": ImportOutcome.REJECTED_CONFLICT,
                "accepted_count": 0,
                "duplicate_count": counts[RowOutcome.DUPLICATE_NO_OP],
                "conflict_count": counts[RowOutcome.DUPLICATE_CONFLICT],
                "rejected_count": counts[RowOutcome.REJECTED],
                "row_results": rewritten,
                "failure_detail": (
                    f"{type(error).__name__}: the database refused this import, so no "
                    f"facts were written; conflicting source records: {named}"
                ),
            }
        )

    def _identify_conflicts(self, pending: Sequence[SourceFact]) -> set[str]:
        """Return the source record IDs that explain a database level refusal.

        Two things can cause one. A fact can collide with something already
        stored, which the rolled-back database can still be asked about. Or two
        facts in the same import can collide with each other, which only the
        pending set knows about. Both are checked, so the receipt names a cause
        whenever there is one to name.
        """
        conflicting: set[str] = set()

        seen_keys: dict[IdempotencyKey, str] = {}
        seen_ids: set[str] = set()
        for fact in pending:
            first = seen_keys.get(fact.idempotency_key)
            if first is not None:
                conflicting.update({first, fact.source_record_id})
            else:
                seen_keys[fact.idempotency_key] = fact.source_record_id
            if fact.source_record_id in seen_ids:
                conflicting.add(fact.source_record_id)
            seen_ids.add(fact.source_record_id)

        for fact in pending:
            stored = self._facts.get(fact.source_record_id) or self._facts.find_by_idempotency_key(
                fact.idempotency_key
            )
            if stored is not None:
                conflicting.add(fact.source_record_id)

        return conflicting

    @staticmethod
    def _rewrite_row(result: RowResult, conflicting: set[str]) -> RowResult:
        """Return one row result as it stands after the import was rolled back."""
        if result.outcome is not RowOutcome.ACCEPTED:
            return result
        if result.source_record_id in conflicting:
            return result.model_copy(
                update={
                    "outcome": RowOutcome.DUPLICATE_CONFLICT,
                    "code": IngestionOutcome.DUPLICATE_CONFLICT.value,
                    "detail": "the database refused this record as a duplicate identity",
                }
            )
        return result.model_copy(
            update={
                "outcome": RowOutcome.NOT_APPLIED,
                "detail": "not written, because another row rejected the import",
            }
        )

    def _build_receipt(
        self,
        *,
        receipt_id: str,
        document_hash: str,
        document_name: str,
        source_system: SourceSystem,
        record_type: SourceRecordType,
        results: Sequence[RowResult],
        row_errors: Sequence[RowError],
        conflicts: Sequence[str],
    ) -> ImportReceipt:
        """Assemble the receipt and decide the document level outcome."""
        counts = dict.fromkeys(RowOutcome, 0)
        for result in results:
            counts[result.outcome] += 1

        if row_errors:
            outcome = ImportOutcome.REJECTED_INVALID
            detail = f"{len(row_errors)} row(s) could not be read"
        elif conflicts:
            outcome = ImportOutcome.REJECTED_CONFLICT
            detail = f"{len(conflicts)} row(s) contradict a stored fact"
        elif counts[RowOutcome.ACCEPTED] == 0:
            outcome = ImportOutcome.DUPLICATE_NO_OP
            detail = None
        else:
            outcome = ImportOutcome.ACCEPTED
            detail = None

        if outcome in (ImportOutcome.REJECTED_INVALID, ImportOutcome.REJECTED_CONFLICT):
            # Nothing was written, so no row may be recorded as accepted.
            results = [
                result.model_copy(update={"outcome": RowOutcome.NOT_APPLIED})
                if result.outcome is RowOutcome.ACCEPTED
                else result
                for result in results
            ]
            counts[RowOutcome.NOT_APPLIED] += counts[RowOutcome.ACCEPTED]
            counts[RowOutcome.ACCEPTED] = 0

        return ImportReceipt(
            receipt_id=receipt_id,
            document_hash=document_hash,
            document_name=document_name,
            source_system=source_system,
            source_record_type=record_type,
            parser_version=PARSER_VERSION,
            received_at=self._now,
            outcome=outcome,
            row_count=len(results),
            accepted_count=counts[RowOutcome.ACCEPTED],
            duplicate_count=counts[RowOutcome.DUPLICATE_NO_OP],
            conflict_count=counts[RowOutcome.DUPLICATE_CONFLICT],
            rejected_count=counts[RowOutcome.REJECTED],
            row_results=tuple(results),
            failure_detail=detail,
        )

    def _document_failure(
        self,
        receipt_id: str,
        document_hash: str,
        document_name: str,
        source_system: SourceSystem,
        record_type: SourceRecordType,
        error: DocumentError,
    ) -> ImportReceipt:
        """Return the receipt for a document that could not be read at all."""
        return ImportReceipt(
            receipt_id=receipt_id,
            document_hash=document_hash,
            document_name=document_name,
            source_system=source_system,
            source_record_type=record_type,
            parser_version=PARSER_VERSION,
            received_at=self._now,
            outcome=ImportOutcome.REJECTED_INVALID,
            row_count=0,
            accepted_count=0,
            duplicate_count=0,
            conflict_count=0,
            rejected_count=0,
            row_results=(),
            failure_detail=f"{error.code.value}: {error.message}",
        )

    def _record(self, receipt: ImportReceipt) -> ImportReceipt:
        """Append the receipt. Always runs, whatever the outcome was."""
        self._receipts.add(
            ImportReceiptRow(
                receipt_id=receipt.receipt_id,
                document_hash=receipt.document_hash,
                document_name=receipt.document_name,
                source_system=receipt.source_system.value,
                source_record_type=receipt.source_record_type.value,
                parser_version=receipt.parser_version,
                received_at=receipt.received_at,
                outcome=receipt.outcome.value,
                row_count=receipt.row_count,
                accepted_count=receipt.accepted_count,
                duplicate_count=receipt.duplicate_count,
                conflict_count=receipt.conflict_count,
                rejected_count=receipt.rejected_count,
                row_outcomes=[result.model_dump(mode="json") for result in receipt.row_results],
                failure_detail=receipt.failure_detail,
            )
        )
        return receipt


__all__ = [
    "ImportOutcome",
    "ImportReceipt",
    "ImportService",
    "RowOutcome",
    "RowResult",
]
"""Re-exported from `app.ingestion.receipts`.

The models moved so that the storage layer can rebuild a receipt without
importing the service that writes one. Callers keep importing them from here,
because this is where the type a caller of `import_document` receives is
documented."""
