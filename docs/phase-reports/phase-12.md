# Phase 12: Evidence-backed payout-to-bank finality

- Date: 2026-08-28
- Exit gate: passed
- Domain contract 5.0.0, parser 3.0.0, baseline 1.0.0, review contract 1.0.0, all unchanged
- New: bank finality 1.0.0, bank statement schema 1.0.0, migration `0004_bank_finality_audits`, `ADR-016`
- Baseline and benchmark output byte-identical to Phase 11.1
- **Two claims below were corrected in [Phase 12.1](phase-12-1.md).** See "Corrected later".

## What this phase is, in one paragraph

Until now every conclusion in this system came from one source: the payment
provider describing its own behaviour. A `RESOLVED` line means the provider's
records agree with each other. It does not mean the merchant has the money. This
adds the only evidence that can say that, a bank statement, and audits it as a
separate conclusion that is never folded into the first.

## What was already there, and what was not

The inspection this phase started with, because it decided the design.

| Found | Consequence |
| --- | --- |
| `SourceRecordType.BANK_TRANSACTION` exists in the contract | Nothing to add to the record-type vocabulary |
| Ingestion refused it because the parser had no CSV schema, deliberately rather than by omission | The refusal path stays, tested against a type with its layout removed |
| `PayoutBatch.utr` already exists as an optional exact bank reference | **No match key was invented.** A payout with no UTR is `UNLINKABLE_PAYOUT` |
| `payouts.csv` already ships one payout with a UTR and one without | The example corpus demonstrates both outcomes without being contrived |
| Runs are keyed by snapshot fingerprint plus rule versions, append-only, with triggers | The audit copies that shape exactly, including the same fingerprint |
| `EvidenceRef` and `verify_against_index` already exist | Certificates cite records the same way decisions do |

## The rule

A payout verifies when **exactly one** bank statement row carries its reference,
that row is a **credit**, and its **amount** and **currency** equal the payout's
**exactly**.

There is no tolerance band, no rounding, no nearest-amount search, no date
window, no case folding of references, no prefix matching, no amount-only
matching and no model-generated link. One minor unit of difference is a
mismatch.

Every one of those omissions is somebody's reasonable-sounding idea, and every
one would produce a finality claim that is right most of the time. That is worse
than no claim: a merchant would act on it and would have no way to know which of
their payouts was in the wrong tail.

Checks are ordered so the answer is the one a reader needs. Direction first,
because a debit is not a weaker credit. Currency next, because two amounts in
different currencies cannot be compared and reporting a size difference would
send somebody looking for a missing hundred. Amount last, exactly.

### Seven outcomes

| Outcome | What the records said |
| --- | --- |
| `VERIFIED_BANK_CREDIT` | Exactly one credit carrying this reference, this exact amount and currency |
| `MISSING_BANK_EVIDENCE` | The payout names a reference and no imported row carries it. **Not** a claim the money failed to arrive |
| `UNLINKABLE_PAYOUT` | The payout carries no reference, so no exact association is possible. A gap in the provider record, not a discrepancy |
| `AMBIGUOUS_BANK_EVIDENCE` | Two or more rows carry the reference. Every candidate is cited |
| `BANK_DIRECTION_MISMATCH` | The one row carrying the reference is a debit |
| `BANK_AMOUNT_MISMATCH` | A different number of minor units. Any difference |
| `BANK_CURRENCY_MISMATCH` | A different currency |

`MISSING_BANK_EVIDENCE` and `UNLINKABLE_PAYOUT` are the two kinds of nothing,
and keeping them apart is most of the value. The first is a prompt to import a
statement. The second is a prompt to ask the provider for a reference, and no
statement will ever fix it.

## Storage

Two new append-only tables, `bank_finality_audits` and
`bank_finality_certificates`, with the same UPDATE and DELETE triggers as the
five before them, created by migration `0004_bank_finality_audits`. Neither
earlier revision is touched.

An audit is keyed by the snapshot fingerprint plus both bank rule versions, and
is written once. **There is no mutable status column.** An audit that said
`MISSING_BANK_EVIDENCE` was telling the truth about a moment when the statement
had not been imported. Importing it later makes a new snapshot and a new audit
beside the old one, so "we did not know yet" stays recoverable, which is
precisely the fact an auditor asks about.

