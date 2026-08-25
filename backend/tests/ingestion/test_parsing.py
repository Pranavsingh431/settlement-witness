"""Tests for deterministic CSV parsing and normalisation."""

from datetime import UTC, datetime

import pytest

from app.domain.facts import SourceRecordType, SourceSystem
from app.ingestion.errors import DocumentError, DocumentErrorCode, RowError, RowErrorCode
from app.ingestion.parsing import (
    ParsedRow,
    compute_document_hash,
    decode_document,
    derive_source_record_id,
    iter_document_rows,
    parse_row,
)
from app.ingestion.schemas import (
    COLUMNS_BY_RECORD_TYPE,
    SUPPORTED_RECORD_TYPES,
    expected_headers,
)
from tests.ingestion.conftest import read_fixture

PAYMENT_HEADER = ",".join(expected_headers(SourceRecordType.PAYMENT_EVENT))


def payment_document(*rows: str) -> bytes:
    """Return a payment event document with the given data rows."""
    return ("\n".join([PAYMENT_HEADER, *rows]) + "\n").encode("utf-8")


def payment_row(**overrides: str) -> str:
    """Return one valid payment event row, with overrides applied."""
    cells = {
        "provider_event_id": "pe-1",
        "event_id": "evt-1",
        "payment_id": "pay-1",
        "merchant_id": "merch-1",
        "event_type": "CAPTURE",
        "amount_minor": "1000",
        "currency": "INR",
        "occurred_at": "2026-08-20T09:15:00+05:30",
    }
    cells.update(overrides)
    return ",".join(cells[name] for name in expected_headers(SourceRecordType.PAYMENT_EVENT))


def parse_one(
    row: str, record_type: SourceRecordType = SourceRecordType.PAYMENT_EVENT
) -> tuple[ParsedRow | None, list[RowError]]:
    """Parse a single data row and return the result and its errors."""
    return parse_row(row.split(","), record_type, 2, "a" * 64, SourceSystem.PSP_API)


class TestDocumentHashAndIdentity:
    """A source record ID is derived from content, never from where it lives."""

    def test_the_hash_is_over_the_raw_bytes(self) -> None:
        """A file differing by one byte is a different document."""
        assert compute_document_hash(b"a") != compute_document_hash(b"b")

    def test_the_hash_is_stable(self) -> None:
        """The same bytes always hash the same."""
        assert compute_document_hash(b"abc") == compute_document_hash(b"abc")

    def test_a_source_record_id_combines_hash_type_and_row(self) -> None:
        """All three parts are present and in a fixed order."""
        record_id = derive_source_record_id(
            "f" * 64, SourceSystem.PSP_API, SourceRecordType.PAYMENT_EVENT, 7
        )
        assert record_id == f"{'f' * 64}:PSP_API:PAYMENT_EVENT:7"

    def test_the_same_row_of_the_same_document_has_one_identity(self) -> None:
        """Re-importing a document produces the identifiers it produced before."""
        args = ("a" * 64, SourceSystem.PSP_API, SourceRecordType.PAYOUT, 3)
        assert derive_source_record_id(*args) == derive_source_record_id(*args)

    def test_identity_does_not_depend_on_a_file_path(self) -> None:
        """The same bytes read from anywhere are the same records.

        A path would also leak a local directory layout into stored identifiers.
        """
        content = read_fixture("payment_events.csv")
        assert compute_document_hash(content) in derive_source_record_id(
            compute_document_hash(content),
            SourceSystem.PSP_API,
            SourceRecordType.PAYMENT_EVENT,
            2,
        )

    def test_row_type_and_system_all_change_the_identity(self) -> None:
        """Row, record type and source system each make a distinct observation."""
        psp = SourceSystem.PSP_API
        base = derive_source_record_id("a" * 64, psp, SourceRecordType.PAYMENT_EVENT, 2)
        assert base != derive_source_record_id("a" * 64, psp, SourceRecordType.PAYMENT_EVENT, 3)
        assert base != derive_source_record_id("a" * 64, psp, SourceRecordType.PAYOUT, 2)
        assert base != derive_source_record_id(
            "a" * 64, SourceSystem.MERCHANT_LEDGER, SourceRecordType.PAYMENT_EVENT, 2
        )


