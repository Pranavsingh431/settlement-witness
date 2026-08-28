"""The review queue API.

Two read endpoints and one command, under their own prefix. The prefix is part
of the design: `/v1/reconciliation` serves what the baseline concluded and has
no verb that changes any of it, and `/v1/review` serves what people did about
those conclusions.

The tests that matter most here are the ones asserting what a response never
says. A queue item carries a workflow state and a baseline status side by side,
and no sequence of commands may turn the second into `RESOLVED`.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.schemas import BASELINE_IS_UNCHANGED
from app.config import Settings
from app.domain.decisions import DecisionStatus
from app.main import create_app
from app.reconciliation.runs import ReconciliationRunService
from app.review.events import ReviewAction
from app.storage.database import (
    create_database_engine,
    create_schema,
    database_url_for,
    session_scope,
)
from tests.reconciliation.conftest import index_of, payment_event, payout, settlement_line
from tests.review.conftest import mixed_facts


@pytest.fixture
def review_engine(tmp_path: Path) -> Iterator[Engine]:
    """Return a database holding one run with all four statuses in it."""
    engine = create_database_engine(database_url_for(tmp_path / "review-api.sqlite"))
    create_schema(engine)
    with session_scope(engine) as session:
        ReconciliationRunService(session).create_run(index_of(*mixed_facts()))  # type: ignore[arg-type]
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def client(review_engine: Engine) -> Iterator[TestClient]:
    """Return a client bound to that database."""
    with TestClient(create_app(Settings(app_env="test"), engine=review_engine)) as opened:
        yield opened


@pytest.fixture
def settled_client(tmp_path: Path) -> Iterator[TestClient]:
    """Return a client whose only run resolved every line it judged."""
    engine = create_database_engine(database_url_for(tmp_path / "settled.sqlite"))
    create_schema(engine)
    with session_scope(engine) as session:
        ReconciliationRunService(session).create_run(
            index_of(
                payment_event("pe-1", payment_id="pay-1"),
                settlement_line("sl-1", payment_id="pay-1", payout_id="payout-1"),
                payout("po-1", payout_id="payout-1"),
            )
        )
    try:
        with TestClient(create_app(Settings(app_env="test"), engine=engine)) as opened:
            yield opened
    finally:
        engine.dispose()


def run_id_of(client: TestClient) -> str:
    """Return the identifier of the one recorded run."""
    runs = client.get("/v1/reconciliation/runs").json()["runs"]
    return str(runs[0]["run_id"])


def queue_of(client: TestClient, **params: int) -> dict[str, Any]:
    """Return the review queue for the one recorded run."""
    response = client.get(f"/v1/review/runs/{run_id_of(client)}/queue", params=params)
    assert response.status_code == 200, response.text
    return dict(response.json())


def first_item(client: TestClient) -> dict[str, Any]:
    """Return the first queue item."""
    return dict(queue_of(client)["items"][0])


def command(item: dict[str, Any], action: str, key: str, note: str | None = None) -> dict[str, Any]:
    """Return a well formed command for one queue item."""
    body: dict[str, Any] = {
        "action": action,
        "decision_fingerprint": item["decision_fingerprint"],
        "idempotency_key": key,
    }
    if note is not None:
        body["note"] = note
    return body


def events_url(client: TestClient, item: dict[str, Any]) -> str:
    """Return the command path for one queue item."""
    return f"/v1/review/runs/{run_id_of(client)}/queue/{item['decision']['decision_id']}/events"


class TestTheQueue:
    """What it holds, in what order, and what it says about itself."""

    def test_it_holds_only_the_two_statuses_that_need_a_person(self, client: TestClient) -> None:
        """Three of the run's four decisions."""
        page = queue_of(client)

        assert page["total"] == 3
        assert {item["baseline_status"] for item in page["items"]} == {
            "EXCEPTION",
            "INSUFFICIENT_EVIDENCE",
        }

    def test_no_resolved_decision_appears(self, client: TestClient) -> None:
        """A resolved line is not work, and this endpoint is a work queue."""
        page = queue_of(client)

        for item in page["items"]:
            assert item["baseline_status"] != "RESOLVED"
            assert item["decision"]["status"] != "RESOLVED"

    def test_every_item_carries_its_certificate(self, client: TestClient) -> None:
        """The evidence and the invariant results, not a summary of them."""
        item = first_item(client)

        assert item["decision"]["evidence"] is not None
        assert item["decision"]["invariant_results"] is not None
        assert item["decision"]["exception_codes"]
        assert len(item["decision_fingerprint"]) == 64

    def test_every_item_starts_open_with_an_empty_timeline(self, client: TestClient) -> None:
        """A state, rather than an absence of one."""
        for item in queue_of(client)["items"]:
            assert item["workflow_state"] == "OPEN"
            assert item["events"] == []

    def test_the_page_says_the_baseline_is_unchanged(self, client: TestClient) -> None:
        """On the wire, so a client that never sees the screen is told too."""
        page = queue_of(client)

        assert page["baseline_unchanged_note"] == BASELINE_IS_UNCHANGED
        assert "does not change" in page["baseline_unchanged_note"]
        for item in page["items"]:
            assert item["baseline_unchanged_note"] == BASELINE_IS_UNCHANGED

    def test_it_carries_the_review_contract_version(self, client: TestClient) -> None:
        """Its own version, and not the domain contract's."""
        page = queue_of(client)

        assert page["review_contract_version"] == "1.0.0"

    def test_the_order_is_the_settlement_line_id(self, client: TestClient) -> None:
        """Fixed, so a page boundary lands in the same place on every call."""
        lines = [
            item["decision"]["subject_settlement_line_id"] for item in queue_of(client)["items"]
        ]

        assert lines == sorted(lines)

    @pytest.mark.parametrize("limit", [1, 2, 3])
    def test_paging_is_stable_and_covers_the_queue_once(
        self, client: TestClient, limit: int
    ) -> None:
        """Every item once, no gaps and no repeats, at every page size."""
        seen: list[str] = []
        for offset in range(0, 3, limit):
            page = queue_of(client, limit=limit, offset=offset)
            assert page["total"] == 3
            assert page["limit"] == limit
            assert page["offset"] == offset
            seen.extend(item["decision"]["decision_id"] for item in page["items"])

        assert len(seen) == len(set(seen)) == 3

    def test_the_same_page_twice_is_the_same_page(self, client: TestClient) -> None:
        """Determinism, asserted rather than assumed."""
        assert queue_of(client, limit=2, offset=0) == queue_of(client, limit=2, offset=0)

    def test_an_offset_past_the_end_is_empty_and_still_counts(self, client: TestClient) -> None:
        """Not an error, and the total still describes the whole queue."""
        page = queue_of(client, offset=99)

        assert page["items"] == []
        assert page["total"] == 3

    def test_a_run_with_nothing_to_review_is_an_empty_queue(
        self, settled_client: TestClient
    ) -> None:
        """Zero, rather than a 404 that would read as a missing run."""
        page = queue_of(settled_client)

        assert page["items"] == []
        assert page["total"] == 0
        assert page["open_total"] == 0

    def test_an_unknown_run_is_a_404(self, client: TestClient) -> None:
        """Named, and nothing else echoed."""
        response = client.get("/v1/review/runs/no-such-run/queue")

        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "not_found"

    @pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
    def test_a_bad_page_request_is_refused(
        self, client: TestClient, params: dict[str, int]
    ) -> None:
        """422, not a silently clamped value."""
        response = client.get(f"/v1/review/runs/{run_id_of(client)}/queue", params=params)

        assert response.status_code == 422


