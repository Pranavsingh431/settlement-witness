# Reconciliation baseline, version 1.0.0

This describes what the deterministic baseline matches, what it refuses to
match, and what a result from it does and does not mean. The code in
`backend/app/reconciliation/` is the definition; this page explains it.

The baseline resolves one shape. Everything else becomes an honest
non-resolution.

## What it matches on

Exact references only.

| Link | On |
| --- | --- |
| Settlement line to payment events | Exact `payment_id` |
| Settlement line to its payout | Exact `payout_id` |

## What it will not match on

Amounts that look close. Timestamps that sit nearby. Text that reads similarly.
A reference it had to guess at.

None of these are missing features. A match made on similarity produces a
resolution nobody can check, and an unverifiable resolution is the failure this
project exists to avoid. If the direct reference is not there, the answer is
that the system does not know.

## The one shape it resolves

- exactly one capture for the payment;
- nothing refunded, reversed or charged back;
- a payout in the snapshot with that exact `payout_id`;
- every applicable invariant determinate and passing.

Anything else is reported, never guessed at.

## Non-resolutions

| Situation | Code | Why not something else |
| --- | --- | --- |
| No payment fact for the line's `payment_id` | `MISSING_PAYMENT` | The line names a payment no source fact describes |
| No payout fact for the line's `payout_id` | `INSUFFICIENT_EVIDENCE` | Nothing to check the batch total against, so nothing can be concluded |
| Payment events but no capture | `INSUFFICIENT_EVIDENCE` | The ceiling for INV-004 is unknown |
| Two or more captures | `UNSUPPORTED_STATE` | Choosing one would decide which the line settled, and nothing says |
| Return dated before its capture | `OUT_OF_ORDER_EVENT` | The sequence is impossible as reported |
| Part of the capture returned | `PARTIAL_REFUND` | A balance remains, and the baseline has no rule for how it settles |
| All of the capture returned | `UNSUPPORTED_STATE` | A returned payment that still settled is a state the contract does not describe |
| More returned than captured | `AMOUNT_MISMATCH` | INV-004 fails: a real break, not an unsupported shape |
| Declared net contradicts the formula | `AMOUNT_MISMATCH` | INV-002 fails |
| Line and payment in different currencies | `CURRENCY_MISMATCH` | INV-001 fails |
| Payout total disagrees with its lines | `AMOUNT_MISMATCH` | INV-003 fails, snapshot relative. See below |

`AMOUNT_MISMATCH` rather than `FEE_MISMATCH` for a net that does not add up.
When a declared net disagrees with the formula there is no way to tell whether
the fee is wrong or the net is, and naming the fee would be a guess.
`FEE_MISMATCH` needs a second source of fee truth, which this baseline does not
have.

`MISSING_SETTLEMENT` is never emitted. It requires knowing when a settlement
stops being plausibly late, which is the settlement window policy, and that is
still deferred.

## Invariants evaluated

Per settlement line, all four required ones:

| Invariant | Checked against |
| --- | --- |
| INV-001 | The line's declared net and every amount on the payment's events |
| INV-002 | The line's own breakdown |
| INV-003 | The payout, against the lines this snapshot has for that exact payout ID |
| INV-004 | The payment's captures and returns |

INV-006 and INV-007 are the verifier's own checks and run through
`verify_decision`, as they have since Phase 1.1.

## Payout grouping is a snapshot, not a completeness claim

This is the most important limitation on the page.

A payout document says what the batch totalled. It does not say which settlement
lines composed it, which is why `project_payout` leaves `settlement_line_ids`
empty rather than inventing them. INV-003 needs a payout that declares its
contents, so one is built from the lines the snapshot actually holds for that
payout ID.

**INV-003 passing means the payout total equals the sum of the lines this system
holds. It does not mean the provider's export was complete, and it cannot.** A
line that was never imported leaves no trace to notice.

The consequence runs both ways and is tested. A payout whose sibling line was
never imported reports a total mismatch, which is correct for this snapshot and
is not evidence that the provider got it wrong. And a payout that appears to
balance may be balancing over an incomplete set.

That is why a missing payout produces `INSUFFICIENT_EVIDENCE` rather than a
resolution, and why every result here is described as snapshot relative. Making
a real completeness claim needs a provider-side control total, which no document
in this contract carries yet.

## Determinism

The same facts always produce the same result, byte for byte.

- Facts are read once, through `SourceFactRepository.fact_index()`, and never
  re-read during a run.
- Every collection is sorted: facts by record ID, lines by settlement line ID,
  events by occurred-at then event ID, payouts by payout ID.
- Evidence, linked record IDs, linked event IDs, invariant results, exception
  codes and reason codes are each ordered.
- `created_at` on every decision is the snapshot's `as_of`, which is the latest
  observation time among the facts. A wall clock would make every run differ in
  a way that has nothing to do with the evidence.
- JSON renders with sorted keys and fixed indentation.

A result that reorders between runs cannot be diffed, and a baseline nobody can
diff is one nobody can trust to have stayed the same when something else
changed.

The snapshot fingerprint is a SHA-256 over each fact's record ID and payload
hash, sorted. It changes when a fact is added, removed or replaced, and does not
change when the same facts are read in a different order.

## Decision authority

The engine builds `DecisionCandidate` objects. It never assigns a status and
never writes a reason code. Every decision comes from `verify_decision`.

The line between what a caller supplies and what the verifier decides:

| Field | Who | Why |
| --- | --- | --- |
| `exception_codes` | The caller | Findings the caller made while examining the case, such as a refund dated before its capture. The verifier has no way to rediscover them. |
| `status` | The verifier | Derived from the backing. `DecisionCandidate` has no status field. |
| `reason_codes` | The verifier | Its own account of which rule fired. `DecisionCandidate` has no reason codes field. |

Reason codes were removed from `DecisionCandidate` rather than accepted and
ignored. A field whose value is discarded is a lie about what a caller controls.
A caller that could supply its own reason codes could describe a decision as
having been reached for reasons that had nothing to do with it, and nothing
would contradict them.

Reason codes are a pure function of the verified evidence, the invariant
results and the exception codes. Two decisions with the same backing carry the
same reasons, whatever the callers that built them believed.

## Running it

```bash
make db-setup
make import-fixtures
make reconcile-fixtures
```

Or directly:

```bash
cd backend && uv run python -m app.reconcile_cli \
  --database ../data/generated/settlement.sqlite [--summary-only]
```

The output is JSON with the snapshot fingerprint, the baseline and contract
versions, the fact and line counts, every decision ordered by settlement line
ID, and counts by status and by exception code. Every status appears in the
counts even at zero: a printed zero is one somebody checked, while a missing key
is ambiguous between none and not measured.

## What the demo fixtures show

Three settlement lines, and exactly one resolves.

| Line | Status | Code | Why |
| --- | --- | --- | --- |
| `line-0001` | `EXCEPTION` | `PARTIAL_REFUND` | `pay-0001` was captured for 1000000 and refunded 150000 |
| `line-0002` | `RESOLVED` | none | `pay-0002` was captured and never returned |
| `line-0003` | `EXCEPTION` | `UNSUPPORTED_STATE` | `pay-0003` was charged back in full and still settled |

One in three is the honest number for this baseline on this data. A baseline
that resolved all three would be guessing at two of them.
