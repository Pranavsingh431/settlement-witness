"""The bank finality API.

One create path and three read paths, matching the reconciliation API. The tests
that matter most are the ones asserting what a response never says: no finality
outcome is a settlement status, and a failing audit is never presented as a
settled line.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.schemas import SETTLEMENT_AND_FINALITY_ARE_SEPARATE
from app.banking.finality import BANK_FINALITY_VERSION, BankFinalityOutcome
from app.config import Settings
from app.domain.decisions import DecisionStatus
from app.domain.facts import SourceRecordType, SourceSystem
from app.ingestion.service import ImportService
from app.main import create_app
from app.storage.database import (
    create_database_engine,
    create_schema,
    database_url_for,
    session_scope,
)
from tests.ingestion.conftest import FIXED_NOW, read_fixture

AUDITS = "/v1/bank-finality/audits"

SETTLEMENT_DOCUMENTS: tuple[tuple[str, SourceRecordType], ...] = (
    ("payment_events.csv", SourceRecordType.PAYMENT_EVENT),
    ("settlement_lines.csv", SourceRecordType.SETTLEMENT_LINE),
    ("payouts.csv", SourceRecordType.PAYOUT),
)


def load(engine: Engine, documents: tuple[tuple[str, SourceRecordType], ...]) -> None:
    """Import the named example documents into a database."""
    with session_scope(engine) as session:
        service = ImportService(session, now=FIXED_NOW)
        for file_name, record_type in documents:
            service.import_document(
                read_fixture(file_name),
                source_system=SourceSystem.PSP_API,
                record_type=record_type,
                document_name=file_name,
            )


def client_over(engine: Engine) -> TestClient:
    """Return a client bound to one database."""
    return TestClient(create_app(Settings(app_env="test"), engine=engine))


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    """Return a database holding the settlement documents and no statement."""
    built = create_database_engine(database_url_for(tmp_path / "finality.sqlite"))
    create_schema(built)
    load(built, SETTLEMENT_DOCUMENTS)
    try:
        yield built
    finally:
        built.dispose()


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    """Return a client over the settlement documents alone."""
    with client_over(engine) as opened:
        yield opened


@pytest.fixture
def settled_client(engine: Engine) -> Iterator[TestClient]:
    """Return a client whose store also holds the matching bank credit."""
    load(engine, (("bank_transactions.csv", SourceRecordType.BANK_TRANSACTION),))
    with client_over(engine) as opened:
        yield opened


def audit_of(client: TestClient) -> dict[str, Any]:
    """Record an audit and return its summary."""
    response = client.post(AUDITS)
    assert response.status_code in {200, 201}, response.text
    return dict(response.json())


def detail_of(client: TestClient, **params: str) -> dict[str, Any]:
    """Record an audit and return it with its certificates."""
    audit_id = audit_of(client)["audit_id"]
    response = client.get(f"{AUDITS}/{audit_id}", params=params)
    assert response.status_code == 200, response.text
    return dict(response.json())


def certificate_for(client: TestClient, payout_id: str) -> dict[str, Any]:
    """Return one payout's certificate from a freshly recorded audit."""
    return next(one for one in detail_of(client)["certificates"] if one["payout_id"] == payout_id)


class TestRecordingAnAudit:
    """Create, and create again."""

    def test_a_new_audit_is_201(self, client: TestClient) -> None:
        """A new immutable conclusion about one snapshot."""
        response = client.post(AUDITS)

        assert response.status_code == 201
        assert response.json()["payout_count"] == 2

    def test_an_identical_audit_is_200(self, client: TestClient) -> None:
        """The same facts under the same rules are one conclusion."""
        first = client.post(AUDITS)
        second = client.post(AUDITS)

        assert first.status_code == 201
        assert second.status_code == 200
        assert second.json()["audit_id"] == first.json()["audit_id"]

    def test_an_empty_store_is_refused(self, tmp_path: Path) -> None:
        """An empty audit would look like a clean result."""
        empty = create_database_engine(database_url_for(tmp_path / "empty.sqlite"))
        create_schema(empty)
        with client_over(empty) as opened:
            response = opened.post(AUDITS)
        empty.dispose()

        assert response.status_code == 409
        assert response.json()["detail"]["error"] == "no_facts"

    def test_the_audit_key_is_not_published(self, client: TestClient) -> None:
        """An idempotency identity, like the run key. Publishing it would invite
        callers to depend on how it is computed."""
        assert "audit_key" not in client.post(AUDITS).json()

    def test_the_summary_carries_both_rule_versions(self, client: TestClient) -> None:
        """Which rules produced it, and which columns the statement was read
        under."""
        summary = audit_of(client)

        assert summary["bank_finality_version"] == BANK_FINALITY_VERSION
        assert summary["bank_statement_schema_version"] == "1.0.0"

    def test_verified_payouts_are_counted_not_rated(self, client: TestClient) -> None:
        """A count, never a percentage.

        A rate here would invite somebody to read ninety percent as nearly
        settled, and the ten percent is where a merchant is missing money.
        """
        summary = audit_of(client)

        assert summary["verified_payout_count"] == 0
        assert not [key for key in summary if "rate" in key or "percent" in key]


