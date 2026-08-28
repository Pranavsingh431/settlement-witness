"""The review event contract and the projection derived from a history of them.

Nothing here touches a database. The projection is a pure function over events,
which is what makes "derived, never stored" testable rather than merely stated.
"""

from datetime import UTC, datetime

import pytest

from app.domain.decisions import DecisionStatus, ReconciliationDecision
from app.domain.version import DOMAIN_SCHEMA_VERSION
from app.reconciliation.batch import reconcile
from app.review.events import (
    REVIEW_CONTRACT_VERSION,
    REVIEWABLE_STATUSES,
    ReviewAction,
    ReviewEvent,
    ReviewWorkflowState,
    certificate_fingerprint,
    command_fingerprint,
    derive_workflow_state,
    normalise_note,
    state_after,
)
from tests.reconciliation.conftest import index_of, payment_event, payout, settlement_line

SAME_INSTANT = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
"""One timestamp shared by several events, so ordering cannot come from it."""


def event(sequence: int, action: ReviewAction, *, at: datetime = SAME_INSTANT) -> ReviewEvent:
    """Return one event at a given sequence."""
    return ReviewEvent(
        event_id=f"e{sequence}",
        sequence=sequence,
        run_id="run-1",
        decision_id="d-1",
        subject_settlement_line_id="line-1",
        decision_fingerprint="a" * 64,
        action=action,
        note=None,
        recorded_at=at,
    )


class TestTheActionsAreTheWholeVocabulary:
    """Four actions, and none of them resolves anything."""

    def test_there_are_exactly_four(self) -> None:
        """Asserted as a set, so a fifth cannot appear unnoticed."""
        assert {action.value for action in ReviewAction} == {
            "ACKNOWLEDGED",
            "REQUEST_EVIDENCE",
            "ESCALATED",
            "CLOSED_WITHOUT_OVERRIDE",
        }

    @pytest.mark.parametrize(
        "forbidden", ["APPROVE", "APPROVED", "RESOLVE", "RESOLVED", "ACCEPT", "CONFIRM"]
    )
    def test_no_action_approves_or_resolves(self, forbidden: str) -> None:
        """The words this phase must not put in front of a person.

        Checked as substrings of every action name, so `RESOLVE_LINE` would fail
        here as surely as `RESOLVE`.
        """
        for action in ReviewAction:
            assert forbidden not in action.value

    def test_the_only_action_mentioning_override_denies_it(self) -> None:
        """`CLOSED_WITHOUT_OVERRIDE` is the one place the word appears.

        A bare `OVERRIDE` check would fail against that name, which is the name
        that makes the guarantee. So the rule is stated as it means: the word
        may appear, and only ever preceded by "WITHOUT_".
        """
        for action in ReviewAction:
            if "OVERRIDE" in action.value:
                assert "WITHOUT_OVERRIDE" in action.value, action.value

    def test_no_workflow_state_is_a_decision_status(self) -> None:
        """The two vocabularies do not overlap, so neither can be read as the other.

        `CLOSED_WITHOUT_OVERRIDE` is the state a closed item is in and it is not
        `RESOLVED`, which is the only status that says a line is supported.
        """
        states = {state.value for state in ReviewWorkflowState}
        statuses = {status.value for status in DecisionStatus}

        assert states.isdisjoint(statuses)

    def test_the_queue_holds_only_the_two_statuses_that_need_a_person(self) -> None:
        """Not resolved, which needs nothing, and not pending, which is waiting."""
        assert {
            DecisionStatus.EXCEPTION,
            DecisionStatus.INSUFFICIENT_EVIDENCE,
        } == REVIEWABLE_STATUSES


