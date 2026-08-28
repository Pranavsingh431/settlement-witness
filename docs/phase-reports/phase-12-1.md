# Phase 12.1: Truthful bank ingestion provenance, and a race-safe audit

- Date: 2026-08-28
- Exit gate: passed
- Parser 3.0.0 to **3.1.0**. Domain contract 5.0.0, baseline 1.0.0, review 1.0.0, bank finality 1.0.0, bank statement schema 1.0.0, all unchanged
- Reconciliation decision content byte-identical. The benchmark report changed by exactly one field, by design
- `ADR-016` amended; `docs/phase-reports/phase-12.md` annotated, not rewritten

## Two defects, reproduced first

### A. The receipt for a bank statement named a parser that could not read one

```text
outcome                   : ACCEPTED
record type               : BANK_TRANSACTION
parser_version on receipt : 3.0.0
bank layout version       : recorded on a later audit, not on this receipt
```

3.0.0 has no bank transaction layout. A receipt stamped with it attributes
evidence to rules that had no way to produce it, which is exactly the kind of
untrue provenance the rest of this system is built to avoid.

Phase 12 argued for that deliberately: `PARSER_VERSION` is an input to the
reconciliation run key, so bumping it would create a new run for every existing
database for a change no decision can observe. The argument was carefully
reasoned and it optimised the wrong thing. `PARSER_VERSION` is recorded on the
import receipt that creates a fact, and its stated job is that a fact can always
be traced to the rules that produced it. The run-key cost was the smaller thing.

### B. Two concurrent audits of one snapshot were not idempotent

```text
loser raised: IntegrityError
  (sqlite3.IntegrityError) UNIQUE constraint failed: bank_finality_audits.audit_key
audits stored: 1   certificates stored: 2
```

The lookup was the fast path and the unique constraint was the guarantee, and
nothing joined the two. The data stayed correct, which is why this was never a
corruption bug, and the second caller got a database error instead of the audit
the first one recorded. Phase 12 tested idempotency with sequential calls, which
is the case that worked.

## A. Provenance

`PARSER_VERSION` is now **3.1.0**, recorded on every new receipt including bank
statements. Minor rather than major: the change accepts a document that was
previously refused and refuses nothing that was previously accepted, and no rule
applying to an existing record type moved.

`BANK_STATEMENT_SCHEMA_VERSION` stays 1.0.0 and now lives beside the layout it
describes, in `app.ingestion.schemas`, re-exported from `app.banking.finality`
because a certificate carries it. The two are not alternatives:

| Version | Names | Recorded on |
| --- | --- | --- |
| `PARSER_VERSION` | The parsing machinery: header sets, coercions, ID derivation | The **receipt** that created the fact |
| `BANK_STATEMENT_SCHEMA_VERSION` | The bank columns and their rules | The **audit** that used them |

**A future change to the bank columns moves both.**
`test_a_bank_layout_change_moves_both_versions` pins them together against the
committed header row, written out rather than derived, so a layout edited without
deciding on new versions fails rather than shipping evidence attributed to rules
that did not read it. Phase 12 changed the layout and moved neither, and nothing
caught it because nothing was watching.

**Nothing rewrites history.** A receipt written under 3.0.0 still says 3.0.0 and
a recorded run keeps the parser version its key was computed from. A migration
that restamped them would destroy the only record of which parser produced the
facts behind them.

## B. The race

```python
savepoint = self._session.begin_nested()
try:
    recorded = self._repository.append(audit(snapshot), audit_key, now=now)
except IntegrityError:
    savepoint.rollback()
    winner = self._repository.find_by_key(audit_key)
    if winner is None:
        raise
    return winner
savepoint.commit()
return recorded
```

Three properties, in order of how easy each is to get wrong.

**Only the savepoint is rolled back.** An audit row without its certificates
would be a conclusion with no evidence behind it, and rolling back the whole
transaction would take the caller's other work with it.

**The loser is answered with the winner's audit**, `was_created` false, which is
the API's 200 rather than 201. That is the same answer an ordinary duplicate
gets, because it is the same fact.

