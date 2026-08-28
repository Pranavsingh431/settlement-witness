"""Reading a bank statement document.

The schema is the narrowest in the project on purpose, so most of what is worth
testing is what it refuses. Every rule the other three schemas enforce applies
here unchanged, because they are the parser's rules rather than each schema's.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.domain.banking import BankDirection
from app.domain.facts import SourceRecordType, SourceSystem
from app.ingestion.errors import RowErrorCode
from app.ingestion.projection import project_bank_transaction
from app.ingestion.receipts import ImportOutcome, ImportReceipt
from app.ingestion.schemas import BANK_TRANSACTION_COLUMNS, expected_headers
from app.ingestion.service import ImportService
from app.storage.database import (
    create_database_engine,
    create_schema,
    database_url_for,
    session_factory,
)
from app.storage.repository import SourceFactRepository
from tests.ingestion.conftest import FIXED_NOW, read_fixture

BANK = SourceRecordType.BANK_TRANSACTION
PSP = SourceSystem.PSP_API

HEADER = ",".join(expected_headers(BANK))
ROW = "bt-1,BANKTXN1,UTR-1,CREDIT,1220500,INR,2026-08-21T20:05:00+05:30"


def document(*rows: str) -> bytes:
    """Return a bank statement document with the exact header."""
    return ("\n".join((HEADER, *rows)) + "\n").encode("utf-8")


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    """Return a session on a fresh migrated database."""
    engine = create_database_engine(database_url_for(tmp_path / "bank.sqlite"))
    create_schema(engine)
    with session_factory(engine)() as opened:
        yield opened
    engine.dispose()


def import_document(session: Session, content: bytes) -> ImportReceipt:
    """Import one bank statement document and return its receipt."""
    return ImportService(session, now=FIXED_NOW).import_document(
        content, source_system=PSP, record_type=BANK, document_name="bank_transactions.csv"
    )


class TestTheSchema:
    """What the columns are, and why there are so few of them."""

    def test_the_headers_are_exact_and_ordered(self) -> None:
        """The order is part of the contract, as it is for every other type."""
        assert expected_headers(BANK) == (
            "provider_event_id",
            "bank_transaction_id",
            "bank_reference",
            "direction",
            "amount_minor",
            "currency",
            "occurred_at",
        )

    def test_there_is_no_free_text_column(self) -> None:
        """A description is the first thing a fuzzy matcher reaches for, and the
        second thing somebody puts a model's opinion in."""
        names = {name for name, _ in BANK_TRANSACTION_COLUMNS}

        assert not names & {"description", "narrative", "counterparty", "remarks", "balance"}

    def test_the_reference_column_is_required_not_optional(self) -> None:
        """A row with no reference could never be cited by any payout.

        Storing it would store a fact nothing can ever use. That is a real
        limitation of this schema and not a convenience: a statement export
        whose rows carry no reference cannot be imported here at all.
        """
        from app.ingestion.schemas import ColumnKind

        kinds = dict(BANK_TRANSACTION_COLUMNS)

        assert kinds["bank_reference"] is ColumnKind.IDENTIFIER


class TestAcceptingAStatement:
    """The ordinary path."""

    def test_a_well_formed_document_is_accepted(self, session: Session) -> None:
        """One row in, one fact stored."""
        receipt = import_document(session, read_fixture("bank_transactions.csv"))

        assert receipt.outcome is ImportOutcome.ACCEPTED
        assert SourceFactRepository(session).count() == 1

    def test_the_stored_fact_projects_to_a_bank_transaction(self, session: Session) -> None:
        """Every field carried across, and the direction kept separate from the
        amount."""
        import_document(session, read_fixture("bank_transactions.csv"))
        fact = SourceFactRepository(session).all_facts()[0]

        transaction = project_bank_transaction(fact)

        assert transaction.bank_transaction_id == "BANKTXN0001"
        assert transaction.bank_reference == "UTR2026082100001"
        assert transaction.direction is BankDirection.CREDIT
        assert transaction.amount_minor == 1_220_500
        assert transaction.currency == "INR"

    def test_the_amount_is_a_magnitude_not_a_signed_number(self, session: Session) -> None:
        """A debit is positive too. The direction carries the sign."""
        import_document(session, read_fixture("bank_transactions_debit.csv"))
        transaction = project_bank_transaction(SourceFactRepository(session).all_facts()[0])

        assert transaction.direction is BankDirection.DEBIT
        assert transaction.amount_minor > 0

    def test_importing_the_same_document_twice_writes_once(self, session: Session) -> None:
        """The same idempotency rule as every other record type."""
        content = read_fixture("bank_transactions.csv")
        import_document(session, content)
        second = import_document(session, content)

        assert second.outcome is ImportOutcome.DUPLICATE_NO_OP
        assert SourceFactRepository(session).count() == 1


