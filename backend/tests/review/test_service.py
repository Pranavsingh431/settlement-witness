"""Appending review events, and the one thing none of them may do.

The central test in this file is the byte-for-byte comparison. Every other
guarantee here is about refusing a bad command; that one is about what happens
when every command is good, which is the case a defect would hide in.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.domain.decisions import DecisionStatus, ReconciliationDecision
from app.reconciliation.batch import reconcile
from app.reconciliation.runs import PersistedRun, ReconciliationRunRepository
from app.review.events import (
    ReviewAction,
    ReviewEvent,
    ReviewWorkflowState,
    certificate_fingerprint,
    derive_workflow_state,
)
from app.review.service import (
    IdempotencyConflict,
    ReviewEventRepository,
    ReviewQueueService,
    StaleCertificate,
    TargetNotReviewable,
)
from app.storage.database import session_factory, session_scope
from tests.reconciliation.conftest import index_of
from tests.review.conftest import RECORDED_AT, decisions_of, mixed_facts, one_with


def append(
    engine: Engine,
    run: PersistedRun,
    decision: ReconciliationDecision,
    action: ReviewAction,
    *,
    key: str,
    note: str | None = None,
    fingerprint: str | None = None,
    at: datetime = RECORDED_AT,
) -> None:
    """Record one event in its own committed transaction."""
    with session_scope(engine) as session:
        ReviewQueueService(session, now=at).append_event(
            run_id=run.run_id,
            decision_id=decision.decision_id,
            action=action,
            decision_fingerprint=fingerprint or certificate_fingerprint(decision),
            idempotency_key=key,
            note=note,
        )


def stored_decisions_json(engine: Engine, run_id: str) -> str:
    """Return every stored decision of a run as one canonical string.

    Read through the domain model and re-serialised canonically, so the
    comparison is over what the decisions mean rather than over how SQLite
    happened to lay the rows out.
    """
    return json.dumps(
        [decision.model_dump(mode="json") for decision in decisions_of(engine, run_id)],
        sort_keys=True,
        separators=(",", ":"),
    )


class TestTheQueueHoldsOnlyWhatNeedsAPerson:
    """Resolved lines are not work, and pending lines are not decisions yet."""

    def test_it_holds_the_exception_and_unknown_lines(
        self, engine: Engine, recorded_run: PersistedRun
    ) -> None:
        """Three of the run's four decisions."""
        with session_factory(engine)() as session:
            page = ReviewQueueService(session).queue(recorded_run.run_id, limit=20, offset=0)

        assert page.total == 3
        assert {item.decision.status for item in page.items} == {
            DecisionStatus.EXCEPTION,
            DecisionStatus.INSUFFICIENT_EVIDENCE,
        }

    def test_the_resolved_line_is_absent(self, engine: Engine, recorded_run: PersistedRun) -> None:
        """It needs nothing, and putting it here would make the queue a list."""
        resolved = one_with(engine, recorded_run.run_id, DecisionStatus.RESOLVED)

        with session_factory(engine)() as session:
            page = ReviewQueueService(session).queue(recorded_run.run_id, limit=20, offset=0)

            assert resolved.decision_id not in {item.decision.decision_id for item in page.items}
            assert (
                ReviewQueueService(session).item(recorded_run.run_id, resolved.decision_id) is None
            )

    def test_a_fresh_item_is_open(self, engine: Engine, recorded_run: PersistedRun) -> None:
        """Nobody has recorded anything, which is a state rather than an absence."""
        with session_factory(engine)() as session:
            page = ReviewQueueService(session).queue(recorded_run.run_id, limit=20, offset=0)

        assert all(item.workflow_state is ReviewWorkflowState.OPEN for item in page.items)
        assert page.open_total == 3

    def test_the_order_is_the_settlement_line_id(
        self, engine: Engine, recorded_run: PersistedRun
    ) -> None:
        """Fixed, and independent of who acted on what and when."""
        with session_factory(engine)() as session:
            page = ReviewQueueService(session).queue(recorded_run.run_id, limit=20, offset=0)

        lines = [item.decision.subject_settlement_line_id for item in page.items]
        assert lines == sorted(lines)

    def test_the_order_does_not_move_when_an_item_is_acted_on(
        self, engine: Engine, recorded_run: PersistedRun
    ) -> None:
        """A queue that reordered itself would make paging skip items."""
        with session_factory(engine)() as session:
            before = [
                item.decision.decision_id
                for item in ReviewQueueService(session)
                .queue(recorded_run.run_id, limit=20, offset=0)
                .items
            ]

        first = one_with(engine, recorded_run.run_id, DecisionStatus.EXCEPTION)
        append(engine, recorded_run, first, ReviewAction.ESCALATED, key="key-0001")

        with session_factory(engine)() as session:
            after = [
                item.decision.decision_id
                for item in ReviewQueueService(session)
                .queue(recorded_run.run_id, limit=20, offset=0)
                .items
            ]

        assert after == before

    @pytest.mark.parametrize("limit", [1, 2, 3])
    def test_paging_covers_the_queue_exactly_once(
        self, engine: Engine, recorded_run: PersistedRun, limit: int
    ) -> None:
        """Whatever the page size, every item appears once and none is skipped."""
        seen: list[str] = []
        with session_factory(engine)() as session:
            service = ReviewQueueService(session)
            for offset in range(0, 3, limit):
                page = service.queue(recorded_run.run_id, limit=limit, offset=offset)
                seen.extend(item.decision.decision_id for item in page.items)

        assert len(seen) == len(set(seen)) == 3

    def test_an_offset_past_the_end_is_an_empty_page(
        self, engine: Engine, recorded_run: PersistedRun
    ) -> None:
        """Not an error, and the totals still describe the whole queue."""
        with session_factory(engine)() as session:
            page = ReviewQueueService(session).queue(recorded_run.run_id, limit=20, offset=99)

        assert page.items == ()
        assert page.total == 3


