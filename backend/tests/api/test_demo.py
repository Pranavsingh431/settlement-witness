"""Tests for the public, fixture-only walkthrough endpoint."""

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.demo import DEMO_FIXTURES
from app.storage.database import session_scope
from app.storage.repository import ImportReceiptRepository, SourceFactRepository


class TestDemoBootstrap:
    """The walkthrough makes the committed example useful without an upload."""

    def test_prepares_all_four_committed_documents_and_real_conclusions(
        self, empty_client: TestClient
    ) -> None:
        """The first call imports fixtures, reconciles them and checks bank finality."""
        response = empty_client.post("/v1/demo/bootstrap")

        assert response.status_code == 201
        payload = response.json()
        assert payload["created"] is True
        assert [item["document_name"] for item in payload["fixture_results"]] == [
            "payment_events.csv",
            "settlement_lines.csv",
            "payouts.csv",
            "bank_transactions.csv",
        ]
        assert all(item["outcome"] == "ACCEPTED" for item in payload["fixture_results"])
        assert all(item["loaded_now"] is True for item in payload["fixture_results"])
        assert payload["run"]["fact_count"] == 11
        assert payload["run"]["settlement_line_count"] == 3
        assert payload["run"]["status_counts"] == {
            "EXCEPTION": 2,
            "INSUFFICIENT_EVIDENCE": 0,
            "PENDING": 0,
            "RESOLVED": 1,
        }
        assert payload["bank_finality_audit"]["payout_count"] == 2
        assert payload["bank_finality_audit"]["bank_transaction_count"] == 1
        assert payload["bank_finality_audit"]["verified_payout_count"] == 1

    def test_deployed_documents_stay_byte_identical_to_the_committed_fixtures(self) -> None:
        """A source-fixture edit must explicitly update the packaged walkthrough."""
        fixture_directory = Path(__file__).parents[3] / "data" / "fixtures" / "ingestion"

        assert {fixture.name: fixture.content for fixture in DEMO_FIXTURES} == {
            fixture.name: (fixture_directory / fixture.name).read_bytes()
            for fixture in DEMO_FIXTURES
        }

    def test_repeating_the_walkthrough_does_not_append_duplicate_facts_or_receipts(
        self, empty_client: TestClient, api_engine: Engine
    ) -> None:
        """A second click reopens the same conclusions instead of creating audit noise."""
        first = empty_client.post("/v1/demo/bootstrap")
        second = empty_client.post("/v1/demo/bootstrap")

        assert first.status_code == 201
        assert second.status_code == 200
        again = second.json()
        assert again["created"] is False
        assert again["run"]["run_id"] == first.json()["run"]["run_id"]
        assert (
            again["bank_finality_audit"]["audit_id"]
            == first.json()["bank_finality_audit"]["audit_id"]
        )
        assert all(item["outcome"] == "DUPLICATE_NO_OP" for item in again["fixture_results"])
        assert all(item["loaded_now"] is False for item in again["fixture_results"])

        with session_scope(api_engine) as session:
            assert SourceFactRepository(session).count() == 11
            assert len(ImportReceiptRepository(session).all_receipts()) == 4

    def test_the_route_has_no_upload_or_user_supplied_document_parameter(
        self, empty_client: TestClient
    ) -> None:
        """Its only input is a POST, so the public button cannot import a visitor file."""
        operation = empty_client.app.openapi()["paths"]["/v1/demo/bootstrap"]["post"]  # type: ignore[attr-defined]

        assert "requestBody" not in operation
        assert operation["summary"] == "Load the bundled synthetic walkthrough"