class TestOneQueueItem:
    """The detail a workspace reads."""

    def test_it_returns_the_certificate_and_the_timeline(self, client: TestClient) -> None:
        """Both, because the whole point is seeing them together."""
        listed = first_item(client)
        response = client.get(
            f"/v1/review/runs/{run_id_of(client)}/queue/{listed['decision']['decision_id']}"
        )

        assert response.status_code == 200
        assert response.json()["decision"] == listed["decision"]
        assert response.json()["events"] == []

    def test_a_resolved_decision_is_not_here(self, client: TestClient) -> None:
        """404 rather than a 200 with an empty timeline.

        It is not in this queue, and serving it would put a settled line on a
        screen whose whole purpose is unsettled ones.
        """
        decisions = client.get(f"/v1/reconciliation/runs/{run_id_of(client)}").json()["decisions"]
        resolved = next(one for one in decisions if one["status"] == "RESOLVED")

        response = client.get(
            f"/v1/review/runs/{run_id_of(client)}/queue/{resolved['decision_id']}"
        )

        assert response.status_code == 404

    def test_an_unknown_decision_is_a_404(self, client: TestClient) -> None:
        """The ordinary missing case."""
        response = client.get(f"/v1/review/runs/{run_id_of(client)}/queue/no-such-decision")

        assert response.status_code == 404

    def test_an_unknown_run_is_a_404(self, client: TestClient) -> None:
        """Checked before the item, so a wrong run is not reported as a wrong item."""
        response = client.get("/v1/review/runs/no-such-run/queue/anything")

        assert response.status_code == 404
        assert "run" in response.json()["detail"]["detail"]


