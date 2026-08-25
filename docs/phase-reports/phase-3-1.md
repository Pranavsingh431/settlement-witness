# Phase 3.1: Settlement gross must reconcile to its capture

- Date: 2026-08-25
- Exit gate: passed. See "Exit gate status".
- Domain contract version: 3.0.0 to 4.0.0
- Baseline version: 1.0.0, unchanged

## Scope

One new required invariant. No change to CSV formats, storage, snapshot wording,
matching rules, status derivation, reason-code derivation, AI scope, APIs or the
frontend.

## The defect, reproduced first

The Phase 3 baseline could resolve a case carrying an unexplained monetary
difference. Run against that code:

```text
capture gross      : 100000
settlement gross   :  80000
unexplained gap    :  20000 minor units

status             : RESOLVED
exception codes    : []
reason codes       : ['ALL_REQUIRED_INVARIANTS_PASSED']
invariants         : INV-001 PASSED, INV-002 PASSED, INV-003 PASSED,
                     INV-004 NOT_APPLICABLE
```

Every check passed, and each was right to pass. INV-001 found one currency.
INV-002 found the line internally consistent: 80000 minus a fee of 2000 and tax
of 360 gave exactly the declared net of 77640. INV-003 found the batch adding
up, because the payout total was that same net. INV-004 had nothing to check,
because nothing was returned.

Nothing compared the line's gross against the capture. Twenty thousand minor
units went missing and the decision said it was resolved, with a complete
evidence certificate behind it.

This is the worst failure this project can have. Not a missed exception, but a
confident resolution over a real difference, backed by evidence that is
genuinely complete. Everything downstream would treat it as settled.

## INV-009

Added to the catalogue, required for resolution, failure mapped to
`AMOUNT_MISMATCH`, evaluated by the baseline for every settlement line.

> For the direct baseline's supported shape, exactly one capture and no return
> events, the settlement line's gross equals the capture amount in the same
> currency.

| Situation | Outcome | Reason code |
| --- | --- | --- |
| One capture, no returns, same currency, equal amounts | `PASSED` | none |
| One capture, no returns, same currency, different amounts | `FAILED` | `SETTLEMENT_GROSS_DOES_NOT_MATCH_CAPTURE`, with `expected_minor` and `observed_minor` |
| One capture, no returns, different currency | `FAILED` | `CURRENCY_NOT_UNIFORM` |
| No capture | `INSUFFICIENT_INPUT` | none |
| Multiple captures, or anything returned | `NOT_APPLICABLE` | none |

`NOT_APPLICABLE` is determinate and does not block a resolution by itself. That
is safe because every shape it covers already carries a non-resolution code from
the baseline: `UNSUPPORTED_STATE` for multiple captures or a full return,
`PARTIAL_REFUND` for a partial one. A test asserts that directly, because it is
the kind of gap that would otherwise open later.

A currency difference fails rather than converting. This layer has no exchange
rate, and inventing one would turn a real break into an argument about which
rate and which rounding.

## Measured behaviour after the fix

| Case | Status | INV-009 | Codes |
| --- | --- | --- | --- |
| Gross 80000 against capture 100000 | `EXCEPTION` | `FAILED`, expected 100000, observed 80000 | `AMOUNT_MISMATCH` |
| Gross equals capture | `RESOLVED` | `PASSED` | none |
| No capture | `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_INPUT` | `INSUFFICIENT_EVIDENCE` |
| Two captures | `EXCEPTION` | `NOT_APPLICABLE` | `UNSUPPORTED_STATE` |
| Partial refund | `EXCEPTION` | `NOT_APPLICABLE` | `PARTIAL_REFUND` |
| Currency mismatch | `EXCEPTION` | `FAILED`, `CURRENCY_NOT_UNIFORM` | `AMOUNT_MISMATCH`, `CURRENCY_MISMATCH` |

## Demo fixtures are unchanged

```text
statuses  : {'RESOLVED': 1, 'EXCEPTION': 2, 'PENDING': 0, 'INSUFFICIENT_EVIDENCE': 0}
exceptions: {'PARTIAL_REFUND': 1, 'UNSUPPORTED_STATE': 1}

  line-0001    EXCEPTION    INV-009=NOT_APPLICABLE     ['PARTIAL_REFUND']
  line-0002    RESOLVED     INV-009=PASSED             []
  line-0003    EXCEPTION    INV-009=NOT_APPLICABLE     ['UNSUPPORTED_STATE']
```

