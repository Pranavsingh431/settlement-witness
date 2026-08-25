# ADR-008: Seeded generation, paired controls and an independent oracle

- Status: Accepted
- Date: 2026-08-25
- Supersedes: none
- Superseded by: none
- Related: [ADR-005](ADR-005-exact-reference-matching-and-snapshot-payouts.md),
  [ADR-006](ADR-006-settlement-gross-must-match-its-capture.md)

## Context

Three phases have produced a reconciliation baseline whose correctness rests on
reasoning and unit tests. Neither shows how it behaves across a population of
cases, and neither gives a later AI-assisted method anything to be measured
against.

`docs/evaluation-contract.md`, written in Phase 1 before any of this existed,
set the obligations. This phase builds the machinery, and four choices in it are
hard to reverse: once results are reported against a corpus, changing how the
corpus is built or graded invalidates every comparison already made.

## Decision

### 1. The oracle is constructed from the specification, never from a run

Each scenario template declares its expected status, exception codes and
evidence, reasoned from the contract, with a one line rationale beside it. The
generator fills in concrete record IDs. Nothing consults the baseline.

The alternative, recording what the baseline produced and calling it expected,
is the mistake that makes a benchmark worthless. It cannot fail. A regression
would move the expectation along with the behaviour, and the suite would go on
reporting a perfect score while the system got worse.

The cost is real: the expectations had to be reasoned out by hand, and getting
one wrong shows up as a failure that has to be adjudicated rather than assumed
to be a bug in the system under test. That adjudication is the work, and it is
the part that makes the number mean something.

Tests hold this open by handing the grader decisions the baseline never
produced, including a replacement that resolves everything, and requiring the
score to fall.

### 2. Generation is seeded, and the seed is mandatory

No corpus is generated without a seed supplied in a configuration file, and the
seed is recorded in every manifest and report alongside the generator, harness,
baseline, parser and domain versions.

A result whose inputs cannot be regenerated is an anecdote. Recording the seed
and not the generator version would be almost as bad, because the same seed
produces a different corpus once the rules change.

Randomness decides amounts only. Coverage is fixed by the configuration, so
changing the seed varies the data without quietly dropping a template.

### 3. Every anomaly has a matched control

Each anomaly is generated alongside a control built from the same amounts,
differing only in the one intended causal change, and records
`paired_control_id`.

Without controls, a system that flagged everything would score perfectly on
recall and be useless. The pair is judged together: the control must resolve and
the anomaly must not, from the same system on the same run.

One template reaches two records. Breaking a line's declared net moves the
payout total, because a payout total is the sum of the nets of its lines. That
is a consequence of one edit rather than two edits, and the payout total is
therefore derived in the builder rather than written per template. A test
confirms the payout changed only in the derived field.

This was found by a test rather than by design. The first version wrote both the
line and the payout explicitly, and the pairing test correctly reported two
differences.

### 4. Public and private are separated, and the public corpus is not a result

The public configuration is committed. The corpus it generates, and any report,
are not: they live under an ignored directory, reproducible from the seed.

A private evaluation supplies its own configuration from outside the repository.
Nothing about it is committed. A held-out set whose answers live in the
repository stops measuring generalisation and starts measuring memory, and every
later change is then made by someone who has seen it.

The public corpus is a format demonstration and a regression guard. It covers
exactly the shapes the baseline was built for, so a perfect score on it says the
baseline still does what it did. It says nothing about how it would do on
anything else, and the documentation says so in three places because it is
exactly the sentence that gets dropped when a number is quoted.

### 5. No answer labels reach the system under test

The documents carry identifiers, amounts and timestamps. Scenario identifiers
are opaque, because they do reach the documents and a name like
`line-NET_FORMULA_MISMATCH-001` would let the system read its answer off its own
input. A test checks every cell against a list of label strings.

### 6. Rates over no cases are null

Not zero, not one. A corpus with no anomalies has no measurable exception
recall, and reporting 1.0 would say the system caught every anomaly when it was
never shown one. Every rate carries its numerator and denominator, because a
rate without its denominator cannot be checked or combined.

### 7. No pass@k

The baseline is deterministic and runs once. Reporting a pass@k would imply a
sampling budget that does not exist, and inviting it now would make the first
non-deterministic method look better than the deterministic one it has to beat.

## Consequences

Good:

- The baseline has a measured floor, on a population rather than on examples.
- A later AI-assisted method can be compared on the same corpus, with the same
  oracle, and has to beat the floor without weakening any safety property.
- A regression in any of the ten shapes fails the run rather than passing
  quietly.
- The harness exercises the real ingestion and reconciliation paths, so a change
  that breaks the parser or the store shows up here too.

Costs and risks:

- The oracle is hand-reasoned, so an error in it is an error in the benchmark.
  The rationale field exists so a reviewer can check each one, and a
  disagreement between oracle and baseline has to be adjudicated rather than
  assumed to favour either.
- The public corpus scores perfectly, which means it does not currently
  discriminate. It is a regression guard, and it will need harder cases before
  it can rank two candidate systems.
- Generated scenarios are far simpler than real merchant data. Nothing here
  models a provider's export quirks, and a system that scores well here may
  still fail on the first real file it sees.
- The corpus and the baseline were written by the same person in adjacent
  phases, so the shapes covered are the shapes that were thought of. That is a
  real limitation of any self-authored benchmark and is not solved by more
  scenarios of the same kind.

## Alternatives considered

**Derive expected outcomes by running the baseline.** Rejected. See decision 1.
It produces a suite that cannot fail.

**Commit the generated corpus rather than the configuration.** Rejected. It adds
a large artifact that must stay in step with the generator, and the seed already
reproduces it exactly. A committed corpus also invites editing a case by hand,
which would break reproducibility silently.

**Skip paired controls and generate anomalies only.** Rejected. A system that
flags everything would score perfectly on recall. The control is what makes a
detection meaningful.

**Report a rate of 0.0 when there are no cases.** Rejected. It is indistinguishable
from a genuine zero and lets an empty run look like a measurement.

**Randomise which templates appear, for variety.** Rejected. Coverage would then
depend on the seed, and a corpus could silently omit a shape. Amounts vary;
coverage does not.

**Grade evidence by overlap rather than exact match.** Rejected. A decision that
reached the right answer while citing the wrong records did not do the work, and
the next case where those records differ would expose it. This mirrors the rule
already stated in the evaluation contract.
