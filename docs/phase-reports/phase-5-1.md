# Phase 5.1: Safely adopt genuine pre-migration Phase 2 databases

- Date: 2026-08-26
- Exit gate: passed. See "Exit gate status".
- Domain contract 5.0.0, parser 3.0.0, baseline 1.0.0, harness 2.0.0, all unchanged

## Scope

Storage migration handling only. No domain model, parser, baseline, API
response, schema file or version changed. `make schema` was run and produced a
byte identical result.

## The defect

Phase 5 replaced `create_all` with Alembic and claimed that an existing database
could be brought forward, offering as evidence a test that builds the initial
schema, imports the example documents, upgrades to head, and finds every fact
and receipt unchanged.

That test starts from the wrong place. It builds the older schema with
`upgrade_to(engine, "0001_initial_schema")`, which writes an `alembic_version`
row, so Alembic already knows the initial revision has run and skips it. A
database built by the Phase 2 code has no such row, because Phase 2 called
`create_all` and Alembic did not exist yet. The upgrade therefore tries to
create a table that is already there.

Reproduced against a database built the Phase 2 way, holding the three example
documents:

```text
legacy database built the create_all way
  tables   : ['import_receipts', 'source_facts']
  stamp    : None
  facts    : 10

upgrading it to head, as Phase 5 would on start:
  FAILED -> OperationalError: (sqlite3.OperationalError) table source_facts already exists
```

So the only upgrade path that existed in a deployment was broken, and the test
that was cited as covering it could never have failed on it. The report
described a working upgrade while the real one could not start.

## The fix

### Recognise before acting

`app/storage/legacy.py` holds one description of the Phase 2 schema and one
function that compares a live database against it. Startup and tests both read
that description, so the two cannot reach different conclusions about the same
database.

An unstamped database is classified into exactly one of three states.

| State | Test | Action |
| --- | --- | --- |
| Empty | No user tables | Migrate from zero, as before |
| Phase 2 | Matches the fingerprint exactly | Stamp `0001_initial_schema`, then migrate to head |
| Anything else | Any difference at all | Refuse, change nothing |

The fingerprint is checked against the live SQLite schema, not against table
names:

- both tables present, `source_facts` and `import_receipts`, and no others;
- every expected column on each, with no extra columns;
- the expected primary key on each;
- the three expected indexes on each;
- all four original append-only triggers.

`alembic_version` is ignored when deciding. It can exist holding no row after an
interrupted first migration, and a database in that state is still unstamped, so
counting it as user data would refuse an otherwise empty database.

### Why the refusal is strict

Stamping a database because two tables happen to share a name would tell Alembic
that revisions it never ran are already applied. The next upgrade would then
build on a schema that is not what it believes it to be, and that failure
surfaces later as wrong reads rather than as a clear stop. An unexplained
difference is therefore a refusal, never a repair, and nothing is dropped,
rewritten or fixed automatically.

Missing triggers are refused for a second reason. A database whose append-only
protection was removed may already have had history rewritten, and adopting it
would vouch for evidence this system cannot vouch for.

The error names every difference it found, because the useful question when this
happens is which database it is:

```text
error: this database carries no migration stamp and is not the Phase 2 schema, so it cannot be adopted safely:
  - unexpected tables are present: ['ledger_notes']
  - table 'import_receipts' is missing
  - table 'source_facts' has the wrong columns; missing [...], unexpected ['id']
  - table 'source_facts' has primary key ['id'], expected ['source_record_id']
  - table 'source_facts' is missing indexes: [...]
  - append-only triggers are missing: [...]
Nothing has been changed. Stamping it would tell the migrations that revisions
they never ran are already applied. If this database really should be brought
forward, inspect it and decide deliberately.
```

`make db-setup` reports it as an operator error on stderr and exits 1, following
the convention already used by the import command, because a person running a
setup command needs to read what was wrong with their database rather than a
stack. The server entry point still fails loudly, which is correct for a service
refusing to start.

### What did not change

The engine is still passed in through `config.attributes["connection"]`. No
database URL was added to `alembic.ini` and the Alembic CLI is still not used. A
clean database and an already migrated one behave exactly as they did in Phase
5, and all four tables remain append-only after an upgrade.

## Changed files

| File | Change |
| --- | --- |
| `backend/app/storage/legacy.py` | New. The Phase 2 fingerprint, the three way classification, and the refusal error |
| `backend/app/storage/migrations.py` | `adopt_if_legacy` stamps a recognised legacy database before either upgrade path runs |
| `backend/app/db_setup.py` | Reports a refusal as an operator error rather than a stack |
| `backend/tests/api/test_legacy_adoption.py` | New. 34 tests over the pre-migration path |
| `backend/tests/storage/test_repository.py` | Two tests for the setup command adopting and refusing |
| `backend/tests/api/test_migrations.py` | Docstrings corrected to say these cover stamped databases only |
| `docs/adr/ADR-009-immutable-runs-and-migrations.md` | Decision 1 replaced with the adoption rule and its refusal behaviour |
| `docs/phase-reports/phase-5.md` | The incorrect claim marked and corrected |

