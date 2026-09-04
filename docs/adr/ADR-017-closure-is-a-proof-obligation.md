# ADR-017: Closure is a proof obligation, not a recommendation

- Status: Accepted
- Date: 2026-09-04
- Supersedes: none
- Superseded by: none
- Related: [ADR-003](ADR-003-derived-status-and-source-fact-verification.md),
  [ADR-015](ADR-015-review-events-annotate-they-do-not-decide.md),
  [ADR-016](ADR-016-settlement-agreement-is-not-bank-finality.md)

## Context

The reconciliation baseline answers a necessary question: which settlement
lines are supported by the evidence, and which are not? That answer alone still
leaves a finance operator translating an exception code into work: who should
own it, what should they request, and what would count as proof that the work is
complete.

A generic AI recommendation is not a safe answer. It can sound operationally
plausible while requesting a record the system cannot ingest, inventing a
tolerance, or implying that a human click can change an immutable conclusion.
That would make the interface more fluent and the control weaker.

Three external findings shaped this decision:

- [FinBalance](https://arxiv.org/abs/2606.15949) reports a substantial gap
  between plausible financial outputs and results that survive source binding
  and ledger replay. A next action must therefore name its evidence, not merely
  sound reasonable.
- [Object-centric conformance alignments](https://arxiv.org/abs/2312.08537)
  preserve the identities and dependencies of the objects moving through a
  process. A payment, settlement line, payout and bank credit cannot be reduced
  to one convenient row without losing the relationship being checked.
- [Temporal-constrained counterfactual explanations](https://arxiv.org/abs/2403.11642)
  show why recourse has to respect the process that can actually produce the
  changed outcome. “Change the number until it matches” is not admissible
  recourse for a financial record.

## Decision

**Every decision served by the API carries a deterministic, versioned
evidence-to-closure plan. The plan describes the next proof the verifier needs;
it never changes the decision and never promises an outcome.**

### 1. The plan is derived from the certificate

`build_closure_plan` is a pure projection over an immutable
`ReconciliationDecision`. It uses the recorded status, exception codes and
contract precedence. It does not read the database, call a model, accept user
text, mutate a review event or write a second conclusion.

The plan carries the baseline status explicitly so a client cannot present the
workflow as a replacement status. A resolved decision has no action and no
owner. Every unresolved decision has at least one bounded action and requires a
new run before it can close.

### 2. An action has an owner and an acceptance test

Each action states:

- the finance-operations lane that owns the next move;
- a bounded instruction;
- the authoritative evidence required; and
- whether today's importer and verifier can check that evidence.

The last field is important. Some findings can be closed with records the
current contract already understands, such as a missing payment event. Others
need a real contract extension, such as an authorised FX conversion or a
versioned adjustment record. Those cases are labelled as capability gaps and
routed to finance control; the interface does not turn them into manual
approval buttons.

### 3. Multiple findings remain multiple obligations

The highest-precedence exception selects the primary owner and headline, but
the plan retains an action for every distinct exception code. Fixing the first
visible problem must not silently hide a second proof obligation.

### 4. Closure requires new evidence and a new conclusion

The common closure gate is intentionally strict:

> Import authoritative evidence and create a new reconciliation run. Close
> only when that new decision is `RESOLVED`, every citation verifies, and every
> required invariant holds.

A review event can track the work. It cannot satisfy this gate. The old
decision remains readable because it was true about the evidence available at
that time.

## Consequences

- The product moves beyond an exception ledger to a controlled route from a
  finding to the next verifiable state without giving AI authority over money.
- The same playbook is served to the batch dashboard, decision certificate and
  any later client. Action meaning no longer lives as unversioned UI copy.
- Unsupported evidence types are visible product gaps. They can be prioritised
  deliberately instead of being hidden behind vague advice.
- This is deterministic recourse, not proof that the named external record is
  correct or available. Only a later import and reconciliation can establish
  that.
- The first version does not rank work by cash exposure. The decision read
  model does not yet carry a verified monetary exposure for every exception,
  and inventing one from whichever amount is convenient would violate the same
  evidence rule this ADR establishes.

## Alternatives rejected

**Let a hosted model write the close plan.** Rejected. A model remains useful
for locating candidate evidence, but the acceptance test for a finance control
belongs to the versioned contract.

**Add a Resolve button.** Rejected. A button records intent or workflow. It
cannot make missing evidence exist or make a failed invariant hold.

**Show one generic “investigate” instruction.** Rejected. It creates no owner,
no completion condition and no testable distinction between collecting a
missing record and investigating a real contradiction.
