# Phase 5: Immutable reconciliation runs and the backend API

- Date: 2026-08-26
- Exit gate: passed. See "Exit gate status".
- Domain contract 5.0.0, parser 3.0.0, baseline 1.0.0, all unchanged

## Scope

Durable reconciliation runs, real migrations, and a typed HTTP API over both. No
frontend, no AI, no fuzzy matching, no new benchmark templates, and no
authentication system.

## Migrations replace `create_all`

ADR-004 recorded that `create_all` would hold only until the first schema change
that had to preserve existing rows. This is that change, so Alembic was added
and `create_schema` now runs the migrations.

The revision stamp is the point. A schema built by `create_all` carries none, so
the next change would have nothing to migrate from and an existing database
could only be recreated, losing every fact and receipt in it.

| Revision | Contains |
| --- | --- |
| `0001_initial_schema` | The Phase 2 schema exactly: source facts, import receipts, and their four triggers |
| `0002_reconciliation_runs` | Reconciliation runs and decisions, and their four triggers |

The engine is passed in rather than read from a URL in the ini file. A stray
connection string would let a migration run against a database the caller did
not name, which in tests would mean quietly migrating a developer's own file.

Measured: 80 ms to migrate a new database, 2 ms to re-run on an existing one.

### A configuration defect found by warnings-as-errors

Alembic warned that `version_path_separator` is deprecated and that it was
falling back to splitting paths on spaces and colons. The project treats
warnings as errors, so this failed the suite rather than being tolerated for
years. Corrected to `path_separator = os`.

## Persisted runs

Two append-only tables with the same UPDATE and DELETE triggers as the fact and
receipt tables.

`reconciliation_runs` holds the run ID, the canonical run key, the snapshot
fingerprint, every rule version, created-at and as-of, the fact and line counts,
and the summary counts.

`reconciliation_decisions` holds the queried columns and the complete decision
as canonical JSON. Storing only the columns would mean replay worked from a
reconstruction rather than from what was decided.

`as_of` and `created_at` differ on purpose. One is the snapshot time the
decisions describe, from the latest observed fact; the other is when someone
asked. Conflating them would make a re-run look like a new conclusion.

### The canonical run key

```text
run_key = sha256(snapshot_fingerprint, baseline_version,
                 domain_schema_version, parser_version)
```

The fingerprint alone is not enough. The same facts under a newer baseline,
contract or parser can reach different conclusions, and recording that as the
same run would let one answer overwrite another.

Re-running is therefore idempotent. Measured: a second create returns the first
run's identifier, keeps its original `created_at`, and writes no rows. Importing
new accepted facts changes the fingerprint and produces a second run, with the
first left exactly as it was.

## The API

| Route | Method | Purpose |
| --- | --- | --- |
| `/health` | GET | Unchanged from Phase 0 |
| `/v1/reconciliation/runs` | POST | Reconcile and record. 201 new, 200 existing, 409 nothing to reconcile |
| `/v1/reconciliation/runs` | GET | Paginated, newest first |
| `/v1/reconciliation/runs/{run_id}` | GET | Run summary and decisions, filterable |
| `/v1/reconciliation/runs/{run_id}/decisions/{decision_id}` | GET | One decision with its certificate |

Verified against a live server on the example documents:

```text
GET  /health                        200
POST /v1/reconciliation/runs        201  (new)
POST /v1/reconciliation/runs        200  (idempotent)
GET  /v1/reconciliation/runs        200
GET  /v1/reconciliation/runs/{id}   200
GET  .../decisions/{id}             200
GET  ?status=EXCEPTION              200
GET  ?status=NOPE                   422
GET  /v1/reconciliation/runs/nope   404
```

The 201 against 200 distinction matters: a caller retrying after a timeout needs
to know whether it created something.

Filters narrow the decisions and never the summary counts, which always describe
the whole run. `filtered` says which view a caller is looking at, so a narrowed
list cannot be mistaken for the complete one.

### What is not exposed, and why

Responses carry evidence references, their verification outcomes, and the
invariant certificate. They do not carry the canonical payloads behind those
citations: a hash is what makes a conclusion checkable, and the payload is
merchant data an explanation does not need. A test asserts no response body
contains one.

The internal run key is not published. It is an idempotency identity, and
publishing it would invite callers to depend on how it is computed.

