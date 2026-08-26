"""Tests for the CSV import endpoints.

The endpoint is a shell around the Phase 2 import service, so most of these
tests are about the shell: what reaches the service, what never does, and what
comes back. The parsing rules themselves are tested against the parser, and
re-testing them here would be testing the same code twice under two names.

Two things are checked repeatedly because they are the point of the endpoint.
Every upload the service processes gets 201 and a receipt, including a rejected
one, because the receipt is the created resource. And every upload the service
never sees leaves nothing behind at all.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine

from app.api.schemas import ImportReceiptView
from app.api.uploads import UNNAMED_DOCUMENT
from app.config import Settings
from app.domain.facts import SourceRecordType
from app.ingestion.receipts import ImportOutcome, RowOutcome
from app.ingestion.schemas import PARSER_VERSION, SUPPORTED_RECORD_TYPES
from app.main import create_app
from app.storage.database import create_database_engine, database_url_for, session_factory
from app.storage.repository import ImportReceiptRepository, SourceFactRepository
from tests.ingestion.conftest import FIXTURE_DIR, read_fixture

DOCUMENTS: dict[SourceRecordType, str] = {
    SourceRecordType.PAYMENT_EVENT: "payment_events.csv",
    SourceRecordType.SETTLEMENT_LINE: "settlement_lines.csv",
    SourceRecordType.PAYOUT: "payouts.csv",
}

IMPORTS = "/v1/imports"


def upload(
    client: TestClient,
    content: bytes,
    *,
    record_type: str,
    source_system: str = "PSP_API",
    file_name: str | None = "document.csv",
) -> Response:
    """Post one document the way a client would."""
    return client.post(
        IMPORTS,
        files={"file": (file_name, content, "text/csv")},
        data={"source_system": source_system, "record_type": record_type},
    )


def upload_fixture(client: TestClient, record_type: SourceRecordType) -> Response:
    """Post one of the example documents as its own record type."""
    name = DOCUMENTS[record_type]
    return upload(client, read_fixture(name), record_type=record_type.value, file_name=name)


def fact_count(engine: Engine) -> int:
    """Return how many facts are stored."""
    with session_factory(engine)() as session:
        return SourceFactRepository(session).count()


@pytest.fixture
def import_client(api_engine: Engine) -> Iterator[TestClient]:
    """Return a client on an empty migrated database, plus its engine."""
    with TestClient(create_app(Settings(app_env="test"), engine=api_engine)) as opened:
        yield opened


class TestAcceptingADocument:
    """The path everything else exists to support."""

    def test_an_accepted_upload_returns_201(self, import_client: TestClient) -> None:
        """A receipt resource was created, and so were the facts."""
        response = upload_fixture(import_client, SourceRecordType.PAYMENT_EVENT)

        assert response.status_code == 201
        assert response.json()["outcome"] == ImportOutcome.ACCEPTED.value

    def test_the_facts_are_stored(self, import_client: TestClient, api_engine: Engine) -> None:
        """The endpoint imports, rather than reporting that it would have."""
        upload_fixture(import_client, SourceRecordType.PAYMENT_EVENT)

        assert fact_count(api_engine) == 5

    def test_the_receipt_reports_what_was_written(self, import_client: TestClient) -> None:
        """Counts, and the derived flag that must agree with them."""
        body = upload_fixture(import_client, SourceRecordType.PAYMENT_EVENT).json()

        assert body["row_count"] == 5
        assert body["accepted_count"] == 5
        assert body["wrote_facts"] is True
        assert body["failure_detail"] is None

    def test_the_declared_fields_are_recorded_not_inferred(self, import_client: TestClient) -> None:
        """A document is read as what the caller declared, and says so."""
        body = upload_fixture(import_client, SourceRecordType.PAYMENT_EVENT).json()

        assert body["source_system"] == "PSP_API"
        assert body["source_record_type"] == "PAYMENT_EVENT"
        assert body["parser_version"] == PARSER_VERSION

    @pytest.mark.parametrize("record_type", sorted(SUPPORTED_RECORD_TYPES))
    def test_every_parser_supported_record_type_imports(
        self, import_client: TestClient, record_type: SourceRecordType
    ) -> None:
        """All three, over HTTP, not only the one the other tests use."""
        response = upload_fixture(import_client, record_type)

        assert response.status_code == 201
        assert response.json()["outcome"] == ImportOutcome.ACCEPTED.value
        assert response.json()["source_record_type"] == record_type.value

    def test_the_imported_facts_can_then_be_reconciled(
        self, import_client: TestClient, api_engine: Engine
    ) -> None:
        """Upload, then reconcile, which is the whole point of uploading."""
        for record_type in SUPPORTED_RECORD_TYPES:
            upload_fixture(import_client, record_type)

        run = import_client.post("/v1/reconciliation/runs")

        assert run.status_code == 201
        assert run.json()["fact_count"] == fact_count(api_engine) == 10
        assert run.json()["decision_count"] > 0


class TestOutcomesThatStillCreateAReceipt:
    """A processed document always leaves a receipt, so it is always 201.

    Returning 422 for a parser rejection would say no resource was created when
    one was, and returning 200 for a duplicate would say the same. See ADR-010.
    """

    def test_a_replayed_document_is_a_duplicate_no_op(
        self, import_client: TestClient, api_engine: Engine
    ) -> None:
        """Re-importing the same file must be safe and must be recorded."""
        first = upload_fixture(import_client, SourceRecordType.PAYMENT_EVENT)
        before = fact_count(api_engine)

        second = upload_fixture(import_client, SourceRecordType.PAYMENT_EVENT)

        assert second.status_code == 201
        assert second.json()["outcome"] == ImportOutcome.DUPLICATE_NO_OP.value
        assert second.json()["wrote_facts"] is False
        assert second.json()["receipt_id"] != first.json()["receipt_id"]
        assert fact_count(api_engine) == before

    def test_a_conflicting_document_is_rejected_and_writes_nothing(
        self, import_client: TestClient, api_engine: Engine
    ) -> None:
        """One provider event with two different payloads is a contradiction.

        The fixture declares `pe-0001` with a different amount from the one the
        example document already stored.
        """
        upload_fixture(import_client, SourceRecordType.PAYMENT_EVENT)
        before = fact_count(api_engine)

        response = upload(
            import_client,
            read_fixture("conflicting_payment_events.csv"),
            record_type="PAYMENT_EVENT",
        )

        assert response.status_code == 201
        assert response.json()["outcome"] == ImportOutcome.REJECTED_CONFLICT.value
        assert response.json()["wrote_facts"] is False
        assert response.json()["conflict_count"] >= 1
        assert fact_count(api_engine) == before

    def test_an_unreadable_document_is_rejected_and_writes_nothing(
        self, import_client: TestClient, api_engine: Engine
    ) -> None:
        """Headers that are not the declared schema."""
        response = upload(import_client, b"nope,not,headers\n1,2,3\n", record_type="PAYMENT_EVENT")

        assert response.status_code == 201
        assert response.json()["outcome"] == ImportOutcome.REJECTED_INVALID.value
        assert response.json()["wrote_facts"] is False
        assert fact_count(api_engine) == 0

    def test_a_document_with_one_bad_row_rejects_the_whole_import(
        self, import_client: TestClient, api_engine: Engine
    ) -> None:
        """All or nothing, which is a Phase 2 rule the API must not soften.

        The fixture holds one readable row and two unreadable ones. The readable
        one is reported as not applied rather than accepted, because nothing was
        written and a receipt may not claim a fact that does not exist.
        """
        response = upload(
            import_client, read_fixture("invalid_mixed_rows.csv"), record_type="PAYMENT_EVENT"
        )

        body = response.json()
        assert response.status_code == 201
        assert body["outcome"] == ImportOutcome.REJECTED_INVALID.value
        assert body["row_count"] == 3
        assert body["rejected_count"] == 2
        assert body["not_applied_count"] == 1
        assert body["accepted_count"] == 0
        assert fact_count(api_engine) == 0

    def test_blank_content_is_rejected_with_a_receipt(self, import_client: TestClient) -> None:
        """An empty file has no header row, which the parser already says."""
        response = upload(import_client, b"", record_type="PAYOUT")

        assert response.status_code == 201
        assert response.json()["outcome"] == ImportOutcome.REJECTED_INVALID.value
        assert response.json()["row_count"] == 0

    def test_invalid_encoding_is_rejected_with_a_receipt(self, import_client: TestClient) -> None:
        """Bytes that are not text at all still get an audit record."""
        response = upload(import_client, b"\xff\xfe\x00\x01not utf8", record_type="PAYOUT")

        assert response.status_code == 201
        assert response.json()["outcome"] == ImportOutcome.REJECTED_INVALID.value

    def test_every_rejection_is_still_fetchable_afterwards(self, import_client: TestClient) -> None:
        """The receipt is a resource, so it has to be there when asked for."""
        receipt_id = upload(import_client, b"rubbish", record_type="PAYOUT").json()["receipt_id"]

        found = import_client.get(f"{IMPORTS}/{receipt_id}")

        assert found.status_code == 200
        assert found.json()["outcome"] == ImportOutcome.REJECTED_INVALID.value


class TestRequestsThatNeverReachTheService:
    """A malformed request is not an import attempt, so it leaves no receipt."""

    @staticmethod
    def _receipt_count(client: TestClient) -> int:
        return int(client.get(IMPORTS).json()["total"])

    def test_an_unsupported_record_type_is_refused(self, import_client: TestClient) -> None:
        """BANK_TRANSACTION is in the contract and has no CSV schema.

        Refused deliberately at the boundary rather than allowed through to
        become a rejected receipt, so a caller learns the type is not importable
        rather than that their file was bad.
        """
        response = upload(import_client, b"anything", record_type="BANK_TRANSACTION")

        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "unsupported_record_type"
        assert self._receipt_count(import_client) == 0

    def test_the_refusal_names_what_is_supported(self, import_client: TestClient) -> None:
        """So the caller can act on it without reading the source."""
        detail = upload(import_client, b"x", record_type="BANK_TRANSACTION").json()["detail"]

        assert "PAYMENT_EVENT" in detail["detail"]
        assert "SETTLEMENT_LINE" in detail["detail"]
        assert "PAYOUT" in detail["detail"]

    def test_an_unreadable_record_type_is_refused(self, import_client: TestClient) -> None:
        """Not an enum member at all."""
        response = upload(import_client, b"x", record_type="NOT_A_RECORD_TYPE")

        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "invalid_request"
        assert self._receipt_count(import_client) == 0

    def test_an_unreadable_source_system_is_refused(self, import_client: TestClient) -> None:
        """Same rule for the other declared field."""
        response = upload(import_client, b"x", record_type="PAYOUT", source_system="NOPE")

        assert response.status_code == 422
        assert self._receipt_count(import_client) == 0

    def test_a_missing_file_is_refused(self, import_client: TestClient) -> None:
        """Nothing to import, so nothing to record."""
        response = import_client.post(
            IMPORTS, data={"source_system": "PSP_API", "record_type": "PAYOUT"}
        )

        assert response.status_code == 422
        assert self._receipt_count(import_client) == 0

    def test_a_missing_declared_field_is_refused(self, import_client: TestClient) -> None:
        """Neither field is optional, because neither is guessed."""
        response = import_client.post(
            IMPORTS,
            files={"file": ("x.csv", b"x", "text/csv")},
            data={"source_system": "PSP_API"},
        )

        assert response.status_code == 422
        assert self._receipt_count(import_client) == 0

    def test_a_non_multipart_body_is_refused(self, import_client: TestClient) -> None:
        """The endpoint takes an upload, not JSON."""
        response = import_client.post(
            IMPORTS, json={"source_system": "PSP_API", "record_type": "PAYOUT"}
        )

        assert response.status_code in (415, 422)
        assert self._receipt_count(import_client) == 0

    def test_a_document_over_the_limit_is_refused(self, api_engine: Engine) -> None:
        """413 before parsing, so the service never sees it."""
        settings = Settings(app_env="test", max_upload_bytes=1024)
        with TestClient(create_app(settings, engine=api_engine)) as client:
            response = upload(client, b"x" * 2048, record_type="PAYOUT")

            assert response.status_code == 413
            assert self._receipt_count(client) == 0

    def test_a_document_at_the_limit_is_accepted(self, api_engine: Engine) -> None:
        """The limit is a limit, not an off-by-one."""
        content = read_fixture("payouts.csv")
        settings = Settings(app_env="test", max_upload_bytes=len(content))
        with TestClient(create_app(settings, engine=api_engine)) as client:
            response = upload(client, content, record_type="PAYOUT")

            assert response.status_code == 201
            assert response.json()["outcome"] == ImportOutcome.ACCEPTED.value

    def test_one_byte_over_the_limit_is_refused(self, api_engine: Engine) -> None:
        """The other side of the same boundary."""
        content = read_fixture("payouts.csv")
        settings = Settings(app_env="test", max_upload_bytes=len(content) - 1)
        with TestClient(create_app(settings, engine=api_engine)) as client:
            assert upload(client, content, record_type="PAYOUT").status_code == 413

    def test_an_oversized_declared_body_is_refused_before_it_is_read(
        self, api_engine: Engine
    ) -> None:
        """The early guard, which reads only the declared length.

        Sent with a `Content-Length` far past the limit so the request is turned
        away before the server spools it, rather than after.
        """
        settings = Settings(app_env="test", max_upload_bytes=1024)
        with TestClient(create_app(settings, engine=api_engine)) as client:
            response = client.post(
                IMPORTS,
                content=b"x" * (1024 * 1024),
                headers={"content-type": "multipart/form-data; boundary=b"},
            )

            assert response.status_code == 413
            assert response.json()["detail"]["error"] == "request_too_large"
            assert self._receipt_count(client) == 0


class TestNamingTheDocument:
    """A file name is client supplied text that gets stored and shown."""

    def test_the_file_name_is_used_as_the_document_name(self, import_client: TestClient) -> None:
        """Its only use. It is a label, never an identifier."""
        response = upload(import_client, b"x", record_type="PAYOUT", file_name="june.csv")

        assert response.json()["document_name"] == "june.csv"

    @pytest.mark.parametrize(
        ("sent", "stored"),
        [
            ("../../etc/passwd", "passwd"),
            (r"C:\Users\me\payouts.csv", "payouts.csv"),
            ("reports/june.csv", "june.csv"),
        ],
    )
    def test_directory_components_are_dropped(
        self, import_client: TestClient, sent: str, stored: str
    ) -> None:
        """A path says something about the sender, nothing about the document."""
        response = upload(import_client, b"x", record_type="PAYOUT", file_name=sent)

        assert response.json()["document_name"] == stored

    def test_a_terminal_escape_is_stripped(self, import_client: TestClient) -> None:
        """A receipt gets printed, and a name should not be able to rewrite a screen."""
        response = upload(
            import_client, b"x", record_type="PAYOUT", file_name="pay\x1b[31mouts.csv"
        )

        assert "\x1b" not in response.json()["document_name"]

    def test_a_long_name_is_shortened(self, import_client: TestClient) -> None:
        """Shortened here rather than refused by the column after the import."""
        response = upload(import_client, b"x", record_type="PAYOUT", file_name="a" * 400 + ".csv")

        assert len(response.json()["document_name"]) == 200

    @pytest.mark.parametrize("sent", ["../", "   ", ".", ".."])
    def test_a_name_with_nothing_usable_falls_back(
        self, import_client: TestClient, sent: str
    ) -> None:
        """Deterministic, so two such uploads are labelled the same way."""
        response = upload(import_client, b"x", record_type="PAYOUT", file_name=sent)

        assert response.json()["document_name"] == UNNAMED_DOCUMENT

    def test_the_document_name_does_not_change_the_document_hash(
        self, import_client: TestClient
    ) -> None:
        """The hash describes the bytes. The name is not part of them."""
        first = upload(import_client, b"x", record_type="PAYOUT", file_name="one.csv")
        second = upload(import_client, b"x", record_type="PAYOUT", file_name="two.csv")

        assert first.json()["document_hash"] == second.json()["document_hash"]
        assert first.json()["document_name"] != second.json()["document_name"]


class TestListingReceipts:
    """The import history, which is the audit trail made readable."""

    @pytest.fixture
    def history(self, import_client: TestClient) -> TestClient:
        """Return a client whose database holds five attempts of four kinds."""
        for record_type in SUPPORTED_RECORD_TYPES:
            upload_fixture(import_client, record_type)
        upload_fixture(import_client, SourceRecordType.PAYOUT)
        upload(import_client, b"rubbish", record_type="PAYMENT_EVENT")
        return import_client

    def test_every_attempt_is_listed(self, history: TestClient) -> None:
        """Including the ones that wrote nothing."""
        body = history.get(IMPORTS).json()

        assert body["total"] == 5
        assert len(body["receipts"]) == 5

    def test_the_newest_attempt_comes_first(self, history: TestClient) -> None:
        """Newest first by the order the attempts were made in."""
        outcomes = [receipt["outcome"] for receipt in history.get(IMPORTS).json()["receipts"]]

        assert outcomes[0] == ImportOutcome.REJECTED_INVALID.value
        assert outcomes[1] == ImportOutcome.DUPLICATE_NO_OP.value

    def test_the_order_is_the_same_on_every_call(self, history: TestClient) -> None:
        """A page boundary that moved between calls would skip a receipt."""
        first = history.get(IMPORTS).json()["receipts"]
        second = history.get(IMPORTS).json()["receipts"]

        assert [receipt["receipt_id"] for receipt in first] == [
            receipt["receipt_id"] for receipt in second
        ]

    def test_receipts_sharing_a_timestamp_still_have_one_order(self, history: TestClient) -> None:
        """Ordering is by the database sequence, which no two receipts share.

        Two attempts can be made inside the same clock tick, so ordering on
        received-at would need a tie-breaker to be stable at all.
        """
        receipts = history.get(IMPORTS).json()["receipts"]
        identifiers = [receipt["receipt_id"] for receipt in receipts]

        assert len(set(identifiers)) == len(identifiers)

    def test_a_page_is_limited(self, history: TestClient) -> None:
        """And says what the whole list would have been."""
        body = history.get(IMPORTS, params={"limit": 2}).json()

        assert len(body["receipts"]) == 2
        assert body["total"] == 5
        assert body["limit"] == 2

    def test_an_offset_continues_the_list(self, history: TestClient) -> None:
        """Paging through gives every receipt exactly once."""
        first = history.get(IMPORTS, params={"limit": 3, "offset": 0}).json()["receipts"]
        second = history.get(IMPORTS, params={"limit": 3, "offset": 3}).json()["receipts"]

        identifiers = [receipt["receipt_id"] for receipt in first + second]
        assert len(identifiers) == 5
        assert len(set(identifiers)) == 5

    def test_an_offset_past_the_end_is_empty(self, history: TestClient) -> None:
        """Not an error, and the total still describes the whole list."""
        body = history.get(IMPORTS, params={"offset": 500}).json()

        assert body["receipts"] == []
        assert body["total"] == 5

    @pytest.mark.parametrize(
        ("parameter", "value"), [("limit", 0), ("limit", 1000), ("offset", -1)]
    )
    def test_page_bounds_are_enforced(
        self, history: TestClient, parameter: str, value: int
    ) -> None:
        """The same bounds the run list uses."""
        assert history.get(IMPORTS, params={parameter: value}).status_code == 422

    def test_an_unfiltered_list_says_so(self, history: TestClient) -> None:
        """So `total` can be read as the size of the whole history."""
        assert history.get(IMPORTS).json()["filtered"] is False

    def test_filtering_by_outcome(self, history: TestClient) -> None:
        """And the total describes the filtered list, not the table."""
        body = history.get(IMPORTS, params={"outcome": "ACCEPTED"}).json()

        assert body["total"] == 3
        assert {receipt["outcome"] for receipt in body["receipts"]} == {"ACCEPTED"}
        assert body["filtered"] is True

    def test_filtering_by_record_type(self, history: TestClient) -> None:
        """Two payout attempts were made, one of them a replay."""
        body = history.get(IMPORTS, params={"record_type": "PAYOUT"}).json()

        assert body["total"] == 2
        assert {receipt["source_record_type"] for receipt in body["receipts"]} == {"PAYOUT"}

    def test_filtering_by_source_system(self, history: TestClient) -> None:
        """Every attempt here declared the same system."""
        assert history.get(IMPORTS, params={"source_system": "PSP_API"}).json()["total"] == 5
        assert history.get(IMPORTS, params={"source_system": "BANK_STATEMENT"}).json()["total"] == 0

    def test_filters_combine(self, history: TestClient) -> None:
        """Narrower still, and the total narrows with it."""
        body = history.get(IMPORTS, params={"outcome": "ACCEPTED", "record_type": "PAYOUT"}).json()

        assert body["total"] == 1

    def test_an_unreadable_filter_is_refused(self, history: TestClient) -> None:
        """Filters are typed, so a typo is an error and not an empty list."""
        assert history.get(IMPORTS, params={"outcome": "NOPE"}).status_code == 422

    def test_the_total_is_not_the_page_size(self, history: TestClient) -> None:
        """A filtered page one row long still reports the filtered total."""
        body = history.get(IMPORTS, params={"outcome": "ACCEPTED", "limit": 1}).json()

        assert len(body["receipts"]) == 1
        assert body["total"] == 3


class TestReadingOneReceipt:
    """The detail view, and what it refuses to include."""

    def test_a_receipt_can_be_fetched_by_its_id(self, import_client: TestClient) -> None:
        """`receipt_id` is the public identity."""
        created = upload_fixture(import_client, SourceRecordType.PAYMENT_EVENT).json()

        found = import_client.get(f"{IMPORTS}/{created['receipt_id']}")

        assert found.status_code == 200
        assert found.json() == created

    def test_an_unknown_receipt_is_a_404(self, import_client: TestClient) -> None:
        """With the shape every other failure on this API uses."""
        response = import_client.get(f"{IMPORTS}/no-such-receipt")

        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "not_found"

    def test_the_404_does_not_echo_anything_but_the_id(self, import_client: TestClient) -> None:
        """No paths, no SQL, no table names."""
        detail = import_client.get(f"{IMPORTS}/missing").json()["detail"]["detail"]

        assert "missing" in detail
        assert "SELECT" not in detail.upper()
        assert "import_receipts" not in detail

    def test_the_response_is_the_same_bytes_every_time(self, import_client: TestClient) -> None:
        """A receipt is a record, so reading it twice reads the same thing."""
        receipt_id = upload_fixture(import_client, SourceRecordType.PAYOUT).json()["receipt_id"]

        first = import_client.get(f"{IMPORTS}/{receipt_id}")
        second = import_client.get(f"{IMPORTS}/{receipt_id}")

        assert first.content == second.content

    def test_row_outcomes_are_reported(self, import_client: TestClient) -> None:
        """One entry per row, with the identity where the row has one."""
        body = upload_fixture(import_client, SourceRecordType.PAYMENT_EVENT).json()

        assert len(body["row_outcomes"]) == 5
        assert {row["outcome"] for row in body["row_outcomes"]} == {RowOutcome.ACCEPTED.value}
        assert all(row["source_record_id"] for row in body["row_outcomes"])

    def test_a_rejected_row_reports_its_code_and_reason(self, import_client: TestClient) -> None:
        """What a person needs in order to fix the file.

        The column and the rule it broke, which is enough to act on without
        the response repeating the cell that broke it.
        """
        body = upload(
            import_client, read_fixture("invalid_mixed_rows.csv"), record_type="PAYMENT_EVENT"
        ).json()

        rejected = [row for row in body["row_outcomes"] if row["outcome"] == "REJECTED"]
        assert len(rejected) == 2
        assert all(row["code"] and row["detail"] for row in rejected)
        assert any("event_type" in row["detail"] for row in rejected)
        assert any("amount_minor" in row["detail"] for row in rejected)


class TestTheResponseHoldsNoDocumentContent:
    """The receipt explains an outcome. It is not a copy of the upload."""

    @pytest.fixture
    def bodies(self, import_client: TestClient) -> list[str]:
        """Return every import response body, as text, for one accepted and one rejected."""
        accepted = upload_fixture(import_client, SourceRecordType.PAYMENT_EVENT)
        rejected = upload(
            import_client, read_fixture("invalid_mixed_rows.csv"), record_type="PAYMENT_EVENT"
        )
        listed = import_client.get(IMPORTS)
        return [accepted.text, rejected.text, listed.text]

    def test_no_raw_csv_line_is_returned(self, bodies: list[str]) -> None:
        """A whole row of the document appearing in a response would be a copy of it."""
        first_data_line = read_fixture("payment_events.csv").decode().splitlines()[1]

        assert all(first_data_line not in body for body in bodies)

    def test_no_header_row_is_returned(self, bodies: list[str]) -> None:
        """The header names the schema, and the schema is documented elsewhere."""
        header = read_fixture("payment_events.csv").decode().splitlines()[0]

        assert all(header not in body for body in bodies)

    def test_no_canonical_payload_is_returned(self, bodies: list[str]) -> None:
        """Payload retrieval is absent from the HTTP surface entirely."""
        assert all("canonical_payload" not in body for body in bodies)
        assert all("payload_hash" not in body for body in bodies)

    def test_no_amount_from_the_document_is_returned(self, bodies: list[str]) -> None:
        """Merchant money does not belong on an endpoint that explains an outcome."""
        assert all("1000000" not in body for body in bodies)
        assert all("250000" not in body for body in bodies)


class TestTheApiChangesNothingItShouldNot:
    """Reading must never write, and no endpoint may edit a stored record."""

    def test_reading_receipts_writes_nothing(
        self, import_client: TestClient, api_engine: Engine
    ) -> None:
        """A list and a detail read leave the history exactly as it was."""
        upload_fixture(import_client, SourceRecordType.PAYOUT)
        receipt_id = import_client.get(IMPORTS).json()["receipts"][0]["receipt_id"]

        before = import_client.get(IMPORTS).json()
        import_client.get(f"{IMPORTS}/{receipt_id}")
        after = import_client.get(IMPORTS).json()

        assert after == before
        assert fact_count(api_engine) == 2

    @pytest.mark.parametrize("method", ["put", "patch", "delete"])
    def test_there_is_no_way_to_change_a_receipt(
        self, import_client: TestClient, method: str
    ) -> None:
        """The audit trail is append-only, and the API offers no exception."""
        receipt_id = upload_fixture(import_client, SourceRecordType.PAYOUT).json()["receipt_id"]

        response = getattr(import_client, method)(f"{IMPORTS}/{receipt_id}")

        assert response.status_code == 405

    def test_the_openapi_surface_is_read_and_create_only(self, import_client: TestClient) -> None:
        """Asserted across the schema, so a new route cannot slip in unnoticed."""
        paths = import_client.get("/openapi.json").json()["paths"]
        methods = {
            method
            for path, operations in paths.items()
            if path.startswith(IMPORTS)
            for method in operations
        }

        assert methods == {"get", "post"}


class TestTheApiAgreesWithTheCommandLine:
    """Two front doors, one importer.

    The endpoint and `python -m app.ingest_cli` must not be two ways of getting
    two answers. They call the same service, and these tests hold them to it,
    because a divergence would mean the API had grown ingestion rules of its
    own.
    """

    @staticmethod
    def _import_with_the_cli(database: Path, fixture: str, record_type: SourceRecordType) -> int:
        """Import one document the way an operator would from a terminal."""
        from app.ingest_cli import run as ingest_run

        return ingest_run(
            [
                "--database",
                str(database),
                "--source-system",
                "PSP_API",
                "--record-type",
                record_type.value,
                str(FIXTURE_DIR / fixture),
            ]
        )

    @pytest.fixture
    def cli_engine(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Engine:
        """Return an engine on a database loaded entirely through the CLI."""
        database = tmp_path / "cli.sqlite"
        for record_type, fixture in sorted(DOCUMENTS.items()):
            assert self._import_with_the_cli(database, fixture, record_type) == 0
        capsys.readouterr()
        return create_database_engine(database_url_for(database))

    def test_the_same_documents_produce_the_same_facts(
        self, import_client: TestClient, api_engine: Engine, cli_engine: Engine
    ) -> None:
        """Every stored fact, field for field, including its payload hash.

        Source record IDs are derived from the document, so they are comparable
        across the two runs. Nothing here is generated per import.
        """
        for record_type, _ in sorted(DOCUMENTS.items()):
            upload_fixture(import_client, record_type)

        with session_factory(api_engine)() as session:
            over_http = SourceFactRepository(session).all_facts()
        with session_factory(cli_engine)() as session:
            over_cli = SourceFactRepository(session).all_facts()
        cli_engine.dispose()

        assert len(over_http) == 10
        assert [fact.model_dump(exclude={"observed_at"}) for fact in over_http] == [
            fact.model_dump(exclude={"observed_at"}) for fact in over_cli
        ]

    def test_the_same_document_produces_the_same_receipt_semantics(
        self, import_client: TestClient, cli_engine: Engine
    ) -> None:
        """Everything but the generated identity and the clock.

        `receipt_id` is a fresh uuid per attempt and `received_at` is the moment
        of the attempt, so those two differ by design. If anything else differs,
        the two front doors disagree about what happened to one file.
        """
        over_http = upload_fixture(import_client, SourceRecordType.PAYMENT_EVENT).json()

        with session_factory(cli_engine)() as session:
            receipt = ImportReceiptRepository(session).page(
                limit=1, offset=0, record_type=SourceRecordType.PAYMENT_EVENT
            )[0]
        cli_engine.dispose()
        over_cli = ImportReceiptView.of(receipt).model_dump(mode="json")

        generated = {"receipt_id", "received_at"}
        assert {key: value for key, value in over_http.items() if key not in generated} == {
            key: value for key, value in over_cli.items() if key not in generated
        }
        assert over_http["receipt_id"] != over_cli["receipt_id"]

    def test_a_rejection_is_rejected_the_same_way(
        self, import_client: TestClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A refused document refuses identically, and the CLI still exits 1."""
        database = tmp_path / "rejected.sqlite"
        status = self._import_with_the_cli(
            database, "invalid_mixed_rows.csv", SourceRecordType.PAYMENT_EVENT
        )
        capsys.readouterr()
        engine = create_database_engine(database_url_for(database))
        with session_factory(engine)() as session:
            over_cli = ImportReceiptRepository(session).page(limit=1, offset=0)[0]
        engine.dispose()

        over_http = upload(
            import_client, read_fixture("invalid_mixed_rows.csv"), record_type="PAYMENT_EVENT"
        ).json()

        assert status == 1
        assert over_http["outcome"] == over_cli.outcome.value
        assert over_http["rejected_count"] == over_cli.rejected_count
        assert over_http["not_applied_count"] == 1
        assert over_http["failure_detail"] == over_cli.failure_detail

    def test_a_document_imported_by_one_is_a_duplicate_to_the_other(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The strongest check that they write the same thing.

        Duplicate detection compares the derived identity and the payload hash.
        A document loaded by the CLI and then posted to the API is only reported
        as an exact duplicate if the CLI wrote exactly what the API would have.
        """
        database = tmp_path / "shared.sqlite"
        assert (
            self._import_with_the_cli(
                database, "payment_events.csv", SourceRecordType.PAYMENT_EVENT
            )
            == 0
        )
        capsys.readouterr()

        engine = create_database_engine(database_url_for(database))
        with TestClient(create_app(Settings(app_env="test"), engine=engine)) as client:
            response = upload_fixture(client, SourceRecordType.PAYMENT_EVENT)
        engine.dispose()

        assert response.status_code == 201
        assert response.json()["outcome"] == ImportOutcome.DUPLICATE_NO_OP.value
        assert response.json()["wrote_facts"] is False
