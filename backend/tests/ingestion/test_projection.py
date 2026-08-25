"""Tests for lifecycle records projected from stored source facts.

A lifecycle record is a view of a fact, not a second copy. These tests hold that
line: every projection carries the source record ID it came from, and nothing is
invented that no document supports.
"""

import pytest
from sqlalchemy.orm import Session

from app.domain.facts import SourceRecordType, SourceSystem
from app.domain.lifecycle import PaymentEvent, PaymentEventType, PayoutBatch, SettlementLine
from app.ingestion.projection import (
    UnsupportedProjectionError,
    project,
    project_settlement_line,
)
from app.ingestion.service import ImportService
from app.storage.repository import SourceFactRepository
from tests.ingestion.conftest import FIXED_NOW, read_fixture

PSP = SourceSystem.PSP_API


def load_and_project(session: Session, fixture: str, record_type: SourceRecordType) -> list[object]:
    """Import a document and return the projections of its stored facts."""
    ImportService(session, now=FIXED_NOW).import_document(
        read_fixture(fixture),
        source_system=PSP,
        record_type=record_type,
        document_name=fixture,
    )
    return [project(fact) for fact in SourceFactRepository(session).all_facts()]


class TestPaymentEventProjection:
    """A payment event row becomes a PaymentEvent."""

    def test_every_row_projects(self, session: Session) -> None:
        """Five rows, five events."""
        records = load_and_project(session, "payment_events.csv", SourceRecordType.PAYMENT_EVENT)
        assert len(records) == 5
        assert all(isinstance(record, PaymentEvent) for record in records)

    def test_a_projection_keeps_its_source_record_id(self, session: Session) -> None:
        """Every lifecycle record traces back to the row that produced it."""
        ImportService(session, now=FIXED_NOW).import_document(
            read_fixture("payment_events.csv"),
            source_system=PSP,
            record_type=SourceRecordType.PAYMENT_EVENT,
            document_name="payment_events.csv",
        )
        for fact in SourceFactRepository(session).all_facts():
            assert project(fact).source_record_id == fact.source_record_id

    def test_amounts_and_types_survive_the_round_trip(self, session: Session) -> None:
        """The first row of the fixture is a capture of 1000000 paise."""
        records = load_and_project(session, "payment_events.csv", SourceRecordType.PAYMENT_EVENT)
        first = records[0]
        assert isinstance(first, PaymentEvent)
        assert first.event_type is PaymentEventType.CAPTURE
        assert first.amount.amount_minor == 1_000_000
        assert first.amount.currency == "INR"

    def test_all_four_event_types_project(self, session: Session) -> None:
        """The fixture exercises captures, a refund and a chargeback."""
        records = load_and_project(session, "payment_events.csv", SourceRecordType.PAYMENT_EVENT)
        kinds = {record.event_type for record in records if isinstance(record, PaymentEvent)}
        assert PaymentEventType.CAPTURE in kinds
        assert PaymentEventType.REFUND in kinds
        assert PaymentEventType.CHARGEBACK in kinds