The snapshot fingerprint is the same digest a reconciliation run over the same
facts carries. `fingerprint` is imported rather than reimplemented, because a
second definition of "which facts did this see" would be a second answer to the
question that makes the two conclusions comparable.

The audit key is deliberately **not** the run key. They move for different
reasons: a baseline change makes a new run and must not make a new audit, and a
bank rule change makes a new audit and must not make a new run.

## Ingestion

One new CSV schema, the narrowest in the project:

```text
provider_event_id, bank_transaction_id, bank_reference, direction,
amount_minor, currency, occurred_at
```

No description, no counterparty, no balance. A column that cannot be compared
exactly is a column that invites a fuzzy match.

`direction` is `CREDIT` or `DEBIT`, as a column rather than as the sign of the
amount. `amount_minor` must be strictly positive in both directions, so a lost
minus cannot turn a payment out into a payment in.

`bank_reference` is required, unlike the payout's optional `utr`. A statement row
with no reference could never be cited by any payout, so storing it would store
a fact nothing can ever use. That is a real limitation and it is written down
rather than worked around.

Every existing rule applies unchanged, because they are the parser's rules
rather than each schema's: exact headers in order, no trimming, whitespace
refused rather than normalised, atomic imports, and a receipt for every attempt
including a refused one.

## Versioning

| Version | Before | After | Why |
| --- | --- | --- | --- |
| Domain schema | 5.0.0 | 5.0.0 | No decision, invariant or code reads a bank fact |
| Baseline | 1.0.0 | 1.0.0 | The reconciliation engine is untouched |
| Parser | 3.0.0 | 3.0.0 | See below. **Corrected to 3.1.0 in [Phase 12.1](phase-12-1.md)** |
| Review contract | 1.0.0 | 1.0.0 | Untouched |
| Bank finality | none | 1.0.0 | A new independent contract |
| Bank statement schema | none | 1.0.0 | The new CSV layout, versioned separately |
| Migration head | 0003 | 0004 | Two new tables |

**The parser version is the interesting one, and it is a deliberate exception.**
Its stated rule is that it changes when a header set changes, and this phase adds
one. It is not bumped, and the reason is what the version is used for rather than
what it is called: it is an input to the reconciliation run key, because a parser
change *can* change a conclusion about the payment records. Adding a layout for a
record type no invariant reads cannot. Bumping it would have created a new run
for every existing database, with different decision identifiers, for a change no
decision can observe.

Every rule that applies to a previously supported row is unchanged, so the
version still means what it meant for every fact it has ever applied to. The bank
layout is versioned by `BANK_STATEMENT_SCHEMA_VERSION`, recorded on every audit,
so a bank fact is still traceable to the rules that produced it. The same
argument keeps `BankTransaction` out of the exported domain schema. Both are
recorded in ADR-016 and in the ingestion contract rather than left as a silent
judgement.

> **Corrected in Phase 12.1. This was wrong.** The argument is about the run key,
> and `PARSER_VERSION` is recorded on the import receipt that creates a fact. A
> bank statement imported under this phase was stamped 3.0.0, a version with no
> bank layout, so the receipt named rules that could not have produced the
> evidence it describes. `PARSER_VERSION` is now 3.1.0, minor because the change
> accepts more and refuses nothing that was accepted before. The
> `BankTransaction`-out-of-the-domain-schema half of the argument stands: that
> one is about a contract no decision reads rather than about provenance.

## API and interface

Four routes under `/v1/bank-finality`, matching the reconciliation API exactly:
create (201 new, 200 for an identical audit), list with a
`snapshot_fingerprint` filter, read one audit with its certificates, read one
certificate.

`verified_payout_count` is a count and never a rate. A percentage would invite
reading ninety percent as nearly settled, and the ten percent is where a
merchant is missing money.

The interface adds a **Bank finality** panel to the run audit screen, found by
the run's own snapshot fingerprint so the join is exact rather than by time.

