# Phase 3.2: Payment event amounts are strictly positive

- Date: 2026-08-25
- Exit gate: passed. See "Exit gate status".
- Domain contract version: 4.0.0 to 5.0.0
- Parser version: 2.0.0 to 3.0.0
- Baseline version: 1.0.0, unchanged

## Scope

One constraint, applied at the domain model and at the parser. No change to
matching logic, payout snapshot semantics, source-fact storage, APIs, frontend,
AI scope, or fixture results.

## The defect, reproduced first

`PaymentEvent` documented its amount as a positive magnitude and did not enforce
it. Run against the Phase 3.1 code, with one capture of 100000 and a refund of
zero:

```text
status          : RESOLVED
exception codes : []
invariants      : INV-001 PASSED, INV-002 PASSED, INV-003 PASSED,
                  INV-004 PASSED, INV-009 NOT_APPLICABLE
```

INV-004 passed, because nothing exceeded the capture. INV-009 became not
applicable, because a return existed. No lifecycle code fired, because zero is
neither a partial refund nor a full one.

The consequence is worse than a stray resolution. It made the Phase 3.1 fix
bypassable in one line:

```text
with gross 80000 against capture 100000 plus a zero refund:
  status: RESOLVED | INV-009: NOT_APPLICABLE
```

Adding a zero-amount refund switched off the settlement gross check. Twenty
thousand minor units unexplained, and INV-009, added specifically to catch that,
stood down because a return technically existed.

## The fix

At the contract boundary, in two places.

**The domain model.** `PaymentEvent` now refuses an amount of zero or less, for
all four event types, at construction. An event amount is the magnitude of
something that happened; zero is the absence of it rather than a smaller
version.

Fixing it in the model rather than in the lifecycle logic was deliberate. The
alternative was to treat a zero return as absent when deciding whether INV-009
applies, which leaves the meaningless record in the store and creates a special
case every future reader has to know.

**The parser.** `amount_minor` on a payment event document is now a
`POSITIVE_AMOUNT_MINOR` column, and zero or negative is `NON_POSITIVE_AMOUNT`.
The whole document is rejected, atomically. Phase 2 established that facts only
enter through ingestion, so this is what stops such a fact ever being stored.

`NON_POSITIVE_AMOUNT` is a separate code from `NEGATIVE_AMOUNT` because they are
separate rules on separate columns. A settlement fee of zero is a free
transaction and valid; a capture of zero is not.

**Money stays signed.** The constraint belongs to `PaymentEvent`. A settlement
net, an adjustment, a fee, a tax and a payout total may all validly be zero, and
a net or an adjustment may be negative. Six tests hold each of those open.

## Measured behaviour after the fix

Domain model, all eight combinations:

```text
CAPTURE     of  0: refused        CAPTURE     of -1: refused
REFUND      of  0: refused        REFUND      of -1: refused
REVERSAL    of  0: refused        REVERSAL    of -1: refused
CHARGEBACK  of  0: refused        CHARGEBACK  of -1: refused
```

Ingestion:

```text
invalid_zero_amount.csv     -> REJECTED_INVALID, 0 facts written
  row 3: NON_POSITIVE_AMOUNT amount_minor must move money, so it must be
         greater than zero, got 0
invalid_negative_amount.csv -> REJECTED_INVALID
  row 2: NON_POSITIVE_AMOUNT ... got -1000
payment_events.csv          -> ACCEPTED, 5 rows
```

The zero-amount document contains a valid capture on row 2 and the zero refund
on row 3. Nothing was written, including the valid row, because imports are
atomic.

## The lifecycle logic has no zero path left

Every returning event now moves at least one minor unit, so a non-empty set of
returns always falls into one of three cases, each carrying a code:

| Returned against a capture of 100000 | Code |
| --- | --- |
| 40000 | `PARTIAL_REFUND` |
| 100000 | `UNSUPPORTED_STATE` |
| 150000 | `AMOUNT_MISMATCH`, from INV-004 failing |

A refund of one minor unit is a partial refund, tested explicitly, because that
is now the smallest return the contract allows.

## Demo fixtures are unchanged

```text
parser version: 3.0.0, contract 5.0.0
statuses  : {'RESOLVED': 1, 'EXCEPTION': 2, 'PENDING': 0, 'INSUFFICIENT_EVIDENCE': 0}
exceptions: {'PARTIAL_REFUND': 1, 'UNSUPPORTED_STATE': 1}
```

