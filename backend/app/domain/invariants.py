"""The invariant catalogue and its pure checks.

An invariant is a deterministic statement about records that is either true,
false, not applicable, or impossible to evaluate with what is available. That
fourth outcome is the important one. Missing information is not a mismatch, and
a system that reports it as one manufactures breaks that finance teams then
waste time disproving.

Checks are pure functions of their arguments. They read nothing, write nothing,
and call nothing outside this package.

INV-006 and INV-007 are stated here but implemented in :mod:`app.domain.decisions`,
because they are checks on a decision rather than on source records, and putting
them here would make the two modules import each other.
"""

from collections.abc import Mapping, Sequence
from enum import StrEnum
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, model_validator

from app.domain.codes import ReasonCode
from app.domain.facts import IngestionOutcome, SourceFact, classify_ingestion
from app.domain.lifecycle import (
    RETURNING_EVENT_TYPES,
    PaymentEvent,
    PaymentEventType,
    PayoutBatch,
    SettlementLine,
)
from app.domain.money import Money
from app.domain.primitives import AmountMinor


class InvariantId(StrEnum):
    """Stable identifiers for the invariants. These never change meaning."""

    INV_001 = "INV-001"
    INV_002 = "INV-002"
    INV_003 = "INV-003"
    INV_004 = "INV-004"
    INV_005 = "INV-005"
    INV_006 = "INV-006"
    INV_007 = "INV-007"
    INV_008 = "INV-008"


class InvariantOutcome(StrEnum):
    """The four answers a check may give."""

    PASSED = "PASSED"
    """The statement holds for the records supplied."""

    FAILED = "FAILED"
    """The statement is false, and everything needed to say so was present."""

    NOT_APPLICABLE = "NOT_APPLICABLE"
    """The statement does not apply here. This is a determinate answer and it
    does not block a resolution."""

    INSUFFICIENT_INPUT = "INSUFFICIENT_INPUT"
    """Not enough was supplied to tell. This is not a failure, and it must never
    be reported as one. It does block a resolution."""


class MissingInputPolicy(StrEnum):
    """What an absent input means for the decision that needed this invariant."""

    PENDING = "PENDING"
    """The information is expected to arrive. Wait rather than judge."""

    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    """The information should be here and is not. The case needs a human or
    another source, and no verdict may be guessed."""

    EXCEPTION = "EXCEPTION"
    """The absence is itself the problem and must be raised."""


