"""What a bank statement row says, and nothing more.

This is the only record in the contract that comes from outside the payment
provider. That is the whole reason it exists: everything else in this system is
the provider describing its own behaviour, and a provider saying it paid a
merchant is not the same fact as a bank saying money arrived.

The model is deliberately thin. A bank transaction carries an identity, a time,
a direction, an amount, a currency and the reference the transfer was sent
under. It carries no description, no counterparty name and no free text, because
none of those can be compared exactly and every one of them invites a fuzzy
match. What cannot be compared exactly is not evidence here.

Deliberately not exported in the domain JSON Schema, and deliberately not a
reason to move `DOMAIN_SCHEMA_VERSION`. No decision, invariant, exception code
or reason code reads any of this. The version that covers it is the bank
finality contract's own, which is what changes when these rules change. Bumping
the domain contract would rewrite the declared version of every recorded
decision and invalidate every recorded run, for a change no decision can
observe.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.domain.primitives import (
    CurrencyCode,
    Identifier,
    SourceRecordId,
    UtcTimestamp,
)

type PositiveAmountMinor = Annotated[StrictInt, Field(gt=0)]
"""A magnitude that must move money. Declared here rather than in
`app.domain.primitives`, so that nothing in the exported domain contract moves
for a record no decision reads."""


class BankDirection(StrEnum):
    """Which way money moved, from the account holder's point of view.

    Two values and no third. A statement that cannot say which way money went is
    not evidence that a payout arrived, and an amount whose sign carries the
    direction would let a mis-signed row read as the opposite fact.
    """

    CREDIT = "CREDIT"
    """Money arrived in the account. The only direction a payout can produce."""

    DEBIT = "DEBIT"
    """Money left the account. Never evidence that a payout was received."""


class BankTransaction(BaseModel):
    """One line of a bank statement, as the bank stated it.

    The amount is a magnitude and is always positive. Direction is carried
    separately rather than as a sign, so a row cannot become its own opposite
    through a lost minus.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    bank_transaction_id: Identifier
    """The bank's own identity for the line."""

    bank_reference: Identifier
    """The reference the transfer carried, which a payout calls its UTR.

    Required. A statement row with no reference cannot be associated with any
    payout by exact matching, and this system does not associate them any other
    way."""

    direction: BankDirection
    amount_minor: PositiveAmountMinor
    currency: CurrencyCode
    occurred_at: UtcTimestamp
    source_record_id: SourceRecordId