**There is no endpoint that changes a decision.** Every reconciliation route is
a GET apart from the create, and a test asserts no other verb exists. Human
override is deferred rather than approximated: the hard part is a record of who
overrode what and why, layered over the immutable decision rather than replacing
it, and that has not been designed.

**There is no authentication and no multi-tenancy.** This is a local and
demonstration backend, and that is said in the module docstring, the OpenAPI
description, `docs/api.md` and ADR-009. Adding a token check without a tenancy
model would look like security and provide none.

## Verification results

Host: macOS on arm64, uv 0.12.5, Python 3.12.12, Node 24.19.0, pnpm 10.15.0.

| Command | Exit | Observed |
| --- | --- | --- |
| `git diff --check` | 0 | No whitespace errors |
| `make ci` | 0 | All nine core checks passed |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy` | 0 | `Success: no issues found in 86 source files` |
| `uv run pytest` | 0 | `760 passed`, `Total coverage: 100.00%` |
| Migration from `0001` with data to head | 0 | 10 facts and 3 receipts unchanged |
| `make db-setup`, `make import-fixtures`, `make api` | 0 | Every endpoint answered as above |
| `make schema` | 0 | Byte identical, no domain model touched |

## Tests

760 total, up from 682. 78 added.

| File | Tests | Covers |
| --- | --- | --- |
| `tests/api/test_endpoints.py` | 37 | Every route, pagination, filters, 404 and 422 paths, determinism, what is not exposed, and agreement with the CLI |
| `tests/api/test_runs.py` | 25 | The run key, persistence, idempotency, round-tripping, replay, atomicity, listing |
| `tests/api/test_migrations.py` | 15 | Upgrading from nothing and from the initial schema with data, and the new tables being append-only |

Every required case is covered explicitly: a clean database through import, run
creation and API read; re-running writing no duplicate rows; new facts producing
a new run; every persisted decision round-tripping through the domain model;
persisted evidence still verifying against stored facts; a failure mid-write
leaving no partial run; direct SQL update and delete refused on both new tables;
migration preserving imported facts and receipts; pagination, filters, unknown
IDs and deterministic ordering; and the CLI output agreeing with the persisted
API output for the same snapshot.

Two defects were found by tests rather than by inspection:

1. Decisions were inserted before the run row they reference, so SQLite rejected
   them on the foreign key. Without a mapper relationship, SQLAlchemy is free to
   batch the decision inserts ahead of the run. Fixed by flushing the run first.
2. `_immutability_triggers` in `database.py` became dead once the migrations
   owned the schema. Coverage found it. Removed rather than left as a second
   copy of the DDL for the two to disagree over.

## Deferred to Phase 6

1. **Human override.** Deliberately absent. It needs a record of who overrode
   what and why, layered over the immutable decision.
2. **Authentication and multi-tenancy.** Absent, and stated as absent.
3. **AI-assisted candidate generation.** Still the thing the harness exists to
   measure.
4. **Frontend.** Still the Phase 0 shell.
5. **Run retention.** Runs accumulate without bound. Pruning an audit trail
   needs a retention policy that has not been decided.
6. **A column-altering migration.** `render_as_batch` is enabled for SQLite's
   table-rebuild path, and it is untested because nothing has needed it yet.
7. **Settlement windows and `MISSING_SETTLEMENT`.** Unchanged.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| `git diff --check` | Passed | Exit 0 |
| Migration upgrade from initial schema to head | Passed | 10 facts and 3 receipts unchanged |
| `make ci` | Passed | Exit 0, all nine checks |
| API integration tests against a temporary database | Passed | 37 endpoint tests, fresh database per test |
| `make schema` if domain models change | Met | Run, byte identical, none changed |
| 100 percent backend coverage | Passed | `Total coverage: 100.00%` |
| Re-run writes no duplicate | Met | Same run ID, row counts unchanged |
| New facts create a new run | Met | New fingerprint, older run untouched |
| Decisions round-trip and replay | Met | Equal to a live reconcile, and re-verified |
| Failed persistence rolls back the run | Met | Zero run and decision rows after a mid-write failure |
| Direct SQL update and delete refused | Met | Both new tables, at the database layer |
| No mutable resolve endpoint | Met | Asserted across the OpenAPI paths |
