"""The deterministic reconciliation baseline.

One settlement line at a time. For each, the engine gathers what the snapshot
directly says about it, runs the invariants that apply, and hands a candidate to
`verify_decision`. It never assigns a status and never writes a reason code.

What it will match on:

- a settlement line to payment events, by exact ``payment_id``;
- a settlement line to its payout, by exact ``payout_id``.

What it will not match on: amounts that look close, timestamps that sit nearby,
text that reads similarly, or a reference it had to guess at. A baseline that
guessed would produce resolutions nobody could check, which is the failure this
project exists to avoid. Everything it cannot justify from a direct reference
becomes an honest non-resolution.
"""

from collections.abc import Sequence

from app.domain.codes import ExceptionCode
from app.domain.decisions import DecisionCandidate, ReconciliationDecision, verify_decision
from app.domain.evidence import EvidenceRef, SourceFactIndex
from app.domain.invariants import (
    InvariantId,
    InvariantOutcome,
    InvariantResult,
    check_money_compatibility,
    check_payout_total,
    check_returns_within_capture,
    check_settlement_line_net,
)
from app.domain.lifecycle import (
    RETURNING_EVENT_TYPES,
    PaymentEvent,
    PaymentEventType,
    SettlementLine,
)
from app.domain.money import Money
from app.domain.payout_snapshot import snapshot_payout
from app.reconciliation.snapshot import FactSnapshot

#: Which exception code an invariant failure raises.
#:
#: INV-002 and INV-003 both report AMOUNT_MISMATCH rather than FEE_MISMATCH. When
#: a declared net disagrees with the formula there is no way to tell whether the
#: fee is wrong or the net is, and naming the fee would be a guess. FEE_MISMATCH
#: needs a second source of fee truth, which this baseline does not have.
EXCEPTION_BY_INVARIANT: dict[InvariantId, ExceptionCode] = {
    InvariantId.INV_001: ExceptionCode.CURRENCY_MISMATCH,
    InvariantId.INV_002: ExceptionCode.AMOUNT_MISMATCH,
    InvariantId.INV_003: ExceptionCode.AMOUNT_MISMATCH,
    InvariantId.INV_004: ExceptionCode.AMOUNT_MISMATCH,
}


def _decision_id(line: SettlementLine, snapshot: FactSnapshot) -> str:
    """Return a decision ID that is stable for one line in one snapshot."""
    return f"{snapshot.digest[:16]}:{line.settlement_line_id}"


def _captures(events: Sequence[PaymentEvent]) -> tuple[PaymentEvent, ...]:
    """Return the capture events among a payment's events."""
    return tuple(event for event in events if event.event_type is PaymentEventType.CAPTURE)


def _returns(events: Sequence[PaymentEvent]) -> tuple[PaymentEvent, ...]:
    """Return the events that give money back."""
    return tuple(event for event in events if event.event_type in RETURNING_EVENT_TYPES)


def _lifecycle_exceptions(events: Sequence[PaymentEvent]) -> tuple[ExceptionCode, ...]:
    """Return what the payment's own event sequence rules out.

    Three things stop this baseline resolving a line even when every amount
    agrees.

    A return dated before its capture means the sequence is impossible as
    reported, so nothing derived from it can be trusted.

    A partial refund leaves a balance that still has to be accounted for. The
    baseline has no rule for how a partially refunded payment should settle, so
    it declines rather than assuming the settlement is unaffected.

    A payment returned in full that still produced a settlement line is a state
    the contract does not yet describe. Reporting it as unsupported is honest;
    picking an interpretation would not be.
    """
    captures = _captures(events)
    returns = _returns(events)
    if not captures or not returns:
        return ()

    codes: list[ExceptionCode] = []

    earliest_capture = min(event.occurred_at for event in captures)
    if any(event.occurred_at < earliest_capture for event in returns):
        codes.append(ExceptionCode.OUT_OF_ORDER_EVENT)

    captured = sum(event.amount.amount_minor for event in captures)
    returned = sum(event.amount.amount_minor for event in returns)
    if 0 < returned < captured:
        codes.append(ExceptionCode.PARTIAL_REFUND)
    elif returned == captured:
        codes.append(ExceptionCode.UNSUPPORTED_STATE)

    return tuple(codes)


