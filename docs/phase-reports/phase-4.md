# Phase 4: Seeded scenario generator and evaluator harness

- Date: 2026-08-25
- Exit gate: passed. See "Exit gate status".
- Generator version: 1.0.0
- Harness version: 1.0.0
- Domain contract 5.0.0, parser 3.0.0, baseline 1.0.0, all unchanged

## Scope

A deterministic synthetic scenario generator and an evaluator that grades the
reconciliation baseline against an independently constructed oracle.

No AI provider, fuzzy matching, API, frontend, decision persistence, or database
schema change.

## Everything here is synthetic

Every scenario is generated. Every merchant identifier begins with
`SYNTH-MERCHANT`, every manifest and report carries `is_synthetic: true`, and no
number below is a statement about any real merchant's records. It is not
Razorpay production data and does not resemble it.

## What was built

`backend/app/benchmark/`, six modules and no new dependency.

| Module | Holds |
| --- | --- |
| `specs.py` | What a scenario is, and the ten template identities |
| `templates.py` | How each shape is built, and the oracle for each, with a rationale |
| `generator.py` | Seeded generation, CSV rendering, manifest assembly |
| `manifest.py` | The manifest format and expected evidence derivation |
| `evaluator.py` | Import, reconcile, grade, and the report |
| `metrics.py` | Rates, with their zero-denominator behaviour |

Plus `app/benchmark_cli.py` behind three Make targets, and
`benchmark/public-corpus.json`, the committed public configuration.

## Public corpus results

The exact output of `make benchmark-evaluate`, seed 20260701, 59 scenarios.

```text
imports  : payment_events.csv=ACCEPTED, settlement_lines.csv=ACCEPTED,
           payouts.csv=ACCEPTED

decision_accuracy                    1.0    59/59
exception_recall                     1.0    27/27
evidence_completeness                1.0    59/59
evidence_verification_completeness   1.0    59/59
false_resolution_rate                0.0    0/27
pass_at_1                            1.0    59/59

pairs    : 27 pairs, 27 both correct, 0 control failures, 0 anomaly failures,
           0 unpaired
failures : 0
```

Per template:

| Template | Scenarios | Accuracy | pass@1 | False resolutions |
| --- | --- | --- | --- | --- |
| `CAPTURE_GROSS_MISMATCH` | 3 | 1.0 | 1.0 | 0 |
| `CURRENCY_MISMATCH` | 3 | 1.0 | 1.0 | 0 |
| `MISSING_PAYMENT` | 3 | 1.0 | 1.0 | 0 |
| `MISSING_PAYOUT` | 3 | 1.0 | 1.0 | 0 |
| `MULTIPLE_CAPTURES` | 3 | 1.0 | 1.0 | 0 |
| `NET_FORMULA_MISMATCH` | 3 | 1.0 | 1.0 | 0 |
| `OUT_OF_ORDER_RETURN` | 3 | 1.0 | 1.0 | 0 |
| `PARTIAL_REFUND` | 3 | 1.0 | 1.0 | 0 |
| `PAYOUT_TOTAL_MISMATCH` | 3 | 1.0 | 1.0 | 0 |
| `RESOLVED_DIRECT` | 32 | 1.0 | 1.0 | 0 |

### What this number does and does not mean

The oracle was reasoned from the contract, template by template, before any of
it was run. It agreeing with the baseline on all 59 scenarios is a real result:
it means the expectations and the implementation were derived independently and
still match.

**It is not a claim of general performance.** The corpus covers exactly the
shapes the baseline was built for. A perfect score says the baseline still does
what it did, which makes this a regression guard, not a ranking.

It also means the corpus does not currently discriminate: it cannot tell a good
system from this one. Harder cases are needed before it can rank two candidates,
and that is named in the deferred list rather than left to be discovered.

## The oracle is independent

Each template declares its expected status, exception codes and evidence,
reasoned from the contract, with a one line rationale so a reader can check it
without executing anything. The generator fills in concrete record IDs. Nothing
consults the baseline.

Recording what the baseline produced and calling it expected is the mistake that
makes a benchmark worthless, because it cannot fail. Five tests hold this open
by handing the grader decisions the baseline never produced:

| Perturbation | Required result |
| --- | --- |
| Drop one decision | pass@1 falls, the scenario is a failure with `actual_status: null` |
| Replace every decision with `RESOLVED` | pass@1 falls, false resolution rate becomes 1.0 |
| Flip one status | Exactly one failure, attributed correctly |
| Add one exception code | Exactly one failure, code set compared exactly |
| Truncate one decision's evidence | Exactly one failure, evidence completeness falls |

The replacement test is the important one. A system that resolves everything
must score badly. If the oracle were derived from the baseline, it would still
pass.

## Paired controls

Every anomaly is generated alongside a control built from the same drawn
amounts, differing only in the one intended causal change, and records
`paired_control_id`. A pair is judged together in the report, and a failure is
attributed to the control or the anomaly.

The tests inspect the structured records, not rendered text. Each template
declares which record collection it may touch, and the test holds the actual
diff against that declaration.

One template reaches two collections, and a test caught it. The first version of
`NET_FORMULA_MISMATCH` wrote both the broken line and a matching payout, and the
pairing test correctly reported two differences. The fix was to derive a payout
total from the nets of its lines in the builder, so breaking the net is one edit
and the payout following is a consequence. A separate test confirms the payout
changed only in that derived field.