class TestDocumentLevelRefusals:
    """A document that cannot be read is refused whole."""

    def test_invalid_utf8_is_refused(self) -> None:
        """A replacement character would silently corrupt an identifier."""
        with pytest.raises(DocumentError) as caught:
            decode_document(b"\xff\xfe not utf-8")
        assert caught.value.code is DocumentErrorCode.UNREADABLE_ENCODING

    def test_an_empty_document_is_refused(self) -> None:
        """No header row means nothing can be interpreted."""
        with pytest.raises(DocumentError) as caught:
            list(iter_document_rows(b"", SourceRecordType.PAYMENT_EVENT))
        assert caught.value.code is DocumentErrorCode.MISSING_HEADER

    def test_a_missing_column_is_refused(self) -> None:
        """Headers must match exactly."""
        content = b"provider_event_id,event_id\npe-1,evt-1\n"
        with pytest.raises(DocumentError) as caught:
            list(iter_document_rows(content, SourceRecordType.PAYMENT_EVENT))
        assert caught.value.code is DocumentErrorCode.UNEXPECTED_COLUMNS

    def test_an_unexpected_column_is_refused(self) -> None:
        """A silently ignored column is a field that stopped being reconciled."""
        with pytest.raises(DocumentError) as caught:
            list(
                iter_document_rows(
                    read_fixture("invalid_headers.csv"), SourceRecordType.PAYMENT_EVENT
                )
            )
        assert caught.value.code is DocumentErrorCode.UNEXPECTED_COLUMNS

    def test_reordered_columns_are_refused(self) -> None:
        """Column order is part of the documented schema."""
        header = ",".join(reversed(expected_headers(SourceRecordType.PAYMENT_EVENT)))
        with pytest.raises(DocumentError) as caught:
            list(iter_document_rows(f"{header}\nx\n".encode(), SourceRecordType.PAYMENT_EVENT))
        assert caught.value.code is DocumentErrorCode.UNEXPECTED_COLUMNS

    def test_a_header_with_no_rows_is_refused(self) -> None:
        """Accepting it would write a receipt implying work was done."""
        with pytest.raises(DocumentError) as caught:
            list(iter_document_rows(payment_document(), SourceRecordType.PAYMENT_EVENT))
        assert caught.value.code is DocumentErrorCode.NO_ROWS

    def test_a_record_type_with_no_schema_is_refused(self) -> None:
        """BANK_TRANSACTION is a valid record type with no CSV schema yet."""
        assert SourceRecordType.BANK_TRANSACTION not in SUPPORTED_RECORD_TYPES
        with pytest.raises(DocumentError) as caught:
            list(iter_document_rows(b"a\n", SourceRecordType.BANK_TRANSACTION))
        assert caught.value.code is DocumentErrorCode.UNSUPPORTED_RECORD_TYPE

    def test_row_numbers_start_at_two(self) -> None:
        """The header is row 1, so numbers match what a spreadsheet shows."""
        rows = list(
            iter_document_rows(
                payment_document(payment_row(), payment_row(provider_event_id="pe-2")),
                SourceRecordType.PAYMENT_EVENT,
            )
        )
        assert [number for number, _ in rows] == [2, 3]


class TestMoneyIsNeverDecimal:
    """The rule that matters most for a reconciliation system."""

    @pytest.mark.parametrize("value", ["12.5", "12.0", "1e3", " 12 .5", "12.", "0x10", "NaN"])
    def test_a_non_integer_amount_is_refused(self, value: str) -> None:
        """Including 12.0, which is integral and still refused.

        A decimal point in a money column means the file is quoting a different
        unit from the one the contract stores. Accepting 12.0 today invites 12.1
        tomorrow.
        """
        _, errors = parse_one(payment_row(amount_minor=value))
        assert [error.code for error in errors] == [RowErrorCode.NOT_AN_INTEGER]

    def test_a_quoted_thousands_separator_is_refused(self) -> None:
        """Read through the real CSV reader, so the quoting is genuine."""
        content = payment_document(payment_row(amount_minor='"1,000"'))
        rows = list(iter_document_rows(content, SourceRecordType.PAYMENT_EVENT))
        _, errors = parse_row(
            rows[0][1], SourceRecordType.PAYMENT_EVENT, 2, "a" * 64, SourceSystem.PSP_API
        )
        assert [error.code for error in errors] == [RowErrorCode.NOT_AN_INTEGER]

    def test_a_negative_magnitude_is_refused(self) -> None:
        """An amount column that is a magnitude must not go below zero."""
        _, errors = parse_one(payment_row(amount_minor="-1"))
        assert [error.code for error in errors] == [RowErrorCode.NEGATIVE_AMOUNT]

    def test_a_signed_adjustment_is_accepted(self) -> None:
        """Adjustments cover credits and debits, so they may be negative."""
        row = "sl-1,line-1,payout-1,pay-1,1000,20,3,-5,972,INR,2026-08-20T09:15:00+05:30"
        parsed, errors = parse_one(row, SourceRecordType.SETTLEMENT_LINE)
        assert errors == []
        assert parsed is not None
        assert parsed.canonical_payload["adjustment_minor"] == -5

    def test_a_parsed_amount_is_an_integer_in_the_payload(self) -> None:
        """The canonical payload holds an int, never a string or a float."""
        parsed, _ = parse_one(payment_row(amount_minor="1000"))
        assert parsed is not None
        assert parsed.canonical_payload["amount_minor"] == 1000
        assert isinstance(parsed.canonical_payload["amount_minor"], int)


