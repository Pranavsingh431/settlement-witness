"""Bank finality endpoints.

Under their own prefix, beside `/v1/reconciliation` rather than inside it. The
separation is the design: a settlement decision says whether the provider's own
records agree, and bank finality says whether a bank statement shows the payout
arriving. They are two conclusions about the same facts from different evidence,
and a line can be `RESOLVED` with no bank evidence at all.

Nothing here can change a decision. There is no code path from these routes to
`reconciliation_decisions`, no field in any response that a decision reads, and
no value of `BankFinalityOutcome` that is also a `DecisionStatus`. A client
cannot render a finality result as a settlement status by accident, because the
two vocabularies have nothing in common.

One create path and three read paths, matching the reconciliation API exactly.
An audit is immutable: new bank evidence is a new snapshot and therefore a new
audit beside the old one.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_session
from app.api.schemas import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    BankFinalityAuditDetail,
    BankFinalityAuditPage,
    BankFinalityAuditSummary,
    BankFinalityCertificateView,
    ErrorEnvelope,
)
from app.banking.audits import BankFinalityAuditRepository, BankFinalityAuditService
from app.banking.finality import BankFinalityOutcome
from app.storage.repository import SourceFactRepository

router = APIRouter(prefix="/v1/bank-finality", tags=["bank finality"])

NOT_FOUND: dict[int | str, dict[str, object]] = {
    404: {"model": ErrorEnvelope, "description": "No such audit or certificate"}
}


def _not_found(what: str, identifier: str) -> HTTPException:
    """Return a 404 that names what was missing without echoing anything else."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "not_found", "detail": f"no {what} with id {identifier!r}"},
    )


@router.post(
    "/audits",
    response_model=BankFinalityAuditSummary,
    responses={
        200: {
            "model": BankFinalityAuditSummary,
            "description": "An identical audit already existed",
        },
        201: {"model": BankFinalityAuditSummary, "description": "A new audit was recorded"},
        409: {"model": ErrorEnvelope, "description": "There is nothing to audit"},
    },
    summary="Audit every payout against the imported bank statement rows",
)
def create_audit(
    response: Response, session: Annotated[Session, Depends(get_session)]
) -> BankFinalityAuditSummary:
    """Audit bank finality over every accepted source fact.

    Idempotent. Auditing the same facts again under the same bank finality rules
    returns the audit already recorded, with status 200 rather than 201, instead
    of writing a second row describing the same conclusion.

    Importing a bank statement later does not change an earlier audit. It makes
    a new snapshot, and this endpoint then records a new audit beside the old
    one, which is what keeps "we had not been shown the statement yet" a
    recoverable fact.
    """
    index = SourceFactRepository(session).fact_index()
    if not index:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "no_facts",
                "detail": "the store holds no accepted source facts to audit",
            },
        )

    recorded = BankFinalityAuditService(session).create_audit(index)
    response.status_code = status.HTTP_201_CREATED if recorded.was_created else status.HTTP_200_OK
    return BankFinalityAuditSummary.of(recorded)


@router.get(
    "/audits",
    response_model=BankFinalityAuditPage,
    summary="List bank finality audits, newest first",
)
def list_audits(
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    snapshot_fingerprint: Annotated[
        str | None,
        Query(
            min_length=64,
            max_length=64,
            description="Return only audits over this exact fact snapshot",
        ),
    ] = None,
) -> BankFinalityAuditPage:
    """Return a page of audit summaries.

    The snapshot filter is how a run and its audit are put side by side: a run
    publishes its snapshot fingerprint, and the audit over the same facts
    carries the same one.
    """
    repository = BankFinalityAuditRepository(session)
    audits = repository.list_audits(
        limit=limit, offset=offset, snapshot_fingerprint=snapshot_fingerprint
    )
    total = (
        repository.count_for_snapshot(snapshot_fingerprint)
        if snapshot_fingerprint is not None
        else repository.count()
    )
    return BankFinalityAuditPage(
        audits=[BankFinalityAuditSummary.of(one) for one in audits],
        total=total,
        limit=limit,
        offset=offset,
        filtered=snapshot_fingerprint is not None,
    )


@router.get(
    "/audits/{audit_id}",
    response_model=BankFinalityAuditDetail,
    responses=NOT_FOUND,
    summary="Read one audit and its certificates",
)
def get_audit(
    audit_id: str,
    session: Annotated[Session, Depends(get_session)],
    outcome: Annotated[
        BankFinalityOutcome | None,
        Query(description="Return only certificates with this outcome"),
    ] = None,
) -> BankFinalityAuditDetail:
    """Return an audit summary and its certificates, ordered by payout ID.

    A filter narrows the certificates returned and never the summary counts,
    which always describe the whole audit. `filtered` says which view a caller
    is looking at, so a narrowed list cannot be mistaken for the complete one.
    """
    repository = BankFinalityAuditRepository(session)
    recorded = repository.get(audit_id)
    if recorded is None:
        raise _not_found("audit", audit_id)

    certificates = repository.certificates_for(audit_id, outcome=outcome)
    return BankFinalityAuditDetail(
        audit=BankFinalityAuditSummary.of(recorded),
        certificates=[BankFinalityCertificateView.of(one) for one in certificates],
        filtered=outcome is not None,
    )


@router.get(
    "/audits/{audit_id}/payouts/{payout_id}",
    response_model=BankFinalityCertificateView,
    responses=NOT_FOUND,
    summary="Read one payout's bank finality certificate",
)
def get_certificate(
    audit_id: str, payout_id: str, session: Annotated[Session, Depends(get_session)]
) -> BankFinalityCertificateView:
    """Return one certificate of one audit.

    The certificate is rebuilt through the model on the way out, so a row that
    no longer satisfies the contract fails here rather than being served as
    though it did.
    """
    repository = BankFinalityAuditRepository(session)
    if repository.get(audit_id) is None:
        raise _not_found("audit", audit_id)

    certificate = repository.find_certificate(audit_id, payout_id)
    if certificate is None:
        raise _not_found("certificate for payout", payout_id)
    return BankFinalityCertificateView.of(certificate)