## No answer labels reach the system under test

The documents carry identifiers, amounts and timestamps only. Scenario
identifiers are opaque, of the form `SW-00001`, because they do reach the
documents; a name like `line-NET_FORMULA_MISMATCH-001` would let the system read
its answer off its own input.

A test checks every cell of every generated document against a list of thirteen
label strings. Measured on the generated corpus: zero hits in all three files.

## Determinism

| Property | How |
| --- | --- |
| Corpus | Same seed and configuration give byte-identical documents and manifest |
| Report | Same corpus renders byte-identical JSON, sorted keys |
| Amounts | Drawn on a step that keeps the fee and tax whole, so a control never fails INV-002 through rounding |
| Coverage | Fixed by the configuration, so a different seed varies data without dropping a template |
| Observation time | Fixed for the evaluation clock, so the reconciliation snapshot does not move with the wall clock |

Verified end to end: generated, then evaluated twice, byte identical at 6787
bytes.

## Metrics and zero denominators

Every rate carries its numerator and denominator, because a rate without its
denominator cannot be checked or combined across runs of different sizes.

**A rate over no cases is `null`, not zero and not one.** A corpus with no
anomalies has no measurable exception recall; reporting 1.0 there would say the
system caught every anomaly when it was never shown one. Tested with an
all-control corpus and with an empty manifest, including what the rendered JSON
shows.

**No pass@k.** The baseline is deterministic and runs once. A test asserts the
report has no such field.

A missing decision is graded as a failure of everything, never skipped.

## Public and private

`benchmark/public-corpus.json` is committed: seed 20260701, 59 scenarios,
covering every template. The corpus and reports it produces are written under
`data/generated/`, which git ignores, because the seed reproduces them exactly.

A private evaluation supplies its own configuration from outside the repository:

```bash
make benchmark-evaluate-private CONFIG=/path/to/private-corpus.json
```

Nothing about it is committed. A held-out set whose answers live in the
repository stops measuring generalisation and starts measuring memory.

## Verification results

Host: macOS on arm64, uv 0.12.5, Python 3.12.12, Node 24.19.0, pnpm 10.15.0.

| Command | Exit | Observed |
| --- | --- | --- |
| `git diff --check` | 0 | No whitespace errors |
| `make ci` | 0 | All nine core checks passed |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy` | 0 | `Success: no issues found in 75 source files` |
| `uv run pytest` | 0 | `669 passed`, `Total coverage: 100.00%` |
| `make benchmark-generate` | 0 | Three documents and a manifest written |
| `make benchmark-evaluate`, twice | 0 | Byte identical, 6787 bytes |
| `make schema` | 0 | Byte identical, no contract model touched |

## Tests

669 total, up from 578. 91 added.

| File | Tests | Covers |
| --- | --- | --- |
| `tests/benchmark/test_generator.py` | 43 | Determinism, coverage, paired controls, synthetic marking, no answer labels, and that every generated document imports through the real parser |
| `tests/benchmark/test_evaluator.py` | 33 | Running the harness, report determinism, oracle independence, paired breakdown, zero denominators, no pass@k, and a refused corpus |
| `tests/benchmark/test_cli.py` | 15 | Both subcommands, byte-identical output, the report file, and the committed public configuration |

Two pieces of dead code were removed rather than tested:
`CorpusManifest.entry_for_line`, which nothing called because the evaluator
indexes decisions rather than scenarios, and a stale helper found by coverage.

## Deferred to Phase 5

1. **AI-assisted candidate generation.** This is what the harness exists to
   measure. It must beat this floor without weakening any safety property.
2. **A discriminating corpus.** The public corpus scores perfectly, so it cannot
   rank two systems. Harder shapes are needed before it can.
3. **Adversarial and near-miss cases.** Nothing here models a case designed to
   be genuinely ambiguous rather than cleanly anomalous.
4. **Settlement windows and `MISSING_SETTLEMENT`.** Still deferred, so no
   template covers them.
5. **Decision persistence, APIs, frontend.** Unchanged.
6. **Real provider export quirks.** Generated scenarios are far simpler than
   real merchant data, and a system scoring well here may fail on the first real
   file it sees.

One limitation is worth naming plainly. The corpus and the baseline were written
by the same person in adjacent phases, so the shapes covered are the shapes that
were thought of. More scenarios of the same kind do not fix that.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| `git diff --check` | Passed | Exit 0 |
| `make ci` | Passed | Exit 0, all nine checks |
| Generate and evaluate twice, byte compare | Passed | 6787 bytes, identical |
| Clean database ingestion exercised end to end | Passed | Fresh temporary database per run, real parser and store |
| 100 percent backend coverage | Passed | `Total coverage: 100.00%` |
| Seed supplied by config and recorded everywhere | Met | Manifest and report both carry it |
| Oracle not derived from the baseline | Met | Five perturbation tests require the score to fall |
| Every anomaly has a valid paired control | Met | Checked on structured records |
| Public corpus at least 50 scenarios, every template | Met | 59 scenarios, all ten templates |
| No imported CSV includes expected labels | Met | Every cell checked against thirteen label strings |
| Zero-denominator behaviour defined | Met | `null`, tested including the rendered JSON |
| No pass@k reported | Met | Asserted on the report fields |
