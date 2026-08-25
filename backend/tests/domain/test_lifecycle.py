"""Tests for the payment to payout lifecycle models."""

import pytest
from pydantic import ValidationError

from app.domain.lifecycle import (
    RETURNING_EVENT_TYPES,
    PaymentEventType,
    PaymentIdentity,
)
from tests.domain.conftest import make_breakdown, make_event, make_payout, make_settlement_line


class TestPaymentIdentity:
    """A payment carries the currency every later event must match."""

    def test_identity_builds(self) -> None:
        """The happy path works."""
        identity = PaymentIdentity(payment_id="pay-1", merchant_id="merch-1", currency="INR")
        assert identity.currency == "INR"

    def test_an_invalid_currency_is_rejected(self) -> None:
        """Shape is checked at the boundary."""
        with pytest.raises(ValidationError):
            PaymentIdentity(payment_id="pay-1", merchant_id="merch-1", currency="rupees")


class TestPaymentEvents:
    """Direction comes from the event type, not from the sign of the amount."""

    def test_the_four_event_types_exist(self) -> None:
        """Capture, refund, reversal and chargeback are all modelled."""
        expected = {"CAPTURE", "REFUND", "REVERSAL", "CHARGEBACK"}
        assert {event_type.value for event_type in PaymentEventType} == expected

    def test_returning_types_exclude_capture(self) -> None:
        """A capture takes money in. It cannot also give it back."""
        assert PaymentEventType.CAPTURE not in RETURNING_EVENT_TYPES
        expected = {
            PaymentEventType.REFUND,
            PaymentEventType.REVERSAL,
            PaymentEventType.CHARGEBACK,
        }
        assert set(RETURNING_EVENT_TYPES) == expected

    def test_every_event_names_the_source_fact_it_came_from(self) -> None:
        """An event with no provenance could never back a resolution."""
        assert make_event().source_record_id == "rec-1"

    def test_an_event_is_immutable(self) -> None:
        """History is not editable."""
        with pytest.raises(ValidationError):
            make_event().event_id = "evt-2"


class TestSettlementLine:
    """The declared net is stored, not computed, so INV-002 has work to do."""

    def test_a_line_can_declare_a_net_that_contradicts_its_breakdown(self) -> None:
        """Sources really do this, and the model must be able to represent it.

        If the model refused, a broken source record would be unrepresentable
        and the system could never report the break.
        """
        line = make_settlement_line(net_minor=1)
        assert line.net_minor == 1
        assert line.breakdown.expected_net_minor == 9_764

    def test_declared_net_is_exposed_as_money(self) -> None:
        """The net carries its currency."""
        assert make_settlement_line().declared_net.amount_minor == 9_764

    def test_currency_comes_from_the_breakdown(self) -> None:
        """One currency per line, held in one place."""
        line = make_settlement_line(breakdown=make_breakdown(currency="USD"))
        assert line.currency == "USD"


class TestPayoutBatch:
    """A payout holds many lines. One payment does not equal one payout."""

    def test_a_payout_can_cover_many_lines(self) -> None:
        """The common case is a batch, not a single line."""
        payout = make_payout(settlement_line_ids=("sl-1", "sl-2", "sl-3"))
        assert len(payout.settlement_line_ids) == 3

    def test_line_ids_are_an_immutable_tuple(self) -> None:
        """A batch cannot gain a line after it is built."""
        assert isinstance(make_payout().settlement_line_ids, tuple)

    def test_the_utr_is_optional(self) -> None:
        """A bank reference is not always available at the time of reading."""
        assert make_payout(utr=None).utr is None

    def test_declared_net_is_exposed_as_money(self) -> None:
        """The batch total carries its currency."""
        assert make_payout().declared_net.currency == "INR"

    def test_a_payout_is_immutable(self) -> None:
        """Frozen, like every other record in the contract."""
        with pytest.raises(ValidationError):
            make_payout().net_minor = 1
