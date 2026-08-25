# Phase 1.2: Harden the source-fact index boundary

- Date: 2026-08-24
- Exit gate: passed. See "Exit gate status".
- Domain contract version: 2.0.0, unchanged

## Scope

One fix to how `backend/app/domain/evidence.py` handles an index it is given.
No model, field, enum, constraint, invariant, exception code, precedence rule or
status derivation changed. The Phase 1.1 candidate and verified decision design
is untouched.

## The problem, reproduced first

`verify_reference` looked a fact up with `index.get(reference.source_record_id)`
and then checked only its source system and payload hash. Callers may supply any
`Mapping`, and a mapping key is a label the caller chose. Nothing required that
key to agree with the `source_record_id` the fact declares about itself.

So a mapping could file one fact under another fact's name, and the verifier
would accept it. Measured against the Phase 1.1 code:

```text
citation record id  : cited-record-id
fact's own record id: different-record-id
verification outcome: VERIFIED
decision status     : RESOLVED
```

The fact matched the citation on source system and payload hash. It differed
only in the identity it declared, which was the one thing not checked. The
verifier trusted the container more than the thing inside it.

## The fix

Two changes, one on each side of the lookup.

**Mapping keys are discarded.** `_coerce_index` now rebuilds any supplied
mapping from its values with `build_fact_index`, so every key in the index the
verifier uses is the record ID its fact declares. A caller's labels never reach
the lookup.

This also means the append-only rule that `build_fact_index` enforces now applies
to a mapping's values: a mapping holding two different facts under one declared
record ID is refused, as it already was for a list.

**The lookup checks identity.** `verify_against_index` verifies three things
before a citation passes: the fact's own record ID, its source system, and its
payload hash. A fact whose declared identity disagrees with the citation resolves
to nothing, exactly as if it were absent, because for that citation it is.

The second check is unreachable through the public entry points once the first is
in place. It is kept, and tested directly, because the alternative is a verifier
that would accept a fact on the strength of where it was filed.

`verify_evidence` now builds the index once per call rather than once per
citation, which follows from the split and avoids rebuilding N times.

## No new codes

`FACT_NOT_FOUND` and `EVIDENCE_FACT_NOT_FOUND` already mean what this case
means: no fact declaring that record ID was supplied. The exception taxonomy is
unchanged at thirteen codes, and no reason code was added.

## What did not change

`make schema` was run and produced byte-identical output, which is the mechanical
confirmation that no part of the published contract moved. The contract version
stays at 2.0.0 and `docs/schema/v2/` is untouched.

That is the honest reading of the versioning rules. The schema describes the
shape of the data, and none of it changed. What changed is the verifier's
handling of malformed input, which the schema never described and no stored
decision can depend on.

## Verification results

Host: macOS on arm64, uv 0.12.5, Python 3.12.12, Node 24.19.0, pnpm 10.15.0.

| Command | Exit | Observed |
| --- | --- | --- |
| `git diff --check` | 0 | No whitespace errors |
| `make schema` | 0 | Output byte identical, so no schema file changed |
| `make ci` | 0 | All nine core checks passed |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy` | 0 | `Success: no issues found in 30 source files` |
| `uv run pytest` | 0 | `283 passed`, `Total coverage: 100.00%` |

Coverage stayed at 100 percent. The backend went from 726 statements to 727.

### Behaviour, measured after the fix

| Input | `verify_reference` | `verify_decision` |
| --- | --- | --- |
| Key `cited-record-id` holding a fact declaring `different-record-id` | `FACT_NOT_FOUND` | `INSUFFICIENT_EVIDENCE` |
| The same, passed straight to `verify_against_index` | `FACT_NOT_FOUND` | n/a |
| A list of facts | `VERIFIED` | `RESOLVED` |
| An index from `build_fact_index` | `VERIFIED` | `RESOLVED` |
| A well formed mapping | `VERIFIED` | `RESOLVED` |
| A fact filed under an unrelated key, cited by its own ID | `VERIFIED` | `RESOLVED` |
| A mapping holding two contradicting facts | Raises, append-only | Raises |

The last two rows are the point of discarding keys rather than rejecting a
mismatch outright. A mislabelled fact is still the fact it is, so a citation that
names it correctly still resolves. A mapping that contradicts itself is refused,
because there is no correct answer to give.

## Tests

283 total, up from 268. 15 added, all in the two files that own this boundary.

| File | Tests | Added |
| --- | --- | --- |
| `tests/domain/test_evidence.py` | 39 | 12: the lying key, the guard reached directly, and the rebuild behaviour |
| `tests/domain/test_decisions.py` | 75 | 3: that a lying mapping cannot resolve, and that the two normal mapping paths still do |

The required cases are covered explicitly. A mapping whose key `cited-record-id`
holds a fact declaring `different-record-id`, with system and hash otherwise
matching, is asserted not verified and asserted unable to produce `RESOLVED`.
Built indexes, plain lists and well formed mappings are asserted unchanged.

Every other test in the suite is unchanged and passing, including the invariant
catalogue tests and the schema drift tests.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| `git diff --check` | Passed | Exit 0 |
| `make ci` | Passed | Exit 0, all nine checks |
| `make schema` only if schemas change | Met | Run, confirmed byte identical, nothing to commit |
| 100 percent backend coverage retained | Passed | `Total coverage: 100.00%` |
| A malformed mapping never yields `VERIFIED` | Passed | Asserted at both entry points |
| A malformed mapping never yields `RESOLVED` | Passed | Yields `INSUFFICIENT_EVIDENCE` |
| Normal indexes and resolutions unchanged | Passed | Four input shapes asserted still resolving |
| Taxonomy not widened | Met | No exception code and no reason code added |
| Verifier stays pure and dependency-free | Met | Reads its arguments only, no new import |
| No major version bump, no schema directory change | Met | 2.0.0 and `docs/schema/v2/` untouched |