class TestAppendingAnEvent:
    """Four actions, and what each of them does and does not do."""

    @pytest.mark.parametrize("action", [one.value for one in ReviewAction])
    def test_every_permitted_action_is_recorded(self, client: TestClient, action: str) -> None:
        """201, with the derived state and the unchanged status side by side."""
        item = first_item(client)
        response = client.post(events_url(client, item), json=command(item, action, "key-00000001"))

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["event"]["action"] == action
        assert body["baseline_status"] == item["baseline_status"]
        assert body["baseline_unchanged_note"] == BASELINE_IS_UNCHANGED

    def test_a_note_is_stored_and_returned(self, client: TestClient) -> None:
        """Plain text, exactly as it was written."""
        item = first_item(client)
        response = client.post(
            events_url(client, item),
            json=command(item, "REQUEST_EVIDENCE", "key-00000001", "need the 3 March statement"),
        )

        assert response.json()["event"]["note"] == "need the 3 March statement"

    def test_the_timeline_grows_in_sequence_order(self, client: TestClient) -> None:
        """Four events, four sequences, in the order they were sent."""
        item = first_item(client)
        for index, action in enumerate(ReviewAction):
            client.post(
                events_url(client, item), json=command(item, action.value, f"key-0000000{index}")
            )

        events = client.get(
            f"/v1/review/runs/{run_id_of(client)}/queue/{item['decision']['decision_id']}"
        ).json()["events"]

        assert [event["sequence"] for event in events] == [1, 2, 3, 4]
        assert [event["action"] for event in events] == [one.value for one in ReviewAction]

    def test_a_retry_returns_the_original_event(self, client: TestClient) -> None:
        """200 rather than 201, and the same event identifier."""
        item = first_item(client)
        body = command(item, "ACKNOWLEDGED", "key-00000001")

        first = client.post(events_url(client, item), json=body)
        second = client.post(events_url(client, item), json=body)

        assert first.status_code == 201
        assert second.status_code == 200
        assert second.json()["event"]["event_id"] == first.json()["event"]["event_id"]

    def test_a_retry_records_no_second_event(self, client: TestClient) -> None:
        """The timeline is what it would have been after one call."""
        item = first_item(client)
        body = command(item, "ACKNOWLEDGED", "key-00000001")
        client.post(events_url(client, item), json=body)
        client.post(events_url(client, item), json=body)

        events = client.get(
            f"/v1/review/runs/{run_id_of(client)}/queue/{item['decision']['decision_id']}"
        ).json()["events"]

        assert len(events) == 1

    def test_reusing_a_key_for_a_different_command_is_refused(self, client: TestClient) -> None:
        """409, and nothing written."""
        item = first_item(client)
        client.post(events_url(client, item), json=command(item, "ACKNOWLEDGED", "key-00000001"))
        response = client.post(
            events_url(client, item), json=command(item, "ESCALATED", "key-00000001")
        )

        assert response.status_code == 409
        assert response.json()["detail"]["error"] == "idempotency_conflict"

        events = client.get(
            f"/v1/review/runs/{run_id_of(client)}/queue/{item['decision']['decision_id']}"
        ).json()["events"]
        assert [event["action"] for event in events] == ["ACKNOWLEDGED"]

    def test_a_stale_fingerprint_is_refused(self, client: TestClient) -> None:
        """Acting on what you were shown, not on what happens to be there."""
        item = first_item(client)
        body = command(item, "ACKNOWLEDGED", "key-00000001")
        body["decision_fingerprint"] = "f" * 64

        response = client.post(events_url(client, item), json=body)

        assert response.status_code == 409
        assert response.json()["detail"]["error"] == "stale_certificate"

    def test_another_item_s_fingerprint_is_refused(self, client: TestClient) -> None:
        """A reviewer with two tabs open, which is the realistic version."""
        page = queue_of(client)
        target, other = page["items"][0], page["items"][1]
        body = command(target, "ACKNOWLEDGED", "key-00000001")
        body["decision_fingerprint"] = other["decision_fingerprint"]

        response = client.post(events_url(client, target), json=body)

        assert response.status_code == 409
        assert response.json()["detail"]["error"] == "stale_certificate"

    def test_a_resolved_target_is_refused(self, client: TestClient) -> None:
        """409 with its own code, so a client can tell it from a missing one."""
        decisions = client.get(f"/v1/reconciliation/runs/{run_id_of(client)}").json()["decisions"]
        resolved = next(one for one in decisions if one["status"] == "RESOLVED")

        response = client.post(
            f"/v1/review/runs/{run_id_of(client)}/queue/{resolved['decision_id']}/events",
            json={
                "action": "ACKNOWLEDGED",
                "decision_fingerprint": "a" * 64,
                "idempotency_key": "key-00000001",
            },
        )

        assert response.status_code == 409
        assert response.json()["detail"]["error"] == "not_reviewable"
        assert "RESOLVED" in response.json()["detail"]["detail"]

    def test_an_unknown_decision_is_a_404(self, client: TestClient) -> None:
        """Missing and not-reviewable are different answers."""
        response = client.post(
            f"/v1/review/runs/{run_id_of(client)}/queue/no-such-decision/events",
            json={
                "action": "ACKNOWLEDGED",
                "decision_fingerprint": "a" * 64,
                "idempotency_key": "key-00000001",
            },
        )

        assert response.status_code == 404

    def test_an_unknown_run_is_a_404(self, client: TestClient) -> None:
        """Checked first, so a wrong run is not reported as a wrong decision."""
        response = client.post(
            "/v1/review/runs/no-such-run/queue/anything/events",
            json={
                "action": "ACKNOWLEDGED",
                "decision_fingerprint": "a" * 64,
                "idempotency_key": "key-00000001",
            },
        )

        assert response.status_code == 404

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"action": "ACKNOWLEDGED"},
            {"action": "RESOLVED", "decision_fingerprint": "a" * 64, "idempotency_key": "k1234567"},
            {"action": "APPROVE", "decision_fingerprint": "a" * 64, "idempotency_key": "k1234567"},
            {
                "action": "ACKNOWLEDGED",
                "decision_fingerprint": "short",
                "idempotency_key": "k1234567",
            },
            {"action": "ACKNOWLEDGED", "decision_fingerprint": "a" * 64, "idempotency_key": "tiny"},
            {
                "action": "ACKNOWLEDGED",
                "decision_fingerprint": "a" * 64,
                "idempotency_key": "k1234567",
                "status": "RESOLVED",
            },
        ],
    )
    def test_a_malformed_command_is_refused(self, client: TestClient, body: dict[str, str]) -> None:
        """Including one that tries to smuggle a status in beside the action."""
        item = first_item(client)
        response = client.post(events_url(client, item), json=body)

        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "invalid_request"

    def test_a_note_longer_than_the_limit_is_refused(self, client: TestClient) -> None:
        """A note is a sentence, not a document."""
        item = first_item(client)
        response = client.post(
            events_url(client, item),
            json=command(item, "ACKNOWLEDGED", "key-00000001", "x" * 501),
        )

        assert response.status_code == 422


