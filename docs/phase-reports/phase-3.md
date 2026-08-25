# Phase 3: Deterministic reconciliation baseline

- Date: 2026-08-25
- Exit gate: passed. See "Exit gate status".
- Baseline version: 1.0.0
- Domain contract version: 2.0.0 to 3.0.0

## Scope

The deterministic reconciliation baseline over the complete accepted fact index.
Evidence-backed decisions for direct, unambiguous links only.

No AI, fuzzy matching, embeddings, semantic search, frontend, API, or decision
persistence.

## What was built

`backend/app/reconciliation/`, three modules and no dependency beyond the domain
and ingestion packages already present.

| Module | Holds |
| --- | --- |
| `snapshot.py` | `FactSnapshot`, built once from the index, with the fingerprint and the ordered projections |
| `baseline.py` | The per-line engine: exact-reference matching, invariant evaluation, exception detection |
| `batch.py` | `ReconciliationBatch`, the ordered result and its summary counts |

Plus `app/domain/payout_snapshot.py` for building a payout that declares the
lines the snapshot holds, and `app/reconcile_cli.py` behind
`make reconcile-fixtures`.

## Contract change: reason codes belong to the verifier

`DecisionCandidate` lost its `reason_codes` field. `verify_decision` now derives
reason codes from the verified evidence, the invariant results and the exception
codes, through a new pure function `derive_reason_codes`.

Exception codes are still supplied by the caller and still carried across. An
exception code is a finding the engine made and the verifier cannot rediscover.
A reason code is the verifier's account of which rule fired.

The field was removed rather than accepted and ignored, because a field whose
value is discarded is a lie about what a caller controls.

Breaking, so the domain version goes to 3.0.0 and the schema to
`docs/schema/v3/`. `v2` was removed on the same reasoning as `v1` before it: it
described a contract in which candidate reason codes were meaningful, which is
no longer true, and nothing consumes it because decisions are still not
persisted.

## A defect found while verifying the demo output

The first run over the fixtures produced this:

```text
line-0001  EXCEPTION  ['PARTIAL_REFUND']    ['REQUIRED_INVARIANT_FAILED']
line-0003  EXCEPTION  ['UNSUPPORTED_STATE'] ['REQUIRED_INVARIANT_FAILED']
```

No invariant failed on either line. Every one of them passed. The reason code
was a per-status fallback that fired whenever nothing more specific applied, and
it named a rule that had never run.

Fixed twice over. A new reason code, `EXCEPTION_CODE_REPORTED`, covers the real
case: nothing failed, nothing was missing, and the case still cannot be settled
because of something found while examining it. And the per-status fallback map
was deleted rather than corrected. With the new code it was unreachable, and an
unreachable table of default reasons is exactly how the wrong one gets printed
again later. Calling the derivation with a status that does not follow from the
backing now raises instead.

The corrected output:

```text
line-0001  EXCEPTION  ['PARTIAL_REFUND']    ['EXCEPTION_CODE_REPORTED']
line-0003  EXCEPTION  ['UNSUPPORTED_STATE'] ['EXCEPTION_CODE_REPORTED']
```

## Demo fixture results

Three settlement lines. One resolves.

```json
{
  "fact_count": 10,
  "settlement_line_count": 3,
  "status_counts": {
    "RESOLVED": 1,
    "EXCEPTION": 2,
    "PENDING": 0,
    "INSUFFICIENT_EVIDENCE": 0
  },
  "exception_counts": { "PARTIAL_REFUND": 1, "UNSUPPORTED_STATE": 1 }
}
```

| Line | Status | Exception | Reason | Why |
| --- | --- | --- | --- | --- |
| `line-0001` | `EXCEPTION` | `PARTIAL_REFUND` | `EXCEPTION_CODE_REPORTED` | `pay-0001` captured 1000000, refunded 150000 |
| `line-0002` | `RESOLVED` | none | `ALL_REQUIRED_INVARIANTS_PASSED` | `pay-0002` captured and never returned |
| `line-0003` | `EXCEPTION` | `UNSUPPORTED_STATE` | `EXCEPTION_CODE_REPORTED` | `pay-0003` charged back in full and still settled |

One in three is the honest number for this baseline on this data. A baseline
that resolved all three would be guessing at two of them.

## Matching rules

Exact references only.

| Link | On |
| --- | --- |
| Settlement line to payment events | Exact `payment_id` |
| Settlement line to its payout | Exact `payout_id` |

Never on amount similarity, timestamp proximity, text, or a guessed reference. A
match made on similarity produces a resolution nobody can check.

## Non-resolutions

| Situation | Code |
| --- | --- |
| No payment fact for the line's `payment_id` | `MISSING_PAYMENT` |
| No payout fact for the line's `payout_id` | `INSUFFICIENT_EVIDENCE` |
| Payment events but no capture | `INSUFFICIENT_EVIDENCE` |
| Two or more captures | `UNSUPPORTED_STATE` |
| Return dated before its capture | `OUT_OF_ORDER_EVENT` |
| Part of the capture returned | `PARTIAL_REFUND` |
| All of the capture returned | `UNSUPPORTED_STATE` |
| INV-001 failed | `CURRENCY_MISMATCH` |
| INV-002, INV-003 or INV-004 failed | `AMOUNT_MISMATCH` |

