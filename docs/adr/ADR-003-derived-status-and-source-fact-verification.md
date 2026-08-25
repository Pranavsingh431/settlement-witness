# ADR-003: Derived status and source-fact verification

- Status: Accepted
- Date: 2026-08-24
- Supersedes: none
- Superseded by: none
- Amends: [ADR-002](ADR-002-domain-contract-and-verifier-authority.md)
- Related: [ADR-001](ADR-001-stack-and-modular-monolith.md)

## Context

ADR-002 claimed that a decision claiming `RESOLVED` without the backing could
not be constructed. A review found that claim was true of two things and false
of two others.

**Gap 1: evidence was never resolved.** `ReconciliationDecision` checked only
that each `EvidenceRef.source_record_id` appeared in
`linked_source_record_ids`. It never checked that a source fact with that ID
existed, nor that its source system or payload hash matched. A decision could
cite a record that had never been observed, with any hash at all, and resolve.
The membership check compared the decision against itself.

**Gap 2: status was asserted, not derived.** `derive_status` existed and was
correct, but nothing required a decision's `status` to equal it. The model
enforced a handful of per-status obligations instead, and they had holes. All
four of these were constructible:

- `EXCEPTION` carrying only `TIMING_PENDING`
- `PENDING` carrying no `TIMING_PENDING`
- `INSUFFICIENT_EVIDENCE` carrying `MALFORMED_RECORD`
- `EXCEPTION` when the highest precedence code was `INSUFFICIENT_EVIDENCE`

The precedence order existed and was tested, and the status rules simply did not
consult it.

Both gaps share a cause. ADR-002 put the whole verifier in a Pydantic validator,
and a validator can only see the object in front of it. That is enough to check
internal coherence and not enough to check anything about the world.

## Decision

### 1. Structural validation and source-fact verification are separate layers

A validator cannot confirm that a record exists, so it must not appear to. The
two jobs are split and named.

| Layer | Where | Can check | Cannot check |
| --- | --- | --- | --- |
| Structural validation | `ReconciliationDecision` validator | Internal coherence, and that the status equals the derived status | Whether any cited fact exists |
| Source-fact verification | `verify_decision(candidate, facts)` | That each citation resolves to a real fact by ID, system and hash | Nothing it was not given |

Verification takes the facts as an explicit argument. Phase 1 has no
persistence, so a caller supplies them. Phase 2 storage will supply the index.
The boundary does not move: the same pure function is called with a larger
index. What changes is who builds it, not what verification means.

We do not claim that constructing a Pydantic model proves a database record
exists. It proves the citation is well formed, and a well formed citation can
still be wrong.

### 2. A decision carries the verification certificate

`ReconciliationDecision.evidence_verification` records one result per citation.
`RESOLVED` requires every one of them to have verified, and a citation with no
result at all counts as unresolved.

This is what stops the split from becoming a convention. Without the field, a
caller could build a `RESOLVED` decision directly and simply not verify. With
it, doing so requires fabricating verification results. That is a deliberate lie
rather than a missing step, and it is visible in the stored decision.

### 3. A candidate is separate from a verified decision

`DecisionCandidate` is what a caller builds. It is structurally validated and
has no `status` field and no verification field, because neither is a caller's
to supply. `verify_decision` turns one into a `ReconciliationDecision`.

The alternative was one model with an optional status. Rejected: an optional
status is a status someone will set.

### 4. Status is derived and the model refuses any other

`derive_status` is the authority. `ReconciliationDecision` computes it from the
decision's own backing and refuses construction when the supplied status
disagrees. Unresolved citations contribute their own exception codes inside
`derive_status`, so a decision cannot dodge them by leaving them out.

This replaces the per-status obligation rules from ADR-002. Those rules were a
restatement of the precedence order, and a restatement can disagree with the
thing it restates. Consulting the order directly cannot.

### 5. Evidence failures reuse the existing taxonomy, with new reason codes

The thirteen exception codes are unchanged. The three ways a citation can fail
map onto two of them, and precision is carried by new reason codes:

| Failure | Exception code | Reason code |
| --- | --- | --- |
| No fact with that record ID | `INSUFFICIENT_EVIDENCE` | `EVIDENCE_FACT_NOT_FOUND` |
| Fact exists, different system | `UNMAPPED_REFERENCE` | `EVIDENCE_SOURCE_SYSTEM_MISMATCH` |
| Fact exists, different hash | `UNMAPPED_REFERENCE` | `EVIDENCE_PAYLOAD_HASH_MISMATCH` |

A missing fact is an absence, so the evidence is not there to judge on. A
mismatch is a contradiction between the decision and the store, which is a
reference that does not resolve to what it claims. `UNMAPPED_REFERENCE` had its
description widened to say so.

Adding a fourteenth exception code was considered and rejected. The taxonomy is
graded against by an evaluator, and widening it for a distinction that reason
codes already carry would make two systems disagree about the same case.

## Consequences

Good:

- The central claim of the project is now true as stated. A resolution requires
  citations that resolve to real facts.
- Four status bypasses are impossible rather than merely undocumented.
- The precedence order has one implementation instead of two that could drift.
- A stored decision carries proof its citations were checked, which is what an
  auditable investigation needs and what the evaluation contract grades.
- Phase 2 inherits a boundary that is already the right shape, rather than one
  that has to be retrofitted around ingestion.

Costs and risks:

- This is a breaking change. Decisions that 1.0.0 accepted cannot be built under
  2.0.0, so the contract version goes to 2.0.0 and the schema moves to
  `docs/schema/v2/`.
- `docs/schema/v1/` was removed rather than left intact, which departs from the
  guidance in ADR-002. It existed for one commit, nothing consumed it because
  there is still no persistence, and republishing it would mean publishing a
  contract with a known correctness gap. The general rule stands for any version
  that has actually been used.
- There are now two models where there was one. That is more surface, and it is
  the price of making the status underivable by a caller.
- `verify_decision` must be given every relevant fact. A caller that supplies a
  partial index will get `INSUFFICIENT_EVIDENCE` rather than a wrong resolution,
  which is the safe direction, but Phase 2 has to make the index complete.

## Alternatives considered

**Leave verification to a separate function callers should run.** Rejected for
the same reason ADR-002 rejected a `verify()` convention. If the decision does
not carry the certificate, nothing forces the check.

**Have the validator look facts up itself.** Rejected. It would need a global
registry or a database handle, which ADR-001 rules out and which would make the
model impure and untestable in isolation.

**Add a `verified: bool` flag instead of a certificate.** Rejected. A boolean is
an assertion, and the whole point is to stop accepting assertions. A per-citation
result says which citation was checked and how it turned out.

**Add a fourteenth exception code for evidence failures.** Rejected. See
decision 5.

**Keep the per-status obligation rules alongside the derived status.** Rejected.
Two implementations of the same precedence order can disagree, and the redundant
one would be the wrong one to trust.