The one resolving line still resolves, and now demonstrably settles the full
amount that was captured rather than merely being internally consistent.

## A documentation defect found in passing

`docs/domain-contract.md` still said version 2.0.0 and pointed at
`docs/schema/v2/`. It drifted during Phase 3, which bumped the contract to 3.0.0
without updating that page. Corrected to 4.0.0 and `docs/schema/v4/`, with a
table recording every major version so far and why each was breaking, so the
next drift is visible rather than silent.

## Versioning

Breaking: a decision that 3.0.0 resolved now needs a further passing check. The
domain version goes to 4.0.0 and the schema to `docs/schema/v4/`. `v3` was
removed on the same reasoning applied to `v1` and `v2` before it: it described a
contract with a known correctness gap, and nothing consumes it because decisions
are still not persisted.

The baseline version stays at 1.0.0. Its matching rules did not change.

## Verification results

Host: macOS on arm64, uv 0.12.5, Python 3.12.12, Node 24.19.0, pnpm 10.15.0.

| Command | Exit | Observed |
| --- | --- | --- |
| `git diff --check` | 0 | No whitespace errors |
| `make schema` | 0 | Regenerated to `docs/schema/v4/` |
| `make ci` | 0 | All nine core checks passed |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy` | 0 | `Success: no issues found in 62 source files` |
| `uv run pytest` | 0 | `542 passed`, `Total coverage: 100.00%` |
| Clean database, import, reconcile twice | 0 | Byte identical, 11360 bytes |

## Tests

542 total, up from 515. 27 added.

| Area | Added | Covers |
| --- | --- | --- |
| INV-009 as an invariant | 13 | Equal and unequal amounts in both directions, both reported amounts, no capture, no events, two captures, all three returning types, a full return, currency difference including when the numbers agree, required for resolution, missing-input policy |
| INV-009 through the baseline | 14 | The regression case, which invariant fails, the reported amounts, the derived reason, the equal case still resolving, a gross above the capture, a one unit difference, and every lifecycle shape |

The required regression test is `TestSettlementGrossMustMatchItsCapture`: one INR
capture of 100000, one settlement line for the same payment with gross 80000 and
internally correct fee, tax and net, a matching payout total, and no returns. It
asserts the status is `EXCEPTION`, `AMOUNT_MISMATCH` is present, INV-009 is
`FAILED` with expected 100000 and observed 80000, and that the equal-amount case
still resolves.

Two further tests guard the edges a tolerance would have hidden: a settled gross
above the capture, and a difference of one minor unit. Both are exceptions,
because the check is equality and there is no band to hide inside.

## Deferred, unchanged from Phase 3

Settlement windows and `MISSING_SETTLEMENT`, fuzzy and AI-assisted candidate
generation, decision persistence, the evaluator harness, `FEE_MISMATCH`, bank
statement reconciliation, APIs and frontend, migrations and audit retention.

One item is worth naming that this phase created. If a provider genuinely
settles a gross that differs from the capture for a documented reason, INV-009
is too strict, and the right answer is a lifecycle record type describing that
adjustment rather than a tolerance. A tolerance would be a threshold nobody
chose, hiding every difference below it forever. Recorded in ADR-006.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| `git diff --check` | Passed | Exit 0 |
| `make schema` | Passed | Regenerated to `docs/schema/v4/` |
| `make ci` | Passed | Exit 0, all nine checks |
| Clean database, import, reconcile twice, byte identical | Passed | 11360 bytes, identical |
| 100 percent backend coverage | Passed | `Total coverage: 100.00%` |
| INV-009 required and mapped to `AMOUNT_MISMATCH` | Met | Catalogue flag and baseline mapping, both tested |
| Every candidate carries INV-009 | Met | Asserted against `REQUIRED_FOR_RESOLUTION` |
| No direct single-capture mismatch resolves | Met | The regression test, plus the two edge tests |
| Existing valid results preserved | Met | Demo fixtures unchanged, 1 resolved and 2 exceptions |
