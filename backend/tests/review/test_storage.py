"""The review event table is append-only, and the database says so.

Not the service, and not the repository. Raw SQL against the table, because a
guarantee that only holds while every writer goes through one Python class is a
convention, and this project already has four tables where it is a rule.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import DatabaseError, IntegrityError

from app.domain.decisions import DecisionStatus
from app.reconciliation.runs import PersistedRun
from app.review.events import ReviewAction, certificate_fingerprint
from app.review.service import ReviewQueueService
from app.storage.database import APPEND_ONLY_TABLES, session_scope
from tests.review.conftest import RECORDED_AT, one_with


def _acknowledge(engine: Engine, run: PersistedRun, key: str = "key-0001") -> None:
    """Record one event through the service, so there is a row to attack."""
    decision = one_with(engine, run.run_id, DecisionStatus.EXCEPTION)
    with session_scope(engine) as session:
        ReviewQueueService(session, now=RECORDED_AT).append_event(
            run_id=run.run_id,
            decision_id=decision.decision_id,
            action=ReviewAction.ACKNOWLEDGED,
            decision_fingerprint=certificate_fingerprint(decision),
            idempotency_key=key,
        )


def _rows(engine: Engine) -> list[tuple[object, ...]]:
    """Return every review event row, in sequence order."""
    with engine.connect() as connection:
        return [
            tuple(row)
            for row in connection.exec_driver_sql(
                "SELECT sequence, action, note FROM review_events ORDER BY sequence"
            )
        ]


class TestTheTableRefusesToBeRewritten:
    """UPDATE and DELETE are refused by the database, INSERT is not."""

    def test_the_table_is_declared_append_only(self) -> None:
        """So the existing trigger tests cover it without being edited."""
        assert "review_events" in APPEND_ONLY_TABLES

    def test_both_triggers_exist(self, engine: Engine) -> None:
        """Created by the migration, like every other protected table."""
        with engine.connect() as connection:
            names = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND tbl_name = 'review_events'"
                )
            }

        assert names == {"trg_review_events_no_update", "trg_review_events_no_delete"}

    def test_a_raw_update_is_refused(self, engine: Engine, recorded_run: PersistedRun) -> None:
        """The action a person would most want to change, changed directly."""
        _acknowledge(engine, recorded_run)
        before = _rows(engine)

        with (
            pytest.raises(DatabaseError, match="review_events is append-only"),
            engine.begin() as connection,
        ):
            connection.execute(text("UPDATE review_events SET action = 'CLOSED_WITHOUT_OVERRIDE'"))

        assert _rows(engine) == before

    def test_a_raw_update_of_the_note_is_refused_too(
        self, engine: Engine, recorded_run: PersistedRun
    ) -> None:
        """Not only the columns that carry meaning to the projection."""
        _acknowledge(engine, recorded_run)
        before = _rows(engine)

        with (
            pytest.raises(DatabaseError, match="review_events is append-only"),
            engine.begin() as connection,
        ):
            connection.execute(text("UPDATE review_events SET note = 'rewritten'"))

        assert _rows(engine) == before

    def test_a_raw_delete_is_refused(self, engine: Engine, recorded_run: PersistedRun) -> None:
        """A history somebody can delete from is not a history."""
        _acknowledge(engine, recorded_run)
        before = _rows(engine)

        with (
            pytest.raises(DatabaseError, match="review_events is append-only"),
            engine.begin() as connection,
        ):
            connection.execute(text("DELETE FROM review_events"))

        assert _rows(engine) == before

    def test_a_raw_insert_is_still_allowed(
        self, engine: Engine, recorded_run: PersistedRun
    ) -> None:
        """Append-only means append. A table nothing can write to is not one."""
        decision = one_with(engine, recorded_run.run_id, DecisionStatus.EXCEPTION)

        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO review_events (event_id, run_id, decision_id, "
                    "subject_settlement_line_id, decision_fingerprint, action, note, "
                    "idempotency_key, command_fingerprint, recorded_at) VALUES "
                    "(:event_id, :run_id, :decision_id, :line_id, :fingerprint, "
                    ":action, NULL, :key, :command, :recorded_at)"
                ),
                {
                    "event_id": "raw-1",
                    "run_id": recorded_run.run_id,
                    "decision_id": decision.decision_id,
                    "line_id": decision.subject_settlement_line_id,
                    "fingerprint": certificate_fingerprint(decision),
                    "action": "ACKNOWLEDGED",
                    "key": "raw-key-0001",
                    "command": "c" * 64,
                    "recorded_at": datetime.now(UTC).isoformat(),
                },
            )

        assert len(_rows(engine)) == 1


class TestTheSchemaSaysWhatItMeans:
    """Constraints, not conventions."""

    def test_there_is_no_status_column(self, engine: Engine) -> None:
        """The workflow state is derived. A column for it could disagree."""
        columns = {column["name"] for column in inspect(engine).get_columns("review_events")}

        assert not columns & {"status", "workflow_state", "state", "current_status"}

    def test_there_is_no_actor_column(self, engine: Engine) -> None:
        """There is no authentication, so there is nobody to record."""
        columns = {column["name"] for column in inspect(engine).get_columns("review_events")}

        assert not columns & {"actor", "actor_id", "reviewer", "reviewer_id", "user", "user_id"}

    def test_an_unknown_action_is_refused_by_the_database(
        self, engine: Engine, recorded_run: PersistedRun
    ) -> None:
        """The four actions are a constraint, not only an enum in Python."""
        decision = one_with(engine, recorded_run.run_id, DecisionStatus.EXCEPTION)

        with (
            pytest.raises(IntegrityError, match="ck_review_events_action"),
            engine.begin() as connection,
        ):
            connection.execute(
                text(
                    "INSERT INTO review_events (event_id, run_id, decision_id, "
                    "subject_settlement_line_id, decision_fingerprint, action, note, "
                    "idempotency_key, command_fingerprint, recorded_at) VALUES "
                    "('x', :run_id, :decision_id, :line_id, :fingerprint, 'RESOLVED', "
                    "NULL, 'k1234567', :command, :recorded_at)"
                ),
                {
                    "run_id": recorded_run.run_id,
                    "decision_id": decision.decision_id,
                    "line_id": decision.subject_settlement_line_id,
                    "fingerprint": certificate_fingerprint(decision),
                    "command": "c" * 64,
                    "recorded_at": datetime.now(UTC).isoformat(),
                },
            )

    def test_the_sequence_is_assigned_by_the_database(
        self, engine: Engine, recorded_run: PersistedRun
    ) -> None:
        """Monotonic, and not derived from a timestamp anywhere."""
        _acknowledge(engine, recorded_run, key="key-0001")
        _acknowledge(engine, recorded_run, key="key-0002")

        assert [row[0] for row in _rows(engine)] == [1, 2]
