# Benchmark

This directory will hold the deterministic dataset generator and the evaluation harness.

It is empty at phase 0. Nothing here is stubbed, because a stub would be a placeholder pretending
to be work. The harness arrives in the phase that defines the lifecycle schema, the invariant
catalogue and the exception taxonomy, because those have to exist before anything can be scored.

When it lands it will own:

- a seeded generator for the payment-to-settlement lifecycle, including duplicate, delayed,
  missing, reversed and partial cases, plus legitimate hard negatives such as split settlements
  and net fee deductions;
- frozen train, validation and test splits, with the labels and mutation logs kept private to the
  evaluator;
- immutable, additive run directories that store raw model output, so a score can be recomputed
  without rerunning the model;
- the comparison baselines, including deterministic rules and a semantic retriever.
