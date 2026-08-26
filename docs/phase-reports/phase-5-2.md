# Phase 5.2: Make legacy adoption prove guarantees, not just object names

- Date: 2026-08-26
- Exit gate: passed. See "Exit gate status".
- Domain contract 5.0.0, parser 3.0.0, baseline 1.0.0, harness 2.0.0, all unchanged

## Scope

The Phase 2 compatibility fingerprint only. No domain model, parser,
reconciliation, API response, schema file or version changed. `make schema` was
run and produced a byte identical result.

## The defect

Phase 5.1 fixed the real migration failure and then described its check as
matching the Phase 2 schema "exactly". It compared object names: which tables
exist, which column names they carry, which index names and trigger names are
present. Names are not guarantees, so the check was weaker than its own
documentation and weaker than the decision it was supporting.

Every database below would have been adopted, stamped, and then read as evidence
for the rest of its life:

| Weakened database | What it silently loses |
| --- | --- |
| `source_facts` without `uq_source_facts_idempotency` | One provider event can be imported twice |
| `import_receipts` without unique `receipt_id` | Two receipts can claim the same identity |
| `ck_source_facts_hash_length` dropped | A payload hash need not be a hash |
| `ck_source_facts_row_number` loosened to `>= 0` | A row number need not point at a row |
| `ck_import_receipts_row_count` dropped | A receipt can report a negative row count |
| `payload_hash` made nullable | Evidence can have no hash at all |
| `ix_source_facts_payload_hash` over a different column | Nothing, but the schema is not the one claimed |
| A trigger of the right name with an empty body | Append-only, the entire premise, is gone |

The last row is the one that matters most. A database with four correctly named
triggers and no append-only behaviour would have passed a check whose stated
purpose was refusing exactly that, because Phase 5.1 read
`SELECT name FROM sqlite_master WHERE type = 'trigger'` and never looked at
`sql`.

## The fix

### Compare the guarantees

`app/storage/legacy.py` now holds a written record of what Phase 2 shipped and
compares a live database against it field by field. For both tables:

| Compared | How |
| --- | --- |
| User tables | Exact set, nothing unexpected |
| Columns | Every column, in order, with declared SQLite type and nullability |
| Primary key | Column list, in order |
| Unique identities | By name where one exists, and by the columns spanned |
| CHECK constraints | Every named check, by the rule it expresses |
| Indexes | By name, indexed column order, and uniqueness |
| Triggers | Full definition, so timing, event, table and abort message all count |

`import_receipts.receipt_id` is compared by its columns rather than its name,
because Phase 2 declared it with `unique=True` on the column instead of as a
named constraint, so SQLite has no name to report for it.

### The exact-schema policy

Every set above is compared for equality, not containment, so an unexpected
index, trigger or check is a refusal.

This was a real choice. An extra index takes no guarantee away, so the check
could have allowed it and been described as a set of required guarantees rather
than an exact schema. Refusing was chosen because it keeps one rule across the
whole check, the same rule that already refuses an unexpected table, and because
an object this code did not put there means somebody changed the schema, which
says nothing about what else they changed. Refusing costs an operator one
deliberate decision. Adopting wrongly costs the audit trail its meaning.

The policy is stated in the module, in ADR-009, and tested directly rather than
left implicit.

### Normalising without weakening

Two databases can express one rule with different formatting. For CHECK
constraints, three things are treated as formatting: case, spacing around
operators and brackets, and one enclosing pair of parentheses that SQLite and
SQLAlchemy disagree about keeping. So `CHECK(LENGTH(payload_hash)=64)` and
`CHECK (length(payload_hash) = 64)` compare equal, and a database written the
first way still adopts. Nothing else is rewritten, so `>= 1` loosened to `>= 0`
is a difference and not a variation. Both directions are tested.

Trigger definitions are compared with their case intact. Lowering case would
also lower the text inside the quoted abort message, which is part of what is
being checked, and every genuine Phase 2 trigger was written by one piece of
code so none of them can differ in case.

The one thing the CHECK normaliser cannot tell apart is two string literals
differing only in internal spacing or case, since it does not parse quoted text.
No Phase 2 check contains a string literal, so nothing reaches that.

### Holding the record to reality

The record is written out rather than reflected from a reference database,
because a fingerprint derived from the current code would agree with whatever
the current code happens to do and would prove nothing. A test therefore builds
a database the way Phase 2 built one and requires the record to equal what that
database reports, field by field, along with a second test requiring the
`create_all` path and the initial migration to produce the same thing. A record
that drifted from the schema it describes fails there rather than in a
deployment.

## Changed files

| File | Change |
| --- | --- |
| `backend/app/storage/legacy.py` | The fingerprint now describes columns, types, nullability, unique identities, checks, index shape and full trigger definitions; adds the two normalisers |
| `backend/tests/api/test_legacy_adoption.py` | Malformed shapes built by editing a real database's own DDL; one shape per guarantee; normalisation and policy tests; the record pinned against reality |
| `docs/adr/ADR-009-immutable-runs-and-migrations.md` | Decision 1 says what the code enforces, and records the exact-schema choice |
| `docs/phase-reports/phase-5-1.md` | The "exactly" overclaim marked and corrected in place |

