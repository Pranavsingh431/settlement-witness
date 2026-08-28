"""What a reviewer did, as an append-only record beside a decision.

A review event is an operational annotation. It is not evidence, it is not a
decision, and it cannot become either.

**It changes nothing.** A recorded decision's status, exception codes, reason
codes, invariant results, evidence, snapshot fingerprint and run key are
whatever the baseline wrote. No action here touches any of them, and there is
no action that could: the four actions describe what a person did about a
finding, not what the finding is.

**There is no action that resolves anything.** `CLOSED_WITHOUT_OVERRIDE` is
named the way it is because the name is the guarantee. A queue item can be
closed operationally, meaning nobody intends to work on it further, and the
line it points at is still an `EXCEPTION` or `INSUFFICIENT_EVIDENCE` afterwards.
Anything that genuinely resolved such a line would be a new source record or
externally verified evidence, imported and reconciled again, producing a new
run. It would never be a button.

**There is no reviewer identity.** This application has no authentication, so
there is nobody to attribute an event to. Storing a name typed into a box, or a
constant like "operator", would look like an audit trail and be a fiction. The
absence is recorded here rather than papered over, and it is the reason this is
a workflow record rather than an accountability one.

**Ordering comes from the database.** Events are ordered by the sequence the
database assigns on insert, never by their timestamps. Two events recorded in
the same millisecond have an order, and a clock that moves backwards cannot
reorder history.
"""

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from app.domain.decisions import DecisionStatus, ReconciliationDecision

REVIEW_CONTRACT_VERSION: Final = "1.0.0"
"""The shape of a review event and of the state derived from a history of them.

Its own version, deliberately not the domain schema version. A review event is
not part of the reconciliation contract: nothing in a decision refers to one,
and the baseline would produce identical output if this package did not exist.
Bumping the domain contract for it would tell every reader of a stored decision
that the meaning of their record had changed, when it had not."""

MAX_NOTE_LENGTH: Final = 500
"""How long a reviewer's note may be.

Bounded because it is stored, served and rendered. A note is a sentence about
what a person is waiting for, not a document."""


class ReviewAction(StrEnum):
    """What a reviewer did. Four actions, and none of them is an override."""

    ACKNOWLEDGED = "ACKNOWLEDGED"
    """Somebody has seen this and taken responsibility for looking at it."""

    REQUEST_EVIDENCE = "REQUEST_EVIDENCE"
    """More records are needed before anything can be said.

    The request is the record. Whatever arrives arrives as an imported source
    document and is reconciled into a new run, which is the only thing that can
    change a conclusion."""

    ESCALATED = "ESCALATED"
    """Passed to somebody else, or to a process outside this system."""

    CLOSED_WITHOUT_OVERRIDE = "CLOSED_WITHOUT_OVERRIDE"
    """No further operational work is planned on this item.

    The name says the whole of what it means. The decision is unchanged, the
    status is unchanged, and the line is still whatever the baseline found it to
    be. Closing a queue item is a statement about a work queue."""


class ReviewWorkflowState(StrEnum):
    """Where an item stands operationally. Derived, never stored."""

    OPEN = "OPEN"
    """Nobody has recorded anything about this item yet."""

    ACKNOWLEDGED = "ACKNOWLEDGED"
    ESCALATED = "ESCALATED"
    WAITING_FOR_EVIDENCE = "WAITING_FOR_EVIDENCE"
    CLOSED_WITHOUT_OVERRIDE = "CLOSED_WITHOUT_OVERRIDE"


#: What each action leaves the item in. A total mapping, so a new action cannot
#: be added without deciding what it means for the projection.
_STATE_AFTER: Final[dict[ReviewAction, ReviewWorkflowState]] = {
    ReviewAction.ACKNOWLEDGED: ReviewWorkflowState.ACKNOWLEDGED,
    ReviewAction.REQUEST_EVIDENCE: ReviewWorkflowState.WAITING_FOR_EVIDENCE,
    ReviewAction.ESCALATED: ReviewWorkflowState.ESCALATED,
    ReviewAction.CLOSED_WITHOUT_OVERRIDE: ReviewWorkflowState.CLOSED_WITHOUT_OVERRIDE,
}

