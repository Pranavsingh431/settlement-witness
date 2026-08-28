"""The human review queue.

Mounted under its own prefix rather than beside the reconciliation routes,
because the separation is the design. `/v1/reconciliation` serves what the
baseline concluded and has no verb that changes any of it. `/v1/review` serves
what people did about those conclusions, and has one verb that appends to that
record and none that touches the other.

Nothing here can change a decision. There is no field in the command that could
carry a status, no code path that writes to `reconciliation_decisions`, and the
review table refuses UPDATE and DELETE at the database. A closed review still
serves the original `EXCEPTION` or `INSUFFICIENT_EVIDENCE`, and every response
says so in `baseline_unchanged_note`.

There is no authentication, so an event records no reviewer. That is a gap and
it is stated as one: an actor column filled from an unauthenticated request
would be a fiction that later reads as an audit trail.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_session
from app.api.schemas import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    ErrorEnvelope,
    ReviewEventCommand,
    ReviewEventReceipt,
    ReviewQueueItemView,
    ReviewQueuePage,
)
from app.reconciliation.runs import ReconciliationRunRepository
from app.review.service import (
    IdempotencyConflict,
    ReviewQueueService,
    ReviewRefusal,
    StaleCertificate,
    TargetNotReviewable,
)

router = APIRouter(prefix="/v1/review", tags=["review"])

NOT_FOUND: dict[int | str, dict[str, object]] = {
    404: {"model": ErrorEnvelope, "description": "No such run or queue item"}
}

CONFLICT: dict[int | str, dict[str, object]] = {
    404: {"model": ErrorEnvelope, "description": "No such run or decision"},
    409: {
        "model": ErrorEnvelope,
        "description": (
            "The decision is not reviewable, the fingerprint is stale, or the "
            "idempotency key was used for a different command"
        ),
    },
}


def _not_found(what: str, identifier: str) -> HTTPException:
    """Return a 404 that names what was missing without echoing anything else."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "not_found", "detail": f"no {what} with id {identifier!r}"},
    )


def _refused(refusal: ReviewRefusal, code: int) -> HTTPException:
    """Return the refusal in the API's envelope, with its own machine code."""
    return HTTPException(status_code=code, detail={"error": refusal.code, "detail": refusal.detail})


@router.get(
    "/runs/{run_id}/queue",
    response_model=ReviewQueuePage,
    responses=NOT_FOUND,
    summary="List the decisions of one run that need a person",
)
def get_queue(
    run_id: str,
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ReviewQueuePage:
    """Return a page of the queue, ordered by settlement line ID.

    Only `EXCEPTION` and `INSUFFICIENT_EVIDENCE` decisions are here. A resolved
    line needs nothing, and a pending one is waiting for information that is
    expected to arrive rather than for somebody to decide something.

    The order is the settlement line ID, which is how the baseline emits
    decisions and how every other view lists them. It does not move when
    somebody acts on an item, so a page boundary lands in the same place on
    every call and paging cannot skip an item because another was closed.
    """
    if ReconciliationRunRepository(session).get(run_id) is None:
        raise _not_found("run", run_id)

    page = ReviewQueueService(session).queue(run_id, limit=limit, offset=offset)
    return ReviewQueuePage.of(run_id, page, limit=limit, offset=offset)


@router.get(
    "/runs/{run_id}/queue/{decision_id}",
    response_model=ReviewQueueItemView,
    responses=NOT_FOUND,
    summary="Read one queue item with its certificate and review timeline",
)
def get_queue_item(
    run_id: str, decision_id: str, session: Annotated[Session, Depends(get_session)]
) -> ReviewQueueItemView:
    """Return one item of the queue.

    A decision that exists and is resolved is a 404 here rather than a 200 with
    an empty timeline. It is not in this queue, and serving it would put a
    resolved line on a screen whose whole purpose is unresolved ones.
    """
    if ReconciliationRunRepository(session).get(run_id) is None:
        raise _not_found("run", run_id)

    item = ReviewQueueService(session).item(run_id, decision_id)
    if item is None:
        raise _not_found("queue item", decision_id)
    return ReviewQueueItemView.of(item)


@router.post(
    "/runs/{run_id}/queue/{decision_id}/events",
    response_model=ReviewEventReceipt,
    responses={
        200: {"model": ReviewEventReceipt, "description": "A retry returned the original event"},
        201: {"model": ReviewEventReceipt, "description": "A new review event was recorded"},
        **CONFLICT,
    },
    summary="Append one human review event beside a decision",
)
def append_review_event(
    run_id: str,
    decision_id: str,
    command: ReviewEventCommand,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> ReviewEventReceipt:
    """Record what a reviewer did. The decision is untouched.

    Four actions and no fifth. None of them changes a status, and the one named
    `CLOSED_WITHOUT_OVERRIDE` is named that way because that is what it does:
    the item leaves the working queue and the line is still whatever the
    baseline found it to be.

    Idempotent on `idempotency_key`. Retrying the same command returns the event
    the first call recorded, with status 200 rather than 201. Reusing the key
    for a different command is refused with 409 and writes nothing, because
    answering with the first event would tell a caller its second action had
    been recorded.
    """
    if ReconciliationRunRepository(session).get(run_id) is None:
        raise _not_found("run", run_id)

    service = ReviewQueueService(session)
    try:
        appended = service.append_event(
            run_id=run_id,
            decision_id=decision_id,
            action=command.action,
            decision_fingerprint=command.decision_fingerprint,
            idempotency_key=command.idempotency_key,
            note=command.note,
        )
    except TargetNotReviewable as refusal:
        if refusal.code == "decision_not_found":
            raise _not_found("decision", decision_id) from refusal
        raise _refused(refusal, status.HTTP_409_CONFLICT) from refusal
    except (StaleCertificate, IdempotencyConflict) as refusal:
        raise _refused(refusal, status.HTTP_409_CONFLICT) from refusal

    item = service.item(run_id, decision_id)
    if item is None:  # pragma: no cover - the append above proved it is reviewable
        raise _not_found("queue item", decision_id)

    response.status_code = status.HTTP_201_CREATED if appended.was_created else status.HTTP_200_OK
    return ReviewEventReceipt.of(appended, item.decision.status)
