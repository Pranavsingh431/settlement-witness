# Phase 2: Auditable CSV ingestion and append-only source-fact storage

- Date: 2026-08-25
- Exit gate: passed. See "Exit gate status".
- Parser version: 1.0.0
- Domain contract version: 2.0.0, unchanged

## Scope

Turn documented CSV documents into immutable source facts, store them so they
cannot be rewritten, keep an audit trail of every attempt, and hand
`verify_decision` the complete fact index that ADR-003 said Phase 2 owed it.

No matching, no decisions, no AI, no API endpoints, no frontend, no generator.

## What was built

### Ingestion, `backend/app/ingestion/`

| Module | Holds |
| --- | --- |
| `schemas.py` | The three column layouts, the column kinds, and `PARSER_VERSION` |
| `errors.py` | Row and document refusal codes |
| `parsing.py` | Deterministic reading, coercion, and source-record ID derivation |
| `service.py` | Row by row examination, idempotency, and the all-or-nothing commit |
| `projection.py` | Lifecycle records projected from stored facts |

Parsing uses the standard library `csv` module with the default dialect, so
behaviour does not depend on anything installed.

### Storage, `backend/app/storage/`

| Module | Holds |
| --- | --- |
| `models.py` | The two tables |
| `database.py` | Engine, schema creation, session scope |
| `repository.py` | `SourceFactRepository` and `ImportReceiptRepository` |

### Developer interface

- `app/db_setup.py`, behind `make db-setup`. Safe to run again.
- `app/ingest_cli.py`, behind `make import-fixtures`. Imports one document with
  a declared source system and record type.
- `data/fixtures/ingestion/`, four valid and four deliberately invalid examples.

SQLAlchemy was added as a dependency. It was already the frozen choice in
ADR-001, so no new decision was needed.

## The CSV schemas

Headers must match exactly, including order.

**Payment events**

```text
provider_event_id, event_id, payment_id, merchant_id, event_type,
amount_minor, currency, occurred_at
```

**Settlement lines**

```text
provider_event_id, settlement_line_id, payout_id, payment_id,
gross_minor, fee_minor, tax_minor, adjustment_minor, net_minor,
currency, occurred_at
```

**Payout batches**

```text
provider_event_id, payout_id, merchant_id, net_minor, currency, utr,
occurred_at
```

`utr` may be empty. Every other column is required. `net_minor` on a settlement
line is stored exactly as declared and never recomputed, because INV-002 exists
to compare it against the formula.

## Tables and repository interfaces

**`source_facts`**, append-only. Primary key `source_record_id`. Unique
constraint on `(source_system, provider_event_id)`, so the idempotency identity
is enforced by the database and not only by the code that writes to it. Columns
mirror `SourceFact` exactly, with the locator flattened and the canonical
payload as JSON.

**`import_receipts`**, append-only. Primary key is a database-assigned
`sequence`, because an audit trail has to be readable in the order things
happened and the receipt identifier is a random uuid. Records the document hash,
document name, source system, record type, parser version, received-at, outcome,
five counts, one entry per row, and any failure detail.

```python
class SourceFactRepository:
    get(source_record_id) -> SourceFact | None
    find_by_idempotency_key(key) -> SourceFact | None
    add(fact) -> None
    count() -> int
    all_facts() -> tuple[SourceFact, ...]
    fact_index() -> SourceFactIndex

class ImportReceiptRepository:
    add(receipt) -> None
    get(receipt_id) -> ImportReceiptRow | None
    all_receipts() -> Sequence[ImportReceiptRow]
    for_document(document_hash) -> Sequence[ImportReceiptRow]
```

Neither has an update method or a delete method. Append-only is enforced by the
absence of a way to do otherwise, and a test asserts the public surface.

## Identity

```text
{document sha256}:{source system}:{record type}:{row number}
```

Never a file path. The same bytes from a different directory are the same
records, and a path would leak a local directory layout into stored identifiers.

The source system is part of the identity because ADR-002 treats one event seen
through two systems as two observations. This was found by a failing test, not
by reasoning: without it, importing one document as a provider feed and as a
merchant ledger collided and the second observation was swallowed.