The distinction is carried by the layout, not only by the words. The finality
badge is a different shape with a different mark and borrows no colour from the
resolved palette; a verified bank credit is stated in the accent colour, never in
the green a settled line uses. Above the outcomes sits a non-dismissible
sentence saying the two are separate conclusions. No certificate contains the
word `RESOLVED`, and a test asserts that.

A payout is not a settlement line, so finality is a panel rather than a column:
one payout covers many lines, and there is no row to put it in without
implying a relationship the records do not state.

## Verified behaviour

136 new backend cases and 34 new frontend cases.

| Suite | Cases | Proves |
| --- | --- | --- |
| `tests/banking/test_finality.py` | 43 | The rule, one outcome at a time, every negative isolated by a one-field paired control |
| `tests/banking/test_audits.py` | 29 | Idempotent replay, immutability of earlier audits, byte-for-byte decision equality, raw UPDATE and DELETE refused |
| `tests/banking/test_ingestion.py` | 21 | The schema, its refusals, and that a bank fact changes no conclusion |
| `tests/api/test_bank_finality.py` | 43 | Every route, every error, the paired controls end to end, and that no response reads as a settled line |
| `RunAuditPage.test.tsx` (new cases) | 15 | The panel, the separation, and that a certificate never says resolved |
| `parse.test.ts`, `client.test.ts` (new cases) | 19 | The wire shapes checked rather than cast |

### The paired controls

Every negative case is the verified case with exactly **one** field changed.
That is what makes them evidence: a test that built each outcome from its own
facts would pass whether or not the rule under test produced the outcome.

```text
control                        -> VERIFIED_BANK_CREDIT
direction: DEBIT               -> BANK_DIRECTION_MISMATCH
currency: USD                  -> BANK_CURRENCY_MISMATCH
amount_minor: +1               -> BANK_AMOUNT_MISMATCH
amount_minor: -1               -> BANK_AMOUNT_MISMATCH
bank_reference: something else -> MISSING_BANK_EVIDENCE
payout utr removed             -> UNLINKABLE_PAYOUT
a second row, same reference   -> AMBIGUOUS_BANK_EVIDENCE
```

The same eight arrangements exist as committed CSV fixtures and are driven
through the whole stack in the API tests, so the outcome a screen would show is
the one the rule produced.

Three of those cases are worth naming:

- **A debit that is otherwise perfect never verifies.** Same reference, same
  amount, same currency, opposite direction.
- **An exact row and a wrong-amount row under one reference is ambiguous**, not
  a verification. Picking the exact one would be choosing the evidence that gives
  the answer somebody wanted.
- **An unlinkable payout beside a lone credit of exactly the right amount stays
  unlinkable.** The most tempting guess in the system, refused: nothing in the
  records says that credit is that payout.

### Nothing is approximate

A reference that is nearly right is missing evidence, not a match: lower case, a
truncated suffix, an extra character, a space where a hyphen was. A reference
padded with whitespace cannot exist as a fact at all, because the contract
refuses it on projection and the parser refuses it on import, which is stronger
than not matching it.

There is no date window either, in either direction. A credit dated years later
still verifies, because when the money arrived is a fact the certificate carries
rather than a condition on the match. A window would be a guess about settlement
timing.

### Nothing here changes a decision

```python
before = stored_decisions_json(engine, run_id)
record_audit(engine, index_of(*settlement_facts()))
assert stored_decisions_json(engine, run_id) == before
```

Asserted for a passing audit and for a failing one, against the stored decisions
and against a freshly recomputed baseline, plus the run's status and exception
counts. And the case the phase exists for, asserted directly: a line that is
`RESOLVED` while its payout is `MISSING_BANK_EVIDENCE`.

## Observed results

The example documents, before and after the bank statement is imported:

```text
run 43e5418ddabd  statuses {"EXCEPTION": 2, "INSUFFICIENT_EVIDENCE": 0, "PENDING": 0, "RESOLVED": 1}

before the statement is imported:
  payout-0001    MISSING_BANK_EVIDENCE    ref=UTR2026082100001
  payout-0002    UNLINKABLE_PAYOUT        ref=None
  verified payouts: 0 of 2

after the statement is imported:
  payout-0001    VERIFIED_BANK_CREDIT     matched=['BANKTXN0001']
  payout-0002    UNLINKABLE_PAYOUT        matched=[]
  verified payouts: 1 of 2
  the earlier audit still says: ['MISSING_BANK_EVIDENCE', 'UNLINKABLE_PAYOUT']
  the recorded run still says : {"EXCEPTION": 2, ..., "RESOLVED": 1}
```

Three things in that output are the whole phase. One payout can never be
verified because the provider recorded no reference for it, and importing a
statement does not change that. The earlier audit still says what it said. And
the reconciliation run is untouched throughout.

**This is a demonstration corpus of two payouts.** One verified credit out of two
payouts is a fact about these fixtures and about nothing else. It is not an
accuracy figure, there is no rate published anywhere, and no number here
describes how this would perform against a real bank statement.

### The baseline is unmoved

```text
reconcile over the example documents: efc16896fdc7bf2cb0649312f07efae3fb4f9931bd7e7b2d5aed3d22c8b9d3dd
public benchmark report            : e5cff7b46a22c4d5b89ee0361ac1e373a4680f2f4a9ec268575b242cf60c4b5c
```

Both identical to Phase 11 and 11.1, and the benchmark hash is identical to
Phase 10 as well. Regenerating the published JSON Schema produced no diff.

## Commands run

```text
uv run ruff format --check .                    148 files already formatted
uv run ruff check .                             All checks passed
uv run mypy                                     no issues in 143 source files
uv run pytest                                   1839 passed, 100.00% coverage
pnpm run format:check                           Prettier clean
pnpm run lint                                   eslint, 0 warnings
pnpm run typecheck                              tsc clean
pnpm run test                                   305 passed, 98.94% statements
pnpm run build                                  production bundle built
make schema                                     no diff
make benchmark-evaluate                         byte-identical to Phase 10
./scripts/verify-containers.sh                  passed, both non-root
```

Migration and legacy-adoption suites run inside `uv run pytest`, and the existing
trigger tests cover the two new tables without being edited, because
`APPEND_ONLY_TABLES` is the list they derive from.

## Limitations

**Exact-reference matching cannot verify a payout whose provider record and bank
record share no reference.** This is the standing limitation and it is not a
defect to be fixed with a cleverer matcher. `payout-0002` in the demo corpus is
permanently `UNLINKABLE_PAYOUT`, whatever statement is imported. Closing it
needs a shared reference in the data, which is a conversation with the provider
and the bank rather than a change here.

**A statement export whose rows carry no reference cannot be imported at all.**
The schema requires `bank_reference`, so such a file is refused whole. That is a
real usability cost, chosen over storing facts nothing could ever cite.

**A verified credit is not proof the merchant kept the money.** It is proof that
a credit carrying that reference, for that amount, arrived in the account the
statement describes. A later reversal is a later statement row, and this phase
does not look for one.

**One statement, one account, no reconciliation of the account itself.** The
audit does not check that the statement is complete, that it belongs to the
merchant, or that the rows sum to anything. It compares one payout to one row.

**The demonstration numbers are fixtures.** Two payouts and one statement row.
Nothing here is a measurement of production accuracy, and no rate is published
anywhere in the API or the interface precisely so that none can be quoted as one.

