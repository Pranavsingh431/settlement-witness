# Evaluation harness, version 2.0.0

This describes the seeded scenario generator and the evaluator that grades the
reconciliation baseline against it. The code in `backend/app/benchmark/` is the
definition; this page explains it.

`docs/evaluation-contract.md` states the obligations. This page states how they
are met.

## Everything here is synthetic

Every scenario is generated. Every merchant identifier begins with
`SYNTH-MERCHANT`, every manifest and every report carries `is_synthetic: true`,
and no number produced by this harness is a statement about any real merchant's
records. It is not Razorpay production data and does not resemble it.

## What a scenario is

A structured description of one reconciliation case: its payment events, its
settlement line, its payout, and what the contract says the outcome must be.

The expectation is part of the specification. It is reasoned from the contract
and written down beside the case, with a one line rationale so a reader can
check the oracle without running anything.

**The expectation is never obtained by running the baseline.** If it were, the
evaluation would confirm only that the baseline agrees with itself, and a
regression would move the expectation along with the behaviour.

## Templates

Ten shapes: one control that must resolve, and nine anomalies that must not.

| Template | Expected | Codes |
| --- | --- | --- |
| `RESOLVED_DIRECT` | `RESOLVED` | none |
| `NET_FORMULA_MISMATCH` | `EXCEPTION` | `AMOUNT_MISMATCH` |
| `CAPTURE_GROSS_MISMATCH` | `EXCEPTION` | `AMOUNT_MISMATCH` |
| `PAYOUT_TOTAL_MISMATCH` | `EXCEPTION` | `AMOUNT_MISMATCH` |
| `MISSING_PAYMENT` | `EXCEPTION` | `MISSING_PAYMENT` |
| `MISSING_PAYOUT` | `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_EVIDENCE` |
| `CURRENCY_MISMATCH` | `EXCEPTION` | `AMOUNT_MISMATCH`, `CURRENCY_MISMATCH` |
| `PARTIAL_REFUND` | `EXCEPTION` | `PARTIAL_REFUND` |
| `OUT_OF_ORDER_RETURN` | `EXCEPTION` | `OUT_OF_ORDER_EVENT`, `PARTIAL_REFUND` |
| `MULTIPLE_CAPTURES` | `EXCEPTION` | `UNSUPPORTED_STATE` |

## Paired controls

Every anomaly is generated alongside a control built from the same drawn
amounts, and the two differ only in the one intended causal change. The anomaly
records `paired_control_id`.

A pair that differed in three ways and resolved differently would have isolated
nothing. The tests check this on the structured records rather than on rendered
text, and each template declares which record collection it is allowed to touch.

One template reaches two collections. `NET_FORMULA_MISMATCH` breaks a line's
declared net, and a payout total is the sum of the nets of its lines, so the
payout follows. That is a consequence of one edit rather than a second edit, and
a separate test confirms the payout changed only in that derived field.

## Seeding and determinism

A seed is supplied explicitly, in a configuration file. Nothing is generated
without one, because a corpus whose seed nobody recorded cannot be reproduced
and therefore cannot be evidence of anything.

The seed is recorded in every manifest and every report. So are the generator,
harness, baseline, parser and domain contract versions, because two corpora with
the same seed differ if the rules changed between them, and a metric can change
meaning without the corpus changing at all.

Harness 2.0.0 corrected exception recall. Until then the metric counted an
anomaly only when its entire code set matched, which is exact-set accuracy under
a recall label. A report from 1.0.0 and one from 2.0.0 give different numbers
for the same run and must not be compared.

Randomness decides amounts only. Which shapes exist, how many of each, and what
each must produce are fixed by the configuration and the templates, so a
different seed varies the data without varying the coverage.

The same seed and configuration produce byte-identical documents, manifest and
report. Amounts are drawn on a step that keeps the two percent fee and eighteen
percent tax whole, so a control never fails INV-002 through rounding rather than
through a modelled fault.

## What the evaluator does

1. Creates a fresh temporary database, discarded afterwards. An evaluation is a
   measurement, not a place to accumulate state.
2. Imports every document through the real Phase 2 ingestion path, with its
   strict parser and its append-only store. A corpus the parser refuses stops
   the run rather than being scored on the part that loaded.
3. Reconciles through the real Phase 3 baseline.
4. Grades each decision against the manifest.

Nothing is simulated or stubbed.

## Metrics