class TestRowLevelRefusals:
    """Each bad cell has its own code."""

    def test_a_blank_required_value_is_refused(self) -> None:
        """Empty is not a value."""
        _, errors = parse_one(payment_row(payment_id=""))
        assert [error.code for error in errors] == [RowErrorCode.MISSING_VALUE]

    def test_a_naive_timestamp_is_refused(self) -> None:
        """Guessing a timezone would make settlement windows wrong."""
        _, errors = parse_one(payment_row(occurred_at="2026-08-20T09:15:00"))
        assert [error.code for error in errors] == [RowErrorCode.NAIVE_TIMESTAMP]

    def test_an_unparseable_timestamp_is_refused(self) -> None:
        """Not a timestamp at all."""
        _, errors = parse_one(payment_row(occurred_at="last tuesday"))
        assert [error.code for error in errors] == [RowErrorCode.INVALID_TIMESTAMP]

    def test_an_invalid_currency_is_refused(self) -> None:
        """ISO 4217 alpha-3 or nothing."""
        _, errors = parse_one(payment_row(currency="rupees"))
        assert [error.code for error in errors] == [RowErrorCode.INVALID_CURRENCY]

    def test_an_invalid_event_type_is_refused(self) -> None:
        """The contract names four event types."""
        _, errors = parse_one(payment_row(event_type="SETTLED"))
        assert [error.code for error in errors] == [RowErrorCode.INVALID_ENUM]

    def test_a_short_row_is_refused(self) -> None:
        """Fewer fields than the header declared."""
        _, errors = parse_one("pe-1,evt-1")
        assert [error.code for error in errors] == [RowErrorCode.WRONG_FIELD_COUNT]

    def test_every_problem_in_a_row_is_reported(self) -> None:
        """One import shows a person everything wrong, not one thing per run."""
        _, errors = parse_one(payment_row(amount_minor="12.5", currency="x", event_type="NOPE"))
        assert len(errors) == 3

    def test_an_error_names_its_column_and_row(self) -> None:
        """A person should be able to go straight to the cell."""
        _, errors = parse_one(payment_row(currency="x"))
        assert errors[0].column == "currency"
        assert errors[0].row_number == 2


class TestNormalisation:
    """What a valid row becomes."""

    def test_a_timestamp_is_stored_as_the_same_instant_in_utc(self) -> None:
        """An offset is presentation, not fact."""
        parsed, _ = parse_one(payment_row(occurred_at="2026-08-20T09:15:00+05:30"))
        assert parsed is not None
        assert parsed.occurred_at == datetime(2026, 8, 20, 3, 45, tzinfo=UTC)

    def test_surrounding_whitespace_is_removed(self) -> None:
        """A padded cell is the same value as an unpadded one."""
        parsed, _ = parse_one(payment_row(payment_id="  pay-1  "))
        assert parsed is not None
        assert parsed.canonical_payload["payment_id"] == "pay-1"

    def test_an_optional_column_may_be_empty(self) -> None:
        """A payout without a bank reference is still a payout."""
        row = "po-1,payout-1,merch-1,1000,INR,,2026-08-20T09:15:00+05:30"
        parsed, errors = parse_one(row, SourceRecordType.PAYOUT)
        assert errors == []
        assert parsed is not None
        assert parsed.canonical_payload["utr"] is None

    def test_the_payload_holds_exactly_the_declared_columns(self) -> None:
        """No extra keys, no missing ones."""
        parsed, _ = parse_one(payment_row())
        assert parsed is not None
        assert set(parsed.canonical_payload) == set(
            expected_headers(SourceRecordType.PAYMENT_EVENT)
        )


class TestSchemaDefinitions:
    """The three documented schemas."""

    def test_three_record_types_are_supported(self) -> None:
        """Payment events, settlement lines and payouts."""
        assert {member.value for member in SUPPORTED_RECORD_TYPES} == {
            "PAYMENT_EVENT",
            "SETTLEMENT_LINE",
            "PAYOUT",
        }

    def test_every_schema_starts_with_the_provider_event_id(self) -> None:
        """It is half the idempotency identity, so every document must carry it."""
        for record_type in SUPPORTED_RECORD_TYPES:
            assert expected_headers(record_type)[0] == "provider_event_id"

    def test_every_column_has_exactly_one_kind(self) -> None:
        """A column cannot be parsed two ways."""
        for columns in COLUMNS_BY_RECORD_TYPE.values():
            names = [name for name, _ in columns]
            assert len(names) == len(set(names))
