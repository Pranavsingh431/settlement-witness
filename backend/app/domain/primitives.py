"""Shared primitive types for the domain contract.

Every identifier, timestamp and code in the contract is built from the types
here, so the rules about shape live in one place rather than being restated on
each model.
"""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import (
    AfterValidator,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
)

# Identifiers are opaque to this layer. The contract only requires that they are
# non-empty, bounded, and carry no leading or trailing whitespace, so that two
# records cannot differ by invisible characters alone.
type Identifier = Annotated[
    str,
    StringConstraints(min_length=1, max_length=200, strip_whitespace=False, pattern=r"^\S(.*\S)?$"),
]

type SourceRecordId = Identifier
type ProviderEventId = Identifier
type PaymentId = Identifier
type MerchantId = Identifier
type EventId = Identifier
type SettlementLineId = Identifier
type PayoutId = Identifier
type DecisionId = Identifier

# ISO 4217 alpha-3. The contract validates the shape and deliberately does not
# hold a list of accepted currencies. A hard coded list would silently reject a
# currency the system had simply not heard of yet, which is a worse failure than
# accepting one that later turns out to be unused.
type CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]

# Lowercase hexadecimal SHA-256 digest.
type PayloadHash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

# Amounts are always integers in the minor unit of their currency, for example
# paise for INR or cents for USD. Floating point is never used for money.
type AmountMinor = StrictInt
type NonNegativeAmountMinor = Annotated[StrictInt, Field(ge=0)]


def to_utc(value: datetime) -> datetime:
    """Return the same instant expressed in UTC.

    Args:
        value: A timezone aware datetime.

    Returns:
        The identical instant with its offset expressed as UTC.

    Raises:
        ValueError: If the datetime carries no timezone. A naive timestamp is
            ambiguous, and guessing a timezone for financial records would make
            ordering and settlement windows wrong in ways that are hard to see.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        message = "timestamp must be timezone aware; naive datetimes are ambiguous"
        raise ValueError(message)
    return value.astimezone(UTC)


# Any aware datetime is accepted and stored as the same instant in UTC. The
# offset it arrived with is presentation, not fact.
type UtcTimestamp = Annotated[datetime, AfterValidator(to_utc)]

# JSON that a canonical payload may contain.
#
# Float is deliberately absent. Every number in a canonical payload is either an
# integer in minor units or a string. Ingestion converts before it builds a
# source fact, so a rounding error cannot enter the system through a payload and
# then be treated as a real difference by a reconciliation check.
type JsonValue = StrictStr | StrictBool | StrictInt | list[JsonValue] | dict[str, JsonValue] | None
type CanonicalPayload = dict[str, JsonValue]
