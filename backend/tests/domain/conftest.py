"""Builders for domain tests.

Each builder produces a valid object and takes overrides, so a test can state
only the one thing it cares about. That keeps a test about fee arithmetic from
being buried in unrelated identifiers.
"""

from datetime import UTC, datetime
from typing import Any

import pytest

from app.domain.codes import ReasonCode
from app.domain.decisions import (
    DecisionCandidate,
    DecisionStatus,
    ReconciliationDecision,
)
from app.domain.evidence import EvidenceOutcome, EvidenceRef, EvidenceVerification
from app.domain.facts import (
    SourceFact,
    SourceLocator,
    SourceLocatorKind,
    SourceRecordType,
    SourceSystem,
    compute_payload_hash,
)
from app.domain.invariants import (
    REQUIRED_FOR_RESOLUTION,
    InvariantOutcome,
    InvariantResult,
)
from app.domain.lifecycle import PaymentEvent, PaymentEventType, PayoutBatch, SettlementLine
from app.domain.money import Money, MoneyBreakdown
from app.domain.primitives import CanonicalPayload

FIXED_TIME = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)
"""One timestamp for every test, so a comparison never fails on the clock."""


def make_money(amount_minor: int = 10_000, currency: str = "INR") -> Money:
    """Return a Money value."""
    return Money(amount_minor=amount_minor, currency=currency)


def make_breakdown(**overrides: Any) -> MoneyBreakdown:
    """Return a consistent breakdown whose net is 10000 - 200 - 36 + 0 = 9764."""
    fields: dict[str, Any] = {
        "currency": "INR",
        "gross_minor": 10_000,
        "fee_minor": 200,
        "tax_minor": 36,
        "adjustment_minor": 0,
    }
    fields.update(overrides)
    return MoneyBreakdown(**fields)


def make_locator(**overrides: Any) -> SourceLocator:
    """Return a file row locator."""
    fields: dict[str, Any] = {
        "kind": SourceLocatorKind.FILE_ROW,
        "reference": "settlements/2026-08-24.csv",
        "row_number": 7,
    }
    fields.update(overrides)
    return SourceLocator(**fields)


def make_fact(payload: CanonicalPayload | None = None, **overrides: Any) -> SourceFact:
    """Return a source fact whose payload hash is computed, not guessed."""
    content: CanonicalPayload = {"amount_minor": 9_764} if payload is None else payload
    fields: dict[str, Any] = {
        "source_record_id": "rec-1",
        "source_system": SourceSystem.PSP_API,
        "source_record_type": SourceRecordType.SETTLEMENT_LINE,
        "source_locator": make_locator(),
        "provider_event_id": "evt-1",
        "observed_at": FIXED_TIME,
        "occurred_at": FIXED_TIME,
        "canonical_payload": content,
        "payload_hash": compute_payload_hash(content),
    }
    fields.update(overrides)
    if "canonical_payload" in overrides and "payload_hash" not in overrides:
        fields["payload_hash"] = compute_payload_hash(fields["canonical_payload"])
    return SourceFact(**fields)


def make_settlement_line(**overrides: Any) -> SettlementLine:
    """Return a settlement line whose declared net matches its breakdown."""
    breakdown = overrides.pop("breakdown", make_breakdown())
    fields: dict[str, Any] = {
        "settlement_line_id": "sl-1",
        "payout_id": "po-1",
        "payment_id": "pay-1",
        "breakdown": breakdown,
        "net_minor": breakdown.expected_net_minor,
        "occurred_at": FIXED_TIME,
        "source_record_id": "rec-1",
    }
    fields.update(overrides)
    return SettlementLine(**fields)