class InvariantSpec(BaseModel):
    """One entry in the catalogue."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    invariant_id: InvariantId
    title: str
    missing_input_policy: MissingInputPolicy
    required_for_resolution: bool
    """Whether a resolved decision must carry a determinate result for this
    invariant. See :data:`REQUIRED_FOR_RESOLUTION`."""


_CATALOGUE: dict[InvariantId, InvariantSpec] = {
    spec.invariant_id: spec
    for spec in (
        InvariantSpec(
            invariant_id=InvariantId.INV_001,
            title="Money is integer minor units and currencies are compatible",
            missing_input_policy=MissingInputPolicy.INSUFFICIENT_EVIDENCE,
            required_for_resolution=True,
        ),
        InvariantSpec(
            invariant_id=InvariantId.INV_002,
            title="Settlement line net follows the signed formula",
            missing_input_policy=MissingInputPolicy.INSUFFICIENT_EVIDENCE,
            required_for_resolution=True,
        ),
        InvariantSpec(
            invariant_id=InvariantId.INV_003,
            title="Payout net equals the sum of its settlement line nets",
            missing_input_policy=MissingInputPolicy.PENDING,
            required_for_resolution=True,
        ),
        InvariantSpec(
            invariant_id=InvariantId.INV_004,
            title="Returned amounts do not exceed the captured amount",
            missing_input_policy=MissingInputPolicy.INSUFFICIENT_EVIDENCE,
            required_for_resolution=True,
        ),
        InvariantSpec(
            invariant_id=InvariantId.INV_005,
            title="Source fact idempotency identity has one payload",
            missing_input_policy=MissingInputPolicy.EXCEPTION,
            required_for_resolution=False,
        ),
        InvariantSpec(
            invariant_id=InvariantId.INV_006,
            title="A resolved decision has source-backed evidence",
            missing_input_policy=MissingInputPolicy.INSUFFICIENT_EVIDENCE,
            required_for_resolution=False,
        ),
        InvariantSpec(
            invariant_id=InvariantId.INV_007,
            title="A resolved decision has passing required invariant results",
            missing_input_policy=MissingInputPolicy.INSUFFICIENT_EVIDENCE,
            required_for_resolution=False,
        ),
        InvariantSpec(
            invariant_id=InvariantId.INV_008,
            title="Source facts are append-only and are never rewritten",
            missing_input_policy=MissingInputPolicy.EXCEPTION,
            required_for_resolution=False,
        ),
    )
}

#: The catalogue, read-only so that no caller can quietly add or drop an entry.
INVARIANT_CATALOGUE: Mapping[InvariantId, InvariantSpec] = MappingProxyType(_CATALOGUE)

#: Invariants a decision must carry a determinate result for before it may be
#: RESOLVED. NOT_APPLICABLE counts as determinate; INSUFFICIENT_INPUT does not.
#:
#: INV-005, INV-006, INV-007 and INV-008 are absent on purpose. INV-005 and
#: INV-008 are checked when a fact is ingested, long before a decision exists.
#: INV-006 and INV-007 are the verifier rule itself, so requiring a decision to
#: carry them as evidence of its own correctness would be circular.
REQUIRED_FOR_RESOLUTION: frozenset[InvariantId] = frozenset(
    invariant_id for invariant_id, spec in _CATALOGUE.items() if spec.required_for_resolution
)


class InvariantResult(BaseModel):
    """The recorded outcome of one check.

    A result carries codes and numbers, never prose. Everything on it can be
    compared exactly by an evaluator.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    invariant_id: InvariantId
    outcome: InvariantOutcome
    reason_code: ReasonCode | None = None
    expected_minor: AmountMinor | None = None
    """What the rule required, for numeric checks."""

    observed_minor: AmountMinor | None = None
    """What the records actually said, for numeric checks."""

    @model_validator(mode="after")
    def _failure_must_name_its_reason(self) -> "InvariantResult":
        """A failed check must say which rule fired, otherwise it is unusable."""
        if self.outcome is InvariantOutcome.FAILED and self.reason_code is None:
            message = "a FAILED invariant result must carry a reason_code"
            raise ValueError(message)
        return self

    @property
    def is_determinate(self) -> bool:
        """Return True when the check reached an answer either way."""
        return self.outcome in (InvariantOutcome.PASSED, InvariantOutcome.NOT_APPLICABLE)


def check_money_compatibility(amounts: Sequence[Money]) -> InvariantResult:
    """INV-001: every amount compared together is in one currency.

    That money is held as integer minor units is enforced by the type system at
    construction, so it cannot be false here. What can only be known across
    records is whether they share a currency, which is what this checks.

    An empty sequence is INSUFFICIENT_INPUT, not PASSED. Claiming that nothing
    is consistent would be a free pass.
    """
    if not amounts:
        return InvariantResult(
            invariant_id=InvariantId.INV_001,
            outcome=InvariantOutcome.INSUFFICIENT_INPUT,
        )
    currencies = {amount.currency for amount in amounts}
    if len(currencies) > 1:
        return InvariantResult(
            invariant_id=InvariantId.INV_001,
            outcome=InvariantOutcome.FAILED,
            reason_code=ReasonCode.CURRENCY_NOT_UNIFORM,
        )
    return InvariantResult(invariant_id=InvariantId.INV_001, outcome=InvariantOutcome.PASSED)


def check_settlement_line_net(line: SettlementLine) -> InvariantResult:
    """INV-002: the declared net equals gross - fee - tax + adjustment."""
    expected = line.breakdown.expected_net_minor
    if line.net_minor != expected:
        return InvariantResult(
            invariant_id=InvariantId.INV_002,
            outcome=InvariantOutcome.FAILED,
            reason_code=ReasonCode.NET_FORMULA_MISMATCH,
            expected_minor=expected,
            observed_minor=line.net_minor,
        )
    return InvariantResult(invariant_id=InvariantId.INV_002, outcome=InvariantOutcome.PASSED)