REVIEWABLE_STATUSES: Final = frozenset(
    {DecisionStatus.EXCEPTION, DecisionStatus.INSUFFICIENT_EVIDENCE}
)
"""The two statuses that need a person.

`RESOLVED` needs nothing: the evidence is complete and every invariant held.
`PENDING` is inside its expected window and is waiting for information that is
expected to arrive, which is the system waiting rather than a person deciding.
Neither enters the queue and neither accepts an event."""


class ReviewEvent(BaseModel):
    """One recorded review action, exactly as it was stored.

    Carries no reviewer, because there is no authentication in this application
    and inventing one would be worse than the gap.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    sequence: int = Field(ge=1)
    """The order the database assigned. What the projection folds over."""

    run_id: str
    decision_id: str
    subject_settlement_line_id: str
    decision_fingerprint: str = Field(min_length=64, max_length=64)
    """A digest of the decision this reviewer was looking at.

    Recorded so an event cannot later be read as though it were about a
    different conclusion. Decisions are immutable, so this is a binding rather
    than a change detector: what it catches is a command aimed at the wrong
    decision, or at one the caller last saw in another run."""

    action: ReviewAction
    note: str | None = Field(default=None, max_length=MAX_NOTE_LENGTH)
    """A sentence from a person, or nothing. Stored and served as plain text."""

    recorded_at: datetime
    """When it was recorded. Never used for ordering. See the module docstring."""


def certificate_fingerprint(decision: ReconciliationDecision) -> str:
    """Return a digest of one decision, exactly as it is stored and served.

    Taken over the whole canonical serialisation rather than over the identifier
    alone, so the fingerprint names the conclusion and not just the row. Two
    runs over different facts produce different decision IDs and different
    fingerprints; a caller echoing back an old fingerprint is told so rather
    than having its action recorded against the current one.

    Args:
        decision: The decision to fingerprint.

    Returns:
        The hex digest, 64 characters.
    """
    canonical = json.dumps(decision.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def command_fingerprint(
    *,
    run_id: str,
    decision_id: str,
    decision_fingerprint: str,
    action: ReviewAction,
    note: str | None,
) -> str:
    """Return a digest of what a command asked for.

    Stored beside the idempotency key so a retry can be told from a reuse. A
    retry of the same command has the same digest and returns the original
    event. A different command under the same key has a different digest and is
    refused, because silently returning the first event would tell a caller its
    second, different action had been recorded.
    """
    digest = hashlib.sha256()
    for part in (run_id, decision_id, decision_fingerprint, action.value, note or ""):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def normalise_note(note: str | None) -> str | None:
    """Return a note with surrounding whitespace removed, or None if it is blank.

    A note of spaces is an empty note. Storing it as one would put a row in the
    timeline that renders as nothing and reads as a missing message.
    """
    if note is None:
        return None
    stripped = note.strip()
    return stripped or None


def derive_workflow_state(events: Sequence[ReviewEvent]) -> ReviewWorkflowState:
    """Return the operational state implied by a history of events.

    Mechanically derived, every time, from the whole history. There is no stored
    current-status column, because a stored status and an event log can disagree
    and then somebody has to decide which one is true.

    The rule is the last event wins, in database sequence order. That includes
    an event after a close: closing says no further work is planned, and a later
    acknowledgement says somebody changed their mind, which is a real thing that
    happens and is visible in the timeline either way.

    Args:
        events: The item's events. Ordered by sequence by the caller; this
            sorts again rather than trusting that, because a projection that
            depends on its input being pre-sorted is a projection that silently
            reports the wrong state when it is not.

    Returns:
        `OPEN` when there are no events, otherwise the state the last one left.
    """
    if not events:
        return ReviewWorkflowState.OPEN
    latest = max(events, key=lambda event: event.sequence)
    return _STATE_AFTER[latest.action]


def state_after(action: ReviewAction) -> ReviewWorkflowState:
    """Return the state one action leaves an item in."""
    return _STATE_AFTER[action]
