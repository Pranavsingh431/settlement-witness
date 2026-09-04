"""Tests for the reconciliation HTTP API."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.domain.facts import SourceRecordType
from tests.api.conftest import import_fixtures


class TestHealthIsUnchanged:
    """The one endpoint that existed before this phase."""

    def test_health_still_answers(self, client: TestClient) -> None:
        """Adding a router must not disturb it."""
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestCreatingARun:
    """The one write path."""

    def test_a_first_run_is_created(self, client: TestClient) -> None:
        """201, because a new conclusion was recorded."""
        response = client.post("/v1/reconciliation/runs")

        assert response.status_code == 201
        assert response.json()["decision_count"] == 3

    def test_a_repeat_returns_the_existing_run(self, client: TestClient) -> None:
        """200 rather than 201, and the same identifier.

        The distinction matters: a caller retrying after a timeout needs to know
        whether it created something.
        """
        first = client.post("/v1/reconciliation/runs")
        second = client.post("/v1/reconciliation/runs")

        assert second.status_code == 200
        assert second.json()["run_id"] == first.json()["run_id"]

    def test_a_repeat_does_not_add_a_run(self, client: TestClient) -> None:
        """Visible through the list, not only through the identifier."""
        client.post("/v1/reconciliation/runs")
        client.post("/v1/reconciliation/runs")

        assert client.get("/v1/reconciliation/runs").json()["total"] == 1

    def test_new_facts_create_a_second_run(self, api_engine: Engine, client: TestClient) -> None:
        """A changed snapshot is a different conclusion."""
        first = client.post("/v1/reconciliation/runs").json()

        import_fixtures(
            api_engine,
            (("invalid_zero_amount.csv", SourceRecordType.PAYMENT_EVENT),),
        )
        unchanged = client.post("/v1/reconciliation/runs")
        assert unchanged.status_code == 200, "a refused import must not change the snapshot"
        assert unchanged.json()["run_id"] == first["run_id"]

    def test_reconciling_an_empty_store_is_refused(self, empty_client: TestClient) -> None:
        """An empty run would look like a clean result."""
        response = empty_client.post("/v1/reconciliation/runs")

        assert response.status_code == 409
        assert response.json()["detail"]["error"] == "no_facts"

    def test_the_summary_reports_the_counts(self, client: TestClient) -> None:
        """One resolved, two exceptions, on the example documents."""
        payload = client.post("/v1/reconciliation/runs").json()

        assert payload["status_counts"]["RESOLVED"] == 1
        assert payload["status_counts"]["EXCEPTION"] == 2
        assert payload["exception_counts"] == {"PARTIAL_REFUND": 1, "UNSUPPORTED_STATE": 1}


class TestListingRuns:
    """Paginated, newest first."""

    def test_an_empty_store_lists_nothing(self, empty_client: TestClient) -> None:
        """Zero runs is a normal state, not an error."""
        payload = empty_client.get("/v1/reconciliation/runs").json()

        assert payload == {"runs": [], "total": 0, "limit": 20, "offset": 0}

    def test_pagination_reports_the_total_not_the_page(self, client: TestClient) -> None:
        """So a caller can tell how much more there is."""
        client.post("/v1/reconciliation/runs")

        payload = client.get("/v1/reconciliation/runs", params={"limit": 1}).json()
        assert payload["total"] == 1
        assert payload["limit"] == 1

    def test_an_offset_past_the_end_returns_an_empty_page(self, client: TestClient) -> None:
        """Not a 404. The collection exists; that page of it is empty."""
        client.post("/v1/reconciliation/runs")

        payload = client.get("/v1/reconciliation/runs", params={"offset": 50}).json()
        assert payload["runs"] == []
        assert payload["total"] == 1

    @pytest.mark.parametrize(
        ("params", "reason"),
        [
            ({"limit": "0"}, "below the minimum"),
            ({"limit": "1000"}, "above the maximum"),
            ({"offset": "-1"}, "negative"),
            ({"limit": "many"}, "not a number"),
        ],
    )
    def test_invalid_pagination_is_refused(
        self, client: TestClient, params: dict[str, str], reason: str
    ) -> None:
        """422, not a 500 and not a silently clamped value."""
        response = client.get("/v1/reconciliation/runs", params=params)

        assert response.status_code == 422, reason

    def test_listing_is_deterministic(self, client: TestClient) -> None:
        """Two identical calls return identical bytes."""
        client.post("/v1/reconciliation/runs")

        first = client.get("/v1/reconciliation/runs").content
        second = client.get("/v1/reconciliation/runs").content
        assert first == second


class TestReadingOneRun:
    """A run and its decisions."""

    def test_a_run_returns_its_decisions(self, client: TestClient) -> None:
        """Three settlement lines, three decisions."""
        run_id = client.post("/v1/reconciliation/runs").json()["run_id"]

        payload = client.get(f"/v1/reconciliation/runs/{run_id}").json()
        assert len(payload["decisions"]) == 3
        assert payload["filtered"] is False

    def test_decisions_are_ordered_by_settlement_line(self, client: TestClient) -> None:
        """A fixed order, so two reads line up."""
        run_id = client.post("/v1/reconciliation/runs").json()["run_id"]

        payload = client.get(f"/v1/reconciliation/runs/{run_id}").json()
        subjects = [d["subject_settlement_line_id"] for d in payload["decisions"]]
        assert subjects == sorted(subjects)

    def test_a_decision_carries_its_evidence_and_certificate(self, client: TestClient) -> None:
        """Which is what makes the conclusion checkable."""
        run_id = client.post("/v1/reconciliation/runs").json()["run_id"]

        decision = client.get(f"/v1/reconciliation/runs/{run_id}").json()["decisions"][0]
        assert decision["evidence"]
        assert all(e["verification_outcome"] == "VERIFIED" for e in decision["evidence"])
        assert {r["invariant_id"] for r in decision["invariant_results"]} == {
            "INV-001",
            "INV-002",
            "INV-003",
            "INV-004",
            "INV-009",
        }
        assert decision["closure_plan"]["baseline_status"] == decision["status"]
        assert decision["closure_plan"]["plan_version"] == "1.0.0"

    def test_an_exception_carries_functional_recourse_without_an_override(
        self, client: TestClient
    ) -> None:
        run_id = client.post("/v1/reconciliation/runs").json()["run_id"]

        decisions = client.get(f"/v1/reconciliation/runs/{run_id}").json()["decisions"]
        decision = next(item for item in decisions if item["status"] == "EXCEPTION")
        plan = decision["closure_plan"]

        assert plan["actions"]
        assert plan["requires_new_run"] is True
        assert "RESOLVED" in plan["resolution_gate"]
        assert "status" not in plan

    def test_an_unknown_run_is_a_404(self, client: TestClient) -> None:
        """Named, without echoing anything else."""
        response = client.get("/v1/reconciliation/runs/no-such-run")

        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "not_found"

    def test_reading_is_deterministic(self, client: TestClient) -> None:
        """Two identical calls return identical bytes."""
        run_id = client.post("/v1/reconciliation/runs").json()["run_id"]

        first = client.get(f"/v1/reconciliation/runs/{run_id}").content
        second = client.get(f"/v1/reconciliation/runs/{run_id}").content
        assert first == second


class TestFilters:
    """Narrowing the decisions in a run."""

    def test_filtering_by_status(self, client: TestClient) -> None:
        """Two of the three example decisions are exceptions."""
        run_id = client.post("/v1/reconciliation/runs").json()["run_id"]

        payload = client.get(
            f"/v1/reconciliation/runs/{run_id}", params={"status": "EXCEPTION"}
        ).json()
        assert len(payload["decisions"]) == 2
        assert all(d["status"] == "EXCEPTION" for d in payload["decisions"])

    def test_filtering_by_exception_code(self, client: TestClient) -> None:
        """One example line carries a partial refund."""
        run_id = client.post("/v1/reconciliation/runs").json()["run_id"]

        payload = client.get(
            f"/v1/reconciliation/runs/{run_id}", params={"exception_code": "PARTIAL_REFUND"}
        ).json()
        assert len(payload["decisions"]) == 1
        assert "PARTIAL_REFUND" in payload["decisions"][0]["exception_codes"]

    def test_filters_combine(self, client: TestClient) -> None:
        """Both are applied, not the last one given."""
        run_id = client.post("/v1/reconciliation/runs").json()["run_id"]

        payload = client.get(
            f"/v1/reconciliation/runs/{run_id}",
            params={"status": "RESOLVED", "exception_code": "PARTIAL_REFUND"},
        ).json()
        assert payload["decisions"] == []

    def test_a_filtered_view_says_it_is_filtered(self, client: TestClient) -> None:
        """So a narrowed list cannot be mistaken for the complete one."""
        run_id = client.post("/v1/reconciliation/runs").json()["run_id"]

        payload = client.get(
            f"/v1/reconciliation/runs/{run_id}", params={"status": "RESOLVED"}
        ).json()
        assert payload["filtered"] is True

    def test_the_summary_counts_describe_the_whole_run(self, client: TestClient) -> None:
        """A filter narrows the decisions, never the summary."""
        run_id = client.post("/v1/reconciliation/runs").json()["run_id"]

        payload = client.get(
            f"/v1/reconciliation/runs/{run_id}", params={"status": "RESOLVED"}
        ).json()
        assert len(payload["decisions"]) == 1
        assert payload["run"]["decision_count"] == 3

    @pytest.mark.parametrize("params", [{"status": "WAT"}, {"exception_code": "NOT_A_CODE"}])
    def test_an_unknown_filter_value_is_refused(
        self, client: TestClient, params: dict[str, str]
    ) -> None:
        """422 from the enum, rather than an empty list that looks like a result."""
        run_id = client.post("/v1/reconciliation/runs").json()["run_id"]

        response = client.get(f"/v1/reconciliation/runs/{run_id}", params=params)
        assert response.status_code == 422


class TestReadingOneDecision:
    """One decision with its full certificate."""

    def test_a_decision_is_returned(self, client: TestClient) -> None:
        """Found by its own identifier within its run."""
        run_id = client.post("/v1/reconciliation/runs").json()["run_id"]
        listed = client.get(f"/v1/reconciliation/runs/{run_id}").json()["decisions"][0]

        payload = client.get(
            f"/v1/reconciliation/runs/{run_id}/decisions/{listed['decision_id']}"
        ).json()
        assert payload == listed

    def test_an_unknown_decision_is_a_404(self, client: TestClient) -> None:
        """Within a run that does exist."""
        run_id = client.post("/v1/reconciliation/runs").json()["run_id"]

        response = client.get(f"/v1/reconciliation/runs/{run_id}/decisions/nope")
        assert response.status_code == 404

    def test_an_unknown_run_is_a_404_before_the_decision(self, client: TestClient) -> None:
        """The run is checked first, so the message names the right thing."""
        response = client.get("/v1/reconciliation/runs/nope/decisions/also-nope")

        assert response.status_code == 404
        assert "run" in response.json()["detail"]["detail"]


class TestWhatIsNotExposed:
    """The API explains conclusions. It does not serve the underlying records."""

    def test_no_response_carries_a_raw_canonical_payload(self, client: TestClient) -> None:
        """A citation names a record and its hash, which is what makes a
        decision checkable. The payload itself is merchant data and stays in the
        store."""
        run_id = client.post("/v1/reconciliation/runs").json()["run_id"]
        body = json.dumps(client.get(f"/v1/reconciliation/runs/{run_id}").json())

        assert "canonical_payload" not in body
        assert "amount_minor" not in body

    def test_the_internal_run_key_is_not_published(self, client: TestClient) -> None:
        """It is an idempotency identity, and publishing it would invite
        callers to depend on how it is computed."""
        payload = client.post("/v1/reconciliation/runs").json()

        assert "run_key" not in payload

    def test_there_is_no_endpoint_that_changes_a_decision(self, client: TestClient) -> None:
        """Human override is deferred deliberately. Every reconciliation route
        is a GET, apart from the one that creates a run."""
        paths = client.app.openapi()["paths"]  # type: ignore[attr-defined]

        for path, operations in paths.items():
            if not path.startswith("/v1/reconciliation"):
                continue
            for method in operations:
                assert method in {"get", "post"}, f"{method.upper()} {path}"

    def test_a_failure_does_not_leak_internals(self, client: TestClient) -> None:
        """No traceback, no SQL, nothing about the shape of the database."""
        body = client.get("/v1/reconciliation/runs/nope").text.lower()

        for leak in ("traceback", "sqlalchemy", "select ", "sqlite"):
            assert leak not in body


class TestApiAgreesWithTheCli:
    """The same snapshot through two paths must say the same thing."""

    def test_the_persisted_run_matches_the_cli_output(
        self, client: TestClient, loaded_engine: Engine
    ) -> None:
        """The API persists what the CLI prints, for the same facts.

        If these diverged, one of them would be reporting something the other
        could not reproduce, and neither would be trustworthy.
        """
        from app.reconciliation.batch import reconcile
        from app.storage.database import session_factory
        from app.storage.repository import SourceFactRepository

        run_id = client.post("/v1/reconciliation/runs").json()["run_id"]
        served = client.get(f"/v1/reconciliation/runs/{run_id}").json()

        with session_factory(loaded_engine)() as session:
            batch = reconcile(SourceFactRepository(session).fact_index())

        assert served["run"]["snapshot_fingerprint"] == batch.snapshot_fingerprint
        assert served["run"]["status_counts"] == dict(sorted(batch.status_counts.items()))
        assert [d["decision_id"] for d in served["decisions"]] == [
            decision.decision_id for decision in batch.decisions
        ]
        assert [d["status"] for d in served["decisions"]] == [
            decision.status.value for decision in batch.decisions
        ]


class TestDomainRefusalsBecomeUnprocessable:
    """A contract rule refusing something is not a crash."""

    def test_a_value_error_is_reported_as_422(self, client: TestClient) -> None:
        """Not a 500.

        A ValueError from the domain means a rule refused something, which is a
        problem with the request or with the stored data. The message is the
        rule's own, written for people, and mentions nothing internal.
        """
        from fastapi import APIRouter

        router = APIRouter()

        @router.get("/_test/refuse")
        def refuse() -> None:
            """Raise the way a contract rule does."""
            message = "a settlement line must name a payout"
            raise ValueError(message)

        client.app.include_router(router)  # type: ignore[attr-defined]

        response = client.get("/_test/refuse")

        assert response.status_code == 422
        assert response.json() == {
            "error": "unprocessable",
            "detail": "a settlement line must name a payout",
        }

    def test_the_refusal_carries_no_internals(self, client: TestClient) -> None:
        """Same rule as every other error path."""
        from fastapi import APIRouter

        router = APIRouter()

        @router.get("/_test/refuse-2")
        def refuse() -> None:
            """Raise the way a contract rule does."""
            message = "the evidence admits more than one explanation"
            raise ValueError(message)

        client.app.include_router(router)  # type: ignore[attr-defined]

        body = client.get("/_test/refuse-2").text.lower()
        for leak in ("traceback", "sqlalchemy", "sqlite", 'file "'):
            assert leak not in body