class TestSettlementLineProjection:
    """A settlement line row becomes a SettlementLine."""

    def test_every_row_projects(self, session: Session) -> None:
        """Three rows, three lines."""
        records = load_and_project(
            session, "settlement_lines.csv", SourceRecordType.SETTLEMENT_LINE
        )
        assert len(records) == 3
        assert all(isinstance(record, SettlementLine) for record in records)

    def test_the_breakdown_carries_every_component(self, session: Session) -> None:
        """Gross, fee, tax and adjustment all survive."""
        records = load_and_project(
            session, "settlement_lines.csv", SourceRecordType.SETTLEMENT_LINE
        )
        line = records[0]
        assert isinstance(line, SettlementLine)
        assert line.breakdown.gross_minor == 1_000_000
        assert line.breakdown.fee_minor == 20_000
        assert line.breakdown.tax_minor == 3_600
        assert line.breakdown.adjustment_minor == 0

    def test_the_declared_net_is_carried_not_recomputed(self, session: Session) -> None:
        """INV-002 exists to compare the declared net against the formula.

        A projection that silently corrected the net would leave that check with
        nothing to find, so a document that declares a wrong net must project a
        line that still declares it.
        """
        header = (
            "provider_event_id,settlement_line_id,payout_id,payment_id,gross_minor,"
            "fee_minor,tax_minor,adjustment_minor,net_minor,currency,occurred_at\n"
        )
        row = "sl-x,line-x,payout-x,pay-x,1000,20,3,0,999999,INR,2026-08-20T09:15:00+00:00\n"
        ImportService(session, now=FIXED_NOW).import_document(
            (header + row).encode("utf-8"),
            source_system=PSP,
            record_type=SourceRecordType.SETTLEMENT_LINE,
            document_name="wrong-net.csv",
        )
        fact = SourceFactRepository(session).all_facts()[0]

        line = project_settlement_line(fact)
        assert line.net_minor == 999_999
        assert line.breakdown.expected_net_minor == 977

    def test_a_negative_adjustment_projects(self, session: Session) -> None:
        """The third fixture row has an adjustment of -500."""
        records = load_and_project(
            session, "settlement_lines.csv", SourceRecordType.SETTLEMENT_LINE
        )
        adjustments = [
            record.breakdown.adjustment_minor
            for record in records
            if isinstance(record, SettlementLine)
        ]
        assert -500 in adjustments


class TestPayoutProjection:
    """A payout row becomes a PayoutBatch."""

    def test_every_row_projects(self, session: Session) -> None:
        """Two rows, two batches."""
        records = load_and_project(session, "payouts.csv", SourceRecordType.PAYOUT)
        assert len(records) == 2
        assert all(isinstance(record, PayoutBatch) for record in records)

    def test_a_bank_reference_is_carried_when_present(self, session: Session) -> None:
        """The first fixture row has a UTR."""
        records = load_and_project(session, "payouts.csv", SourceRecordType.PAYOUT)
        payout = records[0]
        assert isinstance(payout, PayoutBatch)
        assert payout.utr == "UTR2026082100001"

    def test_an_absent_bank_reference_projects_as_none(self, session: Session) -> None:
        """The second fixture row leaves the UTR empty."""
        records = load_and_project(session, "payouts.csv", SourceRecordType.PAYOUT)
        payout = records[1]
        assert isinstance(payout, PayoutBatch)
        assert payout.utr is None

    def test_settlement_line_ids_are_empty(self, session: Session) -> None:
        """A payout document says what the batch totalled, not what composed it.

        The association between a payout and its lines is established by
        matching, in a later phase. Filling it in here would create evidence no
        document supports.
        """
        records = load_and_project(session, "payouts.csv", SourceRecordType.PAYOUT)
        assert all(
            record.settlement_line_ids == ()
            for record in records
            if isinstance(record, PayoutBatch)
        )


class TestUnsupportedProjection:
    """A record type with no projection is refused, not approximated."""

    def test_a_bank_transaction_has_no_projection_yet(self, session: Session) -> None:
        """It is a valid record type in the contract with no shape defined here."""
        ImportService(session, now=FIXED_NOW).import_document(
            read_fixture("payment_events.csv"),
            source_system=PSP,
            record_type=SourceRecordType.PAYMENT_EVENT,
            document_name="payment_events.csv",
        )
        fact = SourceFactRepository(session).all_facts()[0]
        recast = fact.model_copy(update={"source_record_type": SourceRecordType.BANK_TRANSACTION})

        with pytest.raises(UnsupportedProjectionError) as caught:
            project(recast)
        assert caught.value.record_type is SourceRecordType.BANK_TRANSACTION

    def test_a_payload_with_a_non_integer_amount_is_refused(self, session: Session) -> None:
        """Defence in depth against a payload that bypassed the parser."""
        ImportService(session, now=FIXED_NOW).import_document(
            read_fixture("payment_events.csv"),
            source_system=PSP,
            record_type=SourceRecordType.PAYMENT_EVENT,
            document_name="payment_events.csv",
        )
        fact = SourceFactRepository(session).all_facts()[0]
        tampered = fact.model_construct(
            **{
                **fact.__dict__,
                "canonical_payload": {**fact.canonical_payload, "amount_minor": "1000"},
            }
        )

        with pytest.raises(TypeError, match="minor units"):
            project(tampered)
