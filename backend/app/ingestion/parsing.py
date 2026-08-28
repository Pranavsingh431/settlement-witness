"""Deterministic CSV reading and normalisation.

Nothing here is clever. Every coercion is explicit, every refusal has a code,
and the same bytes always produce the same facts. The parser uses the standard
library ``csv`` module with the default dialect, so behaviour does not depend on
anything installed.

The rule that shapes most of this file: a value that could plausibly mean two
things is refused rather than guessed at.
"""

import csv
import hashlib
import io
import re
from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import Final

from pydantic import BaseModel, ConfigDict, ValidationError

from app.domain.banking import BankDirection
from app.domain.facts import (
    SourceFact,
    SourceLocator,
    SourceLocatorKind,
    SourceRecordType,
    SourceSystem,
    compute_payload_hash,
)
from app.domain.lifecycle import PaymentEventType
from app.domain.primitives import CanonicalPayload, JsonValue, to_utc
from app.ingestion.errors import DocumentError, DocumentErrorCode, RowError, RowErrorCode
from app.ingestion.schemas import (
    BANK_DIRECTIONS,
    COLUMNS_BY_RECORD_TYPE,
    PARSER_VERSION,
    ColumnKind,
    expected_headers,
)

#: A whole number, optionally signed. No decimal point, no exponent, no spaces.
#: ``12.0`` fails this on purpose: see RowErrorCode.NOT_AN_INTEGER.
_INTEGER_PATTERN: Final = re.compile(r"^-?\d+$")

_CURRENCY_PATTERN: Final = re.compile(r"^[A-Z]{3}$")