def make_payout(**overrides: Any) -> PayoutBatch:
    """Return a payout covering one settlement line."""
    fields: dict[str, Any] = {
        "payout_id": "po-1",
        "merchant_id": "merch-1",
        "currency": "INR",
        "net_minor": 9_764,
        "settlement_line_ids": ("sl-1",),
        "utr": "UTR123456",
        "occurred_at": FIXED_TIME,
        "source_record_id": "rec-2",
    }
    fields.update(overrides)
    return PayoutBatch(**fields)


def make_event(**overrides: Any) -> PaymentEvent:
    """Return a capture event."""
    fields: dict[str, Any] = {
        "event_id": "evt-1",
        "payment_id": "pay-1",
        "event_type": PaymentEventType.CAPTURE,
        "amount": make_money(),
        "occurred_at": FIXED_TIME,
        "source_record_id": "rec-1",
    }
    fields.update(overrides)
    return PaymentEvent(**fields)


def make_evidence(**overrides: Any) -> EvidenceRef:
    """Return a citation that resolves against ``make_fact()``.

    The default hash is the real hash of the default fact's payload, so a test
    that verifies this citation against that fact succeeds for the right reason
    rather than because both sides happen to hold the same placeholder.
    """
    fields: dict[str, Any] = {
        "source_record_id": "rec-1",
        "source_system": SourceSystem.PSP_API,
        "payload_hash": make_fact().payload_hash,
    }
    fields.update(overrides)
    return EvidenceRef(**fields)


def make_verification(**overrides: Any) -> EvidenceVerification:
    """Return a verification result, verified unless overridden."""
    fields: dict[str, Any] = {
        "source_record_id": "rec-1",
        "outcome": EvidenceOutcome.VERIFIED,
    }
    fields.update(overrides)
    return EvidenceVerification(**fields)


def make_candidate(**overrides: Any) -> DecisionCandidate:
    """Return a candidate that would resolve if its citation checks out."""
    fields: dict[str, Any] = {
        "decision_id": "dec-1",
        "subject_settlement_line_id": "sl-1",
        "linked_source_record_ids": ("rec-1",),
        "linked_event_ids": ("evt-1",),
        "evidence": (make_evidence(),),
        "invariant_results": passing_required_results(),
        "exception_codes": (),
        "reason_codes": (ReasonCode.ALL_REQUIRED_INVARIANTS_PASSED,),
        "created_at": FIXED_TIME,
    }
    fields.update(overrides)
    return DecisionCandidate(**fields)


def passing_required_results() -> tuple[InvariantResult, ...]:
    """Return a passing result for every invariant a resolution requires."""
    return tuple(
        InvariantResult(invariant_id=invariant_id, outcome=InvariantOutcome.PASSED)
        for invariant_id in sorted(REQUIRED_FOR_RESOLUTION, key=lambda item: item.value)
    )


def make_decision(**overrides: Any) -> ReconciliationDecision:
    """Return a fully backed RESOLVED decision unless overridden."""
    fields: dict[str, Any] = {
        "decision_id": "dec-1",
        "status": DecisionStatus.RESOLVED,
        "subject_settlement_line_id": "sl-1",
        "linked_source_record_ids": ("rec-1",),
        "linked_event_ids": ("evt-1",),
        "evidence": (make_evidence(),),
        "evidence_verification": (make_verification(),),
        "invariant_results": passing_required_results(),
        "exception_codes": (),
        "reason_codes": (ReasonCode.ALL_REQUIRED_INVARIANTS_PASSED,),
        "created_at": FIXED_TIME,
    }
    fields.update(overrides)
    if "evidence" in overrides and "evidence_verification" not in overrides:
        # Keep the certificate matched to the citations, so a test that changes
        # the evidence does not fail on an unrelated rule.
        fields["evidence_verification"] = tuple(
            make_verification(source_record_id=reference.source_record_id)
            for reference in fields["evidence"]
        )
    return ReconciliationDecision(**fields)


@pytest.fixture
def resolved_decision() -> ReconciliationDecision:
    """Return a decision that legitimately earned RESOLVED."""
    return make_decision()
