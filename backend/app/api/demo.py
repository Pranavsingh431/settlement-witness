"""A one-click walkthrough over the repository's synthetic fixture documents.

The public hackathon preview needs an understandable first experience. Asking a
reviewer to download four CSV files before the first useful screen is not that.
This endpoint loads only the four committed demonstration documents, then records
the same reconciliation run and bank-finality audit that the ordinary API would
create.

It is deliberately narrow: there is no caller-supplied content, no reset, no
record deletion, and no route to merchant data. Repeating it is idempotent for
the fixture facts and conclusions. A previously accepted fixture is recognised
by its content hash, declared source and declared record type and is not imported
again merely to create duplicate receipts.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.api.demo_fixtures import BANK_TRANSACTIONS, PAYMENT_EVENTS, PAYOUTS, SETTLEMENT_LINES
from app.api.dependencies import get_session
from app.api.schemas import (
    BankFinalityAuditSummary,
    DemoBootstrapResult,
    DemoFixtureResult,
    RunSummary,
)
from app.banking.audits import BankFinalityAuditService
from app.domain.facts import SourceRecordType, SourceSystem
from app.ingestion.parsing import compute_document_hash
from app.ingestion.receipts import ImportOutcome
from app.ingestion.service import ImportService
from app.reconciliation.runs import ReconciliationRunService
from app.storage.repository import ImportReceiptRepository, SourceFactRepository

router = APIRouter(prefix="/v1/demo", tags=["demo"])


@dataclass(frozen=True)
class DemoFixture:
    """One shipped, synthetic document and the way it is declared on import."""

    name: str
    source_system: SourceSystem
    record_type: SourceRecordType
    content: bytes


DEMO_FIXTURES: tuple[DemoFixture, ...] = (
    DemoFixture(
        "payment_events.csv", SourceSystem.PSP_API, SourceRecordType.PAYMENT_EVENT, PAYMENT_EVENTS
    ),
    DemoFixture(
        "settlement_lines.csv",
        SourceSystem.PSP_API,
        SourceRecordType.SETTLEMENT_LINE,
        SETTLEMENT_LINES,
    ),
    DemoFixture("payouts.csv", SourceSystem.PSP_API, SourceRecordType.PAYOUT, PAYOUTS),
    DemoFixture(
        "bank_transactions.csv",
        SourceSystem.BANK_STATEMENT,
        SourceRecordType.BANK_TRANSACTION,
        BANK_TRANSACTIONS,
    ),
)
"""The only documents this endpoint may ever load."""


def _is_already_loaded(
    receipts: ImportReceiptRepository, fixture: DemoFixture, content: bytes
) -> bool:
    """Return whether this exact fixture is already accepted under its declaration."""
    document_hash = compute_document_hash(content)
    return any(
        row.source_system == fixture.source_system.value
        and row.source_record_type == fixture.record_type.value
        and row.outcome in (ImportOutcome.ACCEPTED.value, ImportOutcome.DUPLICATE_NO_OP.value)
        for row in receipts.for_document(document_hash)
    )


def _load_fixtures(session: Session) -> Sequence[DemoFixtureResult]:
    """Import missing fixture documents, leaving accepted fixtures untouched."""
    receipts = ImportReceiptRepository(session)
    importer = ImportService(session)
    results: list[DemoFixtureResult] = []

    for fixture in DEMO_FIXTURES:
        if _is_already_loaded(receipts, fixture, fixture.content):
            results.append(
                DemoFixtureResult(
                    document_name=fixture.name,
                    source_record_type=fixture.record_type,
                    outcome=ImportOutcome.DUPLICATE_NO_OP,
                    loaded_now=False,
                )
            )
            continue

        receipt = importer.import_document(
            fixture.content,
            source_system=fixture.source_system,
            record_type=fixture.record_type,
            document_name=fixture.name,
        )
        results.append(
            DemoFixtureResult(
                document_name=fixture.name,
                source_record_type=fixture.record_type,
                outcome=receipt.outcome,
                loaded_now=receipt.outcome is ImportOutcome.ACCEPTED,
            )
        )

    return results


@router.post(
    "/bootstrap",
    response_model=DemoBootstrapResult,
    responses={
        200: {"model": DemoBootstrapResult, "description": "The walkthrough was already ready"},
        201: {"model": DemoBootstrapResult, "description": "The walkthrough was prepared"},
    },
    summary="Load the bundled synthetic walkthrough",
)
def bootstrap_demo(
    response: Response, session: Annotated[Session, Depends(get_session)]
) -> DemoBootstrapResult:
    """Prepare the public walkthrough without accepting any caller-supplied data.

    The regular import endpoint remains the place for a person to submit their
    own evidence. This route has no body and reads only four versioned fixtures
    distributed with the application, so opening a public preview cannot turn
    into an upload path disguised as a demo button.
    """
    fixture_results = _load_fixtures(session)
    facts = SourceFactRepository(session).fact_index()
    run = ReconciliationRunService(session).create_run(facts)
    audit = BankFinalityAuditService(session).create_audit(facts)
    changed = any(one.loaded_now for one in fixture_results) or run.was_created or audit.was_created
    response.status_code = status.HTTP_201_CREATED if changed else status.HTTP_200_OK
    return DemoBootstrapResult(
        fixture_results=list(fixture_results),
        run=RunSummary.of(run),
        bank_finality_audit=BankFinalityAuditSummary.of(audit),
        created=changed,
    )