class TestWhatTheAuditFinds:
    """The example documents, before and after the statement is imported."""

    def test_without_a_statement_nothing_is_verified(self, client: TestClient) -> None:
        """One payout has a reference and no statement row; one has none."""
        outcomes = {one["payout_id"]: one["outcome"] for one in detail_of(client)["certificates"]}

        assert outcomes == {
            "payout-0001": BankFinalityOutcome.MISSING_BANK_EVIDENCE.value,
            "payout-0002": BankFinalityOutcome.UNLINKABLE_PAYOUT.value,
        }

    def test_the_unlinkable_payout_names_no_reference(self, client: TestClient) -> None:
        """Which is precisely what makes it unlinkable."""
        certificate = certificate_for(client, "payout-0002")

        assert certificate["bank_reference"] is None
        assert certificate["matched_bank_transaction_ids"] == []

    def test_with_the_statement_the_referenced_payout_verifies(
        self, settled_client: TestClient
    ) -> None:
        """The happy path, end to end through the API."""
        certificate = certificate_for(settled_client, "payout-0001")

        assert certificate["outcome"] == BankFinalityOutcome.VERIFIED_BANK_CREDIT.value
        assert certificate["matched_bank_transaction_ids"] == ["BANKTXN0001"]

    def test_the_unlinkable_payout_stays_unlinkable(self, settled_client: TestClient) -> None:
        """A statement arriving does not give a payout a reference it never had.

        The remaining limitation of exact matching, visible in the example
        corpus rather than only described in the documentation.
        """
        assert (
            certificate_for(settled_client, "payout-0002")["outcome"]
            == BankFinalityOutcome.UNLINKABLE_PAYOUT.value
        )

    def test_a_verified_certificate_shows_what_it_compared(
        self, settled_client: TestClient
    ) -> None:
        """Expected and observed, both, so a reader can check the comparison."""
        certificate = certificate_for(settled_client, "payout-0001")

        assert certificate["expected_amount_minor"] == 1_220_500
        assert certificate["observed_amount_minor"] == 1_220_500
        assert certificate["expected_currency"] == "INR"
        assert certificate["observed_direction"] == "CREDIT"

    def test_a_certificate_cites_the_exact_records(self, settled_client: TestClient) -> None:
        """Record IDs and payload hashes, verified against the snapshot."""
        certificate = certificate_for(settled_client, "payout-0001")

        assert len(certificate["evidence"]) == 2
        assert all(one["verification_outcome"] == "VERIFIED" for one in certificate["evidence"])
        assert all(len(one["payload_hash"]) == 64 for one in certificate["evidence"])

    @pytest.mark.parametrize(
        ("document", "expected"),
        [
            ("bank_transactions_amount_mismatch.csv", "BANK_AMOUNT_MISMATCH"),
            ("bank_transactions_currency_mismatch.csv", "BANK_CURRENCY_MISMATCH"),
            ("bank_transactions_debit.csv", "BANK_DIRECTION_MISMATCH"),
            ("bank_transactions_ambiguous.csv", "AMBIGUOUS_BANK_EVIDENCE"),
        ],
    )
    def test_each_paired_control_reaches_the_api_as_its_own_outcome(
        self, engine: Engine, document: str, expected: str
    ) -> None:
        """The same statement with one field changed, through the whole stack."""
        load(engine, ((document, SourceRecordType.BANK_TRANSACTION),))
        with client_over(engine) as opened:
            assert certificate_for(opened, "payout-0001")["outcome"] == expected

    def test_one_minor_unit_over_is_a_mismatch_through_the_api(self, engine: Engine) -> None:
        """No tolerance, visible on the wire."""
        load(
            engine, (("bank_transactions_amount_mismatch.csv", SourceRecordType.BANK_TRANSACTION),)
        )
        with client_over(engine) as opened:
            certificate = certificate_for(opened, "payout-0001")

        assert certificate["expected_amount_minor"] == 1_220_500
        assert certificate["observed_amount_minor"] == 1_220_501


