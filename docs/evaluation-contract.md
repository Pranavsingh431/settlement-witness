# Evaluation contract, version 5.0.0

This defines how Settlement Witness is graded, and it is written before the
system that will be graded exists. That order is deliberate. A benchmark
designed after the fact tends to describe what the system already does well.

Nothing here is implemented yet. The harness arrives in the phase that builds
it, and it must satisfy this document rather than replace it.

## What is public and what is not

**Public.** The schemas in `docs/schema/v5/`, this contract, the domain contract,
and any fixtures in `data/fixtures/`. Public fixtures exist to demonstrate
format. They are examples of what a record looks like, and they are small enough
to read. They are not a test set, and a system that scores well on them has
demonstrated nothing.

**Private.** Evaluator cases are never committed to this repository. That
includes the generated records for the held-out split, the expected decisions,
the mutation log describing which faults were injected into which records, and
the generator seed used to produce them.

The reason is not secrecy for its own sake. If the expected answers sit in the
repository, then every later change is made by someone who has seen them, and
the split stops measuring generalisation and starts measuring memory. Committing
them would also make it impossible to tell honest work from a lookup.

If the private cases are ever needed by another person, they are handed over out
of band along with the seed that produced them, so the recipient can regenerate
rather than trust a file.

## What the system is given

The evaluation input is **generated source records only**.

The system under test receives the same thing it would receive in production: a
set of source facts. It does not receive an expected-answer field, a label, a
hint about which records are faulty, or a count of how many exceptions to look
for. Any field that would leak the answer is stripped before the input is handed
over, and a case whose input contains such a field is a defect in the harness.

This is worth stating plainly because it is the easy mistake. A generator that
writes `"expected_status": "EXCEPTION"` into the record it also feeds to the
system produces a benchmark that measures nothing.

## How a result is graded

A `RESOLVED` result is graded on two things, and neither is the explanation.

1. **Exact linked source IDs.** The set of source record IDs the decision linked
   must equal the set the case requires. Not overlap, not a superset. A decision
   that reached the right answer while citing the wrong records did not do the
   work, and the next case where those records differ will expose it.
2. **The invariant certificate.** The recorded invariant results must match what
   the case requires, outcome by outcome.

Explanation quality is not graded, and there is no explanation field to grade.
A decision that returns the correct status with incomplete evidence is scored as
a failure, not a partial credit. That is the whole thesis of the project: a
right label with the wrong evidence is a lucky guess, and a lucky guess is not
an auditable investigation.

An `EXCEPTION` result is graded on the status, the exception code, and the
linked records. An `INSUFFICIENT_EVIDENCE` or `PENDING` result is graded on the
status alone, since neither asserts a finding.

## Required measures

Every reported run states all five. Reporting a subset is not permitted, because
each one hides a different failure that the others make invisible.

| Measure | Definition | What it catches |
| --- | --- | --- |
| Decision accuracy | Share of cases where the status is exactly right | The headline number, and on its own the least informative |
| Exception recall | Share of expected exception code occurrences the system actually raised | A system that resolves everything and looks accurate on a clean-heavy set |
| Exact exception set accuracy | Share of anomalies whose code set matched exactly | Over-reporting, which recall alone is blind to |
| False-resolution rate | Share of cases marked `RESOLVED` that should not have been | The failure that actually costs money. Weighted above the rest |
| Evidence completeness | Share of resolutions carrying exactly the required source IDs and invariant results | Right answers reached with the wrong evidence |
| pass@1 | First-attempt success, with no retries and no sampling | Retry luck. The demonstration gets one attempt, so this is the product number |

False resolution and evidence completeness are the two that matter most. A
system can score well on decision accuracy while being useless, by resolving
confidently and citing nothing.

`pass@k` for `k` greater than one may be reported as a secondary diagnostic, to
show recovery behaviour. When it is, the sampling budget and the selection rule
are stated alongside it. It never replaces `pass@1`.

Abstention is measured, not penalised by default. `INSUFFICIENT_EVIDENCE` on a
case that genuinely is ambiguous is correct behaviour. It counts as a failure
only when the evidence was sufficient and the system abstained anyway.

## Determinism and seeds

Any generator with a random component is seedable, and every run records:

- the seed;
- the generator version and configuration;
- the domain contract version;
- the split it produced;
- the model, its settings and the prompt version, where a model was involved.

Running the same seed and configuration again must reproduce the same records
byte for byte. A run that cannot be reproduced cannot be checked, and a number
from it is an anecdote.

Raw outputs are stored and scores are derived from them, rather than only the
scores being kept. When a grading rule changes, previous runs are re-scored from
the stored outputs rather than being rerun or quietly dropped.

Run directories are additive. A new run never overwrites an old one.

## Splits

Train, validation and test splits are frozen before the final evaluation, along
with the fault injectors that produced them. The test split is used once, at the
end. Tuning against it converts it into a training set.

## Honesty rules

These exist because the failure they prevent is easy and tempting.

- No number appears in a report unless code in this repository produced it.
- Synthetic results are described as synthetic. They are evidence that the
  system works on data whose ground truth is known, and they are not a claim
  about live merchant outcomes.
- A measure that was not run is reported as not run, never omitted and never
  estimated.
- When a run fails or a check is skipped, the report says so.