## A regression the tests caught

The first fingerprint counted `alembic_version` as a user table. A test that
compares a `create_all` build against a build of `0001_initial_schema` failed on
it, which surfaced a real behaviour change: an empty database carrying an empty
`alembic_version` table, as an interrupted first migration leaves behind, would
have been refused instead of migrating from zero. That breaks the requirement to
preserve clean-database behaviour exactly. Fixed by ignoring the table, and both
cases are now tested.

## Commands run

| Command | Exit | Observed |
| --- | --- | --- |
| `git diff --check` | 0 | No whitespace errors |
| `make ci` | 0 | All nine core checks passed |
| `uv run ruff format --check .` | 0 | `91 files already formatted` |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy` | 0 | `Success: no issues found in 88 source files` |
| `uv run pytest` | 0 | `796 passed`, `Total coverage: 100.00%` |
| `make schema` | 0 | Byte identical, no domain model touched |
| `make verify-containers` | 0 | Both images build, serve and run unprivileged |
| `python -m app.db_setup` on a real legacy file | 0 | Adopted, four tables afterwards |
| `python -m app.reconcile_cli` on the adopted file | 0 | Reconciled the 10 carried facts |
| `python -m app.db_setup` on an unrecognised file | 1 | Refused on stderr, no `alembic_version` written |

## Tests

796 total, up from 760. 36 added.

| File | Tests | Covers |
| --- | --- | --- |
| `tests/api/test_legacy_adoption.py` | 34 | The pre-migration starting point, adoption, refusal, and the empty case |
| `tests/storage/test_repository.py` | 2 | The setup command adopting a legacy database and refusing an unknown one |

The legacy databases are built the way Phase 2 built one, with `create_all` and
raw trigger DDL, never through `upgrade_to`. One test asserts that this build and
a build of `0001_initial_schema` produce the same columns, primary keys and
indexes, so the fingerprint cannot drift away from the schema that actually
shipped without a test failing.

Every required case is covered explicitly: a truly unstamped Phase 2 database
holding imported facts and receipts and the original triggers; that database
upgraded to head with facts and receipts compared field for field, old triggers
still present, new run and decision tables and their triggers present, and the
revision at head; importing still working afterwards; reconciling afterwards
reaching the same run key, snapshot fingerprint and counts as a database
migrated from nothing and loaded with the same documents; an empty database
reaching head; a second setup run on an already-head database changing nothing;
and five malformed shapes each refused without writing `alembic_version`, being
a missing table, partial columns, a missing trigger, an unrelated user table,
and a table rebuilt with the wrong primary key.

## Limitations

1. **Only the Phase 2 shape is adopted.** There is one legacy fingerprint,
   because there was one pre-migration schema. A future schema that needs
   adopting would need its own entry rather than a loosened check.
2. **SQLite only.** The check reads `sqlite_master` and SQLite's own inspection.
   That matches the frozen stack. Another engine would need its own
   implementation.
3. **Column types are not compared in the fingerprint.** Names, primary keys,
   indexes and triggers are. SQLite's type affinity makes a stored type string a
   weak signal, and the test that compares a `create_all` build against the
   migration build does check types, so drift is caught there instead.
4. **A refusal is not a recovery tool.** It reports what differs and stops. There
   is no command that repairs an unrecognised database, deliberately.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| A genuine unstamped Phase 2 database upgrades to head | Passed | Built with `create_all`, not `upgrade_to` |
| Facts and receipts survive field for field | Passed | Every column of all 10 facts and 3 receipts compared |
| Original triggers remain, new tables and triggers appear | Passed | All eight triggers present afterwards |
| Importing and reconciling still work after adoption | Passed | Same run key as a database migrated from nothing |
| Empty database reaches head | Passed | Unchanged from Phase 5 |
| Already-head database unchanged on another setup run | Passed | Stamp and rows identical |
| Malformed shapes fail closed, writing no `alembic_version` | Passed | Five shapes, table set unchanged after each |
| Legacy check centralized, not duplicated | Passed | One module read by startup and tests |
| Engine-passed migration model kept | Passed | No URL in `alembic.ini`, no CLI use |
| `make ci` | Passed | Exit 0, all nine checks |
| `make schema` | Met | Run, byte identical, no domain model changed |
| `make verify-containers` | Passed | Exit 0 |
| 100 percent backend coverage | Passed | `Total coverage: 100.00%` |
| ADR-009 and the Phase 5 report corrected | Passed | Decision 1 replaced, claim marked in place |
