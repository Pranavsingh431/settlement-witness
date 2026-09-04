"""Reconciliation run endpoints.

One create path and four read paths. There is no endpoint here that changes a
stored decision, and there is no plan for one: a mutable resolve endpoint would
make a stored conclusion editable, and the whole contract rests on conclusions
being immutable and replayable.

What people do about a conclusion lives in `app.api.review`, under its own
prefix, and appends beside a decision rather than to it. The separation is
deliberate: it keeps the claim that every reconciliation route is a read
checkable by looking at this file.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_session
from app.api.schemas import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    DecisionView,
    ErrorEnvelope,
    RunDetail,
    RunPage,
    RunSummary,
    RunWorkboard,
)
from app.closure.evidence_requests import EvidenceRequestPackage, build_evidence_request
from app.closure.triage import build_workboard
from app.domain.codes import ExceptionCode
from app.domain.decisions import DecisionStatus
from app.reconciliation.runs import ReconciliationRunRepository, ReconciliationRunService
from app.storage.repository import SourceFactRepository

router = APIRouter(prefix="/v1/reconciliation", tags=["reconciliation"])

NOT_FOUND: dict[int | str, dict[str, object]] = {
    404: {"model": ErrorEnvelope, "description": "No such run or decision"}
}


def _not_found(what: str, identifier: str) -> HTTPException:
    """Return a 404 that names what was missing without echoing anything else."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "not_found", "detail": f"no {what} with id {identifier!r}"},
    )


@router.post(
    "/runs",
    response_model=RunSummary,
    responses={
        200: {"model": RunSummary, "description": "An identical run already existed"},
        201: {"model": RunSummary, "description": "A new run was recorded"},
        409: {"model": ErrorEnvelope, "description": "There is nothing to reconcile"},
    },
    summary="Reconcile the accepted source facts and record the result",
)
def create_run(response: Response, session: Annotated[Session, Depends(get_session)]) -> RunSummary:
    """Run the deterministic baseline over every accepted source fact.

    Idempotent. Reconciling the same facts again under the same rule versions
    returns the run already recorded, with status 200 rather than 201, instead
    of writing a second row describing the same conclusion.
    """
    index = SourceFactRepository(session).fact_index()
    if not index:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "no_facts",
                "detail": "the store holds no accepted source facts to reconcile",
            },
        )

    run = ReconciliationRunService(session).create_run(index)
    response.status_code = status.HTTP_201_CREATED if run.was_created else status.HTTP_200_OK
    return RunSummary.of(run)


@router.get(
    "/runs",
    response_model=RunPage,
    summary="List reconciliation runs, newest first",
)
def list_runs(
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RunPage:
    """Return a page of run summaries.

    Ordered by created-at descending, then by run ID, so a page boundary lands
    in the same place on every call.
    """
    repository = ReconciliationRunRepository(session)
    return RunPage(
        runs=[RunSummary.of(run) for run in repository.list_runs(limit=limit, offset=offset)],
        total=repository.count(),
        limit=limit,
        offset=offset,
    )


@router.get(
    "/runs/{run_id}",
    response_model=RunDetail,
    responses=NOT_FOUND,
    summary="Read one run and its decisions",
)
def get_run(
    run_id: str,
    session: Annotated[Session, Depends(get_session)],
    decision_status: Annotated[
        DecisionStatus | None,
        Query(alias="status", description="Return only decisions with this status"),
    ] = None,
    exception_code: Annotated[
        ExceptionCode | None,
        Query(description="Return only decisions carrying this exception code"),
    ] = None,
) -> RunDetail:
    """Return a run summary and its decisions, ordered by settlement line ID.

    Filters narrow the decisions returned and never the summary counts, which
    always describe the whole run. `filtered` says which view a caller is
    looking at, so a narrowed list cannot be mistaken for the complete one.
    """
    repository = ReconciliationRunRepository(session)
    run = repository.get(run_id)
    if run is None:
        raise _not_found("run", run_id)

    decisions = repository.decisions_for(
        run_id, status=decision_status, exception_code=exception_code
    )
    return RunDetail(
        run=RunSummary.of(run),
        decisions=[DecisionView.of(decision) for decision in decisions],
        filtered=decision_status is not None or exception_code is not None,
    )


@router.get(
    "/runs/{run_id}/workboard",
    response_model=RunWorkboard,
    responses=NOT_FOUND,
    summary="Prioritise unresolved work within each source currency",
)
def get_workboard(run_id: str, session: Annotated[Session, Depends(get_session)]) -> RunWorkboard:
    """Return source-pinned work priority without a cross-currency total.

    The workboard is derived when served. It writes neither a decision nor a
    priority row, and it checks the original cited settlement record and hash
    before using its declared net value.
    """
    repository = ReconciliationRunRepository(session)
    run = repository.get(run_id)
    if run is None:
        raise _not_found("run", run_id)

    return RunWorkboard(
        run_id=run.run_id,
        snapshot_fingerprint=run.snapshot_fingerprint,
        workboard=build_workboard(
            repository.decisions_for(run_id), SourceFactRepository(session).fact_index()
        ),
    )


@router.get(
    "/runs/{run_id}/decisions/{decision_id}/evidence-request",
    response_model=EvidenceRequestPackage,
    responses={
        **NOT_FOUND,
        409: {
            "model": ErrorEnvelope,
            "description": "The recorded decision already resolved and needs no evidence request",
        },
    },
    summary="Download a non-authoritative request for the evidence needed next",
)
def get_evidence_request(
    run_id: str,
    decision_id: str,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> EvidenceRequestPackage:
    """Return an operational handoff for an unresolved decision.

    The response contains cited record identities and acceptance conditions,
    never raw payloads or a state-changing command. A generic filename avoids
    reflecting record identifiers into a response header.
    """
    repository = ReconciliationRunRepository(session)
    if repository.get(run_id) is None:
        raise _not_found("run", run_id)
    decision = repository.find_decision(run_id, decision_id)
    if decision is None:
        raise _not_found("decision", decision_id)
    try:
        package = build_evidence_request(decision)
    except ValueError as refusal:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "evidence_request_not_needed", "detail": str(refusal)},
        ) from refusal

    response.headers["Content-Disposition"] = 'attachment; filename="evidence-request.json"'
    response.headers["Cache-Control"] = "no-store"
    return package


@router.get(
    "/runs/{run_id}/decisions/{decision_id}",
    response_model=DecisionView,
    responses=NOT_FOUND,
    summary="Read one decision with its evidence and invariant certificate",
)
def get_decision(
    run_id: str, decision_id: str, session: Annotated[Session, Depends(get_session)]
) -> DecisionView:
    """Return one decision of one run.

    The decision is rebuilt through the domain model on the way out, so a row
    that no longer satisfies the contract fails here rather than being served as
    though it did.
    """
    repository = ReconciliationRunRepository(session)
    if repository.get(run_id) is None:
        raise _not_found("run", run_id)

    decision = repository.find_decision(run_id, decision_id)
    if decision is None:
        raise _not_found("decision", decision_id)
    return DecisionView.of(decision)