class TestNothingHereResolvesAnything:
    """The claim the whole phase rests on, asserted through the API."""

    def test_closing_every_item_leaves_every_status_where_it_was(self, client: TestClient) -> None:
        """The queue after, compared field by field with the queue before."""
        before = {
            item["decision"]["decision_id"]: item["decision"] for item in queue_of(client)["items"]
        }

        for index, item in enumerate(queue_of(client)["items"]):
            client.post(
                events_url(client, item),
                json=command(item, "CLOSED_WITHOUT_OVERRIDE", f"close-{index}-0000"),
            )

        after = {
            item["decision"]["decision_id"]: item["decision"] for item in queue_of(client)["items"]
        }

        assert after == before

    def test_a_closed_item_still_reports_its_baseline_status(self, client: TestClient) -> None:
        """Both fields, because a client might read either one."""
        item = first_item(client)
        client.post(
            events_url(client, item),
            json=command(item, "CLOSED_WITHOUT_OVERRIDE", "key-00000001"),
        )

        closed = client.get(
            f"/v1/review/runs/{run_id_of(client)}/queue/{item['decision']['decision_id']}"
        ).json()

        assert closed["workflow_state"] == "CLOSED_WITHOUT_OVERRIDE"
        assert closed["baseline_status"] == item["baseline_status"]
        assert closed["decision"]["status"] == item["baseline_status"]
        assert closed["baseline_status"] != "RESOLVED"

    def test_no_status_field_ever_reads_resolved_after_any_action(self, client: TestClient) -> None:
        """Every action, against every item, then every status field checked."""
        for index, item in enumerate(queue_of(client)["items"]):
            for step, action in enumerate(ReviewAction):
                client.post(
                    events_url(client, item),
                    json=command(item, action.value, f"key-{index}-{step}-0000"),
                )

        for item in queue_of(client)["items"]:
            assert item["baseline_status"] != DecisionStatus.RESOLVED.value
            assert item["decision"]["status"] != DecisionStatus.RESOLVED.value

    def test_the_run_endpoint_reports_the_same_statuses_afterwards(
        self, client: TestClient
    ) -> None:
        """The reconciliation API is the source of truth and does not move."""
        run_id = run_id_of(client)
        before = client.get(f"/v1/reconciliation/runs/{run_id}").json()

        for index, item in enumerate(queue_of(client)["items"]):
            client.post(
                events_url(client, item),
                json=command(item, "CLOSED_WITHOUT_OVERRIDE", f"close-{index}-0000"),
            )

        assert client.get(f"/v1/reconciliation/runs/{run_id}").json() == before

    def test_the_open_total_falls_but_the_total_does_not(self, client: TestClient) -> None:
        """Closing removes work, not findings."""
        for index, item in enumerate(queue_of(client)["items"]):
            client.post(
                events_url(client, item),
                json=command(item, "CLOSED_WITHOUT_OVERRIDE", f"close-{index}-0000"),
            )

        page = queue_of(client)
        assert page["total"] == 3
        assert page["open_total"] == 0