class TestTheProjectionIsDerived:
    """State comes from the events, every time, and from nothing else."""

    def test_no_events_is_open(self) -> None:
        """An item nobody has touched, rather than an item with no state."""
        assert derive_workflow_state([]) is ReviewWorkflowState.OPEN

    @pytest.mark.parametrize("action", list(ReviewAction))
    def test_one_event_gives_that_action_s_state(self, action: ReviewAction) -> None:
        """Every action has a state, so none can be recorded and then ignored."""
        assert derive_workflow_state([event(1, action)]) is state_after(action)

    def test_a_history_resolves_to_the_last_event(self) -> None:
        """Acknowledged, then evidence requested, then escalated."""
        history = [
            event(1, ReviewAction.ACKNOWLEDGED),
            event(2, ReviewAction.REQUEST_EVIDENCE),
            event(3, ReviewAction.ESCALATED),
        ]

        assert derive_workflow_state(history) is ReviewWorkflowState.ESCALATED

    def test_identical_timestamps_still_order_deterministically(self) -> None:
        """Every event in the same instant. Sequence decides, and only sequence."""
        history = [
            event(1, ReviewAction.ESCALATED, at=SAME_INSTANT),
            event(2, ReviewAction.CLOSED_WITHOUT_OVERRIDE, at=SAME_INSTANT),
            event(3, ReviewAction.ACKNOWLEDGED, at=SAME_INSTANT),
        ]

        assert len({one.recorded_at for one in history}) == 1
        assert derive_workflow_state(history) is ReviewWorkflowState.ACKNOWLEDGED

    def test_a_clock_that_moved_backwards_cannot_reorder_history(self) -> None:
        """The last event carries the earliest timestamp and still wins.

        A timestamp is a fact about a machine's clock. Ordering a workflow by it
        would let a clock correction rewrite what a queue currently says.
        """
        history = [
            event(1, ReviewAction.ACKNOWLEDGED, at=datetime(2026, 3, 1, 12, 0, tzinfo=UTC)),
            event(
                2, ReviewAction.CLOSED_WITHOUT_OVERRIDE, at=datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
            ),
        ]

        assert derive_workflow_state(history) is ReviewWorkflowState.CLOSED_WITHOUT_OVERRIDE

    def test_the_input_order_does_not_matter(self) -> None:
        """Shuffled events derive the same state as sorted ones.

        The projection sorts rather than trusting its caller, because one that
        depended on pre-sorted input would report the wrong state silently on
        the day somebody passed it a set.
        """
        history = [
            event(3, ReviewAction.ESCALATED),
            event(1, ReviewAction.ACKNOWLEDGED),
            event(2, ReviewAction.REQUEST_EVIDENCE),
        ]

        assert derive_workflow_state(history) is ReviewWorkflowState.ESCALATED

    def test_an_event_after_a_close_reopens_the_item(self) -> None:
        """Closing says no further work is planned, not that nothing may follow.

        Recorded rather than refused. Somebody changing their mind is a real
        thing, the whole history stays visible, and a queue that could never be
        reopened would be worked around outside the system.
        """
        history = [
            event(1, ReviewAction.CLOSED_WITHOUT_OVERRIDE),
            event(2, ReviewAction.ACKNOWLEDGED),
        ]

        assert derive_workflow_state(history) is ReviewWorkflowState.ACKNOWLEDGED


class TestTheFingerprints:
    """What binds an event to a conclusion, and a retry to a command."""

    @staticmethod
    def _decision(**overrides: object) -> ReconciliationDecision:
        """Return one baseline decision over a small snapshot."""
        facts = [
            payment_event("pe-1", payment_id="pay-1"),
            settlement_line("sl-1", payment_id="pay-1", payout_id="payout-1", **overrides),
            payout("po-1", payout_id="payout-1"),
        ]
        return reconcile(index_of(*facts)).decisions[0]

    def test_it_is_stable_for_the_same_decision(self) -> None:
        """A fingerprint has to survive being computed twice."""
        decision = self._decision()

        assert certificate_fingerprint(decision) == certificate_fingerprint(decision)

    def test_it_is_64_hex_characters(self) -> None:
        """The width the storage constraint expects."""
        assert len(certificate_fingerprint(self._decision())) == 64

    def test_a_different_conclusion_has_a_different_fingerprint(self) -> None:
        """It names the conclusion, not the row.

        Two decisions about the same settlement line under different facts are
        two conclusions, and an event recorded against one must not read as
        though it were about the other.
        """
        first = certificate_fingerprint(self._decision())
        second = certificate_fingerprint(self._decision(net_minor=1))

        assert first != second

    def test_a_command_fingerprint_covers_every_part_of_the_command(self) -> None:
        """Change any field and the digest moves, so a reuse cannot pass as a retry."""
        base = {
            "run_id": "run-1",
            "decision_id": "d-1",
            "decision_fingerprint": "a" * 64,
            "action": ReviewAction.ACKNOWLEDGED,
            "note": "one",
        }
        original = command_fingerprint(**base)  # type: ignore[arg-type]

        for field, value in (
            ("run_id", "run-2"),
            ("decision_id", "d-2"),
            ("decision_fingerprint", "b" * 64),
            ("action", ReviewAction.ESCALATED),
            ("note", "two"),
        ):
            assert command_fingerprint(**{**base, field: value}) != original, field  # type: ignore[arg-type]

    def test_an_absent_note_and_a_blank_one_are_the_same_command(self) -> None:
        """Because a blank note is normalised away before it is fingerprinted."""
        assert normalise_note("   ") is None
        assert normalise_note(None) is None
        assert normalise_note("  waiting on the bank  ") == "waiting on the bank"


class TestTheContractIsItsOwn:
    """A review event is not part of the reconciliation contract."""

    def test_it_carries_its_own_version(self) -> None:
        """So a reader can tell which shape of event they are holding."""
        assert REVIEW_CONTRACT_VERSION == "1.0.0"

    def test_it_is_not_the_domain_schema_version(self) -> None:
        """Bumping the domain contract for this would tell every reader of a
        stored decision that the meaning of their record had changed."""
        assert len({REVIEW_CONTRACT_VERSION, DOMAIN_SCHEMA_VERSION}) == 2

    def test_an_event_records_no_reviewer(self) -> None:
        """There is no authentication, so there is nobody to name.

        Asserted on the model rather than left to a docstring, because an actor
        field added later without an authentication story is exactly the fiction
        this is meant to prevent.
        """
        fields = set(ReviewEvent.model_fields)

        assert not fields & {"actor", "actor_id", "reviewer", "reviewer_id", "user", "user_id"}

    def test_an_event_carries_no_status_of_its_own(self) -> None:
        """It annotates a conclusion. It does not hold one."""
        assert "status" not in ReviewEvent.model_fields
