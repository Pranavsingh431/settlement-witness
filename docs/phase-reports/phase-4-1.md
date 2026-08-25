# Phase 4.1: Correct exception-recall semantics

- Date: 2026-08-25
- Exit gate: passed. See "Exit gate status".
- Harness version: 1.0.0 to 2.0.0
- Generator 1.0.0, baseline 1.0.0, parser 3.0.0, domain 5.0.0, all unchanged

## Scope

One metric, renamed to what it measured, and the metric it was supposed to be
added beside it. No change to corpus generation, oracle construction, paired
controls, imports, baseline execution, or any scenario expectation.

## The defect

`exception_recall` counted an anomalous scenario as correct only when its entire
exception code set matched. That is exact-set accuracy under a recall label.

Measured against the Phase 4 code, dropping one of the two codes
`OUT_OF_ORDER_RETURN` expects:

```text
expected codes: {OUT_OF_ORDER_EVENT, PARTIAL_REFUND}
actual   codes: {PARTIAL_REFUND}

reported 'exception_recall' : 0.888889  (8/9)
```

Eight of nine anomalies matched exactly, so the metric reported that. But half
the findings on the affected case had in fact been made, and a metric called
recall should say so. The case scored zero when it deserved one half.

The mislabelling matters beyond tidiness. Recall and exact-set accuracy move in
opposite directions under over-reporting, so a single number reported under
either name hides the other failure mode.

## The fix

**`exception_recall` is now recall.** Denominator: total expected exception code
occurrences across all anomalous scenarios. Numerator: expected codes present in
the actual set. Summed across scenarios, so a case expecting two codes weighs
twice as much as one expecting a single code. Averaging per scenario would let a
system that always finds the easy single code look better than one finding most
of a harder pair.

Within one scenario, codes are compared as sets, so raising the same code twice
does not manufacture recall it did not earn.

**`exact_exception_set_accuracy` is the stricter signal, honestly named.**
Denominator: anomalous scenarios. Numerator: scenarios whose code set matched
exactly. It fails on a missing code and on an extra one.

| Expected | Actual | Recall | Exact set |
| --- | --- | --- | --- |
| Two codes | Both | 2/2 | 1/1 |
| Two codes | One of them | 1/2 | 0/1 |
| Two codes | Both plus an extra | 2/2 | 0/1 |
| Two codes | Neither | 0/2 | 0/1 |

Both appear at the top level, in the per-template breakdown, and in the rendered
JSON. Each scenario result also carries `expected_exception_code_count` and
`matched_exception_code_count`, because a rate is only checkable against the
counts it was computed from.

**pass@1 is unchanged.** It remains the strict composite: exact status, exact
exception code set, exact evidence IDs. A half-recalled case still fails it,
because the set did not match.

## Measured behaviour after the fix

Regrading the same perturbation:

| Case | Recall | Exact set |
| --- | --- | --- |
| Unmodified | 1.0 (11/11) | 1.0 (9/9) |
| One of two codes present | 0.909091 (10/11) | 0.888889 (8/9) |
| Both codes plus an extra | 1.0 (11/11) | 0.888889 (8/9) |

Per template, for the affected scenario: recall 1/2, exact-set 0/1, pass@1 0.0.
The two metrics now disagree on the same run, which is the point of reporting
both.

## Public corpus results

`make benchmark-evaluate`, seed 20260701, 59 scenarios, harness 2.0.0.

```text
decision_accuracy                    1.0    59/59
exception_recall                     1.0    33/33
exact_exception_set_accuracy         1.0    27/27
evidence_completeness                1.0    59/59
evidence_verification_completeness   1.0    59/59
false_resolution_rate                0.0    0/27
pass_at_1                            1.0    59/59
```

The denominators now differ, which is the visible effect of the correction. 27
anomalous scenarios expect 33 code occurrences between them, because
`CURRENCY_MISMATCH` and `OUT_OF_ORDER_RETURN` each expect two.

Per template:

| Template | Recall | Exact set |
| --- | --- | --- |
| `CAPTURE_GROSS_MISMATCH` | 3/3 | 3/3 |
| `CURRENCY_MISMATCH` | 6/6 | 3/3 |
| `MISSING_PAYMENT` | 3/3 | 3/3 |
| `MISSING_PAYOUT` | 3/3 | 3/3 |
| `MULTIPLE_CAPTURES` | 3/3 | 3/3 |
| `NET_FORMULA_MISMATCH` | 3/3 | 3/3 |
| `OUT_OF_ORDER_RETURN` | 6/6 | 3/3 |
| `PARTIAL_REFUND` | 3/3 | 3/3 |
| `PAYOUT_TOTAL_MISMATCH` | 3/3 | 3/3 |
| `RESOLVED_DIRECT` | null (0/0) | null (0/0) |

`RESOLVED_DIRECT` has no anomalies, so both exception metrics are absent rather
than 1.0, following the existing zero-denominator policy. Its decision accuracy
is still measured at 1.0.

The values are unchanged at 1.0 because the baseline raises exactly the expected
codes on this corpus. The correction shows up in the denominators and in the
per-template split, not in the headline. That is expected: the corpus has no
case where the baseline finds some codes and misses others, which is precisely
the case the old metric would have mis-scored.

## Versioning

Harness 1.0.0 to 2.0.0. The meaning of a reported metric changed, so a report
from 1.0.0 and one from 2.0.0 give different numbers for the same run and must
not be compared. The version is recorded in every report, which is what makes
that detectable.

Generator, manifest, corpus scenarios, domain schema, ingestion, reconciliation
and storage are untouched. The corpus is byte identical to Phase 4's.

## Verification results

Host: macOS on arm64, uv 0.12.5, Python 3.12.12, Node 24.19.0, pnpm 10.15.0.

| Command | Exit | Observed |
| --- | --- | --- |
| `git diff --check` | 0 | No whitespace errors |
| `make ci` | 0 | All nine core checks passed |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy` | 0 | `Success: no issues found in 75 source files` |
| `uv run pytest` | 0 | `682 passed`, `Total coverage: 100.00%` |
| `make benchmark-generate` then `make benchmark-evaluate`, twice | 0 | Byte identical, 7927 bytes |

## Tests

682 total, up from 669. 13 added.

| Test | Requires |
| --- | --- |
| One of two expected codes present | Recall 1/2, exact-set 0/1 |
| Expected codes plus an extra | Recall 1.0, exact-set 0 |
| The two metrics differ on one run | Not equal, which is why both are reported |
| Recall counted over code occurrences | Denominator equals total expected codes |
| A repeated code | Counted once, sets not multisets |
| Every code missing | Recall 0.0, so the metric is not always near one |
| pass@1 after the split | Unchanged, half-recalled case still fails |
| Per-scenario counts | `expected_exception_code_count` and `matched_exception_code_count` present |
| A clean run | Both metrics 1.0 |
| Rendered report | Both metrics at top level and per template |
| Harness version | 2.0.0 |
| Empty manifest | Both metrics null |
| Rendered empty report | Both show `null`, denominator 0 |

The all-control assertion in the existing zero-denominator test was extended to
cover the new metric alongside the old one.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| `git diff --check` | Passed | Exit 0 |
| `make ci` | Passed | Exit 0, all nine checks |
| Generate and evaluate twice, byte compare | Passed | 7927 bytes, identical |
| Public report shows both metrics separately | Passed | Recall 33/33, exact-set 27/27, different denominators |
| Recall defined over code occurrences | Met | Summed, deduplicated within a scenario |
| Exact-set accuracy over anomalous scenarios | Met | 27 anomalies, 27 denominator |
| Zero expected codes gives null | Met | `RESOLVED_DIRECT` breakdown and empty manifest |
| pass@1 unchanged | Met | Still the strict composite, asserted after the split |
| Harness version bumped | Met | 2.0.0, recorded in every report |
| Generator and corpus unchanged | Met | Documents byte identical to Phase 4 |