def check_payout_total(payout: PayoutBatch, lines: Sequence[SettlementLine]) -> InvariantResult:
    """INV-003: the payout total equals the sum of the nets of its own lines.

    The lines supplied must be exactly the ones the payout claims. A partial set
    is INSUFFICIENT_INPUT: a sum over some of the lines says nothing about the
    total, and reporting it as a mismatch would be a fabricated break. This is
    the common case for a payout still being assembled, which is why the
    catalogue records the missing-input policy for INV-003 as PENDING.
    """
    supplied = [line.settlement_line_id for line in lines]
    if sorted(supplied) != sorted(payout.settlement_line_ids):
        return InvariantResult(
            invariant_id=InvariantId.INV_003,
            outcome=InvariantOutcome.INSUFFICIENT_INPUT,
        )
    currencies = {line.breakdown.currency for line in lines} | {payout.currency}
    if len(currencies) > 1:
        return InvariantResult(
            invariant_id=InvariantId.INV_003,
            outcome=InvariantOutcome.FAILED,
            reason_code=ReasonCode.CURRENCY_NOT_UNIFORM,
        )
    total = sum(line.net_minor for line in lines)
    if total != payout.net_minor:
        return InvariantResult(
            invariant_id=InvariantId.INV_003,
            outcome=InvariantOutcome.FAILED,
            reason_code=ReasonCode.PAYOUT_TOTAL_MISMATCH,
            expected_minor=total,
            observed_minor=payout.net_minor,
        )
    return InvariantResult(invariant_id=InvariantId.INV_003, outcome=InvariantOutcome.PASSED)


def check_returns_within_capture(events: Sequence[PaymentEvent]) -> InvariantResult:
    """INV-004: refunds, reversals and chargebacks do not exceed the capture.

    With no capture in the supplied events the answer is INSUFFICIENT_INPUT,
    because the ceiling is unknown. With a capture but nothing returned the
    answer is NOT_APPLICABLE, which is determinate and does not block a
    resolution.
    """
    captures = [event for event in events if event.event_type is PaymentEventType.CAPTURE]
    returns = [event for event in events if event.event_type in RETURNING_EVENT_TYPES]

    if not captures:
        return InvariantResult(
            invariant_id=InvariantId.INV_004,
            outcome=InvariantOutcome.INSUFFICIENT_INPUT,
        )

    currencies = {event.amount.currency for event in events}
    if len(currencies) > 1:
        return InvariantResult(
            invariant_id=InvariantId.INV_004,
            outcome=InvariantOutcome.FAILED,
            reason_code=ReasonCode.CURRENCY_NOT_UNIFORM,
        )

    if not returns:
        return InvariantResult(
            invariant_id=InvariantId.INV_004,
            outcome=InvariantOutcome.NOT_APPLICABLE,
            reason_code=ReasonCode.NO_APPLICABLE_RETURN_EVENTS,
        )

    captured = sum(event.amount.amount_minor for event in captures)
    returned = sum(event.amount.amount_minor for event in returns)
    if returned > captured:
        return InvariantResult(
            invariant_id=InvariantId.INV_004,
            outcome=InvariantOutcome.FAILED,
            reason_code=ReasonCode.RETURNS_EXCEED_CAPTURE,
            expected_minor=captured,
            observed_minor=returned,
        )
    return InvariantResult(invariant_id=InvariantId.INV_004, outcome=InvariantOutcome.PASSED)


def check_idempotency(incoming: SourceFact, existing: SourceFact | None) -> InvariantResult:
    """INV-005: one idempotency identity never carries two different payloads.

    A repeated identical delivery passes. It is expected behaviour, not a break.
    """
    outcome = classify_ingestion(incoming, existing)
    if outcome is IngestionOutcome.DUPLICATE_CONFLICT:
        return InvariantResult(
            invariant_id=InvariantId.INV_005,
            outcome=InvariantOutcome.FAILED,
            reason_code=ReasonCode.PAYLOAD_HASH_CONFLICT,
        )
    return InvariantResult(invariant_id=InvariantId.INV_005, outcome=InvariantOutcome.PASSED)


def check_append_only(stored: SourceFact, proposed: SourceFact) -> InvariantResult:
    """INV-008: a stored source fact is never rewritten.

    Two facts with the same source record ID must be identical. Anything else is
    an attempt to edit history, which the contract forbids. A correction is
    recorded as a new fact with its own record ID, not as an overwrite.
    """
    if stored.source_record_id != proposed.source_record_id:
        return InvariantResult(
            invariant_id=InvariantId.INV_008,
            outcome=InvariantOutcome.NOT_APPLICABLE,
        )
    if stored != proposed:
        return InvariantResult(
            invariant_id=InvariantId.INV_008,
            outcome=InvariantOutcome.FAILED,
            reason_code=ReasonCode.SOURCE_FACT_REWRITE_ATTEMPTED,
        )
    return InvariantResult(invariant_id=InvariantId.INV_008, outcome=InvariantOutcome.PASSED)
