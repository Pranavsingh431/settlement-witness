# ADR-006: A settled gross must equal the capture it settles

- Status: Accepted
- Date: 2026-08-25
- Supersedes: none
- Superseded by: none
- Related: [ADR-002](ADR-002-domain-contract-and-verifier-authority.md),
  [ADR-005](ADR-005-exact-reference-matching-and-snapshot-payouts.md)

## Context

The Phase 3 baseline could resolve a case carrying an unexplained monetary
difference. Reproduced against that code:

```text
capture gross      : 100000
settlement gross   :  80000
unexplained gap    :  20000 minor units

status             : RESOLVED
exception codes    : []
invariants         : INV-001 PASSED, INV-002 PASSED, INV-003 PASSED,
                     INV-004 NOT_APPLICABLE
```

Every check passed, and each was right to. INV-001 found one currency. INV-002
found the line internally consistent: 80000 minus fee and tax gave exactly the
declared net. INV-003 found the batch adding up, because the payout total was
the sum of the line nets. INV-004 had nothing to check, because nothing was
returned.

No invariant compared the line's gross against the capture. Twenty thousand
minor units went missing and the decision said it was resolved, with a complete
evidence certificate.

This is the worst failure mode the project has: not a missed exception, but a
confident resolution over a real difference, backed by evidence that is
genuinely complete. Everything downstream would treat it as settled.

## Decision

### INV-009: for a direct single-capture case, settled gross equals captured amount

Added to the catalogue and required for resolution. A failure maps to
`AMOUNT_MISMATCH`, and the baseline evaluates it for every settlement line.

| Situation | Outcome | Reason code |
| --- | --- | --- |
| One capture, no returns, same currency, equal amounts | `PASSED` | none |
| One capture, no returns, same currency, different amounts | `FAILED` | `SETTLEMENT_GROSS_DOES_NOT_MATCH_CAPTURE` with expected and observed |
| One capture, no returns, different currency | `FAILED` | `CURRENCY_NOT_UNIFORM` |
| No capture | `INSUFFICIENT_INPUT` | none |
| Multiple captures, or anything returned | `NOT_APPLICABLE` | none |

### It applies only to the shape a direct match supports

Exactly one capture and nothing returned. The equality stops describing the case
once money has gone back, because a settled gross and a capture then differ for
reasons the records may legitimately explain, and this baseline has no rule for
which. It also stops describing the case when there is more than one capture,
because there would be a choice about which one the line settled, and ADR-005
already refuses to make that choice.

`NOT_APPLICABLE` is determinate and does not block a resolution on its own. That
is safe here because every shape it applies to already carries a non-resolution
code from the baseline: `UNSUPPORTED_STATE` for multiple captures or a full
return, `PARTIAL_REFUND` for a partial one, `OUT_OF_ORDER_EVENT` for a return
before its capture. Nothing passes by being not applicable. There is a test for
that, because it is the kind of gap that would otherwise open later.

### A currency difference fails rather than converting

This layer has no exchange rate. Converting would turn a real break into an
argument about which rate and which rounding, and the answer would depend on
something no document in the contract carries.

Both INV-001 and INV-009 fail in that case, which is not redundant: INV-001 says
the amounts cannot be compared at all, and INV-009 says the comparison it needed
to make was one of them.

## Consequences

Good:

- An unexplained monetary difference can no longer resolve. That is the single
  most valuable thing this contract enforces.
- The failure reports both amounts, so the gap is readable without going and
  finding the capture.
- Fee and tax deductions are still allowed, because the check is on gross rather
  than on net. A line that settles the full capture minus a fee is exactly what
  INV-002 and INV-009 together describe.

Costs and risks:

- Breaking. A decision that 3.0.0 resolved now needs a further passing check, so
  the domain version goes to 4.0.0 and the schema to `docs/schema/v4/`.
- The baseline resolves strictly less than before. On the demo fixtures nothing
  changed, because the one resolving line already settled its full capture, but
  a real merchant file with legitimate gross-level adjustments would now report
  exceptions.
- That last point is the real risk. If a provider genuinely settles a gross that
  differs from the capture for a documented reason, this invariant is too
  strict and the right answer is a new lifecycle record type describing that
  adjustment, not a tolerance. A tolerance would be a threshold nobody chose,
  hiding differences below it forever.

## Alternatives considered

**A tolerance band.** Rejected. Any tolerance is a number nobody chose, and
every difference smaller than it becomes permanently invisible. A reconciliation
system whose answer is "close enough" has given up the thing it exists for.

**Checking net against capture instead of gross.** Rejected. The net is
correctly below the capture by the fee and the tax, so the comparison would have
to model deductions to mean anything, and INV-002 already does that. Gross is
the amount that should equal what was taken.

**Making it a warning rather than a required invariant.** Rejected. A warning on
a resolved decision is a resolution, and the whole point is that this case must
not resolve.

**Applying it to refunded payments too, by netting the returns.** Rejected for
now. It would require deciding how a partial refund is expected to affect a
settlement, which is a policy this contract has not made. Those cases already do
not resolve, so nothing is lost by leaving the check not applicable.

**Converting currencies to compare.** Rejected. See decision 3.