| Metric | Denominator | Measures |
| --- | --- | --- |
| Decision accuracy | Every scenario | Exact status match |
| Exception recall | Expected code occurrences | Expected codes the system actually raised |
| Exact exception set accuracy | Anomalous scenarios | Code set matched exactly, no misses and no extras |
| Evidence completeness | Every scenario | Exact set of cited source record IDs |
| Evidence verification completeness | Every scenario | Every citation resolved against a stored fact |
| False resolution rate | Anomalous scenarios | Resolved when the oracle says it must not |
| pass@1 | Every scenario | Status, codes and evidence all exactly right |

### Recall and exact-set accuracy answer different questions

Exception recall is counted over code occurrences, not over scenarios. A case
expecting `OUT_OF_ORDER_EVENT` and `PARTIAL_REFUND` where only the refund was
raised contributes one out of two, because half the findings were in fact made.

Exact-set accuracy scores that same case zero, because the set did not match. It
is stricter in both directions: it fails on a missing code and on an extra one,
where recall is blind to over-reporting.

| Expected | Actual | Recall | Exact set |
| --- | --- | --- | --- |
| Two codes | Both | 2/2 | 1/1 |
| Two codes | One of them | 1/2 | 0/1 |
| Two codes | Both plus an extra | 2/2 | 0/1 |
| Two codes | Neither | 0/2 | 0/1 |

Both are reported because a single number hides one of the two. A system that
over-reports scores well on recall and badly on exact-set accuracy, and one that
finds half of every pair scores the reverse.

Recall is summed across scenarios rather than averaged per scenario, so a case
expecting two codes weighs twice as much as one expecting a single code.
Averaging per scenario would let a system that always finds the easy single code
look better than one finding most of a harder pair.

Within one scenario, codes are compared as sets. Raising the same code twice
does not manufacture recall it did not earn.

pass@1 is unchanged. It remains the strict composite: exact status, exact
exception code set, and exact evidence IDs.

Every rate is reported with its numerator and denominator, because a rate
without its denominator cannot be checked, combined, or compared across runs of
different sizes.

**A rate over no cases is `null`, not zero and not one.** A corpus containing no
anomalies has no measurable exception recall or exact-set accuracy; reporting
1.0 there would say the system caught every anomaly when it was never shown
one.

**No pass@k.** The baseline is deterministic and runs once. Reporting a pass@k
would imply a sampling budget that does not exist.

A missing decision is graded as a failure of everything, never skipped. A corpus
whose lines produced no decision has not been evaluated, and silently dropping
it would flatter every future run.

## The report

Deterministic JSON with sorted keys: every version, the seed, scenario counts,
every metric, a per-template breakdown, a paired-control breakdown, and every
failure with its expected and actual status, codes and evidence IDs.

A pair is judged together in the breakdown, and a failure is attributed to the
control or the anomaly. Knowing which half failed is the point of pairing.

## Public and private

**Public.** `benchmark/public-corpus.json` is committed. It carries a seed and
produces 59 scenarios covering every template. `make benchmark-generate` writes
the corpus and `make benchmark-evaluate` scores it.

The public corpus is a format demonstration. It shows what a corpus, a manifest
and a report look like, and it acts as a regression guard. **It is not a claim
of general performance.** It covers exactly the shapes the baseline was built
for, so a perfect score on it says the baseline still does what it did, not that
it would do well on anything else.

**Private.** A private evaluation supplies its own configuration file, with its
own seed, from outside this repository:

```bash
make benchmark-evaluate-private CONFIG=/path/to/private-corpus.json
```

Nothing about a private evaluation is committed: not the seed, not the
configuration, not the generated documents, not the manifest, not the report. A
held-out set whose answers live in the repository stops measuring generalisation
and starts measuring memory, and every later change would be made by someone who
had seen it.

## Answer labels never reach the system under test

The CSV documents carry identifiers, amounts and timestamps. They carry no
status, no exception code, no template name and no scenario expectation.

Scenario identifiers are opaque, of the form `SW-00001`, because they do reach
the documents. A scenario called `line-NET_FORMULA_MISMATCH-001` would let the
system read its answer off its own input.

A test checks every cell of every generated document against a list of label
strings.

## Generated artifacts are not committed

Corpora and reports are written under `data/generated/`, which git ignores. What
is committed is the configuration that reproduces them.

## Running it

```bash
make benchmark-generate    # write the public corpus and its manifest
make benchmark-evaluate    # score the baseline against it
make benchmark-evaluate-private CONFIG=path/to/config.json
```
