"""The payment to payout lifecycle.

Nothing here assumes that one payment equals one payout. A payout carries many
settlement lines, a payment can be followed by refunds, reversals and
chargebacks long after it settles, and a settlement line refers to the payment
it came from rather than containing it. Modelling those as one-to-one would make
the common cases look like exceptions.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.domain.money import Money, MoneyBreakdown
from app.domain.primitives import (
    AmountMinor,
    CurrencyCode,
    EventId,
    Identifier,
    MerchantId,
    PaymentId,
    PayoutId,
    SettlementLineId,
    SourceRecordId,
    UtcTimestamp,
)


class PaymentIdentity(BaseModel):
    """The stable identity of one payment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    payment_id: PaymentId
    merchant_id: MerchantId
    currency: CurrencyCode
    """The currency the payment was taken in. Every later event must match it."""


class PaymentEventType(StrEnum):
    """What happened to a payment."""

    CAPTURE = "CAPTURE"
    """Funds taken from the payer. The amount a payment can later give back."""

    REFUND = "REFUND"
    """Funds returned to the payer at the merchant's request."""

    REVERSAL = "REVERSAL"
    """A capture undone by the provider, for example an authorisation expiry."""

    CHARGEBACK = "CHARGEBACK"
    """Funds pulled back by the payer's bank."""


#: Event types that return money to the payer. They are bounded by the captured
#: amount, which is what INV-004 checks.
RETURNING_EVENT_TYPES: frozenset[PaymentEventType] = frozenset(
    {
        PaymentEventType.REFUND,
        PaymentEventType.REVERSAL,
        PaymentEventType.CHARGEBACK,
    }
)


class PaymentEvent(BaseModel):
    """One thing that happened to one payment, as a source reported it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: EventId
    payment_id: PaymentId
    event_type: PaymentEventType
    amount: Money
    """Always a positive magnitude. Direction comes from ``event_type``."""

    occurred_at: UtcTimestamp
    source_record_id: SourceRecordId
    """The source fact this event was read from. Every event is traceable."""


class SettlementLine(BaseModel):
    """One line of a payout, covering one payment.

    ``net_minor`` is what the source declared. It is stored rather than computed
    so that INV-002 has something real to check the signed formula against.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    settlement_line_id: SettlementLineId
    payout_id: PayoutId
    payment_id: PaymentId
    breakdown: MoneyBreakdown
    net_minor: AmountMinor
    """The net as the source declared it, in the breakdown's currency."""

    occurred_at: UtcTimestamp
    source_record_id: SourceRecordId

    @property
    def currency(self) -> str:
        """Return the currency of this line."""
        return self.breakdown.currency

    @property
    def declared_net(self) -> Money:
        """Return the declared net as a Money value."""
        return Money(amount_minor=self.net_minor, currency=self.breakdown.currency)


class PayoutBatch(BaseModel):
    """A batch of settlement lines paid out to a merchant as one transfer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    payout_id: PayoutId
    merchant_id: MerchantId
    currency: CurrencyCode
    net_minor: AmountMinor
    """The batch total as the source declared it. INV-003 checks it."""

    settlement_line_ids: tuple[SettlementLineId, ...]
    """The lines this batch claims to cover. A tuple, so a batch cannot be
    edited after it is built."""

    utr: Identifier | None = None
    """Bank reference for the transfer, when the source provides one."""

    occurred_at: UtcTimestamp
    source_record_id: SourceRecordId

    @property
    def declared_net(self) -> Money:
        """Return the declared batch total as a Money value."""
        return Money(amount_minor=self.net_minor, currency=self.currency)
