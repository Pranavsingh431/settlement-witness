# Phase 2.1: Close ingestion and storage integrity gaps

- Date: 2026-08-25
- Exit gate: passed. See "Exit gate status".
- Parser version: 1.0.0 to 2.0.0
- Domain contract version: 2.0.0, unchanged

## Scope

Three confirmed defects in the Phase 2 ingestion and storage path. No new
capability. No matching, decisions, AI, API endpoints, frontend, migrations or
benchmark data.

Each defect was reproduced against the Phase 2 code before being fixed.

## Defect 1: the parser trimmed input silently

`docs/ingestion-contract.md` said headers must match exactly and that ambiguous
input is refused rather than guessed. The parser did neither: it stripped every
header cell and every field value.

Measured before the fix:

```text
headers with padding    : ACCEPTED  <- contradicts the exact-header rule
padded identifier       : ACCEPTED, silently trimmed
padded amount           : ACCEPTED, silently trimmed
padded timestamp        : ACCEPTED, silently trimmed
padded currency         : ACCEPTED, silently trimmed
padded enum             : ACCEPTED, silently trimmed
whitespace-only utr     : became None
```

The last line is the worst of them. A blank column and a space-filled column
meant the same thing, so one of them was a defect nobody would ever see.

### The fix

Headers are compared exactly, including whitespace. Every cell is read as the
document wrote it, and a cell with leading or trailing whitespace is refused
with a new code, `SURROUNDING_WHITESPACE`.

Trimming is a guess about intent. It also hides a real class of defect: a padded
identifier usually means an export template is broken, and silently accepting it
lets one file produce two identities depending on which system read it.

| Input | Before | After |
| --- | --- | --- |
| `pay-1` | accepted | accepted, unchanged |
| `  pay-1  ` | accepted as `pay-1` | `SURROUNDING_WHITESPACE` |
| `acme retail` | accepted | accepted, internal whitespace is a real value |
| ` event_id` as a header | accepted | `UNEXPECTED_COLUMNS` |
| `utr` exactly empty | absent | absent, unchanged |
| `utr` of `"   "` | became absent | `SURROUNDING_WHITESPACE` |
| required column exactly empty | `MISSING_VALUE` | `MISSING_VALUE`, unchanged |
| required column of `"   "` | `MISSING_VALUE` | `SURROUNDING_WHITESPACE` |

`PARSER_VERSION` goes to 2.0.0. Documents that 1.0.0 accepted can be refused by
2.0.0, which is a major step. Facts already stored are unaffected: the change is
to what is accepted, not to how an accepted row is represented. Receipts record
the new version, which was checked through `make import-fixtures`.

All three valid example documents parse unchanged. None relied on trimming, and
a test now asserts that for each of them.

## Defect 2: a database conflict produced a self-contradicting receipt

`_append_facts` rolled back correctly, and the receipt it returned kept the
`ACCEPTED` row outcomes from the preflight examination while zeroing the count.

Measured before the fix:

```text
outcome        : REJECTED_CONFLICT
accepted_count : 0
conflict_count : 2
row outcomes   : ['ACCEPTED', 'ACCEPTED']   <- still claims ACCEPTED
counts agree with rows? False
```

A receipt whose counts contradict its own rows is worse than no receipt, because
it will be believed.

### The fix

After a database level refusal the receipt is rebuilt rather than patched. Every
pending row is re-examined against the rolled-back database and against the rest
of the import:

- rows that genuinely collided become `DUPLICATE_CONFLICT`;
- rows that were valid but not written become `NOT_APPLIED`;
- rows that already had another outcome keep it;
- every count is recomputed from the rewritten rows;
- the failure detail names the colliding source records and is deterministic.

Two causes are identified, because either can reach the constraint. A fact can
collide with something already stored, which the rolled-back database can still
be asked about. Or two rows of one import can collide with each other, which
only the pending set knows about, either on the idempotency identity or on the
source record ID.

Measured after the fix, forcing a conflict past the preflight check:

| Property | Value |
| --- | --- |
| Facts written | 0, store unchanged at 5 |
| `accepted_count` | 0 |
| Row outcomes | `NOT_APPLIED`, `DUPLICATE_CONFLICT` |
| Conflicting row named | `row-three-collides` |
| Counts agree with rows | Yes, asserted field by field |
| Failure detail | Deterministic across two identical runs |

## Defect 3: append-only was not enforced at the database

The repositories have no update or delete method, which stops the application
from rewriting history by mistake. It does nothing about anything else holding a
connection.

Measured before the fix:

```text
UPDATE source_facts    : succeeded, 5 rows rewritten  <- append-only violated
DELETE source_facts    : succeeded, 5 rows removed
DELETE import_receipts : succeeded, 1 receipts removed
```