class TestNoReviewEventChangesADecision:
    """The guarantee this whole phase rests on, compared byte for byte."""

    def test_every_action_leaves_every_decision_identical(
        self, engine: Engine, recorded_run: PersistedRun
    ) -> None:
        """All four actions, against every reviewable decision in the run.

        Compared as one canonical string over every stored decision, so a
        changed status, a dropped exception code, a reordered evidence list or a
        moved timestamp would all fail here.
        """
        before = stored_decisions_json(engine, recorded_run.run_id)
        reviewable = [
            decision
            for decision in decisions_of(engine, recorded_run.run_id)
            if decision.status is not DecisionStatus.RESOLVED
        ]

        for index, decision in enumerate(reviewable):
            for step, action in enumerate(ReviewAction):
                append(
                    engine,
                    recorded_run,
                    decision,
                    action,
                    key=f"key-{index}-{step}-0000",
                    note=f"note {action.value}",
                )

        assert stored_decisions_json(engine, recorded_run.run_id) == before

    def test_the_recomputed_baseline_is_identical_too(
        self, engine: Engine, recorded_run: PersistedRun
    ) -> None:
        """Not only what is stored: what the baseline says now.

        A stored decision that matched while a fresh reconciliation of the same
        facts disagreed would mean the review path had changed something the
        store was hiding.
        """
        recomputed = reconcile(index_of(*mixed_facts()))  # type: ignore[arg-type]
        before = json.dumps(
            [decision.model_dump(mode="json") for decision in recomputed.decisions],
            sort_keys=True,
            separators=(",", ":"),
        )

        for index, decision in enumerate(
            one
            for one in decisions_of(engine, recorded_run.run_id)
            if one.status is not DecisionStatus.RESOLVED
        ):
            append(engine, recorded_run, decision, ReviewAction.ESCALATED, key=f"k-{index}-000000")

        after = reconcile(index_of(*mixed_facts()))  # type: ignore[arg-type]
        assert (
            json.dumps(
                [decision.model_dump(mode="json") for decision in after.decisions],
                sort_keys=True,
                separators=(",", ":"),
            )
            == before
        )

    def test_a_closed_item_still_carries_its_original_status(
        self, engine: Engine, recorded_run: PersistedRun
    ) -> None:
        """The one thing a closed review must never look like."""
        decision = one_with(engine, recorded_run.run_id, DecisionStatus.EXCEPTION)
        append(engine, recorded_run, decision, ReviewAction.CLOSED_WITHOUT_OVERRIDE, key="key-0001")

        with session_factory(engine)() as session:
            item = ReviewQueueService(session).item(recorded_run.run_id, decision.decision_id)

        assert item is not None
        assert item.workflow_state is ReviewWorkflowState.CLOSED_WITHOUT_OVERRIDE
        assert item.decision.status is DecisionStatus.EXCEPTION

    def test_a_closed_unknown_item_is_still_unknown(
        self, engine: Engine, recorded_run: PersistedRun
    ) -> None:
        """The same for the other status this queue holds."""
        decision = one_with(engine, recorded_run.run_id, DecisionStatus.INSUFFICIENT_EVIDENCE)
        append(engine, recorded_run, decision, ReviewAction.CLOSED_WITHOUT_OVERRIDE, key="key-0002")

        with session_factory(engine)() as session:
            item = ReviewQueueService(session).item(recorded_run.run_id, decision.decision_id)

        assert item is not None
        assert item.decision.status is DecisionStatus.INSUFFICIENT_EVIDENCE

    def test_the_run_summary_counts_are_untouched(
        self, engine: Engine, recorded_run: PersistedRun
    ) -> None:
        """Closing every item does not move a single count."""
        for index, decision in enumerate(
            one
            for one in decisions_of(engine, recorded_run.run_id)
            if one.status is not DecisionStatus.RESOLVED
        ):
            append(
                engine,
                recorded_run,
                decision,
                ReviewAction.CLOSED_WITHOUT_OVERRIDE,
                key=f"close-{index}-0000",
            )

        with session_factory(engine)() as session:
            stored = ReconciliationRunRepository(session).get(recorded_run.run_id)

        assert stored is not None
        assert stored.status_counts == recorded_run.status_counts
        assert stored.exception_counts == recorded_run.exception_counts


