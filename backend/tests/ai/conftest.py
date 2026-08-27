"""Builders for the AI boundary tests.

Snapshots are built from the reconciliation builders rather than from CSV, so a
test can state one linking shape without writing a document for it. Two payments
across two lines are the useful default: with one payment every candidate is
correct, and a linker that selected everything would look perfect.
"""

from collections.abc import Mapping
from typing import Any

import pytest

from app.ai.candidates import LinkProposalRequest, build_request, truth_for
from app.ai.proposals import ProposalOutcome, ProviderIdentity
from app.reconciliation.snapshot import FactSnapshot
from tests.reconciliation.conftest import index_of, payment_event, payout, settlement_line

FIXTURE = ProviderIdentity(name="fixture", version="1")


@pytest.fixture
def snapshot() -> FactSnapshot:
    """Return a snapshot holding two payments, two lines and one payout.

    `line-sl-1` links `pe-1` and the payout. `line-sl-2` links `pe-2` and the
    same payout. So a record that belongs to one line does not belong to the
    other, which is what makes a cross-line selection detectable.
    """
    return FactSnapshot.from_index(
        index_of(
            payment_event("pe-1", payment_id="pay-1"),
            payment_event("pe-2", payment_id="pay-2", amount_minor=50_000),
            settlement_line("sl-1", payment_id="pay-1"),
            settlement_line(
                "sl-2",
                payment_id="pay-2",
                gross_minor=50_000,
                fee_minor=1_000,
                tax_minor=180,
                net_minor=48_820,
            ),
            payout("po-1", net_minor=146_460),
        )
    )


@pytest.fixture
def request_for_line_one(snapshot: FactSnapshot) -> LinkProposalRequest:
    """Return the request for the first settlement line."""
    return build_request("line-sl-1", snapshot)


def payload_for(
    selected: tuple[str, ...],
    outcome: ProposalOutcome | None = None,
    /,
    **overrides: Any,
) -> dict[str, Any]:
    """Return a well-formed selection payload, before any override.

    Two keys, because that is the whole of what a provider may return. Built as
    a plain dict rather than as a model, because what a provider returns is
    untrusted data and the tests need to hand the validator shapes a model would
    refuse to construct.
    """
    decided = outcome or (ProposalOutcome.PROPOSE if selected else ProposalOutcome.ABSTAIN)
    payload: dict[str, Any] = {
        "outcome": decided.value,
        "selected_source_record_ids": list(selected),
    }
    payload.update(overrides)
    return payload


def correct_selection(request: LinkProposalRequest, snapshot: FactSnapshot) -> tuple[str, ...]:
    """Return the records the baseline links for this request, in order."""
    return tuple(sorted(truth_for(request, snapshot)))


def store_state(engine: Any) -> Mapping[str, Any]:
    """Return everything a proposal must never change.

    Read through the repositories rather than by counting rows, so a value that
    changed inside a record is caught as well as a record appearing.
    """
    from app.reconciliation.runs import ReconciliationRunRepository
    from app.storage.database import session_factory
    from app.storage.repository import ImportReceiptRepository, SourceFactRepository

    with session_factory(engine)() as session:
        return {
            "facts": SourceFactRepository(session).all_facts(),
            "receipts": [
                receipt.model_dump_json()
                for receipt in ImportReceiptRepository(session).page(limit=100, offset=0)
            ],
            "runs": [
                run.model_dump_json()
                for run in ReconciliationRunRepository(session).list_runs(limit=100, offset=0)
            ],
        }
