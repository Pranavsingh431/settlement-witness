# ADR-012: The model points, the verifier decides

- Status: Accepted
- Date: 2026-08-27
- Supersedes: none
- Superseded by: none
- Related: [ADR-002](ADR-002-domain-contract-and-verifier-authority.md),
  [ADR-003](ADR-003-derived-status-and-source-fact-verification.md),
  [ADR-005](ADR-005-exact-reference-matching-and-snapshot-payouts.md)

## Context

Every phase so far has been deterministic. A settlement line is resolved when
its citations resolve against stored facts and its required invariants hold, and
`verify_decision` derives the status from that backing. No caller assigns one.

Introducing a model puts pressure on exactly that arrangement. The obvious
design is to let a model produce a decision, or a candidate for one, and check
it afterwards. That is the wrong shape: the checkable part would be downstream
of a generated claim, and every field the model filled in would have to be
disbelieved individually.

The question this ADR answers is what a model is allowed to say at all.

## Decision

**A model may point at records the application already showed it. It may not
assert anything about them.**

Concretely, in four parts.

### 1. A separate contract, not `DecisionCandidate`, in two layers

`DecisionCandidate` is not reused. It carries exception codes, invariant results
and evidence references with payload hashes, and every one of those is something
the verifier derives or deterministic code constructs from real facts. Reusing
it would put a generated value one `model_validate` away from a stored
conclusion.

**`RawLinkSelection` is what a provider returns.** Two fields: an outcome, and a
list of source record IDs. It has no status, no exception code, no reason code,
no payload hash, no invariant result, no confidence and no free text. Those
fields do not exist to be filled in, and `extra="forbid"` means a provider that
sends one is rejected rather than trimmed.

**`LinkProposal` is the envelope the server builds.** It carries the proposal
ID, the subject line, the snapshot fingerprint and the provider identity
alongside the selection, and `bind` is the only thing that makes one.

The split matters because those four fields all have correct values that the
provider does not own. Which line was asked about, which snapshot the question
was against, and which provider answered are things the caller knew before it
called anything. Taking them from the response instead would let a provider name
a different line, claim a different snapshot, sign the answer as somebody else,
or choose what its answer is filed under. An audit trail assembled partly from
the thing being audited is not one.

Two consequences follow from that, and both are improvements over checking:

- A response cannot be about the wrong line or the wrong snapshot, because it
  carries neither. There is no check for those failures because they cannot be
  expressed; a payload carrying either field is refused as an extra.
- The proposal ID is derived by the server from the question and the provider
  identity, so the same question asked of the same provider is the same record.

**The model is forbidden from proposing exception codes.** That is the specific
prohibition this design turns on, and the reason is in ADR-009's successor
discussion: the contract currently turns a bare exception code with no citations
into an `EXCEPTION`. A component that could emit a code without citing anything
would be a path from a generated assertion to a reported finding with nothing
behind it. Rather than change the contract to make that safe, this phase removes
the model's ability to walk the path.

### 2. Selection from a finite environment, not retrieval

A provider receives a fixed list of candidate record IDs and a few structured
reference fields about each. It has no database handle, no filesystem, no tools,
no follow-up query, and no access to the documents the facts came from. It
cannot ask for more records.

That is what makes the boundary enforceable rather than advisory. The validator
checks membership against the same candidate set the request carried, so a
provider that returns an unknown ID has not found a record; it has produced an
invalid proposal.

Every source value in a request is data. Nothing in the codebase composes those
values into a sentence, so a `payment_id` reading "ignore previous instructions"
is a string in a field and has nothing to escape from.

### 3. Deterministic code builds the evidence

When a selection validates, server-side code loads each selected fact and reads
its real record ID, source system and payload hash. The model never supplies a
hash and never chooses one; it named records, and the hashes are a fact about
those records.

A validated selection is still only a proposal. It does not call
`verify_decision`, does not create a run, does not alter baseline output, and is
not presented as a resolved line.

### 4. Failure is typed, never repaired

A provider that raises, times out, returns nothing, or returns something
malformed produces an explicit outcome. There is no retry into a different
answer and no repair of a near-miss. A repaired proposal would be partly the
provider's and partly ours, and nobody could say which part was which.

A rejection is an AI-proposal failure. It is never a reconciliation exception,
and it changes no fact, receipt, run or decision.

## What this does not decide

It does not settle whether a bare exception code with no citations should remain
constructible. That is an open question about the domain contract, recorded in
`docs/domain-contract.md`, and Phase 8 avoids the unsafe path rather than
closing it. The prohibition here is narrower and stricter than any answer to
that question would need to be, which is the right way round while it is open.

It also makes no claim about model performance. No hosted model is called
anywhere in this phase. The only provider is a deterministic fake, and every
number in the shadow report measures the boundary and the harness.

## Consequences

- Widening what a model may say means changing `RawLinkSelection`, which has two
  fields and exists to be read. It cannot be widened by accident.
- Anything a proposal records beyond the selection is the server's to write. A
  later phase that wants a new piece of metadata on a proposal should add it to
  the envelope and to `bind`, not to what a provider may return.
- A useful model must be good at selecting from a set. If that turns out to be
  the wrong shape for a real task, the answer is a new proposal type with its
  own validator, not a loosened one.
- The candidate environment is the whole world a provider sees, so its inclusion
  policy is a load-bearing decision and is documented and tested as one.
- Shadow metrics measure linking against what the deterministic linker already
  does. None of them is a reconciliation accuracy, and calling one that would be
  the same category error this ADR exists to prevent.
- A metric that skips the lines a provider did not answer is not recall, whatever
  it is called. `link_recall` is measured over every true link in the corpus, so
  declining to answer costs the same as answering wrongly, and the conditional
  measure is reported separately as `answered_link_recall`.