class ParsedRow(BaseModel):
    """One row that parsed cleanly, with its canonical payload."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    row_number: int
    source_record_id: str
    canonical_payload: CanonicalPayload
    provider_event_id: str
    occurred_at: datetime


def compute_document_hash(content: bytes) -> str:
    """Return the SHA-256 of the document exactly as it arrived.

    The hash is over the raw bytes, so a file that differs by a byte is a
    different document even if it parses to the same rows. That is what makes it
    usable as the stable half of a source-record ID.
    """
    return hashlib.sha256(content).hexdigest()


def derive_source_record_id(
    document_hash: str,
    source_system: SourceSystem,
    record_type: SourceRecordType,
    row_number: int,
) -> str:
    """Return the stable identity of one row of one document.

    Built from the document hash, the source system, the record type and the
    one-based row number.

    Deliberately not from a file path: the same bytes imported from a different
    directory, or from a stream with no path at all, are the same records, and a
    path would leak a local directory layout into stored identifiers.

    The source system is included because the contract treats one event seen
    through two systems as two observations. Without it, loading the same
    document as a provider feed and as a merchant ledger would collide, and the
    second observation would be silently swallowed as a duplicate of the first.

    Args:
        document_hash: SHA-256 of the document bytes.
        source_system: The system the document was declared to come from.
        record_type: The record type the document was read as.
        row_number: One-based row number, counting the header as row 1.

    Returns:
        A deterministic identifier, stable across machines and runs.
    """
    return f"{document_hash}:{source_system.value}:{record_type.value}:{row_number}"


def decode_document(content: bytes) -> str:
    """Return the document as text, or refuse it.

    Raises:
        DocumentError: If the bytes are not valid UTF-8. A replacement character
            would silently corrupt an identifier or a merchant name.
    """
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        message = f"document is not valid UTF-8: {error}"
        raise DocumentError(DocumentErrorCode.UNREADABLE_ENCODING, message) from error


def _read_rows(text: str, record_type: SourceRecordType) -> list[list[str]]:
    """Return the data rows, having checked the header exactly.

    Raises:
        DocumentError: If headers are missing, wrong, or there are no data rows.
    """
    if record_type not in COLUMNS_BY_RECORD_TYPE:
        message = f"no CSV schema is defined for record type {record_type.value}"
        raise DocumentError(DocumentErrorCode.UNSUPPORTED_RECORD_TYPE, message)

    reader = csv.reader(io.StringIO(text, newline=""))
    rows = list(reader)

    if not rows:
        message = "document is empty, so it has no header row"
        raise DocumentError(DocumentErrorCode.MISSING_HEADER, message)

    header = tuple(rows[0])
    expected = expected_headers(record_type)
    if header != expected:
        # Compared exactly, including whitespace. A header cell of " event_id"
        # is not the column "event_id": accepting it would mean the documented
        # schema is not actually the schema.
        message = (
            f"headers do not match the {record_type.value} schema exactly; "
            f"expected {list(expected)}, got {list(header)}"
        )
        raise DocumentError(DocumentErrorCode.UNEXPECTED_COLUMNS, message)

    data = [row for row in rows[1:] if row]
    if not data:
        message = "document has a valid header and no data rows"
        raise DocumentError(DocumentErrorCode.NO_ROWS, message)
    return data


def _coerce_cell(
    value: str, column: str, kind: ColumnKind, row_number: int
) -> tuple[JsonValue | None, RowError | None]:
    """Return the canonical value for one cell, or the error that refuses it.

    The cell is read exactly as the document wrote it. Nothing is trimmed,
    because trimming is a guess about intent and this parser refuses ambiguous
    input rather than guessing.
    """
    text = value

    def fail(code: RowErrorCode, message: str) -> tuple[None, RowError]:
        return None, RowError(row_number=row_number, column=column, code=code, message=message)

    if text != text.strip():
        return fail(
            RowErrorCode.SURROUNDING_WHITESPACE,
            f"{column} has leading or trailing whitespace, which is refused "
            f"rather than trimmed; got {text!r}",
        )

    if kind is ColumnKind.OPTIONAL_IDENTIFIER:
        # Exactly empty means absent. Whitespace never reaches here, because a
        # whitespace-only cell was refused above rather than becoming empty.
        return (text or None), None

    if not text:
        return fail(RowErrorCode.MISSING_VALUE, f"{column} is required and was empty")

    if kind is ColumnKind.IDENTIFIER:
        return text, None

    if kind in (
        ColumnKind.AMOUNT_MINOR,
        ColumnKind.NON_NEGATIVE_AMOUNT_MINOR,
        ColumnKind.POSITIVE_AMOUNT_MINOR,
    ):
        if not _INTEGER_PATTERN.match(text):
            return fail(
                RowErrorCode.NOT_AN_INTEGER,
                f"{column} must be a whole number of minor units, got {text!r}",
            )
        amount = int(text)
        if kind is ColumnKind.NON_NEGATIVE_AMOUNT_MINOR and amount < 0:
            return fail(
                RowErrorCode.NEGATIVE_AMOUNT,
                f"{column} is a magnitude and must not be negative, got {amount}",
            )
        if kind is ColumnKind.POSITIVE_AMOUNT_MINOR and amount <= 0:
            return fail(
                RowErrorCode.NON_POSITIVE_AMOUNT,
                f"{column} must move money, so it must be greater than zero, got {amount}",
            )
        return amount, None

    if kind is ColumnKind.CURRENCY:
        if not _CURRENCY_PATTERN.match(text):
            return fail(
                RowErrorCode.INVALID_CURRENCY,
                f"{column} must be an ISO 4217 alpha-3 code, got {text!r}",
            )
        return text, None

    if kind is ColumnKind.PAYMENT_EVENT_TYPE:
        try:
            return PaymentEventType(text).value, None
        except ValueError:
            allowed = sorted(member.value for member in PaymentEventType)
            return fail(
                RowErrorCode.INVALID_ENUM,
                f"{column} must be one of {allowed}, got {text!r}",
            )

    if kind is ColumnKind.BANK_DIRECTION:
        try:
            return BankDirection(text).value, None
        except ValueError:
            return fail(
                RowErrorCode.INVALID_ENUM,
                f"{column} must be one of {list(BANK_DIRECTIONS)}, got {text!r}",
            )

    return _coerce_timestamp(text, column, row_number)


def _coerce_timestamp(
    text: str, column: str, row_number: int
) -> tuple[JsonValue | None, RowError | None]:
    """Return an offset-aware timestamp as a UTC ISO string, or an error."""
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None, RowError(
            row_number=row_number,
            column=column,
            code=RowErrorCode.INVALID_TIMESTAMP,
            message=f"{column} must be an ISO 8601 timestamp, got {text!r}",
        )

    try:
        return to_utc(parsed).isoformat(), None
    except ValueError:
        return None, RowError(
            row_number=row_number,
            column=column,
            code=RowErrorCode.NAIVE_TIMESTAMP,
            message=f"{column} must carry a UTC offset, got {text!r}",
        )


def parse_row(
    cells: Sequence[str],
    record_type: SourceRecordType,
    row_number: int,
    document_hash: str,
    source_system: SourceSystem,
) -> tuple[ParsedRow | None, list[RowError]]:
    """Turn one row into a canonical payload, or return everything wrong with it.

    Every column is examined even after the first failure, so one import reports
    all of a row's problems rather than making a person fix them one at a time.
    """
    columns = COLUMNS_BY_RECORD_TYPE[record_type]

    if len(cells) != len(columns):
        return None, [
            RowError(
                row_number=row_number,
                column=None,
                code=RowErrorCode.WRONG_FIELD_COUNT,
                message=f"expected {len(columns)} fields, got {len(cells)}",
            )
        ]

    payload: dict[str, JsonValue] = {}
    errors: list[RowError] = []
    for (column, kind), raw in zip(columns, cells, strict=True):
        value, error = _coerce_cell(raw, column, kind, row_number)
        if error is not None:
            errors.append(error)
        else:
            payload[column] = value

    if errors:
        return None, errors

    occurred_at = to_utc(datetime.fromisoformat(str(payload["occurred_at"])))
    return (
        ParsedRow(
            row_number=row_number,
            source_record_id=derive_source_record_id(
                document_hash, source_system, record_type, row_number
            ),
            canonical_payload=payload,
            provider_event_id=str(payload["provider_event_id"]),
            occurred_at=occurred_at,
        ),
        [],
    )


def build_source_fact(
    parsed: ParsedRow,
    source_system: SourceSystem,
    record_type: SourceRecordType,
    document_hash: str,
    observed_at: datetime,
) -> SourceFact | RowError:
    """Turn a parsed row into a source fact, letting the domain model decide.

    The parser's opinion is not final. If the contract refuses a row this parser
    was willing to accept, the row is rejected, because the contract is the
    definition and this module is only a reader of files.

    Returns:
        The fact, or the error explaining why the contract refused the row.
    """
    try:
        fact = SourceFact(
            source_record_id=parsed.source_record_id,
            source_system=source_system,
            source_record_type=record_type,
            source_locator=SourceLocator(
                kind=SourceLocatorKind.FILE_ROW,
                reference=document_hash,
                row_number=parsed.row_number,
            ),
            provider_event_id=parsed.provider_event_id,
            observed_at=observed_at,
            occurred_at=parsed.occurred_at,
            canonical_payload=parsed.canonical_payload,
            payload_hash=compute_payload_hash(parsed.canonical_payload),
        )
    except ValidationError as error:
        return RowError(
            row_number=parsed.row_number,
            column=None,
            code=RowErrorCode.DOMAIN_VALIDATION_FAILED,
            message=f"the domain contract refused this row: {error.error_count()} problem(s)",
        )
    return fact


def iter_document_rows(
    content: bytes, record_type: SourceRecordType
) -> Iterator[tuple[int, list[str]]]:
    """Yield each data row with its one-based row number.

    The header is row 1, so the first data row is row 2 and the numbers match
    what a person sees when they open the file.
    """
    text = decode_document(content)
    yield from enumerate(_read_rows(text, record_type), start=2)


__all__ = [
    "PARSER_VERSION",
    "ParsedRow",
    "build_source_fact",
    "compute_document_hash",
    "decode_document",
    "derive_source_record_id",
    "iter_document_rows",
    "parse_row",
]
