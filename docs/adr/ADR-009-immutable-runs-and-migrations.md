# ADR-009: Immutable reconciliation runs, idempotent run keys and real migrations

- Status: Accepted
- Date: 2026-08-26
- Supersedes: none
- Superseded by: none
- Related: [ADR-004](ADR-004-append-only-import-and-atomicity.md),
  [ADR-005](ADR-005-exact-reference-matching-and-snapshot-payouts.md)

## Context

Phase 3 computed reconciliation results and returned them. Phase 5 has to store
them and serve them, which forces three decisions that cannot be quietly undone
once runs exist and something has read them.

ADR-004 also left an explicit debt: setup was `create_all`, and it said the
first schema change that had to preserve existing rows would need a migration
tool and a new ADR. This is that change.

## Decision

### 1. Migrations replace `create_all`

Alembic, with the engine passed in rather than a URL read from the ini file.
`create_schema` now runs the migrations, so every database this application
creates or opens is stamped with a revision.

The stamp is the point. A schema built by `create_all` has no revision, so the
next change would have nothing to migrate from and an existing database could
only be recreated, losing every fact and receipt in it. Adding the tool at the
moment of the first data-preserving change, rather than earlier, means the first
migration describes a schema that actually shipped.

The initial revision recreates the Phase 2 schema exactly. That is necessary for
bringing an old database forward but not sufficient, because a database built by
the old `create_all` path carries no revision stamp at all, and the migrations
would try to create tables it already has. Such a database is adopted, on these
terms:

- **Empty:** migrate from zero, as any new database does.
- **Recognisably Phase 2:** stamp it at the initial revision, then migrate
  forward normally. Nothing is recreated and no existing row is touched.
- **Anything else:** refuse, with one error naming what differed, and change
  nothing.

Recognising it means inspecting the live SQLite schema rather than trusting the
table names: both Phase 2 tables present, each with exactly its expected columns,
its primary key and its indexes, and all four original append-only triggers in
place. The description lives in one module, `app/storage/legacy.py`, so startup
and tests cannot come to different conclusions about the same database.

The refusal is the part worth defending. Stamping a database because two tables
happen to share a name would tell Alembic that revisions it never ran are
already applied, and the next upgrade would then build on a schema that is not
what it believes it to be. That failure would surface much later as wrong reads
rather than as a clear stop, so an unexplained difference is a refusal and never
a repair. Missing triggers are refused for a second reason: a database whose
append-only protection was removed may already have had history rewritten, and
adopting it would vouch for evidence this system cannot vouch for.

Alembic's own `alembic_version` table is ignored when deciding, because it can
exist holding no row after an interrupted first migration, and a database in
that state is still unstamped.

Not passing a URL through the ini file is deliberate. A stray connection string
would let a migration run against a database the caller did not name, and in
tests that would mean quietly migrating a developer's own file.

### 2. A run is immutable, and a changed snapshot is a new run

`reconciliation_runs` and `reconciliation_decisions` are append-only, with the
same UPDATE and DELETE triggers as the fact and receipt tables. New facts, or a
new rule version, produce a new run beside the old one.

A conclusion that can be revised in place is not an audit trail. The question
this system exists to answer is what was concluded, on what evidence, at what
point, and an editable row cannot answer it. Keeping the old run also means a
decision that cited a fact can still be replayed against that fact, because
neither has moved.

The complete decision is stored as canonical JSON alongside the columns that are
queried. Storing only the columns would mean replay worked from a reconstruction
rather than from what was decided. A test requires every stored decision to
round-trip through the domain model and to equal what the baseline produces live
for the same snapshot.

### 3. The canonical run key is the snapshot plus every rule version

`run_key = sha256(snapshot_fingerprint, baseline_version, domain_schema_version,
parser_version)`, and it is unique in the database.

The fingerprint alone is not enough. The same facts reconciled under a newer
baseline, contract or parser can reach different conclusions, and recording that
as the same run would let one answer overwrite another. Every version that can
change the outcome is in the key.

Re-running is therefore idempotent: the second attempt finds the first rather
than writing a duplicate. That is not an optimisation. Two rows describing one
conclusion would make the history ambiguous about how many times something was
decided, which is exactly the question an audit trail is asked.

The API reports this honestly, 201 for a run it created and 200 for one it
found. A caller retrying after a timeout needs to know which happened.

### 4. There is no endpoint that changes a decision

Every reconciliation route is a `GET`, apart from the one that creates a run. A
test asserts no other verb exists.

Human override is a real need, and it is deferred rather than approximated. A
resolve endpoint would make a stored conclusion editable, which contradicts
decision 2 and the whole contract behind it. Doing it properly means a separate
record of who overrode what and why, layered over the immutable decision rather
than replacing it, and that has not been designed.

### 5. Responses expose citations, not payloads

A response carries evidence references, their verification outcomes, and the
invariant certificate. It does not carry the canonical payloads behind them.

A citation names a record and its payload hash, which is what makes a conclusion
checkable by anyone holding the same facts. The payload itself is merchant data,
and an endpoint that exists to explain a conclusion has no reason to serve it.
The internal run key is not published either, because it is an idempotency
identity and publishing it would invite callers to depend on how it is computed.

### 6. No authentication, said plainly

This is a local and demonstration backend. It assumes one merchant's data and
one trusted operator, and the documentation says so in the module docstring, the
OpenAPI description and `docs/api.md`.

Adding a token check without a tenancy model would look like security and
provide none. Saying there is none is more useful than implying there is.

## Consequences

Good:

- An existing database can be upgraded without losing a row, which is what the
  append-only guarantee was always for.
- A stored run is replayable: its decisions round-trip through the contract and
  its citations still resolve against the stored facts.
- Re-running is free and safe, so a caller can retry without thinking.
- The history of what was concluded, and under which rules, is complete.

Costs and risks:

- Alembic is a real dependency with its own configuration, and a migration that
  is wrong is harder to recover from than a schema that is wrong.
- SQLite cannot alter most things in place, so a future column change will need
  the table-rebuild path. `render_as_batch` is enabled for that, and it is
  untested because nothing has needed it yet.
- Runs accumulate without bound. Each new snapshot writes a run and a row per
  settlement line, and nothing prunes them, because pruning an audit trail needs
  a retention policy that has not been decided.
- The run key includes rule versions, so bumping any of them makes every
  previous run un-matchable and the next reconciliation writes a fresh one. That
  is correct and it means a version bump has a storage cost.
- No authentication means this cannot be deployed anywhere real as it stands.

## Alternatives considered

**Keep `create_all` and recreate the database on change.** Rejected. It throws
away the facts and the audit trail, which are the two things the system promises
to keep.

**Key a run on the snapshot fingerprint alone.** Rejected. The same facts under
a newer baseline can reach a different conclusion, and treating those as one run
would overwrite one answer with another.

**Update a run in place when facts change.** Rejected outright. It contradicts
ADR-004 and makes the history unanswerable.

**Store only the queried columns and rebuild the decision on read.** Rejected.
Replay would then work from a reconstruction rather than from what was decided,
and any change to the reconstruction would silently rewrite history.

**Add a `PATCH` endpoint for human override now.** Rejected. See decision 4. The
part that is hard is the record of who overrode what and why, not the endpoint.

**Return 201 for every create.** Rejected. A caller retrying after a timeout
cannot then tell whether it created something, which is the one thing it needs
to know.

**Serve canonical payloads alongside citations, for convenience.** Rejected. The
hash is what makes a citation checkable; the payload is merchant data that the
explanation does not need.
