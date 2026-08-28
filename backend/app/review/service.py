"""Reading the review queue and appending review events.

Two responsibilities, deliberately in one place because they share exactly one
rule: nothing here may touch a recorded decision. There is no method on this
repository that updates one, and the table it writes to has database triggers
refusing UPDATE and DELETE, so the guarantee does not depend on this file being
read carefully.

A command is refused for five reasons, and each of them writes nothing:

- the run or the decision does not exist;
- the decision is not one this queue holds, which is every status except
  `EXCEPTION` and `INSUFFICIENT_EVIDENCE`;
- the fingerprint the caller echoed back is not this decision's, which means
  they are acting on something other than what they were looking at;
- the idempotency key was used before for a different command;
- the note is longer than a note may be.

The fourth is the interesting one. A retry of the same command returns the
original event rather than recording a second one, and a different command under
a used key is refused rather than quietly answered with the first event, because
answering would tell a caller its action had been recorded when it had not.
"""

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.decisions import ReconciliationDecision
from app.reconciliation.runs import ReconciliationRunRepository
from app.review.events import (
    REVIEWABLE_STATUSES,
    ReviewAction,
    ReviewEvent,
    ReviewWorkflowState,
    certificate_fingerprint,
    command_fingerprint,
    derive_workflow_state,
    normalise_note,
)
from app.storage.models import ReviewEventRow


class ReviewRefusal(ValueError):
    """A command that was refused. Nothing was written.

    A `ValueError` so it reaches the application's existing handler as a 422 if
    it ever escapes uncaught, rather than as a 500. The API translates each
    subclass into its own status and code before that happens.
    """

    def __init__(self, code: str, detail: str) -> None:
        """Record the machine code and the sentence written for a person."""
        super().__init__(detail)
        self.code = code
        self.detail = detail


class TargetNotReviewable(ReviewRefusal):
    """The decision exists and does not belong in this queue."""


class StaleCertificate(ReviewRefusal):
    """The caller echoed a fingerprint that is not this decision's."""


class IdempotencyConflict(ReviewRefusal):
    """The key was used before, for a different command."""


class ReviewQueueItem(BaseModel):
    """One reviewable decision, with the workflow recorded beside it.

    The decision is the baseline's, untouched. `workflow_state` is derived from
    `events` and describes the queue, not the conclusion. Both are carried
    together because the whole point of this screen is that a reader sees them
    side by side and cannot mistake one for the other.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    decision: ReconciliationDecision
    decision_fingerprint: str
    workflow_state: ReviewWorkflowState
    events: tuple[ReviewEvent, ...]


class ReviewQueueSlice(BaseModel):
    """One page of the queue, and two counts describing the whole of it.

    Both counts are of the queue rather than of the run. Reporting the run's
    decision count here would overstate the work by every resolved line, which
    is the number a queue exists to exclude.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[ReviewQueueItem, ...]
    total: int
    """How many reviewable decisions the run holds."""

    open_total: int
    """How many of those are not closed.

    Derived from the events like every other state here. Carried so a summary
    can say how much work is left without paging through the queue to count."""


