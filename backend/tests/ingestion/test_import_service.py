"""Tests for the import service: outcomes, idempotency and atomicity."""

from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.domain.facts import SourceRecordType, SourceSystem
from app.ingestion.service import ImportOutcome, ImportReceipt, ImportService, RowOutcome
from app.storage.repository import ImportReceiptRepository, SourceFactRepository
from tests.ingestion.conftest import FIXED_NOW, read_fixture

PSP = SourceSystem.PSP_API


def run_import(
    session: Session,
    fixture: str,
    record_type: SourceRecordType = SourceRecordType.PAYMENT_EVENT,
    *,
    source_system: SourceSystem = PSP,
) -> ImportReceipt:
    """Import one fixture document and return its receipt."""
    return ImportService(session, now=FIXED_NOW).import_document(
        read_fixture(fixture),
        source_system=source_system,
        record_type=record_type,
        document_name=fixture,
    )


def fact_count(session: Session) -> int:
    """Return how many facts are stored."""
    return SourceFactRepository(session).count()


class TestValidImports:
    """One test per documented CSV schema."""

    def test_payment_events_import(self, session: Session) -> None:
        """Five rows, all new."""
        receipt = run_import(session, "payment_events.csv")

        assert receipt.outcome is ImportOutcome.ACCEPTED
        assert receipt.row_count == 5
        assert receipt.accepted_count == 5
        assert fact_count(session) == 5

    def test_settlement_lines_import(self, session: Session) -> None:
        """Three rows, all new."""
        receipt = run_import(session, "settlement_lines.csv", SourceRecordType.SETTLEMENT_LINE)

        assert receipt.outcome is ImportOutcome.ACCEPTED
        assert receipt.accepted_count == 3
        assert fact_count(session) == 3

    def test_payouts_import(self, session: Session) -> None:
        """Two rows, one with an empty bank reference."""
        receipt = run_import(session, "payouts.csv", SourceRecordType.PAYOUT)

        assert receipt.outcome is ImportOutcome.ACCEPTED
        assert receipt.accepted_count == 2
        assert fact_count(session) == 2

    def test_all_three_types_coexist(self, session: Session) -> None:
        """The three schemas share one store without colliding."""
        run_import(session, "payment_events.csv")
        run_import(session, "settlement_lines.csv", SourceRecordType.SETTLEMENT_LINE)
        run_import(session, "payouts.csv", SourceRecordType.PAYOUT)

        assert fact_count(session) == 10

    def test_a_receipt_records_the_parser_version(self, session: Session) -> None:
        """A fact can always be traced to the rules that produced it."""
        assert run_import(session, "payment_events.csv").parser_version == "1.0.0"

    def test_every_row_gets_a_recorded_outcome(self, session: Session) -> None:
        """The receipt accounts for every row, not just the interesting ones."""
        receipt = run_import(session, "payment_events.csv")
        assert [result.row_number for result in receipt.row_results] == [2, 3, 4, 5, 6]
        assert all(r.outcome is RowOutcome.ACCEPTED for r in receipt.row_results)