**An `IntegrityError` that is not a taken audit key is re-raised.** If no winner
is holding the key, the constraint that refused the insert was some other one,
and reporting that as a successful duplicate would turn storage corruption into a
silent success. This is the half that a careless fix gets wrong, so it has its
own tests.

The same shape as the import service's savepoint and the review command's, which
is not a coincidence: it is the third place in this codebase where a check and a
constraint have a window between them.

## Verified behaviour

19 new backend cases.

| Class | Cases | Proves |
| --- | --- | --- |
| `TestTheReceiptSaysWhichRulesReadTheDocument` | 6 | A bank receipt reports 3.1.0; every record type reports the same version; a refusal reports it too; the audit records the matching layout version; a layout change moves both |
| `TestTheParserVersionChangesIdentityAndNotContent` | 6 | Different run keys across the bump; identical decision bodies; no decision carries a parser version; a historical 3.0.0 receipt and run read back unchanged |
| `TestConcurrentAuditsAreIdempotent` | 7 | The loser gets the winner's audit with `was_created` false; exactly one audit; a complete, undoubled certificate set; the winner untouched; an unrelated failure still raised and writing nothing |

The race is forced by making `find_by_key` miss once, which is exactly a lost
race: this caller looked before the winner committed, and its insert then meets a
key that is already taken.

### The tests fail against the old behaviour

Each defect was put back on its own, with everything else left in place:

```text
parser version returned to 3.0.0        6 failed
savepoint removed                       5 failed, all IntegrityError
unrelated failure masked as duplicate   2 failed
```

## Observed results

### Decision content is byte-identical

```text
reconcile over the example documents: efc16896fdc7bf2cb0649312f07efae3fb4f9931bd7e7b2d5aed3d22c8b9d3dd
snapshot fingerprint                : 7092df18a31c4b9386a3120e3134d6867f8728c1674a367bc73018f887cb84dc
status counts                       : {"EXCEPTION": 2, "INSUFFICIENT_EVIDENCE": 0, "PENDING": 0, "RESOLVED": 1}
```

Identical to Phase 12, and to every phase since Phase 10. No decision carries a
parser version, so nothing in one can move when it changes; a test asserts that
rather than relying on it.

### The benchmark report changed by exactly one field, by design

```text
Phase 12   : e5cff7b46a22c4d5b89ee0361ac1e373a4680f2f4a9ec268575b242cf60c4b5c
Phase 12.1 : 27747836964f0231210b486d9f127a9b8af8462a86e51d52ed477dc47a2b1819
```

**This is the one hash in the project that is not identical to the previous
phase, and it should not be.** The report embeds the parser version that produced
the facts it grades, and that version genuinely changed. Every measured value is
unchanged:

```text
scenario_count       : 59
decision_accuracy    : 1.0 (59/59)
exception_recall     : 1.0 (33/33)
false_resolution_rate: 0.0 (0/27)
harness 2.0.0, baseline 1.0.0, domain 5.0.0   parser 3.0.0 -> 3.1.0
```

A run key computed under 3.1.0 differs from one under 3.0.0 for the same facts.
That is correct provenance, not unwanted duplication: reconciling the same
snapshot under a different parser records a second run beside the first rather
than pretending the two were the same conclusion.

### The race, after the fix

```text
winner : 2efda798f1f3  created=True
loser  : 2efda798f1f3  created=False
same audit: True
audits stored: 1   certificates stored: 2
```

## Changed files

| File | Change |
| --- | --- |
| `backend/app/ingestion/schemas.py` | `PARSER_VERSION` 3.1.0; `BANK_STATEMENT_SCHEMA_VERSION` moved here beside its layout |
| `backend/app/banking/finality.py` | Re-exports the layout version rather than owning it |
| `backend/app/banking/audits.py` | Savepoint, re-read on a lost race, re-raise otherwise |
| `backend/tests/banking/test_ingestion.py` | `TestTheReceiptSaysWhichRulesReadTheDocument` |
| `backend/tests/banking/test_audits.py` | Two new classes |
| `backend/tests/ingestion/test_import_service.py` | The pinned version assertion |
| `frontend/src/test/fixtures.ts` and three test files | Fixtures say 3.1.0, because they are copied from real responses |
| `docs/adr/ADR-016-...md` | Phase 12.1 amendment |
| `docs/phase-reports/phase-12.md` | "Corrected later"; original text left as written |
| `docs/ingestion-contract.md`, `docs/api.md` | The corrected version and the rule tying the two together |

