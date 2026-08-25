# Ingestion contract, parser version 3.0.0

This describes the CSV documents Settlement Witness accepts and what it does
with them. The code in `backend/app/ingestion/` is the definition; this page
explains it.

Ingestion reads, validates and normalises. It decides nothing about
reconciliation, and it never edits a fact that is already stored.

## The three documents

A document is read as exactly one record type, declared by the caller. It is
never guessed from the file name or the contents.

Headers must match exactly, including order and whitespace. A missing column, an
unexpected column, and a padded column name are all refusals. A file that is
nearly right is more dangerous than one that is obviously wrong, because a
silently ignored column is a field that quietly stopped being reconciled.

### Payment events

```text
provider_event_id, event_id, payment_id, merchant_id, event_type,
amount_minor, currency, occurred_at
```

`event_type` is one of `CAPTURE`, `REFUND`, `REVERSAL`, `CHARGEBACK`.
`amount_minor` must be strictly greater than zero; direction comes from the
type. An event that moved no money is not an event, and a zero refund was
previously usable to switch off the settlement gross check.

### Settlement lines

```text
provider_event_id, settlement_line_id, payout_id, payment_id,
gross_minor, fee_minor, tax_minor, adjustment_minor, net_minor,
currency, occurred_at
```

`gross_minor`, `fee_minor` and `tax_minor` are non-negative magnitudes.
`adjustment_minor` and `net_minor` are signed.

`net_minor` is stored exactly as the document declares it. It is never
recomputed. INV-002 exists to compare the declared net against the signed
formula, and a parser that corrected it would leave that check with nothing to
find.

### Payout batches

```text
provider_event_id, payout_id, merchant_id, net_minor, currency, utr,
occurred_at
```

`utr` may be empty, meaning the bank reference was not available when the
document was produced. Every other column is required.

## Column rules

Every cell is read exactly as the document wrote it. Nothing is trimmed.

| Kind | Accepts | Refuses |
| --- | --- | --- |
| Identifier | Non-empty text | Empty, or any surrounding whitespace |
| Amount, minor units | A whole number, optionally signed | `12.5`, `12.0`, `1e3`, `"1,000"`, `NaN` |
| Amount, magnitude | The same, and not below zero | Anything negative |
| Amount, must move money | The same, and above zero | Zero and anything negative |
| Currency | ISO 4217 alpha-3, upper case | `inr`, `rupees`, `IN` |
| Timestamp | ISO 8601 with an offset | Anything naive, anything unparseable |
| Enum | A value the contract defines | Anything else |
| Optional identifier | Text, or exactly empty meaning absent | Whitespace-only |

### Whitespace is refused, never trimmed

A cell with leading or trailing whitespace is rejected with
`SURROUNDING_WHITESPACE`. This applies to every column: identifiers, amounts,
currency, enums and timestamps alike.

Trimming is a guess about what the producer meant, and this parser refuses
ambiguous input rather than guessing. It also hides a real class of defect. A
padded identifier usually means an export template is broken, and silently
accepting it lets one file produce two different identities depending on which
system read it.

Whitespace inside a value is left alone. A merchant name of `acme retail` is a
real value; ` acme retail` is a defect.

A whitespace-only cell is refused too, including in the optional `utr` column.
It is not the same as an empty cell, and quietly treating it as one would make a
blank column and a space-filled column mean the same thing, so one of them would
be a defect nobody ever saw. `utr` may be exactly empty, and nothing else.

### Money is never decimal

`12.0` is refused as firmly as `12.5`. A decimal point in a money column means
the file is quoting a different unit from the one the contract stores. Accepting
the integral case today invites the fractional case tomorrow, and the difference
would surface much later as a settlement break that is really a rounding error.

Every amount is stored as an integer count of the currency's minor unit, and a
canonical payload holds no floats at all.

### Timestamps carry an offset

A naive timestamp is refused rather than assumed to be UTC. Guessing a timezone
for a financial record makes ordering and settlement windows wrong in ways that
are hard to see later.

An offset that is present is accepted and stored as the same instant in UTC,
because the offset a timestamp arrived with is presentation, not fact.

### The contract has the last word

A row that this parser accepts can still be refused by the domain model, for
example an identifier longer than 200 characters. That row is rejected. The
parser is a reader of files; `backend/app/domain/` is the definition.