All three documents still import and the reconciliation result is identical, at
11360 bytes and byte identical across two runs.

## A consequence worth naming

A database populated before this version could hold a fact carrying a zero event
amount. Reconciling over it now raises during projection rather than producing a
decision.

That is the safe direction, because the alternative was resolving a settlement
whose gross was never checked. It does mean a run fails loudly rather than
reporting one bad line. There is no such database today, since decisions are not
persisted and the fixtures are clean, and the behaviour is tested so it is a
known consequence rather than a surprise. Recorded in ADR-007.

## Versioning

Breaking twice over.

| Version | From | To | Why |
| --- | --- | --- | --- |
| Domain contract | 4.0.0 | 5.0.0 | Events 4.0.0 accepted are refused at construction |
| Parser | 2.0.0 | 3.0.0 | Documents 2.0.0 accepted are refused at import |

Schema regenerated to `docs/schema/v5/`; `v4` removed on the same reasoning as
its predecessors. The baseline version is unchanged, because no matching rule
moved.

## Verification results

Host: macOS on arm64, uv 0.12.5, Python 3.12.12, Node 24.19.0, pnpm 10.15.0.

| Command | Exit | Observed |
| --- | --- | --- |
| `git diff --check` | 0 | No whitespace errors |
| `make schema` | 0 | Regenerated to `docs/schema/v5/` |
| `make ci` | 0 | All nine core checks passed |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy` | 0 | `Success: no issues found in 62 source files` |
| `uv run pytest` | 0 | `578 passed`, `Total coverage: 100.00%` |
| Clean database, import, reconcile twice | 0 | Byte identical, 11360 bytes |

## Tests

578 total, up from 542. 36 added.

| Area | Added | Covers |
| --- | --- | --- |
| Domain model | 14 | Zero and negative for all four event types, the message naming the type, one minor unit accepted, refusal outside CSV ingestion, and six holding Money's signedness open |
| Ingestion | 11 | Zero and negative rejected atomically with the new code, the valid row in the same document not written, a receipt still written, valid documents unaffected, and settlement lines and payouts still permitted zero components |
| Reconciliation | 6 | The former bypass unreachable at projection and at reconcile, the gross mismatch it hid unreachable, a real partial refund still reported normally, every return producing a non-resolution, and a one unit refund being partial |
| Parsing | 5 | The magnitude rule redirected to a column that still has it, a zero fee accepted, and the positive rule on event amounts |

One existing test was redirected rather than deleted:
`test_a_negative_magnitude_is_refused` used a payment event amount, which is no
longer a magnitude column. It now uses a settlement fee, and a new test covers
the positive rule separately, so both rules stay held.

## Deferred, unchanged

Settlement windows and `MISSING_SETTLEMENT`, fuzzy and AI-assisted candidate
generation, decision persistence, the evaluator harness, `FEE_MISMATCH`, bank
statement reconciliation, APIs and frontend, migrations and audit retention.

Added by this phase: if a provider legitimately reports a zero-amount event, for
instance a cancelled refund recorded rather than omitted, this contract refuses
the document. The right answer is a lifecycle event type describing that state,
not permitting zero amounts everywhere. Recorded in ADR-007.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| `git diff --check` | Passed | Exit 0 |
| `make schema` | Passed | Regenerated to `docs/schema/v5/` |
| `make ci` | Passed | Exit 0, all nine checks |
| Clean database import and reconcile determinism | Passed | Byte identical, 11360 bytes |
| 100 percent backend coverage | Passed | `Total coverage: 100.00%` |
| Zero capture refused by the model | Met | Tested for all four types |
| Zero refund, reversal, chargeback refused | Met | Same test, parametrised |
| Negative amount refused outside CSV ingestion | Met | Built directly from `PaymentEvent` |
| CSV zero rejected atomically with a new code | Met | `NON_POSITIVE_AMOUNT`, zero facts written |
| Valid positive events import and reconcile unchanged | Met | Fixtures identical at 11360 bytes |
| Former bypass cannot reach reconciliation | Met | Refused at construction, at import, and at projection |
| Money not made globally positive | Met | Six tests holding zero and negative open |