class TestIdempotency:
    """Duplicate delivery is normal. Contradiction is not."""

    def test_an_exact_duplicate_import_is_a_no_op(self, session: Session) -> None:
        """Re-running an import must be safe."""
        run_import(session, "payment_events.csv")
        second = run_import(session, "payment_events.csv")

        assert second.outcome is ImportOutcome.DUPLICATE_NO_OP
        assert second.accepted_count == 0
        assert second.duplicate_count == 5
        assert fact_count(session) == 5

    def test_a_duplicate_import_still_writes_a_receipt(self, session: Session) -> None:
        """A no-op is auditable. Silence would not be."""
        run_import(session, "payment_events.csv")
        run_import(session, "payment_events.csv")

        receipts = ImportReceiptRepository(session).all_receipts()
        assert len(receipts) == 2
        assert receipts[1].outcome == ImportOutcome.DUPLICATE_NO_OP.value

    def test_the_same_identity_with_a_different_payload_conflicts(self, session: Session) -> None:
        """One of the two observations is wrong and neither may be preferred."""
        run_import(session, "payment_events.csv")
        conflict = run_import(session, "conflicting_payment_events.csv")

        assert conflict.outcome is ImportOutcome.REJECTED_CONFLICT
        assert conflict.conflict_count == 1

    def test_a_conflict_writes_no_partial_data(self, session: Session) -> None:
        """The store is exactly as it was before the rejected import."""
        run_import(session, "payment_events.csv")
        before = SourceFactRepository(session).all_facts()

        run_import(session, "conflicting_payment_events.csv")

        assert SourceFactRepository(session).all_facts() == before

    def test_a_conflict_never_overwrites_the_stored_fact(self, session: Session) -> None:
        """Append-only means the first observation survives."""
        run_import(session, "payment_events.csv")
        stored = SourceFactRepository(session).all_facts()[0]

        run_import(session, "conflicting_payment_events.csv")

        assert SourceFactRepository(session).get(stored.source_record_id) == stored

    def test_a_conflict_is_auditable(self, session: Session) -> None:
        """The receipt names the row and both payload hashes."""
        run_import(session, "payment_events.csv")
        receipt = run_import(session, "conflicting_payment_events.csv")

        conflicts = [r for r in receipt.row_results if r.outcome is RowOutcome.DUPLICATE_CONFLICT]
        assert len(conflicts) == 1
        assert conflicts[0].detail is not None
        assert "different payload hash" in conflicts[0].detail

    def test_the_same_document_under_two_source_systems_is_not_a_duplicate(
        self, session: Session
    ) -> None:
        """Identity is the system and the provider event ID together.

        Two systems can disagree, and that disagreement is worth keeping.
        """
        run_import(session, "payment_events.csv", source_system=SourceSystem.PSP_API)
        second = run_import(
            session, "payment_events.csv", source_system=SourceSystem.MERCHANT_LEDGER
        )

        assert second.outcome is ImportOutcome.ACCEPTED
        assert fact_count(session) == 10


class TestAtomicity:
    """A document is accepted whole or not at all."""

    @pytest.mark.parametrize(
        "fixture",
        [
            "invalid_float_money.csv",
            "invalid_naive_timestamp.csv",
            "invalid_headers.csv",
            "invalid_mixed_rows.csv",
        ],
    )
    def test_an_invalid_document_writes_no_facts(self, session: Session, fixture: str) -> None:
        """A half loaded file is worse than a rejected one, because the gap is invisible."""
        receipt = run_import(session, fixture)

        assert receipt.outcome is ImportOutcome.REJECTED_INVALID
        assert fact_count(session) == 0

    def test_one_bad_row_rejects_the_whole_document(self, session: Session) -> None:
        """The mixed fixture has one good row and two bad ones."""
        receipt = run_import(session, "invalid_mixed_rows.csv")

        assert receipt.rejected_count == 2
        assert fact_count(session) == 0

    def test_a_rejected_receipt_claims_no_accepted_rows(self, session: Session) -> None:
        """Nothing was written, so no row may be recorded as accepted.

        The good row is recorded as NOT_APPLIED: it was acceptable, and it is not
        in the store.
        """
        receipt = run_import(session, "invalid_mixed_rows.csv")

        assert receipt.accepted_count == 0
        assert any(r.outcome is RowOutcome.NOT_APPLIED for r in receipt.row_results)

    def test_an_invalid_document_still_writes_a_receipt(self, session: Session) -> None:
        """A refusal leaves a trace rather than a silence."""
        run_import(session, "invalid_float_money.csv")

        receipts = ImportReceiptRepository(session).all_receipts()
        assert len(receipts) == 1
        assert receipts[0].outcome == ImportOutcome.REJECTED_INVALID.value

    def test_an_unreadable_document_records_the_document_level_reason(
        self, session: Session
    ) -> None:
        """Headers wrong means there were no rows to report on."""
        receipt = run_import(session, "invalid_headers.csv")

        assert receipt.row_count == 0
        assert receipt.failure_detail is not None
        assert "UNEXPECTED_COLUMNS" in receipt.failure_detail

    def test_invalid_utf8_is_rejected_with_a_receipt(self, session: Session) -> None:
        """Malformed bytes are a document level refusal."""
        receipt = ImportService(session, now=FIXED_NOW).import_document(
            b"\xff\xfe garbage",
            source_system=PSP,
            record_type=SourceRecordType.PAYMENT_EVENT,
            document_name="broken.csv",
        )

        assert receipt.outcome is ImportOutcome.REJECTED_INVALID
        assert receipt.failure_detail is not None
        assert "UNREADABLE_ENCODING" in receipt.failure_detail
        assert fact_count(session) == 0

    def test_a_document_that_contradicts_itself_is_rejected(self, session: Session) -> None:
        """A file can disagree with itself, and that is caught before any write."""
        header = (
            "provider_event_id,event_id,payment_id,merchant_id,event_type,"
            "amount_minor,currency,occurred_at\n"
        )
        rows = (
            "pe-1,evt-1,pay-1,m-1,CAPTURE,100,INR,2026-08-20T09:15:00+00:00\n"
            "pe-1,evt-2,pay-2,m-1,CAPTURE,200,INR,2026-08-20T09:16:00+00:00\n"
        )
        receipt = ImportService(session, now=FIXED_NOW).import_document(
            (header + rows).encode("utf-8"),
            source_system=PSP,
            record_type=SourceRecordType.PAYMENT_EVENT,
            document_name="self-conflicting.csv",
        )

        assert receipt.outcome is ImportOutcome.REJECTED_CONFLICT
        assert fact_count(session) == 0


