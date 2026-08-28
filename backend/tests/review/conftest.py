"""A recorded run holding one of each status a review queue has to handle.

Built from hand-made facts rather than from the example documents, because the
example documents produce no `INSUFFICIENT_EVIDENCE` decision and that is half
of what this queue is for.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.domain.decisions import DecisionStatus, ReconciliationDecision
from app.reconciliation.runs import (
    PersistedRun,
    ReconciliationRunRepository,
    ReconciliationRunService,
)
from app.storage.database import (
    create_database_engine,
    create_schema,
    database_url_for,
    session_factory,
    session_scope,
)
from tests.reconciliation.conftest import index_of, payment_event, payout, settlement_line

RECORDED_AT = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
"""A fixed timestamp, so a recorded time is a fact about the test."""


def mixed_facts() -> tuple[object, ...]:
    """Return facts producing one resolved, two exception and one unknown line.

    `sl-4` is the resolved one. `sl-2` cites a payout this snapshot does not
    hold, which is `INSUFFICIENT_EVIDENCE` rather than a failure: the records
    admit more than one explanation and none was chosen.
    """
    return (
        payment_event("pe-1", payment_id="pay-1"),
        settlement_line("sl-1", payment_id="pay-1", payout_id="po-1", net_minor=1),
        payment_event("pe-2", payment_id="pay-2"),
        settlement_line("sl-2", payment_id="pay-2", payout_id="po-missing"),
        settlement_line("sl-3", payment_id="pay-3", payout_id="po-1"),
        payment_event("pe-4", payment_id="pay-4"),
        settlement_line("sl-4", payment_id="pay-4", payout_id="payout-1"),
        payout("po-4", payout_id="payout-1"),
    )


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    """Return an engine on a fresh migrated database."""
    built = create_database_engine(database_url_for(tmp_path / "review.sqlite"))
    create_schema(built)
    try:
        yield built
    finally:
        built.dispose()


@pytest.fixture
def recorded_run(engine: Engine) -> PersistedRun:
    """Return a run recorded over the mixed facts."""
    with session_scope(engine) as session:
        return ReconciliationRunService(session).create_run(index_of(*mixed_facts()))  # type: ignore[arg-type]


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """Return a session that commits at the end of the test."""
    with session_scope(engine) as opened:
        yield opened


def decisions_of(engine: Engine, run_id: str) -> tuple[ReconciliationDecision, ...]:
    """Return every decision of a run, rebuilt through the domain model."""
    with session_factory(engine)() as reading:
        return ReconciliationRunRepository(reading).decisions_for(run_id)


def one_with(engine: Engine, run_id: str, status: DecisionStatus) -> ReconciliationDecision:
    """Return the first decision of a run carrying one status."""
    return next(decision for decision in decisions_of(engine, run_id) if decision.status is status)
