# ADR-016: Records agreeing is not money arriving

- Status: Accepted
- Date: 2026-08-28
- Supersedes: none
- Superseded by: none
- Related: [ADR-003](ADR-003-derived-status-and-source-fact-verification.md),
  [ADR-005](ADR-005-exact-reference-matching-and-snapshot-payouts.md),
  [ADR-009](ADR-009-immutable-runs-and-migrations.md),
  [ADR-015](ADR-015-review-events-annotate-they-do-not-decide.md)

## Context

Everything this system had concluded until now came from one source: the payment
provider describing its own behaviour. A `RESOLVED` settlement line means the
provider's payment events, settlement line and payout agree with each other and
with the invariants over them.

It does not mean the merchant has the money. A provider can be internally
consistent and the transfer can fail, bounce, go to a closed account, or simply
not have been made. The only record that can say money arrived is a bank
statement, and until this phase this system had never read one.

The dangerous version of that gap is not the gap. It is a screen that shows
`RESOLVED` and lets a merchant read it as "paid". The word is doing work it was
never entitled to do, and the more the rest of the system is trusted the more
that costs.

## Decision

**Bank finality is a second, independent, evidence-pinned conclusion about the
same facts, and it is never folded into the first.**

### 1. Two conclusions, two vocabularies

A settlement decision has a `DecisionStatus`. A bank finality certificate has a
`BankFinalityOutcome`. The two enums share no value, a test asserts they are
disjoint, and no outcome contains the word `RESOLVED`.

That is not decoration. A client that indexes a badge map by string, or a person
scanning a table, must not be able to read one as the other, and the cheapest
way to guarantee that is to make the words different.

The interface follows: the finality badge is a different shape with a different
mark and borrows no colour from the resolved palette, and both the API and the
screen carry the sentence saying the two are separate.

### 2. Exact reference, correct direction, exact amount, exact currency

A payout verifies when exactly one bank statement row carries its reference,
that row is a credit, and its amount and currency equal the payout's exactly.

There is no tolerance band, no rounding, no nearest-amount search, no date
window, no case folding of references, no prefix matching and no "probable
match". One minor unit of difference is a mismatch.

Every one of those omissions is somebody's reasonable-sounding idea, and every
one would produce a finality claim that is right most of the time. A finality
claim that is right most of the time is worse than none, because a merchant
would act on it and would have no way to know which of their payouts was in the
wrong tail.

### 3. Seven outcomes, kept apart

`VERIFIED_BANK_CREDIT`, `MISSING_BANK_EVIDENCE`, `UNLINKABLE_PAYOUT`,
`AMBIGUOUS_BANK_EVIDENCE`, `BANK_DIRECTION_MISMATCH`, `BANK_AMOUNT_MISMATCH`,
`BANK_CURRENCY_MISMATCH`.

They are not collapsed into "verified" and "not verified" because the action a
person takes differs for every one: chase the bank, chase the provider for a
reference, ask which of two rows is the payout, or investigate a real
discrepancy.

Two of them are worth naming here.

`MISSING_BANK_EVIDENCE` says this system has not been shown the money arriving.
It does not say the money failed to arrive. The statement may simply not have
been imported, and the honest report of that is a statement about our evidence
rather than about the world.

`UNLINKABLE_PAYOUT` is the case where the provider's own record carries no bank
reference. There is nothing to match on, so nothing is matched. A payout with no
reference and a lone credit of exactly the right amount is the most tempting
guess in the whole system, and it is refused: nothing in the records says that
credit is that payout.

### 4. Immutable audits, no current-status column

An audit is keyed by the snapshot fingerprint and the bank finality rule
versions, and is written once. Importing a statement later does not change an
earlier audit. It makes a new snapshot, and a new audit beside the old one.

This is the same rule as ADR-009 and it matters more here. An audit that said
`MISSING_BANK_EVIDENCE` was telling the truth about a moment when the statement
had not been imported. A mutable status column would silently rewrite what was
known and when, and "we did not know yet" would become unrecoverable, which is
precisely the fact an auditor asks about.