## Idempotency behaviour

| Case | Row outcome | Document outcome | Facts written |
| --- | --- | --- | --- |
| No fact holds the identity | `ACCEPTED` | `ACCEPTED` | All |
| Same identity, same payload hash | `DUPLICATE_NO_OP` | `DUPLICATE_NO_OP` | None |
| Same identity, different payload hash | `DUPLICATE_CONFLICT` | `REJECTED_CONFLICT` | None |
| Row unreadable | `REJECTED` | `REJECTED_INVALID` | None |
| Row fine, document rejected | `NOT_APPLIED` | as above | None |

A stored fact is never overwritten in either duplicate case. A document is also
checked against itself, so a file that contradicts its own earlier rows is caught
before anything is written.

`NOT_APPLIED` exists because an earlier version of this phase reported a good row
as `ACCEPTED` on a rejected import, which claimed a fact existed that did not.

## Atomicity behaviour

A document is accepted whole or not at all. Facts are appended inside a
savepoint; the receipt is written outside it. A refusal writes no facts and
always writes a receipt.

This needed the documented pysqlite workaround. Its legacy transaction handling
commits before a `SAVEPOINT`, so `begin_nested` did not roll back and the
guarantee was untrue while appearing to work. It was caught by a test that
imports inside a failing session scope and then asserts the store is empty. The
fix disables the driver's implicit BEGIN and emits BEGIN explicitly.

## Verification results

Host: macOS on arm64, uv 0.12.5, Python 3.12.12, Node 24.19.0, pnpm 10.15.0.