class TestACommandIsRefusedWithoutWriting:
    """Five refusals, and each of them leaves the table as it was."""

    def _events(self, engine: Engine, run_id: str) -> int:
        """Return how many events the run has attracted."""
        with session_factory(engine)() as session:
            return ReviewEventRepository(session).count_for_run(run_id)

    def test_a_resolved_decision_is_refused(
        self, engine: Engine, recorded_run: PersistedRun
    ) -> None:
        """It does not need this queue and cannot be put in it."""
        resolved = one_with(engine, recorded_run.run_id, DecisionStatus.RESOLVED)

        with pytest.raises(TargetNotReviewable) as raised, session_scope(engine) as session:
            ReviewQueueService(session).append_event(
                run_id=recorded_run.run_id,
                decision_id=resolved.decision_id,
                action=ReviewAction.ACKNOWLEDGED,
                decision_fingerprint=certificate_fingerprint(resolved),
                idempotency_key="key-0001",
            )

        assert raised.value.code == "not_reviewable"
        assert "RESOLVED" in raised.value.detail
        assert self._events(engine, recorded_run.run_id) == 0

    def test_an_unknown_decision_is_refused(
        self, engine: Engine, recorded_run: PersistedRun
    ) -> None:
        """Named separately, because missing and not-reviewable are different answers."""
        with pytest.raises(TargetNotReviewable) as raised, session_scope(engine) as session:
            ReviewQueueService(session).append_event(
                run_id=recorded_run.run_id,
                decision_id="no-such-decision",
                action=ReviewAction.ACKNOWLEDGED,
                decision_fingerprint="a" * 64,
                idempotency_key="key-0001",
            )

        assert raised.value.code == "decision_not_found"
        assert self._events(engine, recorded_run.run_id) == 0

    def test_a_stale_fingerprint_is_refused(
        self, engine: Engine, recorded_run: PersistedRun
    ) -> None:
        """Acting on what you were shown, not on what happens to be there."""
        decision = one_with(engine, recorded_run.run_id, DecisionStatus.EXCEPTION)

        with pytest.raises(StaleCertificate) as raised, session_scope(engine) as session:
            ReviewQueueService(session).append_event(
                run_id=recorded_run.run_id,
                decision_id=decision.decision_id,
                action=ReviewAction.ACKNOWLEDGED,
                decision_fingerprint="f" * 64,
                idempotency_key="key-0001",
            )

        assert raised.value.code == "stale_certificate"
        assert self._events(engine, recorded_run.run_id) == 0

    def test_another_decision_s_fingerprint_is_refused(
        self, engine: Engine, recorded_run: PersistedRun
    ) -> None:
        """The mismatch that a random 64 characters would not catch.

        A reviewer with two tabs open is the realistic version of this, and the
        fingerprint they echo back is a real one belonging to the other item.
        """
        target = one_with(engine, recorded_run.run_id, DecisionStatus.EXCEPTION)
        other = one_with(engine, recorded_run.run_id, DecisionStatus.INSUFFICIENT_EVIDENCE)

        with pytest.raises(StaleCertificate), session_scope(engine) as session:
            ReviewQueueService(session).append_event(
                run_id=recorded_run.run_id,
                decision_id=target.decision_id,
                action=ReviewAction.ACKNOWLEDGED,
                decision_fingerprint=certificate_fingerprint(other),
                idempotency_key="key-0001",
            )

        assert self._events(engine, recorded_run.run_id) == 0


