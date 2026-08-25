"""Why a row or a document was refused.

Every refusal carries a stable code and, where a row is involved, the one-based
row number a person would see in a spreadsheet. Messages are for people; codes
are for tests, receipts and anything that has to compare two runs.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class RowErrorCode(StrEnum):
    """A precise reason one row could not become a source fact."""

    MISSING_VALUE = "MISSING_VALUE"
    """A required column was empty."""

    SURROUNDING_WHITESPACE = "SURROUNDING_WHITESPACE"
    """A cell carried leading or trailing whitespace.

    Refused rather than trimmed. Trimming is a guess about what the producer
    meant, and this parser refuses ambiguous input rather than guessing. It also
    hides a real class of defect: a padded identifier usually means an export
    template is broken, and silently accepting it lets the same file produce two
    different identities depending on which system read it.

    A cell containing only whitespace is refused here too. It is not the same as
    an empty cell, and quietly treating it as one would make a blank column and a
    space-filled column mean the same thing."""

    NOT_AN_INTEGER = "NOT_AN_INTEGER"
    """A money column held something other than a whole number of minor units.

    This is the code for ``12.5`` and equally for ``12.0``. A decimal point in a
    money column means the file is quoting a different unit from the one the
    contract stores, and guessing which would be worse than refusing."""

    NEGATIVE_AMOUNT = "NEGATIVE_AMOUNT"
    """A magnitude column, such as a fee, held a value below zero."""

    INVALID_CURRENCY = "INVALID_CURRENCY"
    """Not an ISO 4217 alpha-3 code."""

    NAIVE_TIMESTAMP = "NAIVE_TIMESTAMP"
    """A timestamp with no offset. Ordering it against an aware one is guesswork."""

    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    """Not an ISO 8601 timestamp at all."""

    INVALID_ENUM = "INVALID_ENUM"
    """A value outside the set the contract defines for that column."""

    WRONG_FIELD_COUNT = "WRONG_FIELD_COUNT"
    """The row had more or fewer fields than the header declared."""

    DOMAIN_VALIDATION_FAILED = "DOMAIN_VALIDATION_FAILED"
    """The row parsed but the domain model refused it. The contract is the
    final word, so a row that this parser considers acceptable and the models do
    not is still a rejected row."""


class DocumentErrorCode(StrEnum):
    """A precise reason a whole document could not be read."""

    UNREADABLE_ENCODING = "UNREADABLE_ENCODING"
    """The bytes are not valid UTF-8."""

    MISSING_HEADER = "MISSING_HEADER"
    """The file was empty, so there was no header row."""

    UNEXPECTED_COLUMNS = "UNEXPECTED_COLUMNS"
    """Headers do not match the declared record type exactly."""

    UNSUPPORTED_RECORD_TYPE = "UNSUPPORTED_RECORD_TYPE"
    """No CSV schema is defined for the declared record type."""

    NO_ROWS = "NO_ROWS"
    """A valid header with no data rows. Accepting it would write an import
    receipt implying work was done."""


class RowError(BaseModel):
    """One row that could not be turned into a source fact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    row_number: int
    """One-based, counting the header as row 1, so it matches a spreadsheet."""

    column: str | None
    code: RowErrorCode
    message: str


class DocumentError(Exception):
    """Raised when a document cannot be read at all.

    Distinct from a row error: there is no partial reading of a file whose
    encoding is broken or whose headers are wrong.
    """

    def __init__(self, code: DocumentErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