| Command | Exit | Observed |
| --- | --- | --- |
| `git diff --check` | 0 | No whitespace errors |
| `make schema` | 0 | Byte identical, so no domain model was touched |
| `make ci` | 0 | All nine core checks passed |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy` | 0 | `Success: no issues found in 51 source files` |
| `uv run pytest` | 0 | `412 passed`, `Total coverage: 100.00%` |
| `make db-setup` on a deleted database | 0 | Created `source_facts` and `import_receipts` |
| `make import-fixtures` | 0 | Three documents `ACCEPTED`, 5 + 3 + 2 rows |
| `make import-fixtures` again | 0 | Three documents `DUPLICATE_NO_OP`, still 10 facts |

Migration from a clean temporary database was exercised twice: by `make db-setup`
against a deleted file, and by tests that build a database in `tmp_path`.

### Measured outcomes, end to end through the CLI

Against one database, in order:

| Document | Outcome | Facts after | Receipts after |
| --- | --- | --- | --- |
| `payment_events.csv` | `ACCEPTED` | 5 | 1 |
| `settlement_lines.csv` | `ACCEPTED` | 8 | 2 |
| `payouts.csv` | `ACCEPTED` | 10 | 3 |
| `payment_events.csv` again | `DUPLICATE_NO_OP` | 10 | 4 |
| `conflicting_payment_events.csv` | `REJECTED_CONFLICT` | 10 | 5 |
| `invalid_float_money.csv` | `REJECTED_INVALID` | 10 | 6 |
| `invalid_naive_timestamp.csv` | `REJECTED_INVALID` | 10 | 7 |
| `invalid_headers.csv` | `REJECTED_INVALID` | 10 | 8 |
| `invalid_mixed_rows.csv` | `REJECTED_INVALID` | 10 | 9 |

Every rejected attempt left the store untouched and its own receipt behind. Exit
status was 0 for accepted and duplicate imports and 1 for rejected ones.

Refusals reported precisely: `12.5` and `12.0` both as `NOT_AN_INTEGER`, a naive
timestamp as `NAIVE_TIMESTAMP`, an extra column as `UNEXPECTED_COLUMNS` naming
both header lists, and the mixed file as `INVALID_ENUM` on row 3 and
`MISSING_VALUE` on row 4.

## Tests

412 total, up from 283. 129 added.

| File | Tests | Covers |
| --- | --- | --- |
| `tests/ingestion/test_parsing.py` | 40 | Coercion, refusals, identity derivation, header checks |
| `tests/ingestion/test_import_service.py` | 35 | Outcomes, idempotency, atomicity, out-of-order input |
| `tests/storage/test_repository.py` | 28 | Persistence, restart, the fact index, setup |
| `tests/ingestion/test_projection.py` | 14 | Lifecycle records and their source record IDs |
| `tests/ingestion/test_cli.py` | 12 | The command, its exit statuses and its output |

Every required case is covered explicitly:

- Valid import of each of the three CSV types.
- Lifecycle projections preserve their source record IDs.
- An exact duplicate import is a no-op and still writes a receipt.
- A conflicting payload is an auditable conflict that writes no partial data and
  does not overwrite the stored fact.
- Malformed documents are atomic, across four different failure shapes.
- The fact index from storage produces a `RESOLVED` decision through
  `verify_decision`.
- The database is closed, reopened, and the facts and audit history are still
  there, including a conflicting import rejected after a restart.
- Out-of-order input is stored in document order without reordering or
  rewriting, and the fixture genuinely exercises it: occurred-at order and row
  order differ.

Three defects were found by these tests rather than by inspection, and each was
fixed rather than worked around:

1. The source record ID omitted the source system, so one document loaded as two
   systems collided.
2. The audit trail had no insertion order; sorting fell back to a random uuid.
3. pysqlite silently disabled the savepoint, making the atomicity guarantee
   untrue.

A fourth was found by reading coverage: `ImportReceiptRepository.get` still
looked up by primary key after the key changed to `sequence`, so it could never
find a receipt.

## The read path

`SourceFactRepository.fact_index()` returns the complete accepted index.

**Storage must supply the complete index.** A partial index is safe but not
useful: a citation whose fact was left out resolves to nothing, so the decision
comes back `INSUFFICIENT_EVIDENCE` rather than a wrong resolution. Safe is not
the same as correct. Both halves of that are tested: a complete index resolves,
an empty one abstains with `EVIDENCE_FACT_NOT_FOUND`.

## Deferred to Phase 3

Nothing below is stubbed.

1. **The matching engine.** Nothing links a settlement line to a payment or a
   payout to its lines. `PayoutBatch.settlement_line_ids` is deliberately empty.
2. **Decisions in practice.** The contract can express one and storage can
   supply the evidence, but nothing produces one from real data yet.
3. **Invariant evaluation over stored data.** The checks exist and are pure;
   nothing runs them across a loaded database.
4. **The settlement window rule.** Still undefined, so `TIMING_PENDING` has no
   emitter. Unchanged from Phase 1.
5. **Bank statement ingestion.** `BANK_TRANSACTION` is a valid record type with
   no CSV schema and no projection. Importing one is refused rather than
   approximated.
6. **The AI provider interface.** Not built, per ADR-001.
7. **The benchmark harness and generator.** `docs/evaluation-contract.md`
   defines the obligations; `benchmark/` is still empty. The fixtures here are
   contract examples, not a dataset.
8. **API endpoints and frontend.** Still `/health` and the phase 0 shell.
9. **Schema migrations.** Setup is `create_all`. The first change that must
   preserve existing rows needs a migration tool and its own ADR.
10. **Audit retention.** The receipt table grows without bound by design.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| `git diff --check` | Passed | Exit 0 |
| `make ci` | Passed | Exit 0, all nine checks |
| `make schema` if domain schemas change | Met | Run, byte identical, nothing to commit |
| Setup from a clean temporary database | Passed | `make db-setup` on a deleted file, plus tests in `tmp_path` |
| 100 percent backend coverage | Passed | `Total coverage: 100.00%` |
| Strict typing, formatting, linting | Passed | mypy, ruff format, ruff check all clean |
| Valid import of each CSV type | Passed | Three tests, and `make import-fixtures` |
| Exact duplicate is a no-op | Passed | `DUPLICATE_NO_OP`, no new facts, receipt written |
| Conflict is auditable and writes nothing | Passed | Store byte identical before and after |
| Malformed file is atomic | Passed | Four failure shapes, zero facts each |
| Index works with `verify_decision` | Passed | `RESOLVED` from a stored fact |
| Restart preserves facts and audit | Passed | Engine disposed and reopened |
| Out-of-order input is not reordered | Passed | Row order preserved, occurred-at order differs |