class AppendedReviewEvent(BaseModel):
    """An event, and whether this call recorded it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event: ReviewEvent
    workflow_state: ReviewWorkflowState
    was_created: bool
    """False when a retry returned the event the first call recorded."""


def _to_event(row: ReviewEventRow) -> ReviewEvent:
    """Return a stored row as a domain level record."""
    return ReviewEvent(
        event_id=row.event_id,
        sequence=row.sequence,
        run_id=row.run_id,
        decision_id=row.decision_id,
        subject_settlement_line_id=row.subject_settlement_line_id,
        decision_fingerprint=row.decision_fingerprint,
        action=ReviewAction(row.action),
        note=row.note,
        recorded_at=_as_utc(row.recorded_at),
    )


def _as_utc(value: datetime) -> datetime:
    """Return a stored timestamp as an aware UTC datetime.

    SQLite has no timestamp type, so a datetime comes back without its offset.
    Everything written here was UTC before storage.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class ReviewEventRepository:
    """Read and append review events. There is no way to change one."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def events_for_decision(self, run_id: str, decision_id: str) -> tuple[ReviewEvent, ...]:
        """Return one decision's events in the order the database assigned."""
        statement = (
            select(ReviewEventRow)
            .where(ReviewEventRow.run_id == run_id, ReviewEventRow.decision_id == decision_id)
            .order_by(ReviewEventRow.sequence)
        )
        return tuple(_to_event(row) for row in self._session.scalars(statement))

    def events_for_run(self, run_id: str) -> dict[str, tuple[ReviewEvent, ...]]:
        """Return every event of one run, grouped by decision.

        One query for a whole page rather than one per item, because a queue of
        fifty items would otherwise be fifty round trips to say that most of
        them have no events at all.
        """
        statement = (
            select(ReviewEventRow)
            .where(ReviewEventRow.run_id == run_id)
            .order_by(ReviewEventRow.sequence)
        )
        grouped: dict[str, list[ReviewEvent]] = {}
        for row in self._session.scalars(statement):
            grouped.setdefault(row.decision_id, []).append(_to_event(row))
        return {decision_id: tuple(events) for decision_id, events in grouped.items()}

    def find_by_idempotency_key(self, key: str) -> tuple[ReviewEvent, str] | None:
        """Return the event recorded under this key and the command that made it."""
        row = self._session.scalars(
            select(ReviewEventRow).where(ReviewEventRow.idempotency_key == key)
        ).one_or_none()
        return (_to_event(row), row.command_fingerprint) if row is not None else None

    def count_for_run(self, run_id: str) -> int:
        """Return how many events one run has attracted."""
        total = self._session.scalar(
            select(func.count()).select_from(ReviewEventRow).where(ReviewEventRow.run_id == run_id)
        )
        return int(total or 0)

    def append(
        self,
        *,
        run_id: str,
        decision: ReconciliationDecision,
        decision_fingerprint: str,
        action: ReviewAction,
        note: str | None,
        idempotency_key: str,
        fingerprint: str,
        now: datetime,
    ) -> ReviewEvent:
        """Append one event. The caller owns the transaction.

        Raises:
            IntegrityError: When the key was taken between the service's check
                and this insert.
        """
        row = ReviewEventRow(
            event_id=uuid4().hex,
            run_id=run_id,
            decision_id=decision.decision_id,
            subject_settlement_line_id=decision.subject_settlement_line_id,
            decision_fingerprint=decision_fingerprint,
            action=action.value,
            note=note,
            idempotency_key=idempotency_key,
            command_fingerprint=fingerprint,
            recorded_at=now,
        )
        self._session.add(row)
        self._session.flush()
        return _to_event(row)


