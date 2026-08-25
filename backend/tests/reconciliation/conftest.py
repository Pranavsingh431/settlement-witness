"""Builders for reconciliation tests.

Facts are built directly rather than imported from CSV, so a test can state one
lifecycle shape without writing a document for it. Every fact is a real
SourceFact with a computed payload hash, so evidence verification is exercised
for real.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.evidence import SourceFactIndex, build_fact_index
from app.domain.facts import (
    SourceFact,
    SourceLocator,
    SourceLocatorKind,
    SourceRecordType,
    SourceSystem,
    compute_payload_hash,
)
from app.domain.primitives import CanonicalPayload

BASE_TIME = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)
"""One anchor instant. Tests offset from it rather than naming absolute times."""

PSP = SourceSystem.PSP_API


def at(minutes: int) -> str:
    """Return an ISO timestamp offset from the anchor."""
    return (BASE_TIME + timedelta(minutes=minutes)).isoformat()


def make_fact(
    record_type: SourceRecordType, provider_event_id: str, payload: CanonicalPayload
) -> SourceFact:
    """Return a source fact carrying one canonical payload."""
    return SourceFact(
        source_record_id=f"{record_type.value}:{provider_event_id}",
        source_system=PSP,
        source_record_type=record_type,
        source_locator=SourceLocator(
            kind=SourceLocatorKind.API_RESOURCE, reference=f"/v1/{provider_event_id}"
        ),
        provider_event_id=provider_event_id,
        observed_at=BASE_TIME,
        occurred_at=datetime.fromisoformat(str(payload["occurred_at"])),
        canonical_payload=payload,
        payload_hash=compute_payload_hash(payload),
    )


def payment_event(provider_event_id: str, **overrides: Any) -> SourceFact:
    """Return a capture of 100000 minor units unless overridden."""
    payload: CanonicalPayload = {
        "provider_event_id": provider_event_id,
        "event_id": f"evt-{provider_event_id}",
        "payment_id": "pay-1",
        "merchant_id": "merch-1",
        "event_type": "CAPTURE",
        "amount_minor": 100_000,
        "currency": "INR",
        "occurred_at": at(0),
    }
    payload.update(overrides)
    return make_fact(SourceRecordType.PAYMENT_EVENT, provider_event_id, payload)


def settlement_line(provider_event_id: str, **overrides: Any) -> SourceFact:
    """Return a line whose declared net follows the formula unless overridden."""
    payload: CanonicalPayload = {
        "provider_event_id": provider_event_id,
        "settlement_line_id": f"line-{provider_event_id}",
        "payout_id": "payout-1",
        "payment_id": "pay-1",
        "gross_minor": 100_000,
        "fee_minor": 2_000,
        "tax_minor": 360,
        "adjustment_minor": 0,
        "net_minor": 97_640,
        "currency": "INR",
        "occurred_at": at(60),
    }
    payload.update(overrides)
    return make_fact(SourceRecordType.SETTLEMENT_LINE, provider_event_id, payload)


def payout(provider_event_id: str, **overrides: Any) -> SourceFact:
    """Return a payout totalling one default settlement line unless overridden."""
    payload: CanonicalPayload = {
        "provider_event_id": provider_event_id,
        "payout_id": "payout-1",
        "merchant_id": "merch-1",
        "net_minor": 97_640,
        "currency": "INR",
        "utr": "UTR1",
        "occurred_at": at(120),
    }
    payload.update(overrides)
    return make_fact(SourceRecordType.PAYOUT, provider_event_id, payload)


def index_of(*facts: SourceFact) -> SourceFactIndex:
    """Return a fact index over the given facts."""
    return build_fact_index(facts)


def complete_case() -> SourceFactIndex:
    """Return the one shape this baseline is able to resolve.

    A single capture, one settlement line whose net follows the formula, and a
    payout whose total equals that line. Nothing refunded, nothing ambiguous.
    """
    return index_of(payment_event("pe-1"), settlement_line("sl-1"), payout("po-1"))