class TestFinalityIsNeverASettlementStatus:
    """The confusion this whole phase exists to prevent."""

    def test_no_certificate_carries_a_status_field(self, settled_client: TestClient) -> None:
        """It carries an outcome. A field called status would invite the join
        that must not happen."""
        for certificate in detail_of(settled_client)["certificates"]:
            assert "status" not in certificate
            assert "resolved" not in certificate

    def test_no_outcome_is_a_decision_status(self, client: TestClient) -> None:
        """Asserted against the contract's own vocabulary."""
        statuses = {member.value for member in DecisionStatus}

        for certificate in detail_of(client)["certificates"]:
            assert certificate["outcome"] not in statuses

    def test_no_certificate_field_reads_resolved(self, settled_client: TestClient) -> None:
        """Not even in a verified audit, where it would be most tempting.

        Scoped to the certificates rather than the whole body, because the body
        also carries the sentence explaining that a line can be RESOLVED with no
        bank evidence. That sentence is the point; a certificate saying it about
        itself would be the defect.
        """
        rendered = json.dumps(detail_of(settled_client)["certificates"])

        assert "RESOLVED" not in rendered

    def test_every_response_says_the_two_are_separate(self, client: TestClient) -> None:
        """On the wire, so a client that never sees the screen is told too."""
        assert (
            client.get(AUDITS).json()["settlement_and_finality_are_separate"]
            == SETTLEMENT_AND_FINALITY_ARE_SEPARATE
        )
        assert (
            detail_of(client)["settlement_and_finality_are_separate"]
            == SETTLEMENT_AND_FINALITY_ARE_SEPARATE
        )

    def test_a_resolved_line_sits_beside_a_payout_with_no_bank_evidence(
        self, client: TestClient
    ) -> None:
        """Both true at once, from the two endpoints, over the same facts.

        This is the shape of the product claim. The provider's records agree
        about `line-0002`, and no bank has said the money reached the merchant.
        """
        run_id = client.post("/v1/reconciliation/runs").json()["run_id"]
        decisions = client.get(f"/v1/reconciliation/runs/{run_id}").json()["decisions"]
        resolved = [one for one in decisions if one["status"] == "RESOLVED"]
        certificates = detail_of(client)["certificates"]

        assert resolved
        assert all(
            one["outcome"] != BankFinalityOutcome.VERIFIED_BANK_CREDIT.value for one in certificates
        )

    def test_an_audit_records_no_reconciliation_run(self, client: TestClient) -> None:
        """The audit endpoint creates one thing, and it is not a run."""
        before = client.get("/v1/reconciliation/runs").json()["total"]
        client.post(AUDITS)

        assert client.get("/v1/reconciliation/runs").json()["total"] == before