## Identity

A source record ID is derived, not assigned:

```text
{document sha256}:{source system}:{record type}:{row number}
```

Row numbers are one-based and count the header as row 1, so they match what a
person sees when they open the file.

Three deliberate choices are in that shape.

**Never a file path.** The same bytes imported from a different directory, or
from a stream with no path at all, are the same records. A path would also leak
a local directory layout into stored identifiers.

**The document hash, not a name.** A file renamed is the same document. A file
changed by one byte is a different one.

**The source system.** The contract treats one event seen through two systems as
two observations, because the systems can disagree and that disagreement is
worth keeping. Without the system in the identity, loading one document as a
provider feed and as a merchant ledger would collide, and the second observation
would be swallowed as a duplicate of the first.

The source locator on every fact records the document hash and the one-based row
number, so an exception can point a person at the exact row that caused it.

## Idempotency

Identity for duplicate detection is `(source_system, provider_event_id)`, as the
domain contract defines it.

| Case | Outcome | Effect |
| --- | --- | --- |
| No fact holds that identity | `ACCEPTED` | The fact is stored |
| Same identity, same payload hash | `DUPLICATE_NO_OP` | Nothing is written, and that is success |
| Same identity, different payload hash | `DUPLICATE_CONFLICT` | The whole import is rejected |

Re-running an import must be safe, because providers resend and operators retry.
A conflict is different: one of the two observations is wrong, and neither may
be silently preferred, so nothing is written and the disagreement is recorded.

Neither case ever overwrites a stored fact. Source facts are append-only, and a
correction is a later fact from a later document.

A document is also checked against itself, so a file that contradicts its own
earlier rows is caught before anything is written.

## Atomicity

**A document is accepted whole or not at all.** One malformed row, or one
conflict with a stored fact, rejects the entire import.

A half-loaded file is worse than a rejected one, because the gap is invisible. A
later reconciliation would report a missing settlement that was never missing,
only never loaded.

**Every attempt leaves a receipt.** Facts are written inside a savepoint that is
rolled back when the import is refused. The receipt is written outside it. A
refusal therefore writes no facts and still records what was tried, what was
wrong with it, and when.

On a rejected import, no row is recorded as `ACCEPTED`. A row that was fine is
recorded as `NOT_APPLIED`: it was acceptable, and it is not in the store.
Recording it as accepted would claim a fact exists that does not.

This holds for a refusal that comes from the database rather than from the
preflight examination. Reaching the unique constraint means the two disagreed,
and the receipt then describes the empty result rather than the optimistic one:
every pending row is re-examined against the rolled-back database and against
the rest of the import, rows that genuinely collided become
`DUPLICATE_CONFLICT`, the rest become `NOT_APPLIED`, the counts are recomputed
from the rewritten rows, and the failure detail names the colliding source
records. A receipt whose counts contradict its rows is worse than no receipt.

## Outcomes

Document level:

| Outcome | Meaning | Facts written | Exit status |
| --- | --- | --- | --- |
| `ACCEPTED` | Every row was new | All of them | 0 |
| `DUPLICATE_NO_OP` | Every row was already stored, unchanged | None | 0 |
| `REJECTED_CONFLICT` | A row contradicts a stored fact | None | 1 |
| `REJECTED_INVALID` | A row or the document could not be read | None | 1 |

Row level: `ACCEPTED`, `DUPLICATE_NO_OP`, `DUPLICATE_CONFLICT`, `REJECTED`,
`NOT_APPLIED`.

Row refusal codes: `MISSING_VALUE`, `SURROUNDING_WHITESPACE`, `NOT_AN_INTEGER`,
`NEGATIVE_AMOUNT`, `NON_POSITIVE_AMOUNT`, `INVALID_CURRENCY`, `NAIVE_TIMESTAMP`,
`INVALID_TIMESTAMP`, `INVALID_ENUM`, `WRONG_FIELD_COUNT`,
`DOMAIN_VALIDATION_FAILED`.

`NEGATIVE_AMOUNT` and `NON_POSITIVE_AMOUNT` are different rules on different
columns. A settlement fee may be zero, so only a negative one is refused. A
payment event amount must move money, so zero is refused as well.

Document refusal codes: `UNREADABLE_ENCODING`, `MISSING_HEADER`,
`UNEXPECTED_COLUMNS`, `UNSUPPORTED_RECORD_TYPE`, `NO_ROWS`.