def _money_under_comparison(
    line: SettlementLine, events: Sequence[PaymentEvent]
) -> tuple[Money, ...]:
    """Return every amount INV-001 must find compatible, in a fixed order."""
    return (line.declared_net, *(event.amount for event in events))


def reconcile_line(line: SettlementLine, snapshot: FactSnapshot) -> DecisionCandidate:
    """Build the candidate for one settlement line.

    Pure: it reads the line and the snapshot and returns a draft. It assigns no
    status, writes no reason code, and touches nothing outside its arguments.

    Args:
        line: The settlement line being reconciled.
        snapshot: The facts the run is allowed to reason about.

    Returns:
        A candidate carrying the evidence, the invariant results and the
        exception codes the snapshot supports.
    """
    events = snapshot.events_for_payment(line.payment_id)
    payout = snapshot.payout_for(line.payout_id)
    captures = _captures(events)

    record_ids: list[str] = [line.source_record_id]
    record_ids.extend(event.source_record_id for event in events)
    if payout is not None:
        record_ids.append(payout.source_record_id)

    exceptions: list[ExceptionCode] = []
    results: list[InvariantResult] = []

    # INV-001 and INV-002 need only the line and whatever events were found.
    results.append(check_money_compatibility(_money_under_comparison(line, events)))
    results.append(check_settlement_line_net(line))

    # INV-003 is evaluated against the lines this snapshot has for this payout.
    # See app.domain.payout_snapshot for why that is not a completeness claim.
    if payout is None:
        exceptions.append(ExceptionCode.INSUFFICIENT_EVIDENCE)
        results.append(
            InvariantResult(
                invariant_id=InvariantId.INV_003,
                outcome=InvariantOutcome.INSUFFICIENT_INPUT,
            )
        )
    else:
        evidenced = snapshot.lines_for_payout(payout.payout_id)
        results.append(check_payout_total(snapshot_payout(payout, evidenced), evidenced))

    # INV-004 needs the payment's events.
    results.append(check_returns_within_capture(events))

    if not events:
        exceptions.append(ExceptionCode.MISSING_PAYMENT)
    elif not captures:
        exceptions.append(ExceptionCode.INSUFFICIENT_EVIDENCE)
    elif len(captures) > 1:
        # Two captures for one payment. Choosing one would decide which the line
        # settled, and nothing in the records says.
        exceptions.append(ExceptionCode.UNSUPPORTED_STATE)

    exceptions.extend(_lifecycle_exceptions(events))

    # Every invariant this engine evaluates has an entry in the map, which
    # test_the_exception_map_covers_every_evaluated_invariant holds true.
    exceptions.extend(
        EXCEPTION_BY_INVARIANT[result.invariant_id]
        for result in results
        if result.outcome is InvariantOutcome.FAILED
    )

    return DecisionCandidate(
        decision_id=_decision_id(line, snapshot),
        subject_settlement_line_id=line.settlement_line_id,
        linked_source_record_ids=tuple(sorted(set(record_ids))),
        linked_event_ids=tuple(sorted(event.event_id for event in events)),
        evidence=tuple(
            EvidenceRef(
                source_record_id=record_id,
                source_system=snapshot.fact_for(record_id).source_system,
                payload_hash=snapshot.fact_for(record_id).payload_hash,
            )
            for record_id in sorted(set(record_ids))
        ),
        invariant_results=tuple(sorted(results, key=lambda result: result.invariant_id.value)),
        exception_codes=tuple(sorted(set(exceptions), key=lambda code: code.value)),
        created_at=snapshot.as_of,
    )


def reconcile_all(
    snapshot: FactSnapshot, index: SourceFactIndex
) -> tuple[ReconciliationDecision, ...]:
    """Reconcile every settlement line in the snapshot.

    Each candidate goes through `verify_decision`, which resolves its citations
    against the same index and derives the status and the reason codes. This
    module never assigns either.

    Args:
        snapshot: The facts to reason about.
        index: The same facts, for evidence verification.

    Returns:
        Decisions ordered by settlement line ID.
    """
    return tuple(
        verify_decision(reconcile_line(line, snapshot), index) for line in snapshot.settlement_lines
    )
