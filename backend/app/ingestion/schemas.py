"""The CSV document schemas this system accepts.

Three schemas, each tied to one record type. Headers are exact: a missing column
and an unexpected column are both errors. A file that is nearly right is more
dangerous than one that is obviously wrong, because a silently ignored column is
a field that quietly stopped being reconciled.
"""

from enum import StrEnum
from typing import Final

from app.domain.facts import SourceRecordType

PARSER_VERSION: Final = "3.0.0"
"""Version of the parsing and normalisation rules.

Recorded on every import receipt, so a fact can always be traced to the rules
that produced it. It changes when a header set, a coercion rule or the
source-record ID derivation changes.

2.0.0 stopped trimming whitespace and started refusing it. 3.0.0 started
refusing a payment event amount of zero. Each is a major step because documents
the previous version accepted can be refused by the next. Facts already stored
are unaffected: the change is to what is accepted, not to how an accepted row is
represented.
"""


class ColumnKind(StrEnum):
    """How one column is parsed and what it must contain."""

    IDENTIFIER = "IDENTIFIER"
    """Non-empty text with no surrounding whitespace."""

    AMOUNT_MINOR = "AMOUNT_MINOR"
    """A signed integer count of the currency's minor unit. Never decimal."""

    NON_NEGATIVE_AMOUNT_MINOR = "NON_NEGATIVE_AMOUNT_MINOR"
    """An amount that must not be below zero, such as a fee magnitude."""

    POSITIVE_AMOUNT_MINOR = "POSITIVE_AMOUNT_MINOR"
    """An amount that must move money, so zero is refused as well as negative.

    Payment event amounts only. A settlement line's fee or tax may legitimately
    be zero; an event that took or returned nothing may not exist."""

    CURRENCY = "CURRENCY"
    """An ISO 4217 alpha-3 code, upper case."""

    TIMESTAMP = "TIMESTAMP"
    """An ISO 8601 timestamp that carries an offset. Naive values are refused."""

    PAYMENT_EVENT_TYPE = "PAYMENT_EVENT_TYPE"
    """One of the payment event types the contract defines."""

    OPTIONAL_IDENTIFIER = "OPTIONAL_IDENTIFIER"
    """Identifier text, or empty to mean absent."""


#: Column layouts, in the order the documents declare them. The order is part of
#: the contract: it is what a reader of the file sees, and it is checked.
PAYMENT_EVENT_COLUMNS: Final[tuple[tuple[str, ColumnKind], ...]] = (
    ("provider_event_id", ColumnKind.IDENTIFIER),
    ("event_id", ColumnKind.IDENTIFIER),
    ("payment_id", ColumnKind.IDENTIFIER),
    ("merchant_id", ColumnKind.IDENTIFIER),
    ("event_type", ColumnKind.PAYMENT_EVENT_TYPE),
    ("amount_minor", ColumnKind.POSITIVE_AMOUNT_MINOR),
    ("currency", ColumnKind.CURRENCY),
    ("occurred_at", ColumnKind.TIMESTAMP),
)

SETTLEMENT_LINE_COLUMNS: Final[tuple[tuple[str, ColumnKind], ...]] = (
    ("provider_event_id", ColumnKind.IDENTIFIER),
    ("settlement_line_id", ColumnKind.IDENTIFIER),
    ("payout_id", ColumnKind.IDENTIFIER),
    ("payment_id", ColumnKind.IDENTIFIER),
    ("gross_minor", ColumnKind.NON_NEGATIVE_AMOUNT_MINOR),
    ("fee_minor", ColumnKind.NON_NEGATIVE_AMOUNT_MINOR),
    ("tax_minor", ColumnKind.NON_NEGATIVE_AMOUNT_MINOR),
    ("adjustment_minor", ColumnKind.AMOUNT_MINOR),
    ("net_minor", ColumnKind.AMOUNT_MINOR),
    ("currency", ColumnKind.CURRENCY),
    ("occurred_at", ColumnKind.TIMESTAMP),
)

PAYOUT_COLUMNS: Final[tuple[tuple[str, ColumnKind], ...]] = (
    ("provider_event_id", ColumnKind.IDENTIFIER),
    ("payout_id", ColumnKind.IDENTIFIER),
    ("merchant_id", ColumnKind.IDENTIFIER),
    ("net_minor", ColumnKind.AMOUNT_MINOR),
    ("currency", ColumnKind.CURRENCY),
    ("utr", ColumnKind.OPTIONAL_IDENTIFIER),
    ("occurred_at", ColumnKind.TIMESTAMP),
)

#: Which layout belongs to which record type. A document is read as exactly one
#: record type, declared by the caller, so a file cannot be guessed at.
COLUMNS_BY_RECORD_TYPE: Final[dict[SourceRecordType, tuple[tuple[str, ColumnKind], ...]]] = {
    SourceRecordType.PAYMENT_EVENT: PAYMENT_EVENT_COLUMNS,
    SourceRecordType.SETTLEMENT_LINE: SETTLEMENT_LINE_COLUMNS,
    SourceRecordType.PAYOUT: PAYOUT_COLUMNS,
}

#: Record types this parser can read. BANK_TRANSACTION is a valid source record
#: type in the contract and has no CSV schema yet, so importing one is refused
#: rather than approximated.
SUPPORTED_RECORD_TYPES: Final[frozenset[SourceRecordType]] = frozenset(COLUMNS_BY_RECORD_TYPE)


def expected_headers(record_type: SourceRecordType) -> tuple[str, ...]:
    """Return the exact header row a document of this record type must have."""
    return tuple(name for name, _ in COLUMNS_BY_RECORD_TYPE[record_type])