Every row is examined even after the first failure, so one import reports
everything wrong with a file rather than one problem per run.

## Storage

Two tables, and the difference between them is the point.

`source_facts` holds what the system believes. Append-only: a row is inserted
once and never updated or deleted. The idempotency identity is enforced by a
unique constraint, not only by the code that writes to it.

`import_receipts` holds what the system was told and what it did about it,
including the attempts it refused. Each receipt records the document hash, the
document name, the source system and record type, the parser version, the
received-at time, the outcome, the row counts, one entry per row, and any
failure detail. Ordering is by a database-assigned sequence, because an audit
trail that reorders attempts is not an audit trail.

Neither repository has an update method or a delete method. That stops the
application from rewriting history by mistake, and it does nothing about a
migration script or a maintenance session holding a connection. So both tables
also carry SQLite triggers that abort any `UPDATE` or `DELETE`:

```text
source_facts is append-only: UPDATE is not permitted
import_receipts is append-only: DELETE is not permitted
```

`INSERT` is unaffected. The triggers are created by ordinary `make db-setup`,
with `IF NOT EXISTS`, so a clean database is protected without a separate
hardening step and setup stays safe to run again.

## The read path

`SourceFactRepository.fact_index()` returns the complete accepted fact index for
`verify_decision`.

**Storage must supply the complete index.** A partial index is safe but not
useful: a citation whose fact was left out resolves to nothing, so the decision
comes back `INSUFFICIENT_EVIDENCE` rather than a wrong resolution. Safe is not
the same as correct. That is why completeness is storage's responsibility rather
than each caller's, and why this method returns everything rather than a
filtered view.

The index is keyed by each fact's own source record ID, which is what the
Phase 1.2 hardening requires of any index handed to a verifier.

## Lifecycle records

Payment events, settlement lines and payout batches are projected from stored
facts on demand. They are views, not a second copy.

One place a correction can come from, and no possibility of the two disagreeing.
Every projection carries the `source_record_id` of the fact it came from, so any
lifecycle record traces back to the row of the document that produced it.

`PayoutBatch.settlement_line_ids` is empty. A payout document says what the
batch totalled, not which lines composed it. That association is established by
matching, in a later phase. Filling it in here would create evidence that no
document supports.

## Running an import

```bash
make db-setup
make import-fixtures
```

Or one document at a time:

```bash
cd backend && uv run python -m app.ingest_cli \
  --database ../data/generated/settlement.sqlite \
  --source-system PSP_API --record-type PAYMENT_EVENT \
  ../data/fixtures/ingestion/payment_events.csv
```

The source system and record type are declared, never inferred. A file read as
the wrong record type fails loudly on its headers. A file read as the wrong
source system would import cleanly and be wrong, which is why the caller has to
say.

## Example documents

`data/fixtures/ingestion/` holds a valid example of each schema and several
deliberately invalid ones. They are contract examples, small enough to read.
They are not the benchmark dataset, and a system that handles them proves only
that it handles them.

| File | Demonstrates |
| --- | --- |
| `payment_events.csv` | Five rows, including a refund dated after a later capture |
| `settlement_lines.csv` | Three rows, one with a negative adjustment |
| `payouts.csv` | Two rows, one with no bank reference |
| `conflicting_payment_events.csv` | One row contradicting a stored fact |
| `invalid_float_money.csv` | `12.5` and `12.0` |
| `invalid_naive_timestamp.csv` | A timestamp with no offset |
| `invalid_headers.csv` | An unexpected column |
| `invalid_mixed_rows.csv` | One good row and two bad ones, to show atomicity |
| `invalid_zero_amount.csv` | A capture followed by a refund of zero |
| `invalid_negative_amount.csv` | A capture of a negative amount |

## Parser version

`PARSER_VERSION` is `2.0.0` and is recorded on every receipt, so a fact can
always be traced to the rules that produced it. It changes when a header set, a
coercion rule, or the source-record ID derivation changes.

2.0.0 stopped trimming whitespace and started refusing it. 3.0.0 started
refusing a payment event amount of zero. Each is a major step because documents
the previous version accepted can be refused by the next. Facts already stored
are unaffected in both cases: the change is to what is accepted, not to how an
accepted row is represented.
