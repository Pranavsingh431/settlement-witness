"""Tests for the public, non-persistent Track 04 demonstration endpoint."""

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.reconciliation.runs import ReconciliationRunRepository
from app.storage.database import session_scope
from app.storage.repository import ImportReceiptRepository, SourceFactRepository


class TestTrack04DemoBatch:
    """The public Track 04 batch evaluates useful synthetic data without an upload."""

    def test_runs_the_59_case_batch_and_reports_the_track_measures(
        self, empty_client: TestClient
    ) -> None:
        """The public result shows throughput, match rate and every exception type."""
        response = empty_client.get("/v1/demo/batch")

        assert response.status_code == 200
        payload = response.json()
        assert payload["is_synthetic"] is True
        assert payload["scenario_count"] == 59
        assert payload["decision_count"] == 59
        assert payload["source_record_count"] > 50
        assert [item["document_name"] for item in payload["source_documents"]] == [
            "payment_events.csv",
            "settlement_lines.csv",
            "payouts.csv",
        ]
        assert payload["resolved_count"] == 32
        assert payload["exception_count"] == 24
        assert payload["insufficient_evidence_count"] == 3
        assert payload["auto_match_rate"] == {"numerator": 32, "denominator": 59, "value": 0.542373}
        assert payload["contract_agreement"] == {"numerator": 59, "denominator": 59, "value": 1.0}
        assert payload["exception_recall"] == {"numerator": 33, "denominator": 33, "value": 1.0}
        assert payload["false_resolution_rate"] == {"numerator": 0, "denominator": 27, "value": 0.0}
        assert payload["processing_duration_ms"] > 0
        assert payload["throughput_lines_per_second"] > 0
        assert {item["code"] for item in payload["exception_breakdown"]} >= {
            "AMOUNT_MISMATCH",
            "MISSING_PAYMENT",
            "INSUFFICIENT_EVIDENCE",
        }
        assert "not measure real-merchant performance" in payload["limitation"]

    def test_repeating_the_batch_writes_nothing_to_the_application_database(
        self, empty_client: TestClient, api_engine: Engine
    ) -> None:
        """A public demo click never adds shared facts, receipts or audit history."""
        first = empty_client.get("/v1/demo/batch")
        second = empty_client.get("/v1/demo/batch")

        assert first.status_code == 200
        assert second.status_code == 200
        for field in (
            "corpus_name",
            "seed",
            "scenario_count",
            "source_record_count",
            "decision_count",
            "auto_match_rate",
            "exception_breakdown",
            "contract_agreement",
        ):
            assert second.json()[field] == first.json()[field]

        with session_scope(api_engine) as session:
            assert SourceFactRepository(session).count() == 0
            assert len(ImportReceiptRepository(session).all_receipts()) == 0
            assert ReconciliationRunRepository(session).count() == 0

    def test_the_route_has_no_upload_or_user_supplied_document_parameter(
        self, empty_client: TestClient
    ) -> None:
        """Its only input is a GET, so the public button cannot import a visitor file."""
        operation = empty_client.app.openapi()["paths"]["/v1/demo/batch"]["get"]  # type: ignore[attr-defined]

        assert "requestBody" not in operation
        assert (
            operation["summary"] == "Run the read-only 59-scenario synthetic reconciliation batch"
        )
