# ADR-007: Payment event amounts are strictly positive

- Status: Accepted
- Date: 2026-08-25
- Supersedes: none
- Superseded by: none
- Related: [ADR-002](ADR-002-domain-contract-and-verifier-authority.md),
  [ADR-006](ADR-006-settlement-gross-must-match-its-capture.md)

## Context

ADR-006 added INV-009 so that a settlement line settling a different amount from
its capture could not resolve. It applies to the shape a direct match supports,
exactly one capture and nothing returned, and is `NOT_APPLICABLE` otherwise.

That was safe on the reasoning that every other shape carries its own
non-resolution code. One shape did not.

`PaymentEvent` documented its amount as a positive magnitude and did not enforce
it. With one positive capture and a refund of zero:

```text
capture : 100000, refund: 0

status          : RESOLVED
exception codes : []
invariants      : INV-001 PASSED, INV-002 PASSED, INV-003 PASSED,
                  INV-004 PASSED, INV-009 NOT_APPLICABLE
```

INV-004 passed, because nothing exceeded the capture. INV-009 became not
applicable, because a return existed. No lifecycle code fired, because zero is
neither a partial refund nor a full one. Adding a single zero-amount refund
switched off the gross check and cost nothing.

Which means the ADR-006 fix was bypassable in one line:

```text
with gross 80000 against capture 100000 plus a zero refund:
  status: RESOLVED | INV-009: NOT_APPLICABLE
```

## Decision

### A payment event amount must be strictly greater than zero

Enforced by a validator on `PaymentEvent`, for all four event types. Zero and
negative are both refused, at construction, wherever the event is built.

An event amount is the magnitude of something that happened: money taken, or
money given back. Zero is not a smaller version of that, it is the absence of
it, and a record of nothing happening should not exist. The model always said
this in its docstring and never enforced it.

Fixing it in the model rather than in the lifecycle logic is deliberate. The
alternative was to treat a zero return as absent when deciding whether INV-009
applies, which would leave the meaningless record in the store and require every
future reader to remember the same special case. Refusing it at the boundary
means there is no special case to remember.

### The parser refuses it too, with its own code

`amount_minor` on a payment event document is now a `POSITIVE_AMOUNT_MINOR`
column, and a zero or negative value is `NON_POSITIVE_AMOUNT`. The whole
document is rejected, atomically, as every invalid row already was.

This is what makes the guarantee hold in practice. Phase 2 established that
facts only enter through ingestion, so refusing the row is what stops such a
fact ever being stored.

`NON_POSITIVE_AMOUNT` is a separate code from `NEGATIVE_AMOUNT` because they are
separate rules on separate columns. A settlement fee of zero is a free
transaction and is valid; a capture of zero is not.

### Money stays signed

The constraint belongs to `PaymentEvent`, not to `Money`. A settlement net, an
adjustment, a fee, a tax and a payout total may all validly be zero, and a net
or an adjustment may be negative. Making `Money` positive would have broken
every one of those, and there are tests holding each open.

## Consequences

Good:

- The ADR-006 guarantee is no longer bypassable. Every non-empty set of returns
  now falls into partial, full, or exceeding the capture, and each carries a
  code, so the lifecycle logic has no zero path left.
- The model now enforces what its documentation always claimed, which is one
  fewer place where a reader can be misled by a docstring.
- The refusal happens at the boundary, so nothing downstream needs a special
  case for an event that moved nothing.

Costs and risks:

- Breaking twice over. The domain contract goes to 5.0.0 and the parser to
  3.0.0, because events and documents that were previously accepted are now
  refused.
- A database populated before this version could hold a fact carrying a zero
  event amount. Reconciling over it now raises during projection rather than
  producing a decision. That is the safe direction, since the alternative was
  resolving a settlement whose gross was never checked, but it means a run fails
  loudly rather than reporting one bad line. There is no such database yet,
  because decisions are not persisted and the fixtures are clean, and the
  behaviour is tested so it is a known consequence rather than a surprise.
- If a provider legitimately reports a zero-amount event, for instance a
  cancelled refund recorded as an event rather than omitted, this contract
  refuses the document. The right answer there is a lifecycle event type
  describing that state, not permitting zero amounts everywhere.

## Alternatives considered

**Treat a zero return as absent in the lifecycle logic.** Rejected. It leaves a
meaningless record in the store and creates a special case every future reader
has to know. The record should not exist.

**Make `Money` positive.** Rejected. Settlement nets, adjustments, fees, taxes
and payout totals may all validly be zero, and nets and adjustments may be
negative. The constraint is a property of an event, not of money.

**Emit an exception code for a zero-amount event instead of refusing it.**
Rejected. That would make an unreadable record into a reconciliation finding,
when it is really a defect in the document. Ingestion is where a malformed
document is refused, and it already refuses several other shapes this way.

**Allow zero but make INV-009 applicable when all returns are zero.** Rejected.
It fixes this specific bypass and leaves the underlying problem, which is that a
record describing nothing is allowed to exist and influence logic. The next
reader of that data would face the same trap in a different form.