The snapshot fingerprint is deliberately the same digest a reconciliation run
over the same facts carries, so the two conclusions about one moment can be put
side by side without either being re-derived.

### 5. Its own versions, and no existing one moves

`BANK_FINALITY_VERSION` and `BANK_STATEMENT_SCHEMA_VERSION` both start at 1.0.0.
The domain contract, the baseline and the parser version do not move.

The parser version is the interesting one. Its stated rule is that it changes
when a header set changes, and this phase adds one. It is not bumped, because it
is an input to the reconciliation run key: bumping it would create a new run for
every existing database, with different decision IDs, for a change that cannot
alter a single conclusion about the payment records. It is in the run key
precisely because a parser change *can* change a conclusion, and adding a layout
for a record type no invariant reads cannot. The bank layout is versioned by
`BANK_STATEMENT_SCHEMA_VERSION`, which is recorded on every audit, so a bank
fact is still traceable to the rules that produced it.

The same argument applies to the domain contract. `BankTransaction` is not in
the exported domain JSON Schema and does not move `DOMAIN_SCHEMA_VERSION`,
because no decision, invariant, exception code or reason code reads it, and
bumping it would rewrite the declared version of every recorded decision.

## Consequences

- **A payout whose provider record and bank record share no reference cannot be
  verified by this system, ever.** That is the standing limitation of exact
  matching and it is not a defect to be fixed with a cleverer matcher. Closing
  it needs a shared reference in the data, which is a conversation with the
  provider and the bank rather than a change here.
- The bank statement schema requires a reference on every row, so a statement
  export whose rows carry none cannot be imported at all. That is a real
  usability cost, chosen over storing facts nothing could ever cite.
- A verified count is published and a verified *rate* is not. A rate invites
  reading ninety percent as nearly settled, and the ten percent is where a
  merchant is missing money.
- The audit reads only payouts and bank transactions. It cannot see settlement
  lines or payment events, so it cannot be tempted to infer finality from the
  provider's own records, which is the thing it exists to be independent of.
- Nothing in this phase writes to a reconciliation decision, and a test compares
  every stored and recomputed decision byte for byte before and after auditing.


## Amendment, Phase 12.1

Section 5 argued that `PARSER_VERSION` should not move for the bank layout,
because it is an input to the reconciliation run key and a layout no invariant
reads cannot change a conclusion. **That argument was about the wrong thing and
the decision it reached was wrong.**

`PARSER_VERSION` is recorded on the import receipt that creates a fact, and its
stated job is that a fact can always be traced to the rules that produced it. A
bank statement imported under Phase 12 was stamped 3.0.0, a version that had no
bank layout and no way to parse one. The receipt named rules that could not have
produced the evidence it describes, which is exactly the kind of untrue
provenance the rest of this system is built to avoid.

The run-key cost was real and it was the smaller thing. A new run key for the
same facts under a genuinely different parser is correct provenance, not
unwanted duplication, and it is what the run key is for.

**`PARSER_VERSION` is now 3.1.0.** Minor rather than major: the change accepts a
document that was previously refused and refuses nothing that was previously
accepted, and no rule applying to an existing record type moved.

`BANK_STATEMENT_SCHEMA_VERSION` stays 1.0.0 and now lives beside the layout it
describes. The two are not alternatives: `PARSER_VERSION` names the machinery
and goes on the receipt, and the layout version names the columns and goes on
the audit. **A future change to the bank columns moves both**, and a test pins
them together against the committed header row so a layout edited without moving
either fails rather than shipping evidence attributed to rules that did not read
it.

Nothing rewrites history. A receipt written under 3.0.0 still says 3.0.0, and a
recorded run keeps the parser version its key was computed from. Decision
content is unaffected either way: no decision carries a parser version, and a
byte-for-byte comparison across the bump asserts it.

The other Phase 12 argument in section 5, keeping `BankTransaction` out of the
exported domain schema, is unchanged. That one is about a contract no decision
reads, not about provenance on a receipt.