**No authentication, unchanged.** Anyone who can reach the API can record an
audit. Audits are append-only and cannot alter anything, so the worst case is
noise rather than corruption, but it is still a gap.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| No existing decision, status, invariant, code, run key or baseline version changed | Passed | Byte-for-byte comparison stored and recomputed; all four versions unmoved |
| A line may be RESOLVED while finality is MISSING_BANK_EVIDENCE | Passed | `test_a_resolved_line_can_have_no_bank_evidence`, and the observed output above |
| No fuzzy, amount-only, model-generated, tolerance or likely match | Passed | `TestNothingIsApproximate`; the audit reads only payouts and bank rows |
| Exact reference, direction, amount and currency required | Passed | `TestTheOneArrangementThatVerifies` plus every paired control |
| Six failure cases kept distinct | Passed | Seven outcomes, one test each, isolated by one field |
| Minimal documented CSV schema, existing parser conventions | Passed | Seven columns, no free text; whitespace, atomicity and receipt rules unchanged |
| Stable identity, time, currency, positive amount, direction, exact reference | Passed | All required; the reference required rather than optional |
| No transaction descriptions or free text ingested | Passed | `test_there_is_no_free_text_column` |
| Deterministic audit over one exact snapshot | Passed | Pure function of the snapshot; two audits byte-identical |
| Immutable audits keyed by snapshot plus rule version, idempotent replay | Passed | `TestAnAuditIsRecordedOnce`; 200 on the second call |
| Certificate identifies payout, snapshot, versions, cited facts, outcome, expected versus observed | Passed | Every field asserted, and the citations verified by the existing verifier |
| No mutable current-status column; new facts make a new audit | Passed | `TestAnEarlierAuditIsNeverRewritten`; no such column exists |
| Create, list, read endpoints on the immutable-run pattern | Passed | 201/200, list with a snapshot filter, detail, single certificate |
| Certificates exposed in the run audit UI | Passed | The Bank finality panel, joined by snapshot fingerprint |
| The distinction unmistakable; never a generic Resolved badge | Passed | Different badge, different palette, non-dismissible sentence, and the word absent from certificates |
| Exact cited records and mismatch values shown | Passed | Record IDs, payload hashes, expected and observed side by side |
| Review workflow separate; no bank action mutates review or decisions | Passed | No import of `app.review` in `app.banking`; no writer to either table |
| Exact happy path verifies | Passed | 4 cases |
| Each negative isolated by a one-field paired control | Passed | 8 arrangements, in unit tests and as committed fixtures |
| One minor unit is a mismatch, no tolerance | Passed | Over and under, both |
| Missing reference is unlinked, not guessed | Passed | Including beside a lone exact credit |
| Two rows are ambiguous, not arbitrarily selected | Passed | Including when one of them is exact |
| A debit never verifies | Passed | Otherwise identical to the verified control |
| Byte-for-byte decision proof before and after | Passed | Stored and recomputed, passing and failing audits |
| Old audits unchanged after later evidence | Passed | Through the service and through the API |
| Raw UPDATE and DELETE refused, INSERT allowed | Passed | Both tables, raw SQL |
| A finality failure is never presented as a settled line | Passed | API and frontend tests, including the word `RESOLVED` |
| Paired-control fixtures and an honest report | Passed | 7 committed CSV fixtures; no accuracy claimed anywhere |
| ADR, API docs, README, phase report | Passed | ADR-016 and the four documents |
| The remaining limitation stated plainly | Passed | README, ADR-016 and "Limitations" above |
| Full CI, migrations, schema, frontend, containers, byte-identical baseline | Passed | Commands and hashes above |

## Corrected later

Two claims here were reasoned carefully and reached the wrong answer.

| Claimed here | What was true | Fixed in |
| --- | --- | --- |
| The parser version deliberately does not move for the new header set, because bumping it would create a new run for a change no decision can observe | `PARSER_VERSION` goes on the import receipt that creates a fact, not only into the run key. A bank statement was stamped 3.0.0, a version that had no bank layout, so the receipt attributed evidence to rules that could not have read it. The run-key cost was the smaller thing, and a new run key for a genuinely different parser is correct provenance. | [Phase 12.1](phase-12-1.md), part A |
| Bank finality audits are idempotent on the audit key | True of a second call that sees the first. Two writers whose lookups both miss produced an `IntegrityError` for the loser rather than the winner's audit. The unique constraint kept the data correct and the caller got a database error. | [Phase 12.1](phase-12-1.md), part B |

The exit-gate row "Immutable audits keyed by snapshot plus rule version,
idempotent replay" was tested with sequential calls, which is the case that
worked. There was no row asserting anything about concurrent ones.

## Unresolved

Nothing blocking. The open items are the standing limitation of exact-reference
matching, the statement schema requiring a reference on every row, the absence of
any completeness check on the statement itself, and the unchanged absence of
authentication. All four are described above and none is hidden behind a passing
row.
