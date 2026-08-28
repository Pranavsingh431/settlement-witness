"""Facts for the bank finality tests, built as one-field paired controls.

Every negative case here is the verified case with exactly one field changed.
That is what makes the tests evidence rather than illustration: a test that
built each outcome from a different set of facts would pass whether or not the
rule under test was the thing producing the outcome.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

from app.domain.evidence import SourceFactIndex
from app.domain.facts import SourceFact, SourceRecordType
from app.storage.database import (
    create_database_engine,
    create_schema,
    database_url_for,
)
from tests.reconciliation.conftest import BASE_TIME, index_of, make_fact, payout

VERIFIED_REFERENCE = "UTR-2026-08-21-0001"
"""The reference the verified payout and its credit both carry."""

PAYOUT_NET_MINOR = 97_640
"""The payout total every paired control starts from."""


def bank_transaction(provider_event_id: str, **overrides: Any) -> SourceFact:
    """Return a credit that exactly settles the default payout.

    Change one field and it stops verifying, which is the point. Nothing here
    varies unless a test varies it.
    """
    payload: dict[str, Any] = {
        "provider_event_id": provider_event_id,
        "bank_transaction_id": f"BANKTXN-{provider_event_id}",
        "bank_reference": VERIFIED_REFERENCE,
        "direction": "CREDIT",
        "amount_minor": PAYOUT_NET_MINOR,
        "currency": "INR",
        "occurred_at": BASE_TIME.isoformat(),
    }
    payload.update(overrides)
    return make_fact(SourceRecordType.BANK_TRANSACTION, provider_event_id, payload)


def linkable_payout(provider_event_id: str = "po-1", **overrides: Any) -> SourceFact:
    """Return a payout carrying the verified reference.

    Overrides win, so a caller can take the reference away again and get the
    unlinkable control out of the same helper.
    """
    fields: dict[str, Any] = {"utr": VERIFIED_REFERENCE, "net_minor": PAYOUT_NET_MINOR}
    fields.update(overrides)
    return payout(provider_event_id, **fields)


def facts_for(**overrides: Any) -> SourceFactIndex:
    """Return the verified arrangement with one bank field changed.

    The whole paired-control idea in one function. `facts_for()` is the control
    and `facts_for(direction="DEBIT")` is the case, and the only difference
    between them is the field named.
    """
    return index_of(linkable_payout(), bank_transaction("bt-1", **overrides))


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    """Return an engine on a fresh migrated database."""
    built = create_database_engine(database_url_for(tmp_path / "banking.sqlite"))
    create_schema(built)
    try:
        yield built
    finally:
        built.dispose()
