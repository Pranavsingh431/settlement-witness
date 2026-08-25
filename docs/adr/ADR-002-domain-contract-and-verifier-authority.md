# ADR-002: Domain contract and verifier authority

- Status: Accepted
- Date: 2026-08-24
- Supersedes: none
- Superseded by: none
- Related: [ADR-001](ADR-001-stack-and-modular-monolith.md)

## Context

ADR-001 recorded that probabilistic proposals are separated from deterministic
authority, and left the boundary to be drawn when there was something to draw it
around. Phase 1 defines the facts, evidence, decisions, invariants and exceptions
the system reasons about, so the boundary has to be drawn now.

Drawing it later would not work. Once ingestion, matching and a model call
exist, every one of them has an opinion about what a decision is, and the
contract becomes whatever those three happen to agree on. The parts of this
system that are hard to reverse are the meanings, not the code.

Four questions had to be settled together, because answering any one of them
differently changes the others.

## Decision

### 1. The models are the contract; the documents describe it

`backend/app/domain/` is the executable source of truth. `docs/domain-contract.md`
explains it and `docs/schema/v1/` is generated from it by `make schema`. Where a
document and the code disagree, the code is right and the document is a bug.

A test regenerates the schema and compares it against the committed files, so a
model change that is not reflected in the artifact fails the build. Without that
test the published schema would drift into describing a contract that no longer
exists, which is worse than publishing nothing.

### 2. The verifier is enforced by construction, not by convention

A `ReconciliationDecision` that claims `RESOLVED` without evidence, or with a
failed or unevaluated required invariant, cannot be constructed. The rule lives
in a model validator, so it applies to every caller including ones written after
this ADR.

The alternative was a `verify()` function that callers are expected to run. That
is a convention, and conventions are followed until the day someone is in a
hurry. The point of this project is that a resolution is trustworthy, and a rule
that can be skipped does not support that claim.

`derive_status` computes the status from the backing rather than accepting one,
so `RESOLVED` is what remains when nothing else applies. A resolution is the
absence of any reason not to resolve, not a positive claim a caller may assert.

### 3. Model output is excluded structurally, not by policy

No model in the contract has a field for prose, a confidence value or a chain of
reasoning, and every model sets `extra="forbid"`. `EvidenceRef` carries a source
record ID, a source system and a payload hash, and nothing else.

This is stronger than a rule saying model output must not override an invariant.
There is no field to put it in, so there is nothing to weigh. A future phase that
wants to store a model's explanation must store it outside the decision, where it
cannot be mistaken for evidence.

### 4. Structural validity and source consistency are checked in different places

| Kind of field | Checked by | Reason |
| --- | --- | --- |
| Derived by this system, such as `payload_hash` | The model, at construction | The value is ours. A fact whose hash disagrees with its own payload is incoherent, not bad source data. |
| Declared by a source, such as `SettlementLine.net_minor` | An invariant | Sources publish inconsistent records. The model must be able to represent one. |

This is why `net_minor` is stored rather than computed. A computed property
would make INV-002 compare a number against itself, and the check would pass on
every record including the broken ones.

### 5. Four outcomes for an invariant, not two

`PASSED`, `FAILED`, `NOT_APPLICABLE` and `INSUFFICIENT_INPUT`. The last two exist
because a boolean forces a lie when information is missing.

`NOT_APPLICABLE` is determinate and does not block a resolution: a payment with
no refunds genuinely has nothing for INV-004 to check. `INSUFFICIENT_INPUT` does
block one: a payment whose capture was never observed has a ceiling nobody knows.
Collapsing these two into `FAILED` would manufacture breaks that a finance team
then has to disprove, which is the specific failure this project exists to avoid.

### 6. Money is integer minor units, everywhere, with a signed formula

`net_minor = gross_minor - fee_minor - tax_minor + adjustment_minor`, with
gross, fee and tax held as non-negative magnitudes and adjustment signed.

Floats are rejected even inside canonical payloads, and even when integral.
Ingestion converts before a fact is built. The published schema contains no
`"number"` type, and a test asserts it.

### 7. Source facts are append-only, and identity is (system, provider event ID)

Facts are frozen. A correction is a later fact, never an edit. An identical
replay is a no-op, because duplicate webhook delivery is normal. The same
identity with a different payload hash is `DUPLICATE_CONFLICT`, because one
observation is wrong and neither may be silently preferred.

## Consequences

Good:

- An unbacked resolution is unrepresentable, so the central claim of the project
  holds by construction rather than by review.
- Missing information has a name, so the system can abstain honestly instead of
  inventing a mismatch.
- The contract is machine-readable outside Python, and cannot silently drift
  from the code.
- Later phases inherit a fixed vocabulary, so ingestion, matching and evaluation
  cannot each invent their own.

Costs and risks:

- Some invariant failure branches are unreachable through the models, because
  the validator refuses to construct the input that would reach them. They are
  kept as a second line of defence for decisions that arrive without validation,
  such as one read back from storage, and are tested through `model_construct`.
  This looks like redundancy and is deliberate.
- Rejecting floats in canonical payloads pushes work onto ingestion. That is the
  right place for it, but it is real work that Phase 2 has to do.
- A frozen contract is harder to change than an informal one. That is the point,
  and the versioning rules exist so that changing it is possible rather than
  merely discouraged.
- `DomainSchemaVersion` repeats the version string, because a type checker
  cannot read a variable into `Literal`. A test asserts the two agree.

## Alternatives considered

**A verifier function callers must remember to run.** Rejected. See decision 2.

**Boolean invariants.** Rejected. Two outcomes force missing information to be
reported as a failure, which is the exact behaviour this project is trying to
beat.

**Computed `net_minor` on the settlement line.** Rejected. It would make the
model reject broken source records, so the system could never report the break,
and it would make INV-002 vacuous.

**Decimal instead of integer minor units.** Rejected. Decimal avoids the
representation problem but reintroduces questions of precision and rounding mode
at every boundary, including JSON. An integer count of the smallest unit has one
obvious answer at every boundary and serialises exactly.

**A confidence score on a decision, used as a tie-breaker.** Rejected. It would
create exactly the field this contract exists to remove. A number between zero
and one invites the reader to treat a guess as partial evidence.

**One model for a payment-to-payout pair.** Rejected. A payout carries many
lines and a payment can be followed by refunds and chargebacks long after it
settles. A one-to-one model would make the common cases look like exceptions.

**Deferring the evaluation contract until the harness is built.** Rejected. A
benchmark designed after the system tends to describe what the system already
does well. `docs/evaluation-contract.md` is written first for that reason.