class TestOutOfOrderInput:
    """Events arrive late and out of sequence. Ingestion does not reorder them."""

    def test_rows_are_stored_in_the_order_the_document_declared(self, session: Session) -> None:
        """Row numbers follow the file, not the timestamps.

        The payment events fixture has a refund dated after a later capture, so
        occurred-at order and row order genuinely differ.
        """
        run_import(session, "payment_events.csv")
        facts = SourceFactRepository(session).all_facts()

        row_numbers = [fact.source_locator.row_number for fact in facts]
        assert row_numbers == [2, 3, 4, 5, 6]

    def test_occurred_at_order_is_not_row_order(self, session: Session) -> None:
        """Confirms the fixture really does exercise out of order arrival."""
        run_import(session, "payment_events.csv")
        facts = SourceFactRepository(session).all_facts()

        occurred = [fact.occurred_at for fact in facts]
        assert occurred != sorted(occurred)

    def test_a_later_document_does_not_rewrite_earlier_facts(self, session: Session) -> None:
        """Append-only across imports, not just within one."""
        run_import(session, "payment_events.csv")
        before = SourceFactRepository(session).all_facts()

        run_import(session, "settlement_lines.csv", SourceRecordType.SETTLEMENT_LINE)
        after = SourceFactRepository(session).all_facts()

        for fact in before:
            assert fact in after

    def test_observed_at_and_occurred_at_are_recorded_separately(self, session: Session) -> None:
        """A fact observed today may describe something that happened days ago."""
        run_import(session, "payment_events.csv")
        fact = SourceFactRepository(session).all_facts()[0]

        assert fact.observed_at == FIXED_NOW
        assert fact.occurred_at != fact.observed_at


class TestReceiptContents:
    """What an auditor can read back."""

    def test_a_receipt_records_the_document_hash_and_time(self, session: Session) -> None:
        """Enough to identify exactly which bytes were offered, and when."""
        receipt = run_import(session, "payment_events.csv")

        assert len(receipt.document_hash) == 64
        assert receipt.received_at == FIXED_NOW

    def test_receipts_survive_for_every_attempt(self, session: Session) -> None:
        """Four attempts, four receipts, whatever their outcomes."""
        run_import(session, "payment_events.csv")
        run_import(session, "payment_events.csv")
        run_import(session, "conflicting_payment_events.csv")
        run_import(session, "invalid_float_money.csv")

        outcomes = [r.outcome for r in ImportReceiptRepository(session).all_receipts()]
        assert outcomes == [
            ImportOutcome.ACCEPTED.value,
            ImportOutcome.DUPLICATE_NO_OP.value,
            ImportOutcome.REJECTED_CONFLICT.value,
            ImportOutcome.REJECTED_INVALID.value,
        ]

    def test_attempts_for_one_document_can_be_listed(self, session: Session) -> None:
        """The history of one file is retrievable on its own."""
        first = run_import(session, "payment_events.csv")
        run_import(session, "payment_events.csv")

        attempts = ImportReceiptRepository(session).for_document(first.document_hash)
        assert len(attempts) == 2

    def test_a_document_name_is_a_label_not_an_identifier(
        self, session: Session, tmp_path: Path
    ) -> None:
        """The same bytes under two names are the same records."""
        content = read_fixture("payment_events.csv")
        service = ImportService(session, now=FIXED_NOW)

        first = service.import_document(
            content,
            source_system=PSP,
            record_type=SourceRecordType.PAYMENT_EVENT,
            document_name="one.csv",
        )
        second = service.import_document(
            content,
            source_system=PSP,
            record_type=SourceRecordType.PAYMENT_EVENT,
            document_name="two.csv",
        )

        assert first.document_hash == second.document_hash
        assert second.outcome is ImportOutcome.DUPLICATE_NO_OP