class ReviewQueueService:
    """The review queue over one recorded run, and the events appended to it."""

    def __init__(self, session: Session, *, now: datetime | None = None) -> None:
        """Create a service bound to one session.

        Args:
            session: The unit of work events are written in.
            now: The timestamp to record. Passed in by tests so a recorded time
                is a fact about the test rather than about when it ran.
        """
        self._session = session
        self._runs = ReconciliationRunRepository(session)
        self._events = ReviewEventRepository(session)
        self._now = now

    def _timestamp(self) -> datetime:
        """Return the time to record against an event."""
        return self._now if self._now is not None else datetime.now(UTC)

    def reviewable_decisions(self, run_id: str) -> tuple[ReconciliationDecision, ...]:
        """Return the run's decisions that need a person, in a fixed order.

        Ordered by settlement line ID, which is how the baseline emits them and
        how every other view of a run lists them. A queue whose order depended
        on when somebody last looked at an item would put a page boundary in a
        different place on every call.
        """
        decisions = self._runs.decisions_for(run_id)
        return tuple(decision for decision in decisions if decision.status in REVIEWABLE_STATUSES)

    def queue(self, run_id: str, *, limit: int, offset: int) -> ReviewQueueSlice:
        """Return a page of the queue and two counts describing the whole of it.

        Args:
            run_id: The recorded run to read.
            limit: How many items to return.
            offset: Where to start.

        Returns:
            The page, the size of the queue, and how many of its items are not
            closed. Both counts describe the whole queue rather than the page,
            because a caller paging through needs to know what it is paging
            through, and reporting the run's decision count would overstate the
            work by every resolved line.
        """
        reviewable = self.reviewable_decisions(run_id)
        by_decision = self._events.events_for_run(run_id)
        open_total = sum(
            1
            for decision in reviewable
            if derive_workflow_state(by_decision.get(decision.decision_id, ()))
            is not ReviewWorkflowState.CLOSED_WITHOUT_OVERRIDE
        )
        return ReviewQueueSlice(
            items=tuple(
                self._item(run_id, decision, by_decision)
                for decision in reviewable[offset : offset + limit]
            ),
            total=len(reviewable),
            open_total=open_total,
        )

    def item(self, run_id: str, decision_id: str) -> ReviewQueueItem | None:
        """Return one queue item, or None when it is not in the queue.

        None covers both "no such decision" and "that decision is not
        reviewable". The caller distinguishes them, because a resolved line is a
        different answer from a missing one.
        """
        decision = self._runs.find_decision(run_id, decision_id)
        if decision is None or decision.status not in REVIEWABLE_STATUSES:
            return None
        return self._item(run_id, decision, {})

    def _item(
        self,
        run_id: str,
        decision: ReconciliationDecision,
        cached: dict[str, tuple[ReviewEvent, ...]],
    ) -> ReviewQueueItem:
        """Return one item, deriving its state from its events every time."""
        events = cached.get(decision.decision_id)
        if events is None:
            events = self._events.events_for_decision(run_id, decision.decision_id)
        return ReviewQueueItem(
            run_id=run_id,
            decision=decision,
            decision_fingerprint=certificate_fingerprint(decision),
            workflow_state=derive_workflow_state(events),
            events=events,
        )

    def append_event(
        self,
        *,
        run_id: str,
        decision_id: str,
        action: ReviewAction,
        decision_fingerprint: str,
        idempotency_key: str,
        note: str | None = None,
    ) -> AppendedReviewEvent:
        """Record one review action, or explain why it was not recorded.

        Args:
            run_id: The recorded run the decision belongs to.
            decision_id: The decision being reviewed.
            action: What the reviewer did.
            decision_fingerprint: The fingerprint the reviewer was shown. Checked
                against the stored decision, so an action aimed at a conclusion
                the caller last saw elsewhere is refused rather than recorded
                against this one.
            idempotency_key: The caller's key for this command.
            note: An optional sentence. Blank is the same as absent.

        Returns:
            The event, and whether this call recorded it.

        Raises:
            TargetNotReviewable: The decision is not one this queue holds.
            StaleCertificate: The fingerprint is not this decision's.
            IdempotencyConflict: The key was used for a different command.
        """
        decision = self._runs.find_decision(run_id, decision_id)
        if decision is None:
            message = f"no decision with id {decision_id!r} in run {run_id!r}"
            raise TargetNotReviewable("decision_not_found", message)

        if decision.status not in REVIEWABLE_STATUSES:
            message = (
                f"decision {decision_id!r} is {decision.status.value} and is not in the "
                "review queue; only EXCEPTION and INSUFFICIENT_EVIDENCE decisions are"
            )
            raise TargetNotReviewable("not_reviewable", message)

        actual = certificate_fingerprint(decision)
        if decision_fingerprint != actual:
            message = (
                "the decision fingerprint does not match the recorded decision; "
                "reload the item and act on what is currently stored"
            )
            raise StaleCertificate("stale_certificate", message)

        cleaned = normalise_note(note)
        fingerprint = command_fingerprint(
            run_id=run_id,
            decision_id=decision_id,
            decision_fingerprint=actual,
            action=action,
            note=cleaned,
        )

        recorded = self._events.find_by_idempotency_key(idempotency_key)
        if recorded is not None:
            return self._replay(recorded, fingerprint, run_id, decision_id)

        savepoint = self._session.begin_nested()
        try:
            event = self._events.append(
                run_id=run_id,
                decision=decision,
                decision_fingerprint=actual,
                action=action,
                note=cleaned,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                now=self._timestamp(),
            )
        except IntegrityError:
            # The key was taken between the read above and this insert. Only the
            # savepoint is rolled back, so nothing partial survives, and the
            # winner is then read and compared like any other retry.
            savepoint.rollback()
            raced = self._events.find_by_idempotency_key(idempotency_key)
            if raced is None:  # pragma: no cover - the constraint is the only writer here
                raise
            return self._replay(raced, fingerprint, run_id, decision_id)
        savepoint.commit()

        return AppendedReviewEvent(
            event=event,
            workflow_state=derive_workflow_state(
                self._events.events_for_decision(run_id, decision_id)
            ),
            was_created=True,
        )

    def _replay(
        self,
        recorded: tuple[ReviewEvent, str],
        fingerprint: str,
        run_id: str,
        decision_id: str,
    ) -> AppendedReviewEvent:
        """Return the original event for a retry, or refuse a reused key.

        The comparison is over what the command asked for, not over what the
        event looks like, so a caller retrying an identical command gets its
        event back and a caller reusing a key for a different action is told.
        """
        event, made_by = recorded
        if made_by != fingerprint:
            message = (
                "this idempotency key was used for a different review command; "
                "use a new key, or retry the original command unchanged"
            )
            raise IdempotencyConflict("idempotency_conflict", message)
        return AppendedReviewEvent(
            event=event,
            workflow_state=derive_workflow_state(
                self._events.events_for_decision(run_id, decision_id)
            ),
            was_created=False,
        )