class TestReadingAudits:
    """List, read, filter."""

    def test_audits_are_listed_newest_first(self, engine: Engine) -> None:
        """Two snapshots, two audits, and the later one first."""
        with client_over(engine) as opened:
            opened.post(AUDITS)
        load(engine, (("bank_transactions.csv", SourceRecordType.BANK_TRANSACTION),))
        with client_over(engine) as opened:
            second = opened.post(AUDITS).json()["audit_id"]
            listed = opened.get(AUDITS).json()

        assert listed["total"] == 2
        assert listed["audits"][0]["audit_id"] == second

    def test_the_earlier_audit_still_says_what_it_said(self, engine: Engine) -> None:
        """The immutability claim, through the API.

        Importing the statement does not turn an earlier "we have not been shown
        this" into a verification. It records a new audit beside it.
        """
        with client_over(engine) as opened:
            first_id = opened.post(AUDITS).json()["audit_id"]
        load(engine, (("bank_transactions.csv", SourceRecordType.BANK_TRANSACTION),))
        with client_over(engine) as opened:
            opened.post(AUDITS)
            old = opened.get(f"{AUDITS}/{first_id}").json()["certificates"]

        assert {one["outcome"] for one in old} == {
            BankFinalityOutcome.MISSING_BANK_EVIDENCE.value,
            BankFinalityOutcome.UNLINKABLE_PAYOUT.value,
        }

    def test_an_audit_can_be_found_by_snapshot(self, client: TestClient) -> None:
        """How a run and its audit are put side by side."""
        summary = audit_of(client)
        found = client.get(
            AUDITS, params={"snapshot_fingerprint": summary["snapshot_fingerprint"]}
        ).json()

        assert found["filtered"] is True
        assert [one["audit_id"] for one in found["audits"]] == [summary["audit_id"]]

    def test_the_snapshot_matches_the_reconciliation_run(self, client: TestClient) -> None:
        """The same digest, so the join is exact rather than by time."""
        run = client.post("/v1/reconciliation/runs").json()
        summary = audit_of(client)

        assert summary["snapshot_fingerprint"] == run["snapshot_fingerprint"]

    def test_an_unknown_snapshot_is_an_empty_page(self, client: TestClient) -> None:
        """Not an error. There is simply no audit over those facts."""
        found = client.get(AUDITS, params={"snapshot_fingerprint": "f" * 64}).json()

        assert found["audits"] == []
        assert found["total"] == 0

    def test_a_short_snapshot_filter_is_refused(self, client: TestClient) -> None:
        """A fingerprint is 64 characters, and a prefix is not one."""
        assert client.get(AUDITS, params={"snapshot_fingerprint": "abc"}).status_code == 422

    def test_certificates_can_be_filtered_by_outcome(self, client: TestClient) -> None:
        """And the summary counts still describe the whole audit."""
        body = detail_of(client, outcome=BankFinalityOutcome.UNLINKABLE_PAYOUT.value)

        assert body["filtered"] is True
        assert [one["payout_id"] for one in body["certificates"]] == ["payout-0002"]
        assert body["audit"]["payout_count"] == 2

    def test_an_unfiltered_read_says_so(self, client: TestClient) -> None:
        """So a narrowed list cannot be mistaken for the complete one."""
        assert detail_of(client)["filtered"] is False

    def test_one_certificate_can_be_read_directly(self, client: TestClient) -> None:
        """The detail a workspace asks for."""
        audit_id = audit_of(client)["audit_id"]
        response = client.get(f"{AUDITS}/{audit_id}/payouts/payout-0001")

        assert response.status_code == 200
        assert response.json()["payout_id"] == "payout-0001"

    def test_an_unknown_audit_is_a_404(self, client: TestClient) -> None:
        """Named, and nothing else echoed."""
        response = client.get(f"{AUDITS}/nope")

        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "not_found"

    def test_an_unknown_payout_is_a_404(self, client: TestClient) -> None:
        """The audit exists and that payout is not in it."""
        audit_id = audit_of(client)["audit_id"]

        assert client.get(f"{AUDITS}/{audit_id}/payouts/nope").status_code == 404

    def test_an_unknown_audit_is_checked_before_the_payout(self, client: TestClient) -> None:
        """So a wrong audit is not reported as a wrong payout."""
        body = client.get(f"{AUDITS}/nope/payouts/payout-0001").json()

        assert "audit" in body["detail"]["detail"]

    @pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
    def test_page_bounds_are_enforced(self, client: TestClient, params: dict[str, int]) -> None:
        """422, not a silently clamped value."""
        assert client.get(AUDITS, params=params).status_code == 422

    def test_paging_is_stable(self, client: TestClient) -> None:
        """The same page twice is the same page."""
        client.post(AUDITS)

        assert (
            client.get(AUDITS, params={"limit": 1}).json()
            == client.get(AUDITS, params={"limit": 1}).json()
        )


class TestTheApiSaysWhatItIs:
    """Conventions this API shares with the rest of the backend."""

    def test_it_is_separate_from_the_reconciliation_routes(self, client: TestClient) -> None:
        """So the claim that no reconciliation route changes anything holds."""
        paths = client.app.openapi()["paths"]  # type: ignore[attr-defined]

        assert any(path.startswith("/v1/bank-finality") for path in paths)
        assert not any(path.startswith("/v1/reconciliation") and "bank" in path for path in paths)

    def test_no_route_offers_a_verb_that_edits(self, client: TestClient) -> None:
        """GET and POST only. There is no PUT, PATCH or DELETE anywhere here."""
        paths = client.app.openapi()["paths"]  # type: ignore[attr-defined]

        for path, operations in paths.items():
            if not path.startswith("/v1/bank-finality"):
                continue
            for method in operations:
                assert method in {"get", "post"}, f"{method.upper()} {path}"

    def test_no_response_carries_a_canonical_payload(self, settled_client: TestClient) -> None:
        """The same rule as every other endpoint on this API."""
        audit_id = audit_of(settled_client)["audit_id"]
        body = settled_client.get(f"{AUDITS}/{audit_id}").text

        assert "canonical_payload" not in body
        assert "merchant_id" not in body

    def test_a_failure_does_not_leak_internals(self, client: TestClient) -> None:
        """No traceback, no SQL, nothing about the shape of the database."""
        body = client.get(f"{AUDITS}/nope").text.lower()

        for leak in ("traceback", "sqlalchemy", "select ", "sqlite"):
            assert leak not in body
