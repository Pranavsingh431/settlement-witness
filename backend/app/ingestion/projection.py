"""Canonical lifecycle records, projected from stored source facts.

A lifecycle record is a view of a fact, not a second copy of it. Source facts
are the store; payment events, settlement lines and payout batches are derived
from them on demand.

Keeping it that way means there is one place a correction can come from, and no
possibility of the two disagreeing. It also means a projection always carries
the ``source_record_id`` of the fact it came from, so every lifecycle record can
be traced back to the row of the document that produced it.
"""

from app.domain.facts import SourceFact, SourceRecordType
from app.domain.lifecycle import (
    PaymentEvent,
    PaymentEventType,
    PayoutBatch,
    SettlementLine,
)
from app.domain.money import Money, MoneyBreakdown
from app.domain.primitives import CanonicalPayload

type LifecycleRecord = PaymentEvent | SettlementLine | PayoutBatch


class UnsupportedProjectionError(ValueError):
    """Raised when a fact has no lifecycle projection defined.

    ``BANK_TRANSACTION`` is a valid record type in the contract with no CSV
    schema and no projection yet. Refusing is honest; inventing a shape for it
    would not be.
    """

    def __init__(self, record_type: SourceRecordType) -> None:
        super().__init__(f"no lifecycle projection is defined for {record_type.value}")
        self.record_type = record_type


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


def project(fact: SourceFact) -> LifecycleRecord:
    """Return the lifecycle record a fact describes.

    Raises:
        UnsupportedProjectionError: If the record type has no projection.
    """
    if fact.source_record_type is SourceRecordType.PAYMENT_EVENT:
        return project_payment_event(fact)
    if fact.source_record_type is SourceRecordType.SETTLEMENT_LINE:
        return project_settlement_line(fact)
    if fact.source_record_type is SourceRecordType.PAYOUT:
        return project_payout(fact)
    raise UnsupportedProjectionError(fact.source_record_type)
