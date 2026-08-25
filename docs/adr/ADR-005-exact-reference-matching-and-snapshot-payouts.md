# ADR-005: Exact-reference matching and snapshot-relative payout grouping

- Status: Accepted
- Date: 2026-08-25
- Supersedes: none
- Superseded by: none
- Related: [ADR-002](ADR-002-domain-contract-and-verifier-authority.md),
  [ADR-003](ADR-003-derived-status-and-source-fact-verification.md),
  [ADR-004](ADR-004-append-only-import-and-atomicity.md)

## Context

Phase 3 is the first phase that produces decisions from real data. Three things
had to be settled before writing any matching code, and all three are hard to
reverse: once decisions exist and later phases compare against them, changing
what counts as a match invalidates every result already produced.

## Decision

### 1. Matching is on exact references only

A settlement line links to payment events by exact `payment_id` and to its
payout by exact `payout_id`. Nothing else creates a match. Not an amount that
looks close, not a timestamp nearby, not a reference that could be corrected
into a known one.

The reason is the whole thesis of the project. A match made on similarity
produces a resolution that nobody can check: the evidence certificate would cite
records that a person then has to agree look related, which is not a proof, it is
an opinion with references attached. An exact reference either resolves or it
does not, and the answer is the same for everyone who looks.

This makes the baseline resolve less than a fuzzy one would. That is the point.
The measure that matters is false resolutions, not resolution rate, and a
baseline that abstains is the honest floor a later phase has to beat while
staying evidence-complete.

### 2. Payout grouping is snapshot relative, and says so

A payout document says what the batch totalled, not which settlement lines
composed it. Phase 2 recorded that by leaving `PayoutBatch.settlement_line_ids`
empty rather than inventing them.

INV-003 needs a payout that declares its contents. One is built from the lines
the snapshot holds for that payout ID.

**INV-003 passing means the payout total equals the sum of the lines this system
holds. It does not mean the provider's export was complete, and it cannot.** A
line that was never imported leaves no trace to notice.

This limitation is stated in the code, in `docs/reconciliation-baseline.md`, and
here, because it is exactly the kind of thing that gets quietly forgotten and
then reported as a completeness guarantee. A real completeness claim needs a
provider-side control total, which no document in this contract carries.

A missing payout therefore produces `INSUFFICIENT_EVIDENCE` rather than a
resolution. Resolving a line whose payout was never observed would be asserting
something about a batch the system has never seen.

### 3. Reason codes are the verifier's, not the caller's

`DecisionCandidate` lost its `reason_codes` field. The verifier derives reason
codes from the verified evidence, the invariant results and the exception codes.

Exception codes are still supplied by the caller and still carried across. The
distinction is deliberate. An exception code is a finding: the engine noticed a
refund dated before its capture, and the verifier has no way to rediscover that.
A reason code is an account of which rule fired, which is the verifier's own
statement about its own decision.

The field was removed rather than accepted and ignored. A field whose value is
discarded is a lie about what a caller controls, and this project has removed
several of those already. A caller that could write its own reason codes could
describe a decision as having been reached for reasons that had nothing to do
with it, and nothing in the record would contradict it.

This is a breaking contract change, so the domain version goes to 3.0.0 and the
schema directory follows it. ADR-006 later took the contract to 4.0.0, and the
published schema now lives at `docs/schema/v4/`.

### 4. A decision's created-at is the snapshot time

Every decision in a run carries the snapshot's `as_of`, which is the latest
observation time among the facts, rather than a wall clock reading.

Reproducibility is the reason. A run over the same facts has to produce the same
decisions, byte for byte, or the output cannot be diffed and a change in the
result never clearly means a change in the evidence. A wall clock would make
every run differ in a way that has nothing to do with what was reconciled.

The cost is that `created_at` no longer means the instant a person triggered the
run. It means the state the decision describes, which is the more useful of the
two for an auditable result.

### 5. Decisions are not persisted in this phase

The baseline computes and returns. Nothing is written to a table.

A decision is derived from facts that already are persisted and append-only. Its
correct storage design depends on questions this phase cannot answer: whether a
decision is superseded when new facts arrive, whether a run is stored whole or
per line, and what a stored decision means once the baseline version changes.
Guessing at those and building a table would be harder to undo than leaving it.

## Consequences

Good:

- Every resolution is checkable by anyone with the same facts, because it rests
  on references that either match or do not.
- A result is reproducible and diffable, so a change in output always means a
  change in the facts or the rules.
- The honest floor is established, and it is low: one of three demo lines
  resolves. A later phase has something real to beat.
- No caller can influence how a decision explains itself.

Costs and risks:

- The baseline resolves little. A merchant file with corrupted references would
  produce almost nothing but exceptions, and that is the correct behaviour
  rather than a bug to fix by loosening the match.
- Snapshot-relative INV-003 can report a mismatch that is an artefact of an
  incomplete import rather than a provider error. The documentation says so in
  three places, and the risk is that somebody reads a result without them.
- Tying `created_at` to the snapshot makes it useless for asking when a run was
  executed. Nothing needs that yet, and a run timestamp can be added alongside
  it if something does.
- Not persisting decisions means a result has to be recomputed to be seen again.
  That is cheap now and will not stay cheap.

## Alternatives considered

**Fuzzy or scored candidate generation.** Rejected for this phase. It is worth
building later, and it has to be measured against this baseline rather than
introduced in place of one. Without a deterministic floor there is nothing to
show that a cleverer matcher is actually better rather than merely more
confident.

**Resolving a line whose payout is absent.** Rejected. It would assert something
about a batch the system has never observed.

**Choosing the most plausible capture when there are several.** Rejected.
Nothing in the records says which one the line settled, and the plausible choice
is still a guess wearing a certificate.

**Treating a fully returned payment as resolvable.** Rejected. The contract has
no rule for whether a fully charged-back payment should still settle. Reporting
`UNSUPPORTED_STATE` is honest; picking an interpretation is not.

**Emitting `MISSING_SETTLEMENT` for a capture with no line.** Rejected. It needs
the settlement window policy to say when a settlement stops being plausibly
late, and inventing a window here would make the code depend on a number nobody
chose.

**Keeping `reason_codes` on the candidate and ignoring them.** Rejected. See
decision 3.

**A wall clock `created_at`, with tests comparing everything except that field.**
Rejected. A determinism guarantee with an exception carved into it is not a
determinism guarantee, and the exception would grow.