`app/storage/migrations.py` and `app/db_setup.py` are unchanged. The entry point
is still `plan_adoption`, returning `FRESH` or `ADOPT_LEGACY` or raising
`UnrecognisedSchemaError`, so nothing outside this module had to move.

## How the malformed databases are built

Each one reads a real Phase 2 database's own DDL out of `sqlite_master`, edits a
single clause, drops the table and rebuilds it from the edited statement. That
keeps every variant a visibly minimal change from something genuine rather than
a hand-written table that might differ in ways the test did not intend. The
helper raises if no edit changed anything, so a variant that stopped matching
the DDL fails loudly instead of quietly testing a valid database.

## Commands run

| Command | Exit | Observed |
| --- | --- | --- |
| `git diff --check` | 0 | No whitespace errors |
| `make ci` | 0 | All nine core checks passed |
| `uv run ruff format --check .` | 0 | All files already formatted |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy` | 0 | `Success: no issues found in 88 source files` |
| `uv run pytest` | 0 | `842 passed`, `Total coverage: 100.00%` |
| `make schema` | 0 | Byte identical, no domain model touched |
| `make verify-containers` | 0 | Both images build, serve and run unprivileged |
| `python -m app.db_setup` on a real legacy file | 0 | Adopted, four tables afterwards |
| `python -m app.db_setup` on a no-op-trigger file | 1 | Refused on stderr, no `alembic_version` written |

## Tests

842 total, up from 796. 46 added.

| File | Tests | Covers |
| --- | --- | --- |
| `tests/api/test_legacy_adoption.py` | 80 | The pre-migration starting point, adoption, every refusal shape, normalisation, and the exact-schema policy |

Twenty malformed shapes are each checked twice, once for being refused with the
right sentence and once for being refused without writing anything: a missing
table, partial columns, an unrelated table, loosened nullability, a changed
column type, reordered columns, a wrong primary key, a missing
`uq_source_facts_idempotency`, a missing unique `receipt_id`, a weakened check, a
removed check, an index over the wrong columns, an index made unique, a renamed
index, an extra index, a missing trigger, a no-op trigger body, a changed abort
message, changed trigger timing, and an extra trigger.

All Phase 5.1 behaviour is still covered and still passing: a fresh database, an
empty `alembic_version` table, an already-stamped database, adoption of a real
`create_all`-era database preserving its 10 facts and 3 receipts field for
field, and refusal of an unknown table.

## Limitations

1. **Extra objects are refused, not reported as harmless.** That is the chosen
   policy and it is documented, but an operator who added an index for query
   performance has to drop it before an adoption will proceed.
2. **Column types are compared as declared text.** SQLite stores the declared
   type verbatim, so `VARCHAR(100)` and `TEXT` are different here even though
   they share an affinity. That is stricter than SQLite itself and it is the
   safe direction.
3. **Trigger comparison is static, not behavioural.** The definition is read and
   compared, rather than an UPDATE being attempted. Attempting one would mean
   writing to a database that might be refused a moment later, which this check
   must never do.
4. **Still one legacy fingerprint.** There was one pre-migration schema. A future
   schema needing adoption would need its own record rather than a loosened
   check.
5. **SQLite only**, as in Phase 5.1. The check reads `sqlite_master` and
   SQLite's own inspection, matching the frozen stack.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| Exact table set, columns, types, nullability, primary-key order | Passed | Six column and key shapes refused |
| Unique identities verified, both named and unnamed | Passed | Two shapes refused, one per table |
| Every named CHECK verified by rule, not existence | Passed | Weakened and removed checks both refused |
| Index names, column order and uniqueness verified | Passed | Four index shapes refused |
| Trigger timing, event, table and abort message verified | Passed | No-op body, changed message and changed timing all refused |
| Extra-index and extra-trigger policy chosen and tested | Passed | Refused, stated in the module and ADR-009, tested directly |
| Normalisation tolerates formatting, not meaning | Passed | Reformatted check adopts, loosened check refused |
| A genuine `create_all` database still adopts intact | Passed | 10 facts and 3 receipts compared field for field |
| All Phase 5.1 behaviour preserved | Passed | Fresh, empty version table, already stamped, unknown table |
| Refusals write no `alembic_version` | Passed | Table set unchanged after each of twenty shapes |
| Documentation says what the code enforces | Passed | ADR-009 decision 1 and the Phase 5.1 report corrected |
| `make ci` | Passed | Exit 0, all nine checks |
| `make schema` | Met | Run, byte identical, no domain model changed |
| `make verify-containers` | Passed | Exit 0 |
| 100 percent backend coverage | Passed | `Total coverage: 100.00%` |