def test_engine_fixture_creates_a_usable_schema(engine: Engine) -> None:
    """The setup path produces a database the repositories can use."""
    from app.storage.database import session_factory

    with session_factory(engine)() as opened:
        assert SourceFactRepository(opened).count() == 0


class TestTheContractIsTheFinalWord:
    """A row this parser accepts can still be refused by the domain contract."""

    def test_an_over_long_identifier_is_rejected_by_the_contract(self, session: Session) -> None:
        """The parser only checks that an identifier is non-empty.

        The contract bounds it at 200 characters. A row that passes the parser
        and fails the model is a rejected row, because the contract is the
        definition and the parser is only a reader of files.
        """
        header = (
            "provider_event_id,event_id,payment_id,merchant_id,event_type,"
            "amount_minor,currency,occurred_at\n"
        )
        row = f"{'p' * 201},evt-1,pay-1,merch-1,CAPTURE,1000,INR,2026-08-20T09:15:00+00:00\n"
        receipt = ImportService(session, now=FIXED_NOW).import_document(
            (header + row).encode("utf-8"),
            source_system=PSP,
            record_type=SourceRecordType.PAYMENT_EVENT,
            document_name="over-long-id.csv",
        )

        assert receipt.outcome is ImportOutcome.REJECTED_INVALID
        assert receipt.rejected_count == 1
        assert receipt.row_results[0].code == "DOMAIN_VALIDATION_FAILED"
        assert fact_count(session) == 0


class TestReceiptHelpers:
    """Small conveniences on the receipt."""

    def test_wrote_facts_is_true_only_when_something_was_stored(self, session: Session) -> None:
        """A duplicate import changed nothing, so it wrote nothing."""
        first = run_import(session, "payment_events.csv")
        second = run_import(session, "payment_events.csv")
        rejected = run_import(session, "invalid_float_money.csv")

        assert first.wrote_facts
        assert not second.wrote_facts
        assert not rejected.wrote_facts


class TestSavepointRollback:
    """Defence in depth for the all-or-nothing guarantee.

    The row by row examination catches every conflict before anything is
    written, so the database constraint should never fire in practice. It is
    still enforced, and this proves that if it does fire, the savepoint rolls
    back every fact from that import and the receipt still survives.

    Reaching it means calling the append step directly with facts the
    examination would never have produced together.
    """

    def test_a_constraint_violation_rolls_back_every_fact_of_the_import(
        self, session: Session
    ) -> None:
        """Two facts sharing an idempotency identity. Neither is written."""
        run_import(session, "payment_events.csv")
        session.flush()
        stored = SourceFactRepository(session).all_facts()[0]

        service = ImportService(session, now=FIXED_NOW)
        colliding = stored.model_copy(update={"source_record_id": "another-record-id"})
        fresh = stored.model_copy(
            update={
                "source_record_id": "a-brand-new-record-id",
                "provider_event_id": "pe-unseen",
            }
        )

        receipt = service._append_facts(
            service._build_receipt(
                receipt_id="r-x",
                document_hash="c" * 64,
                document_name="x.csv",
                source_system=PSP,
                record_type=SourceRecordType.PAYMENT_EVENT,
                results=[],
                row_errors=[],
                conflicts=[],
            ),
            [fresh, colliding],
        )

        assert receipt.outcome is ImportOutcome.REJECTED_CONFLICT
        assert receipt.accepted_count == 0
        assert receipt.failure_detail is not None
        assert "none were written" in receipt.failure_detail
        assert SourceFactRepository(session).get("a-brand-new-record-id") is None
        assert SourceFactRepository(session).count() == 5
