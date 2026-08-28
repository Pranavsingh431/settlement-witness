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

    def test_a_record_type_with_no_schema_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Every record type has a layout now, so one is taken away to check.

        The guard is not decoration. It is what makes a record type added to the
        contract without a CSV layout fail loudly at the first document rather
        than being read against some other type's columns, and there is no way
        to reach it without removing a layout, because the mapping is total.
        """
        monkeypatch.delitem(COLUMNS_BY_RECORD_TYPE, SourceRecordType.BANK_TRANSACTION)

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

    @pytest.mark.parametrize("value", ["12.5", "12.0", "1e3", "12.", "0x10", "NaN"])
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
        """A magnitude column, such as a fee, must not go below zero."""
        row = "sl-1,line-1,payout-1,pay-1,1000,-20,3,0,977,INR,2026-08-20T09:15:00+05:30"
        _, errors = parse_one(row, SourceRecordType.SETTLEMENT_LINE)
        assert [error.code for error in errors] == [RowErrorCode.NEGATIVE_AMOUNT]

    def test_a_zero_magnitude_is_accepted(self) -> None:
        """A fee of zero is a free transaction, which is a real thing.

        Only payment event amounts must move money. Settlement components may be
        zero, and this holds the two rules apart.
        """
        row = "sl-1,line-1,payout-1,pay-1,1000,0,0,0,1000,INR,2026-08-20T09:15:00+05:30"
        parsed, errors = parse_one(row, SourceRecordType.SETTLEMENT_LINE)
        assert errors == []
        assert parsed is not None
        assert parsed.canonical_payload["fee_minor"] == 0

    @pytest.mark.parametrize("value", ["0", "-1", "-1000"])
    def test_a_payment_event_amount_must_move_money(self, value: str) -> None:
        """Zero and negative alike. An event that moved nothing is not an event."""
        _, errors = parse_one(payment_row(amount_minor=value))
        assert [error.code for error in errors] == [RowErrorCode.NON_POSITIVE_AMOUNT]

    def test_one_minor_unit_is_a_valid_event_amount(self) -> None:
        """The rule is strictly positive, not a minimum size."""
        parsed, errors = parse_one(payment_row(amount_minor="1"))
        assert errors == []
        assert parsed is not None
        assert parsed.canonical_payload["amount_minor"] == 1

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

    def test_an_unpadded_cell_is_taken_exactly_as_written(self) -> None:
        """Nothing is added or removed from a well formed value."""
        parsed, _ = parse_one(payment_row(payment_id="pay-1"))
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

    def test_every_record_type_in_the_contract_is_supported(self) -> None:
        """Payment events, settlement lines, payouts and bank transactions.

        Asserted against the contract's own enum rather than a written-out list,
        so a fifth record type is a failure here until it has a layout.
        """
        assert set(SUPPORTED_RECORD_TYPES) == set(SourceRecordType)

    def test_every_schema_starts_with_the_provider_event_id(self) -> None:
        """It is half the idempotency identity, so every document must carry it."""
        for record_type in SUPPORTED_RECORD_TYPES:
            assert expected_headers(record_type)[0] == "provider_event_id"

    def test_every_column_has_exactly_one_kind(self) -> None:
        """A column cannot be parsed two ways."""
        for columns in COLUMNS_BY_RECORD_TYPE.values():
            names = [name for name, _ in columns]
            assert len(names) == len(set(names))


class TestWhitespaceIsRefusedNotTrimmed:
    """Padding is a refusal, because trimming is a guess about intent.

    An earlier version of this parser trimmed every cell and every header. That
    contradicted the documented exact-header rule, and it hid a real class of
    defect: a padded identifier usually means an export template is broken, and
    silently accepting it lets one file produce two identities depending on
    which system read it.
    """

    @pytest.mark.parametrize(
        ("column", "value"),
        [
            ("provider_event_id", "  pe-1  "),
            ("payment_id", "pay-1 "),
            ("merchant_id", " merch-1"),
            ("amount_minor", " 1000 "),
            ("currency", " INR"),
            ("event_type", "CAPTURE "),
            ("occurred_at", " 2026-08-20T09:15:00+05:30"),
        ],
    )
    def test_padding_in_any_column_is_refused(self, column: str, value: str) -> None:
        """Identifiers, numbers, currency, enums and timestamps alike."""
        _, errors = parse_one(payment_row(**{column: value}))

        assert [error.code for error in errors] == [RowErrorCode.SURROUNDING_WHITESPACE]
        assert errors[0].column == column

    def test_a_tab_counts_as_whitespace(self) -> None:
        """Not just spaces."""
        _, errors = parse_one(payment_row(payment_id="\tpay-1"))
        assert [error.code for error in errors] == [RowErrorCode.SURROUNDING_WHITESPACE]

    def test_internal_whitespace_is_left_alone(self) -> None:
        """Only the edges are refused. A name with a space in it is a real value."""
        parsed, errors = parse_one(payment_row(merchant_id="acme retail"))
        assert errors == []
        assert parsed is not None
        assert parsed.canonical_payload["merchant_id"] == "acme retail"

    def test_a_padded_header_is_refused(self) -> None:
        """A header cell of " event_id" is not the column "event_id"."""
        header = PAYMENT_HEADER.replace("event_id", " event_id", 1)
        content = (header + "\n" + payment_row() + "\n").encode("utf-8")

        with pytest.raises(DocumentError) as caught:
            list(iter_document_rows(content, SourceRecordType.PAYMENT_EVENT))
        assert caught.value.code is DocumentErrorCode.UNEXPECTED_COLUMNS

    def test_a_whitespace_only_optional_column_is_refused(self) -> None:
        """It is not the same as an empty cell and must not become one.

        Otherwise a blank column and a space filled column would mean the same
        thing, and one of them would be a defect nobody ever saw.
        """
        row = "po-1,payout-1,merch-1,1000,INR,   ,2026-08-20T09:15:00+05:30"
        _, errors = parse_one(row, SourceRecordType.PAYOUT)

        assert [error.code for error in errors] == [RowErrorCode.SURROUNDING_WHITESPACE]

    def test_an_exactly_empty_optional_column_is_still_accepted(self) -> None:
        """The documented behaviour for an absent bank reference is unchanged."""
        row = "po-1,payout-1,merch-1,1000,INR,,2026-08-20T09:15:00+05:30"
        parsed, errors = parse_one(row, SourceRecordType.PAYOUT)

        assert errors == []
        assert parsed is not None
        assert parsed.canonical_payload["utr"] is None

    def test_a_whitespace_only_required_column_is_refused_as_whitespace(self) -> None:
        """Reported as padding rather than as a missing value, which is what it is."""
        _, errors = parse_one(payment_row(payment_id="   "))
        assert [error.code for error in errors] == [RowErrorCode.SURROUNDING_WHITESPACE]

    def test_an_exactly_empty_required_column_is_still_missing(self) -> None:
        """The existing code for a genuinely blank required cell is unchanged."""
        _, errors = parse_one(payment_row(payment_id=""))
        assert [error.code for error in errors] == [RowErrorCode.MISSING_VALUE]


class TestValidFixturesStillImportUnchanged:
    """The documented examples must be unaffected by the stricter rules."""

    @pytest.mark.parametrize(
        ("fixture", "record_type", "rows"),
        [
            ("payment_events.csv", SourceRecordType.PAYMENT_EVENT, 5),
            ("settlement_lines.csv", SourceRecordType.SETTLEMENT_LINE, 3),
            ("payouts.csv", SourceRecordType.PAYOUT, 2),
        ],
    )
    def test_every_valid_fixture_still_parses(
        self, fixture: str, record_type: SourceRecordType, rows: int
    ) -> None:
        """No fixture relied on trimming."""
        parsed_rows = list(iter_document_rows(read_fixture(fixture), record_type))
        assert len(parsed_rows) == rows

        for row_number, cells in parsed_rows:
            parsed, errors = parse_row(
                cells, record_type, row_number, "a" * 64, SourceSystem.PSP_API
            )
            assert errors == []
            assert parsed is not None