class TestTheApiSaysWhatItIs:
    """Conventions this API shares with the rest of the backend."""

    def test_the_review_routes_are_separate_from_the_reconciliation_ones(
        self, client: TestClient
    ) -> None:
        """So the claim that no reconciliation route changes anything still holds."""
        paths = client.app.openapi()["paths"]  # type: ignore[attr-defined]
        review = [path for path in paths if path.startswith("/v1/review")]

        assert review
        for path in paths:
            if path.startswith("/v1/reconciliation"):
                assert not path.startswith("/v1/review")

    def test_no_review_route_offers_a_verb_that_edits(self, client: TestClient) -> None:
        """GET and POST only. There is no PUT, PATCH or DELETE anywhere here."""
        paths = client.app.openapi()["paths"]  # type: ignore[attr-defined]

        for path, operations in paths.items():
            if not path.startswith("/v1/review"):
                continue
            for method in operations:
                assert method in {"get", "post"}, f"{method.upper()} {path}"

    def test_no_response_carries_an_actor(self, client: TestClient) -> None:
        """There is no authentication, so there is nobody to name."""
        item = first_item(client)
        client.post(events_url(client, item), json=command(item, "ESCALATED", "key-00000001"))
        body = client.get(f"/v1/review/runs/{run_id_of(client)}/queue").text.lower()

        for word in ('"actor"', '"reviewer"', '"user"', '"assignee"', '"approved_by"'):
            assert word not in body

    def test_no_response_carries_a_canonical_payload(self, client: TestClient) -> None:
        """The same rule as every other endpoint on this API."""
        body = client.get(f"/v1/review/runs/{run_id_of(client)}/queue").text

        assert "canonical_payload" not in body
        assert "amount_minor" not in body

    def test_a_failure_does_not_leak_internals(self, client: TestClient) -> None:
        """No traceback, no SQL, nothing about the shape of the database."""
        body = client.get("/v1/review/runs/nope/queue").text.lower()

        for leak in ("traceback", "sqlalchemy", "select ", "sqlite"):
            assert leak not in body

    def test_a_note_is_returned_as_text_not_markup(self, client: TestClient) -> None:
        """Stored and served verbatim. Escaping is the renderer's job, and the
        server's job is not to invent structure that was never there."""
        item = first_item(client)
        client.post(
            events_url(client, item),
            json=command(item, "ESCALATED", "key-00000001", "<b>see</b> ticket #4"),
        )

        events = client.get(
            f"/v1/review/runs/{run_id_of(client)}/queue/{item['decision']['decision_id']}"
        ).json()["events"]

        assert events[0]["note"] == "<b>see</b> ticket #4"
