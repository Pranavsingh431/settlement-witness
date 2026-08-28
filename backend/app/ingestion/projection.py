"""Canonical lifecycle records, projected from stored source facts.

A lifecycle record is a view of a fact, not a second copy of it. Source facts
are the store; payment events, settlement lines and payout batches are derived
from them on demand.

Keeping it that way means there is one place a correction can come from, and no
possibility of the two disagreeing. It also means a projection always carries
the ``source_record_id`` of the fact it came from, so every lifecycle record can
be traced back to the row of the document that produced it.
"""

from collections.abc import Callable
from typing import Final

from app.domain.banking import BankDirection, BankTransaction
from app.domain.facts import SourceFact, SourceRecordType
from app.domain.lifecycle import (
    PaymentEvent,
    PaymentEventType,
    PayoutBatch,
    SettlementLine,
)
from app.domain.money import Money, MoneyBreakdown
from app.domain.primitives import CanonicalPayload

type LifecycleRecord = PaymentEvent | SettlementLine | PayoutBatch | BankTransaction


def _text(payload: CanonicalPayload, key: str) -> str:
    """Return a required text field from a canonical payload."""
    return str(payload[key])


def _amount(payload: CanonicalPayload, key: str) -> int:
    """Return a required integer minor-unit field from a canonical payload."""
    value = payload[key]
    if not isinstance(value, int) or isinstance(value, bool):
        message = f"{key} must be an integer number of minor units"
        raise TypeError(message)
    return value


def project_payment_event(fact: SourceFact) -> PaymentEvent:
    """Return the payment event a fact describes."""
    payload = fact.canonical_payload
    return PaymentEvent(
        event_id=_text(payload, "event_id"),
        payment_id=_text(payload, "payment_id"),
        event_type=PaymentEventType(_text(payload, "event_type")),
        amount=Money(
            amount_minor=_amount(payload, "amount_minor"),
            currency=_text(payload, "currency"),
        ),
        occurred_at=fact.occurred_at,
        source_record_id=fact.source_record_id,
    )


def project_settlement_line(fact: SourceFact) -> SettlementLine:
    """Return the settlement line a fact describes.

    ``net_minor`` is carried across exactly as the document declared it. It is
    not recomputed here, because INV-002 exists to compare the declared net
    against the formula, and a projection that silently corrected it would leave
    that check with nothing to find.
    """
    payload = fact.canonical_payload
    return SettlementLine(
        settlement_line_id=_text(payload, "settlement_line_id"),
        payout_id=_text(payload, "payout_id"),
        payment_id=_text(payload, "payment_id"),
        breakdown=MoneyBreakdown(
            currency=_text(payload, "currency"),
            gross_minor=_amount(payload, "gross_minor"),
            fee_minor=_amount(payload, "fee_minor"),
            tax_minor=_amount(payload, "tax_minor"),
            adjustment_minor=_amount(payload, "adjustment_minor"),
        ),
        net_minor=_amount(payload, "net_minor"),
        occurred_at=fact.occurred_at,
        source_record_id=fact.source_record_id,
    )


def project_payout(fact: SourceFact) -> PayoutBatch:
    """Return the payout batch a fact describes.

    ``settlement_line_ids`` is empty. A payout document says what the batch
    totalled, not which lines composed it, and that association is established
    by matching in a later phase. Filling it in from guesswork here would create
    evidence that no document supports.
    """
    payload = fact.canonical_payload
    utr = payload.get("utr")
    return PayoutBatch(
        payout_id=_text(payload, "payout_id"),
        merchant_id=_text(payload, "merchant_id"),
        currency=_text(payload, "currency"),
        net_minor=_amount(payload, "net_minor"),
        settlement_line_ids=(),
        utr=str(utr) if utr is not None else None,
        occurred_at=fact.occurred_at,
        source_record_id=fact.source_record_id,
    )


def project_bank_transaction(fact: SourceFact) -> BankTransaction:
    """Return the bank statement line a fact describes.

    The amount is carried as the magnitude the document declared, with the
    direction beside it. Nothing here folds the two into a signed number: a
    credit and a debit are different facts about the world, and a sign is one
    lost character away from being the other.
    """
    payload = fact.canonical_payload
    return BankTransaction(
        bank_transaction_id=_text(payload, "bank_transaction_id"),
        bank_reference=_text(payload, "bank_reference"),
        direction=BankDirection(_text(payload, "direction")),
        amount_minor=_amount(payload, "amount_minor"),
        currency=_text(payload, "currency"),
        occurred_at=fact.occurred_at,
        source_record_id=fact.source_record_id,
    )


#: One projection per record type the contract defines.
#:
#: A mapping rather than a chain of branches, so the day a fifth record type is
#: added it is missing from here loudly rather than falling through to a default.
#: `test_every_record_type_has_a_projection` asserts the mapping is total.
PROJECTIONS: Final[dict[SourceRecordType, Callable[[SourceFact], LifecycleRecord]]] = {
    SourceRecordType.PAYMENT_EVENT: project_payment_event,
    SourceRecordType.SETTLEMENT_LINE: project_settlement_line,
    SourceRecordType.PAYOUT: project_payout,
    SourceRecordType.BANK_TRANSACTION: project_bank_transaction,
}


def project(fact: SourceFact) -> LifecycleRecord:
    """Return the lifecycle record a fact describes."""
    return PROJECTIONS[fact.source_record_type](fact)