class TestRefusingAStatement:
    """The rules that make this evidence rather than input."""

    def test_a_missing_reference_is_refused(self, session: Session) -> None:
        """The column that makes a row citable at all."""
        receipt = import_document(session, read_fixture("invalid_bank_missing_reference.csv"))

        assert receipt.outcome is ImportOutcome.REJECTED_INVALID
        assert SourceFactRepository(session).count() == 0

    def test_an_unknown_direction_is_refused(self, session: Session) -> None:
        """Not mapped to the nearest thing, and not defaulted to CREDIT."""
        receipt = import_document(session, read_fixture("invalid_bank_direction.csv"))

        assert receipt.outcome is ImportOutcome.REJECTED_INVALID
        codes = {row.code for row in receipt.row_results if row.code}
        assert RowErrorCode.INVALID_ENUM.value in codes

    def test_the_direction_refusal_names_what_is_allowed(self, session: Session) -> None:
        """So a caller can fix the file without reading the source."""
        receipt = import_document(session, read_fixture("invalid_bank_direction.csv"))
        details = " ".join(row.detail or "" for row in receipt.row_results)

        assert "CREDIT" in details
        assert "DEBIT" in details

    @pytest.mark.parametrize("amount", ["0", "-1220500"])
    def test_an_amount_that_does_not_move_money_is_refused(
        self, session: Session, amount: str
    ) -> None:
        """A statement line for nothing, and one whose sign carries a direction.

        The second is the important one: a negative amount is how a statement
        export would smuggle a debit past a direction column.
        """
        content = document(f"bt-1,BANKTXN1,UTR-1,CREDIT,{amount},INR,2026-08-21T20:05:00+05:30")
        receipt = import_document(session, content)

        assert receipt.outcome is ImportOutcome.REJECTED_INVALID
        assert SourceFactRepository(session).count() == 0

    def test_surrounding_whitespace_is_refused_not_trimmed(self, session: Session) -> None:
        """A reference differing by an invisible character is a different
        reference, so the parser refuses it rather than deciding."""
        content = document("bt-1,BANKTXN1, UTR-1,CREDIT,1220500,INR,2026-08-21T20:05:00+05:30")
        receipt = import_document(session, content)

        assert receipt.outcome is ImportOutcome.REJECTED_INVALID
        codes = {row.code for row in receipt.row_results if row.code}
        assert RowErrorCode.SURROUNDING_WHITESPACE.value in codes

    def test_a_naive_timestamp_is_refused(self, session: Session) -> None:
        """The same rule as every other document."""
        content = document("bt-1,BANKTXN1,UTR-1,CREDIT,1220500,INR,2026-08-21T20:05:00")
        receipt = import_document(session, content)

        assert receipt.outcome is ImportOutcome.REJECTED_INVALID

    def test_an_unexpected_column_is_refused(self, session: Session) -> None:
        """A silently ignored column is a field that quietly stopped being
        checked, which is worse here than anywhere."""
        content = (
            (HEADER + ",description\n")
            + "bt-1,BANKTXN1,UTR-1,CREDIT,1220500,INR,2026-08-21T20:05:00+05:30,payout\n"
        ).encode("utf-8")
        receipt = import_document(session, content)

        assert receipt.outcome is ImportOutcome.REJECTED_INVALID

    def test_one_bad_row_refuses_the_whole_document(self, session: Session) -> None:
        """Atomic, like every other import. Half a statement is not evidence."""
        content = document(
            ROW,
            "bt-2,BANKTXN2,UTR-2,SIDEWAYS,1220500,INR,2026-08-21T20:06:00+05:30",
        )
        receipt = import_document(session, content)

        assert receipt.outcome is ImportOutcome.REJECTED_INVALID
        assert SourceFactRepository(session).count() == 0

    def test_a_refused_statement_still_leaves_a_receipt(self, session: Session) -> None:
        """A refusal is as visible as an acceptance."""
        import_document(session, read_fixture("invalid_bank_direction.csv"))

        from app.storage.repository import ImportReceiptRepository

        assert ImportReceiptRepository(session).count() == 1