`AMOUNT_MISMATCH` rather than `FEE_MISMATCH` for a net that does not add up:
there is no way to tell whether the fee or the net is wrong, and `FEE_MISMATCH`
needs a second source of fee truth the baseline does not have.

`MISSING_SETTLEMENT` is never emitted, and a test asserts it.

## Payout grouping is snapshot relative

INV-003 is evaluated against the settlement lines the snapshot holds for that
exact payout ID, because a payout document says what the batch totalled and not
which lines composed it.

**INV-003 passing means the payout total equals the sum of the lines this system
holds. It does not mean the provider's export was complete, and it cannot.** A
line that was never imported leaves no trace to notice.

Stated in the code, in `docs/reconciliation-baseline.md`, in ADR-005 and here,
because it is exactly the kind of limitation that gets quietly forgotten and
then reported as a guarantee. A test covers the consequence directly: a payout
whose sibling line was never imported reports a total mismatch, which is correct
for that snapshot and is not evidence the provider got it wrong.

## Determinism

| Property | How |
| --- | --- |
| Facts read once | `SourceFactRepository.fact_index()`, into a `FactSnapshot` never re-read |
| Every collection ordered | Facts by record ID, lines by line ID, events by occurred-at then event ID, payouts by payout ID |
| Every decision field ordered | Evidence, linked IDs, invariant results, exception codes, reason codes |
| `created_at` | The snapshot's `as_of`, the latest observation time among the facts, not a wall clock |
| JSON | Sorted keys, fixed indentation |

Verified end to end: a clean database, the fixtures imported, then reconciled
twice.

```text
byte-for-byte identical: 10787 bytes, sha256 ddb263c8cd66d208a706e52d48a2a07c
snapshot fingerprint   : 7092df18a31c4b9386a3120e3134d686
```

## Verification results

Host: macOS on arm64, uv 0.12.5, Python 3.12.12, Node 24.19.0, pnpm 10.15.0.

| Command | Exit | Observed |
| --- | --- | --- |
| `git diff --check` | 0 | No whitespace errors |
| `make ci` | 0 | All nine core checks passed |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy` | 0 | `Success: no issues found in 62 source files` |
| `uv run pytest` | 0 | `515 passed`, `Total coverage: 100.00%` |
| `make schema` | 0 | Contract models changed, regenerated to `docs/schema/v3/` |
| `make db-setup` on a deleted database | 0 | Tables and triggers created |
| `make import-fixtures` | 0 | Three documents `ACCEPTED` |
| `make reconcile-fixtures`, twice | 0 | Byte identical output |

## Tests

515 total, up from 447. 68 added, all in `tests/reconciliation/`.

| File | Tests | Covers |
| --- | --- | --- |
| `test_baseline.py` | 27 | The resolvable shape, missing evidence, ambiguity, lifecycle ordering, arithmetic breaks, snapshot grouping, deferred behaviour |
| `test_determinism.py` | 29 | Byte identical runs, ordering, fingerprints, decision authority, reason derivation, summary counts |
| `test_cli.py` | 12 | The command, its output, and the demo results being what the report says |

Every required case is covered explicitly: one evidence-complete case resolves;
missing payment and missing payout do not; formula mismatch, currency mismatch,
payout mismatch, out-of-order event, partial refund and multi-capture ambiguity
do not; repeated runs are byte identical; every resolved decision's citations
resolve through the stored index; and candidate reason-code injection is
impossible because the field no longer exists, with a test asserting a
`ValidationError` when one is supplied.

Two dead branches were removed rather than left partially covered: a guard for a
`FAILED` invariant with no reason code, which `InvariantResult` already forbids,
and a lookup miss in the exception map, which a test now holds closed by
asserting the map covers every invariant the engine evaluates.

## Deferred to Phase 4

Nothing below is stubbed.

1. **Settlement windows and `MISSING_SETTLEMENT`.** Still undefined, so
   `TIMING_PENDING` and `MISSING_SETTLEMENT` have no emitter. A capture with no
   settlement line produces no decision at all.
2. **Fuzzy and AI-assisted candidate generation.** Worth building, and it has to
   be measured against this baseline rather than introduced in place of one.
3. **Decision persistence.** No table. What a stored decision means once the
   baseline version changes is a question this phase cannot answer.
4. **The evaluator harness.** `docs/evaluation-contract.md` defines the
   obligations; `benchmark/` is still empty. The demo fixtures are contract
   examples, not a dataset, and one resolved line is not a metric.
5. **`FEE_MISMATCH` in practice.** Needs a second source of fee truth.
6. **Bank statement reconciliation.** `BANK_TRANSACTION` still has no CSV schema
   and no projection.
7. **API endpoints and frontend.** Still `/health` and the phase 0 shell.
8. **Schema migrations and audit retention.** Unchanged from Phase 2.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| `git diff --check` | Passed | Exit 0 |
| `make ci` | Passed | Exit 0, all nine checks |
| `make schema` after contract change | Passed | Regenerated to `docs/schema/v3/` |
| Import into a clean database, reconcile twice, compare | Passed | Byte identical, 10787 bytes |
| 100 percent backend coverage | Passed | `Total coverage: 100.00%` |
| Reads only through `fact_index()` | Met | The snapshot is the only reader |
| Decisions only through `verify_decision` | Met | The engine builds candidates and nothing else |
| No status or reason code assigned by the engine | Met | Neither field exists on a candidate |
| No decision persistence | Met | No table added |