class TestIdempotency:
    """A retry is not a second event, and a reuse is not a retry."""

    def test_the_same_command_twice_records_one_event(
        self, engine: Engine, recorded_run: PersistedRun
    ) -> None:
        """The same event comes back, with `was_created` false."""
        decision = one_with(engine, recorded_run.run_id, DecisionStatus.EXCEPTION)
        command = {
            "run_id": recorded_run.run_id,
            "decision_id": decision.decision_id,
            "action": ReviewAction.ACKNOWLEDGED,
            "decision_fingerprint": certificate_fingerprint(decision),
            "idempotency_key": "key-0001",
        }

        with session_scope(engine) as session:
            first = ReviewQueueService(session, now=RECORDED_AT).append_event(**command)  # type: ignore[arg-type]
        with session_scope(engine) as session:
            second = ReviewQueueService(session, now=RECORDED_AT).append_event(**command)  # type: ignore[arg-type]

        assert first.was_created is True
        assert second.was_created is False
        assert second.event == first.event

    def test_a_retry_with_the_same_note_is_still_a_retry(
        self, engine: Engine, recorded_run: PersistedRun
    ) -> None:
        """Including when the note differs only by surrounding whitespace."""
        decision = one_with(engine, recorded_run.run_id, DecisionStatus.EXCEPTION)
        append(
            engine, recorded_run, decision, ReviewAction.ACKNOWLEDGED, key="key-0001", note="hello"
        )

        with session_scope(engine) as session:
            again = ReviewQueueService(session).append_event(
                run_id=recorded_run.run_id,
                decision_id=decision.decision_id,
                action=ReviewAction.ACKNOWLEDGED,
                decision_fingerprint=certificate_fingerprint(decision),
                idempotency_key="key-0001",
                note="  hello  ",
            )

        assert again.was_created is False

    def test_reusing_a_key_for_a_different_action_is_refused(
        self, engine: Engine, recorded_run: PersistedRun
    ) -> None:
        """Answering with the first event would say the second was recorded."""
        decision = one_with(engine, recorded_run.run_id, DecisionStatus.EXCEPTION)
        append(engine, recorded_run, decision, ReviewAction.ACKNOWLEDGED, key="key-0001")

        with pytest.raises(IdempotencyConflict) as raised, session_scope(engine) as session:
            ReviewQueueService(session).append_event(
                run_id=recorded_run.run_id,
                decision_id=decision.decision_id,
                action=ReviewAction.ESCALATED,
                decision_fingerprint=certificate_fingerprint(decision),
                idempotency_key="key-0001",
            )

        assert raised.value.code == "idempotency_conflict"

    def test_the_refused_reuse_writes_nothing(
        self, engine: Engine, recorded_run: PersistedRun
    ) -> None:
        """Atomically: one event before, one event after, and it is the first one."""
        decision = one_with(engine, recorded_run.run_id, DecisionStatus.EXCEPTION)
        append(engine, recorded_run, decision, ReviewAction.ACKNOWLEDGED, key="key-0001")

        with pytest.raises(IdempotencyConflict), session_scope(engine) as session:
            ReviewQueueService(session).append_event(
                run_id=recorded_run.run_id,
                decision_id=decision.decision_id,
                action=ReviewAction.CLOSED_WITHOUT_OVERRIDE,
                decision_fingerprint=certificate_fingerprint(decision),
                idempotency_key="key-0001",
            )

        with session_factory(engine)() as session:
            events = ReviewEventRepository(session).events_for_decision(
                recorded_run.run_id, decision.decision_id
            )

        assert [event.action for event in events] == [ReviewAction.ACKNOWLEDGED]

    def test_a_key_taken_between_the_check_and_the_insert_is_handled(
        self, engine: Engine, recorded_run: PersistedRun, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The database constraint is the real guarantee, not the lookup.

        The lookup is made to miss, so the insert hits the unique constraint the
        way a concurrent writer would. Only the savepoint is rolled back, the
        winner is read, and the caller is answered as though its retry had been
        seen in the ordinary way.
        """
        decision = one_with(engine, recorded_run.run_id, DecisionStatus.EXCEPTION)
        append(engine, recorded_run, decision, ReviewAction.ACKNOWLEDGED, key="key-0001")
        monkeypatch.setattr(
            ReviewEventRepository,
            "find_by_idempotency_key",
            _missing_once(ReviewEventRepository.find_by_idempotency_key),
        )

        with session_scope(engine) as session:
            answered = ReviewQueueService(session).append_event(
                run_id=recorded_run.run_id,
                decision_id=decision.decision_id,
                action=ReviewAction.ACKNOWLEDGED,
                decision_fingerprint=certificate_fingerprint(decision),
                idempotency_key="key-0001",
            )

        assert answered.was_created is False
        assert answered.event.action is ReviewAction.ACKNOWLEDGED

    def test_a_raced_key_with_a_different_command_is_still_refused(
        self, engine: Engine, recorded_run: PersistedRun, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Losing the race does not turn a conflicting reuse into a success."""
        decision = one_with(engine, recorded_run.run_id, DecisionStatus.EXCEPTION)
        append(engine, recorded_run, decision, ReviewAction.ACKNOWLEDGED, key="key-0001")
        monkeypatch.setattr(
            ReviewEventRepository,
            "find_by_idempotency_key",
            _missing_once(ReviewEventRepository.find_by_idempotency_key),
        )

        with pytest.raises(IdempotencyConflict), session_scope(engine) as session:
            ReviewQueueService(session).append_event(
                run_id=recorded_run.run_id,
                decision_id=decision.decision_id,
                action=ReviewAction.ESCALATED,
                decision_fingerprint=certificate_fingerprint(decision),
                idempotency_key="key-0001",
            )


type Lookup = Callable[[ReviewEventRepository, str], tuple[ReviewEvent, str] | None]


def _missing_once(original: Lookup) -> Lookup:
    """Return a lookup that reports nothing the first time it is called.

    Simulates a second writer taking the key between this request's check and
    its insert, which is the only way the database constraint is reached in a
    single process.
    """
    seen = {"count": 0}

    def lookup(repository: ReviewEventRepository, key: str) -> tuple[ReviewEvent, str] | None:
        seen["count"] += 1
        if seen["count"] == 1:
            return None
        return original(repository, key)

    return lookup


class TestTheTimeline:
    """What the derived state is built from."""

    def test_events_come_back_in_sequence_order(
        self, engine: Engine, recorded_run: PersistedRun
    ) -> None:
        """Even when every one carries the same timestamp."""
        decision = one_with(engine, recorded_run.run_id, DecisionStatus.EXCEPTION)
        order = [
            ReviewAction.ACKNOWLEDGED,
            ReviewAction.REQUEST_EVIDENCE,
            ReviewAction.ESCALATED,
            ReviewAction.CLOSED_WITHOUT_OVERRIDE,
        ]
        for index, action in enumerate(order):
            append(engine, recorded_run, decision, action, key=f"key-000{index}", at=RECORDED_AT)

        with session_factory(engine)() as session:
            events = ReviewEventRepository(session).events_for_decision(
                recorded_run.run_id, decision.decision_id
            )

        assert [event.action for event in events] == order
        assert [event.sequence for event in events] == [1, 2, 3, 4]
        assert len({event.recorded_at for event in events}) == 1
        assert derive_workflow_state(events) is ReviewWorkflowState.CLOSED_WITHOUT_OVERRIDE

    def test_one_item_s_events_do_not_appear_on_another(
        self, engine: Engine, recorded_run: PersistedRun
    ) -> None:
        """A timeline belongs to the decision it was recorded against."""
        first = one_with(engine, recorded_run.run_id, DecisionStatus.EXCEPTION)
        second = one_with(engine, recorded_run.run_id, DecisionStatus.INSUFFICIENT_EVIDENCE)
        append(engine, recorded_run, first, ReviewAction.ESCALATED, key="key-0001")

        with session_factory(engine)() as session:
            service = ReviewQueueService(session)
            escalated = service.item(recorded_run.run_id, first.decision_id)
            untouched = service.item(recorded_run.run_id, second.decision_id)

        assert escalated is not None
        assert untouched is not None
        assert escalated.workflow_state is ReviewWorkflowState.ESCALATED
        assert untouched.workflow_state is ReviewWorkflowState.OPEN
        assert untouched.events == ()

    def test_a_note_is_stored_and_returned_as_written(
        self, engine: Engine, recorded_run: PersistedRun
    ) -> None:
        """Text, exactly as it arrived, with only the surrounding space removed."""
        decision = one_with(engine, recorded_run.run_id, DecisionStatus.EXCEPTION)
        append(
            engine,
            recorded_run,
            decision,
            ReviewAction.REQUEST_EVIDENCE,
            key="key-0001",
            note="  need the bank statement for 3 March  ",
        )

        with session_factory(engine)() as session:
            item = ReviewQueueService(session).item(recorded_run.run_id, decision.decision_id)

        assert item is not None
        assert item.events[0].note == "need the bank statement for 3 March"

    def test_a_blank_note_is_stored_as_nothing(
        self, engine: Engine, recorded_run: PersistedRun
    ) -> None:
        """Rather than as a timeline entry that renders as an empty line."""
        decision = one_with(engine, recorded_run.run_id, DecisionStatus.EXCEPTION)
        append(engine, recorded_run, decision, ReviewAction.ESCALATED, key="key-0001", note="   ")

        with session_factory(engine)() as session:
            item = ReviewQueueService(session).item(recorded_run.run_id, decision.decision_id)

        assert item is not None
        assert item.events[0].note is None

    def test_the_open_total_falls_as_items_are_closed(
        self, engine: Engine, recorded_run: PersistedRun
    ) -> None:
        """The count a summary shows, derived from the events like everything else."""
        reviewable = [
            decision
            for decision in decisions_of(engine, recorded_run.run_id)
            if decision.status is not DecisionStatus.RESOLVED
        ]
        for index, decision in enumerate(reviewable):
            append(
                engine,
                recorded_run,
                decision,
                ReviewAction.CLOSED_WITHOUT_OVERRIDE,
                key=f"close-{index}-0000",
            )
            with session_factory(engine)() as session:
                page = ReviewQueueService(session).queue(recorded_run.run_id, limit=20, offset=0)

            assert page.total == 3
            assert page.open_total == 3 - (index + 1)

    def test_the_recorded_time_is_the_service_s_clock(
        self, engine: Engine, recorded_run: PersistedRun
    ) -> None:
        """Recorded, and never used to order anything."""
        decision = one_with(engine, recorded_run.run_id, DecisionStatus.EXCEPTION)

        with session_scope(engine) as session:
            appended = ReviewQueueService(session).append_event(
                run_id=recorded_run.run_id,
                decision_id=decision.decision_id,
                action=ReviewAction.ACKNOWLEDGED,
                decision_fingerprint=certificate_fingerprint(decision),
                idempotency_key="key-0001",
            )

        assert appended.event.recorded_at.tzinfo is not None
        assert appended.event.recorded_at <= datetime.now(UTC)


def test_a_session_bound_service_reads_no_other_run(
    engine: Engine, recorded_run: PersistedRun, session: Session
) -> None:
    """A queue is asked for by run, and answers about that run only."""
    assert ReviewQueueService(session).queue("no-such-run", limit=20, offset=0).total == 0
