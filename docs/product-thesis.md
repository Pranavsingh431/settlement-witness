# Product thesis: from exception detection to evidence-to-closure

Settlement Witness is not trying to be another reconciliation table with an AI
chat box. Its product thesis is narrower and stronger:

> A finance controller should not only say what failed. It should preserve the
> financial objects that produced the finding, route the next action, state the
> exact proof needed to close it, and refuse to call the work complete until a
> new evidence-backed conclusion exists.

## What is distinctive here

1. **Evidence-bound conclusions.** Source records, payload hashes, invariant
   results and exception codes travel together. A plausible total that cannot
   survive replay is not a result.
2. **Functional abstention.** Insufficient evidence becomes a concrete request
   for the missing proof, not an “unknown” bucket that quietly becomes manual
   work.
3. **Proof-carrying recourse.** Every close plan names an owner, next action,
   required evidence and closure gate. The recommendation is testable.
4. **Separate kinds of truth.** Provider-record agreement, human workflow and
   bank-credit finality remain separate conclusions. One cannot borrow the
   appearance of another.
5. **Bounded AI authority.** AI can search the candidate space in a measured
   shadow task. The deterministic verifier retains authority over financial
   conclusions and exposes abstention, invalid output and false links
   separately.

The first three ideas respond directly to the failure pattern reported by
[FinBalance](https://arxiv.org/abs/2606.15949): financial outputs can look
right while failing source binding, aggregation or replay. The object identity
rule is consistent with [object-centric process conformance](https://arxiv.org/abs/2312.08537),
and the close-plan constraint follows the lesson from
[temporal-constrained counterfactual explanations](https://arxiv.org/abs/2403.11642):
recourse has to be possible inside the process, not merely capable of changing
the classifier's answer.

## The operator journey

```text
59-line batch
    -> evidence-backed decision
    -> unresolved item routed to an owner
    -> exact proof requested
    -> authoritative record imported
    -> new immutable run
    -> closure gate passes or the item stays open
    -> bank credit verified as a separate finality conclusion
```

This is the finance-ops loop the product closes. It is not “AI matched a row.”
It is “the system moved a finding toward closure without losing the evidence or
changing what was previously known.”

## Product sequence

### Step 1 — Evidence-to-closure controller: shipped

The API now derives a versioned close plan for every decision. The dashboard
shows the operational route for every exception class, and the audit
certificate shows the action, owner, exact proof and new-run closure gate.

### Step 2 — Verified cash-impact prioritisation

Rank open work by a monetary exposure derived from the same cited source facts,
with currency kept explicit. Do not sum unlike currencies and do not substitute
gross, net or payout amounts silently. Until that read model exists, the
product does not publish an invented “cash at risk” number.

### Step 3 — Evidence request packages

Turn a close plan into a downloadable, non-authoritative request containing the
record identities, missing proof and acceptance condition. The package may help
an operator communicate; importing the returned evidence and creating a new
run remains the only closure path.

### Step 4 — Measured evidence location

Use the existing bounded AI proposal interface to locate candidate records in
ambiguous, generated cases. Promote it only if it adds coverage beyond exact
matching while preserving precision, safe abstention and the no-write boundary.

### Step 5 — Production operating controls

Add authenticated actors, tenant isolation, durable rate limits and operational
monitoring before any real merchant data is accepted. The public deployment
remains a synthetic reviewer demonstration.

## What this does not claim

The generated corpus is a regression and shadow environment, not a
representative production dataset. The project does not claim that its measured
figures are real-merchant performance, and it does not claim that the design is
the first possible evidence-based reconciliation system. Its contribution is
the executable combination of evidence-pinned decisions, functional recourse,
separate finality and bounded AI authority.