class TestTheStatementIsNotReconciled:
    """A bank fact changes no decision, because no invariant reads one."""

    @staticmethod
    def _settlement() -> tuple[object, ...]:
        """Return facts the baseline resolves, with a bank-referenced payout."""
        from tests.banking.conftest import linkable_payout
        from tests.reconciliation.conftest import payment_event, settlement_line

        return (
            payment_event("pe-1", payment_id="pay-1"),
            settlement_line("sl-1", payment_id="pay-1", payout_id="payout-1"),
            linkable_payout("po-1", payout_id="payout-1"),
        )

    def test_every_conclusion_is_identical_with_a_statement_in_the_store(self) -> None:
        """The status, the codes, the invariants and the citations, all equal.

        The decision ID is excluded because it is derived from the snapshot
        digest, and adding a fact is a new snapshot. That is correct rather than
        incidental: a new fact is a new moment, so it is a new run. What must
        not change is what the baseline concluded about the settlement records,
        and no invariant reads a bank fact.
        """
        from app.reconciliation.batch import reconcile
        from tests.banking.conftest import bank_transaction
        from tests.reconciliation.conftest import index_of

        settlement = self._settlement()
        without = reconcile(index_of(*settlement))  # type: ignore[arg-type]
        with_bank = reconcile(index_of(*settlement, bank_transaction("bt-1")))  # type: ignore[arg-type]

        assert [one.model_dump(exclude={"decision_id"}) for one in with_bank.decisions] == [
            one.model_dump(exclude={"decision_id"}) for one in without.decisions
        ]

    def test_only_the_snapshot_half_of_the_decision_id_moves(self) -> None:
        """The settlement line half is unchanged, which is what identifies it."""
        from app.reconciliation.batch import reconcile
        from tests.banking.conftest import bank_transaction
        from tests.reconciliation.conftest import index_of

        settlement = self._settlement()
        without = reconcile(index_of(*settlement)).decisions[0]  # type: ignore[arg-type]
        with_bank = reconcile(index_of(*settlement, bank_transaction("bt-1"))).decisions[0]  # type: ignore[arg-type]

        assert without.decision_id.split(":")[1] == with_bank.decision_id.split(":")[1]
        assert without.decision_id != with_bank.decision_id

    def test_the_status_counts_do_not_move(self) -> None:
        """A statement arriving cannot turn an exception into a resolution."""
        from app.reconciliation.batch import reconcile
        from tests.banking.conftest import bank_transaction
        from tests.reconciliation.conftest import index_of

        settlement = self._settlement()
        without = reconcile(index_of(*settlement))  # type: ignore[arg-type]
        with_bank = reconcile(index_of(*settlement, bank_transaction("bt-1")))  # type: ignore[arg-type]

        assert with_bank.status_counts == without.status_counts
        assert with_bank.exception_counts == without.exception_counts

    def test_a_statement_alone_reconciles_to_nothing(self) -> None:
        """No settlement lines means no decisions, not an empty success."""
        from app.reconciliation.batch import reconcile
        from tests.banking.conftest import bank_transaction
        from tests.reconciliation.conftest import index_of

        batch = reconcile(index_of(bank_transaction("bt-1")))

        assert batch.decisions == ()