### The fix

Both tables carry SQLite triggers that abort `UPDATE` and `DELETE`. Created by
ordinary `create_schema` with `IF NOT EXISTS`, so a clean database is protected
without a separate step and setup stays safe to run again.

Measured after the fix:

```text
UPDATE source_facts    : refused -> source_facts is append-only: UPDATE is not permitted
DELETE source_facts    : refused -> source_facts is append-only: DELETE is not permitted
UPDATE import_receipts : refused -> import_receipts is append-only: UPDATE is not permitted
DELETE import_receipts : refused -> import_receipts is append-only: DELETE is not permitted

facts intact   : 5
receipts intact: 1
INSERT still allowed: ACCEPTED (3 rows)
```

The tests issue SQL directly against the tables rather than going through a
repository, so they fail at the database layer and not because a method is
missing. One test reads `sqlite_master` and asserts the triggers present match
the declared `APPEND_ONLY_TABLES` exactly.

ADR-004 carries an amendment recording this, including two consequences: a
trigger raises the same `IntegrityError` a unique constraint does, and a future
migration will have to drop the triggers deliberately, which makes rewriting
history an explicit act rather than an accident.

## Verification results

Host: macOS on arm64, uv 0.12.5, Python 3.12.12, Node 24.19.0, pnpm 10.15.0.

| Command | Exit | Observed |
| --- | --- | --- |
| `git diff --check` | 0 | No whitespace errors |
| `make ci` | 0 | All nine core checks passed |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy` | 0 | `Success: no issues found in 51 source files` |
| `uv run pytest` | 0 | `447 passed`, `Total coverage: 100.00%` |
| `make db-setup` on a deleted database | 0 | Created both tables and four triggers |
| `make import-fixtures` | 0 | Three documents `ACCEPTED`, parser version `2.0.0` |
| `make import-fixtures` again | 0 | Three documents `DUPLICATE_NO_OP`, parser version `2.0.0` |
| `make schema` | 0 | Byte identical, so no domain model was touched |

## Tests

447 total, up from 412. 35 added.

| Area | Added | Covers |
| --- | --- | --- |
| Whitespace refusal | 16 | Every column kind padded, tabs, internal whitespace, padded headers, whitespace-only optional and required columns, and all three valid fixtures still parsing |
| Conflict receipt truthfulness | 9 | Zero facts written, no row still accepted, the colliding row named, the innocent row not applied, counts agreeing field by field, deterministic detail, self-colliding pairs on both identity and record ID, and a non-accepted row keeping its outcome |
| Append-only at the database | 10 | Update and delete refused on both tables, bulk and single row, refusal changes nothing, insert still allowed, protections from ordinary setup, setup run three times, triggers matching the declared list |

## Changed files

| File | Change |
| --- | --- |
| `backend/app/ingestion/errors.py` | Added `SURROUNDING_WHITESPACE` |
| `backend/app/ingestion/schemas.py` | `PARSER_VERSION` to 2.0.0, with the reason |
| `backend/app/ingestion/parsing.py` | Headers compared exactly, cells read as written, whitespace refused |
| `backend/app/ingestion/service.py` | `_conflict_receipt`, `_identify_conflicts`, `_rewrite_row` |
| `backend/app/storage/database.py` | `APPEND_ONLY_TABLES` and the immutability triggers |
| `backend/tests/ingestion/test_parsing.py` | Whitespace and fixture regression tests |
| `backend/tests/ingestion/test_import_service.py` | Conflict receipt tests |
| `backend/tests/storage/test_repository.py` | Direct mutation tests |
| `docs/ingestion-contract.md` | Whitespace rejected not trimmed, trigger enforcement, parser version |
| `docs/adr/ADR-004-append-only-import-and-atomicity.md` | Amendment on database level immutability |
| `docs/phase-reports/phase-2-1.md` | This report |

No CSV schema, lifecycle projection, import interface or Phase 1 verifier
contract changed. No valid fixture behaviour changed.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| `git diff --check` | Passed | Exit 0 |
| `make ci` | Passed | Exit 0, all nine checks |
| `make db-setup` from a fresh database | Passed | Deleted file, recreated with tables and triggers |
| Valid fixtures import, then re-import as no-ops | Passed | Three `ACCEPTED`, then three `DUPLICATE_NO_OP` |
| Direct database mutation tests | Passed | 10 tests, failing at the database layer |
| `make schema` byte identical | Passed | No domain model touched |
| 100 percent backend coverage | Passed | `Total coverage: 100.00%` |
| Parser version bumped and recorded | Passed | 2.0.0 on every receipt, seen through the CLI |
| Domain schema version unchanged | Passed | Still 2.0.0, schemas unchanged |