No migration, no schema change, no storage change, no matching change, no
baseline change.

## Commands run

```text
uv run ruff format --check .                    148 files already formatted
uv run ruff check .                             All checks passed
uv run mypy                                     no issues in 143 source files
uv run pytest                                   1858 passed, 100.00% coverage
pnpm run format:check                           Prettier clean
pnpm run lint                                   eslint, 0 warnings
pnpm run typecheck                              tsc clean
pnpm run test                                   305 passed, 98.94% statements
pnpm run build                                  production bundle built
make schema                                     no diff
make benchmark-evaluate                         one field changed, every metric identical
./scripts/verify-containers.sh                  passed, both non-root
```

Migration and legacy-adoption suites run inside `uv run pytest`, unchanged and
passing: this phase adds no revision, and the legacy fingerprint compares the
Phase 2 schema, which no version constant is part of.

## Limitations

**A database holding runs recorded under 3.0.0 will record a second run for the
same facts on the next reconciliation.** That is the intended consequence and it
is worth stating plainly: the run count goes up by one per distinct snapshot, and
both runs stay readable with the parser version each was computed under. Nothing
is rewritten and no decision differs between them.

**The race test forces the window rather than opening a real one.** SQLite
serialises writers, so producing a genuine interleaving in a single process is
not possible; making the lookup miss reproduces exactly the state a lost race
leaves behind. The guarantee that matters is enforced by the unique constraint
either way, and what changed is how the loser is answered.

**Everything Phase 12 listed still stands**, unchanged: exact-reference matching
cannot verify a payout whose provider and bank records share no reference, a
statement export with no references cannot be imported, a verified credit is not
proof the merchant kept the money, and the demonstration numbers are fixtures
rather than a measurement.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| Both defects reproduced first | Passed | Output quoted above, before any fix |
| `PARSER_VERSION` 3.0.0 to 3.1.0, minor not major | Passed | Accepts more, refuses nothing previously accepted |
| 3.1.0 recorded on every new receipt including bank statements | Passed | 4 cases, one per record type, plus a refused document |
| `BANK_STATEMENT_SCHEMA_VERSION` kept at 1.0.0 as the layout contract | Passed | Moved beside its layout, recorded on every audit |
| The rule that a future layout change moves both, documented and tested | Passed | `test_a_bank_layout_change_moves_both_versions`, pinned against the header row |
| No historical receipt or run rewritten | Passed | A stored 3.0.0 receipt and run read back unchanged |
| A new run key under 3.1.0, treated as correct provenance | Passed | Different keys asserted; the consequence stated in "Limitations" |
| Phase 12's claim updated rather than its rationale preserved | Passed | "Corrected later" in phase-12.md, an amendment in ADR-016, and the ingestion contract rewritten |
| Decision content byte-identical across the bump | Passed | Canonical JSON comparison, plus no decision carrying a parser version |
| One audit row on a race; winner 201, loser 200 with the same audit | Passed | `TestConcurrentAuditsAreIdempotent`, 5 cases |
| No partial certificate rows | Passed | Complete and undoubled, and the winner's set untouched |
| Savepoint used; only it rolled back | Passed | `begin_nested`, rolled back on the error path only |
| An unrelated `IntegrityError` re-raised, not masked | Passed | 2 cases, both failing against a masking fix |
| Append-only behaviour and atomicity preserved | Passed | Triggers untouched; the existing storage suites pass |
| Frontend unchanged except corrected version provenance | Passed | Fixtures and three assertions say 3.1.0; no component changed |
| Full CI, migrations, schema, frontend, containers, benchmark comparison | Passed | Commands and hashes above |

## Unresolved

Nothing blocking. The open items are the extra run recorded per snapshot on
first reconciliation after this phase, the forced rather than genuinely
concurrent race test, and everything Phase 12 already listed. All are described
above and none is hidden behind a passing row.
