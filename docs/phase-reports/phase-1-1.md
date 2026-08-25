# Phase 1.1: Make verifier authority real

- Date: 2026-08-24
- Exit gate: passed. See "Exit gate status".
- Domain contract version: 1.0.0 to 2.0.0

## Scope

Close two verified gaps between what ADR-002 claimed and what the code enforced.
No new capability. No ingestion, persistence, API, matching, AI, frontend or
database.

## The two gaps, reproduced before the fix

Both were confirmed by running against the phase 1 code, not by reading it.

**Gap 1: evidence was never resolved.** `ReconciliationDecision` checked only
that each `EvidenceRef.source_record_id` appeared in
`linked_source_record_ids`. That compares the decision against itself. A
decision could cite record `rec-1` from `PSP_API` with payload hash `aaa...`,
with no such fact existing anywhere, and be constructed without complaint.

**Gap 2: status was asserted, not derived.** `derive_status` existed and was
correct, and nothing required a decision's `status` to equal it. All four
bypasses below were constructible:

```text
GAP: accepted -> EXCEPTION with only TIMING_PENDING
GAP: accepted -> PENDING with no TIMING_PENDING
GAP: accepted -> INSUFFICIENT_EVIDENCE carrying MALFORMED_RECORD
GAP: accepted -> EXCEPTION while highest code is INSUFFICIENT_EVIDENCE
```

Both gaps share a cause. ADR-002 put the whole verifier in a Pydantic validator,
and a validator can only see the object in front of it. That is enough for
internal coherence and not enough for anything about the world.

## What changed

### The source-fact verification boundary

New module `backend/app/domain/evidence.py`. `EvidenceRef` moved here from
`decisions.py`, joined by the verification types and three pure functions:

| Function | Does |
| --- | --- |
| `build_fact_index(facts)` | Returns a read-only mapping from record ID to fact. Refuses two different facts under one ID, because source facts are append-only |
| `verify_reference(ref, facts)` | Resolves one citation: record ID exists, source system matches, payload hash matches exactly |
| `verify_evidence(evidence, facts)` | The same for every citation, in order |
| `exception_codes_for(evidence, verification)` | The codes that unresolved citations imply |

Facts are supplied as an argument. Nothing is looked up from global state.

### The certificate

`ReconciliationDecision` gained `evidence_verification`, one result per
citation. `RESOLVED` requires every one to have verified, and a citation with no
result at all counts as unresolved. Without that last rule a decision could
avoid every evidence code by never recording the check.

This is what stops the split from being a convention. Building a `RESOLVED`
decision by hand now requires fabricating verification results, which is a
deliberate lie rather than a missing step, and it is visible in the stored
decision.

### Candidate and verified decision

`DecisionCandidate` is the draft a caller builds. It is structurally validated
and has no `status` field and no verification field, because neither is a
caller's to supply. `verify_decision(candidate, facts)` produces a
`ReconciliationDecision`.

### Status is derived

`derive_status` is the authority and gained an `evidence_verification`
argument. `ReconciliationDecision` computes the status from its own backing and
refuses construction when the supplied status disagrees. The per-status
obligation rules from phase 1 were removed: they were a restatement of the
precedence order, and a restatement can disagree with the thing it restates.

### Codes

Four new reason codes. The thirteen exception codes are unchanged.

| Failure | Exception code | Reason code |
| --- | --- | --- |
| No fact with that record ID | `INSUFFICIENT_EVIDENCE` | `EVIDENCE_FACT_NOT_FOUND` |
| Fact exists, different source system | `UNMAPPED_REFERENCE` | `EVIDENCE_SOURCE_SYSTEM_MISMATCH` |
| Fact exists, different payload hash | `UNMAPPED_REFERENCE` | `EVIDENCE_PAYLOAD_HASH_MISMATCH` |
| A resolution whose certificate records a failure | n/a, INV-006 result | `EVIDENCE_NOT_VERIFIED` |

`UNMAPPED_REFERENCE` had its description widened to cover a reference that
resolves to something other than what it claimed. A fourteenth exception code
was considered and rejected: the taxonomy is graded against by an evaluator, and
widening it for a distinction reason codes already carry would make two systems
disagree about the same case.

## Status bypasses that are now impossible

Each is refused at construction with `ValidationError`, and each has a test.

| Attempt | Refused because |
| --- | --- |
| `EXCEPTION` carrying only `TIMING_PENDING` | `status EXCEPTION contradicts the backing, which implies PENDING` |
| `PENDING` carrying no `TIMING_PENDING` | `status PENDING contradicts the backing, which implies RESOLVED` |
| `INSUFFICIENT_EVIDENCE` alongside `MALFORMED_RECORD` | `status INSUFFICIENT_EVIDENCE contradicts the backing, which implies EXCEPTION` |
| `EXCEPTION` while the highest code is `INSUFFICIENT_EVIDENCE` | `status EXCEPTION contradicts the backing, which implies INSUFFICIENT_EVIDENCE` |
| `RESOLVED` with citations that were never checked | An unchecked citation is not a verified one |
| `RESOLVED` whose certificate records a failure | The failure implies a code, and the code implies a status |
| `RESOLVED` citing a record with no matching fact | Verified through `verify_decision`, which yields `INSUFFICIENT_EVIDENCE` |
| `RESOLVED` citing a record with a mismatched hash or system | Yields `EXCEPTION` with `UNMAPPED_REFERENCE` |
| A certificate naming a record the decision never cited | `evidence_verification names records the decision does not cite` |
| Two verification results for one citation | `more than one result for the same record` |
| The same record cited twice | `evidence cites the same source record more than once` |

The four legitimate paths still work. `RESOLVED`, `EXCEPTION`, `PENDING` and
`INSUFFICIENT_EVIDENCE` each construct when their backing supports them, and
each has a test.

## Verification results

Host: macOS on arm64, uv 0.12.5, Python 3.12.12, Node 24.19.0, pnpm 10.15.0.

| Command | Exit | Observed |
| --- | --- | --- |
| `git diff --check` | 0 | No whitespace errors |
| `make schema` | 0 | 15 files written to `docs/schema/v2/`, byte identical to the committed set |
| `make ci` | 0 | All nine core checks passed |
| `uv run ruff format --check .` | 0 | `30 files already formatted` |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy` | 0 | `Success: no issues found in 30 source files` |
| `uv run pytest` | 0 | `268 passed`, `Total coverage: 100.00%` |

Coverage stayed at 100 percent. The backend grew from 627 statements to 726,
with no uncovered line or branch.

### End to end behaviour, measured

Against a real `SourceFact` whose payload hash was computed from its payload:

| Citation | Status | Exception codes | Reason codes |
| --- | --- | --- | --- |
| Matching fact | `RESOLVED` | none | `ALL_REQUIRED_INVARIANTS_PASSED` |
| Nonexistent record ID | `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | `EVIDENCE_FACT_NOT_FOUND` |
| Payload hash mismatch | `EXCEPTION` | `UNMAPPED_REFERENCE` | `EVIDENCE_PAYLOAD_HASH_MISMATCH` |
| Source system mismatch | `EXCEPTION` | `UNMAPPED_REFERENCE` | `EVIDENCE_SOURCE_SYSTEM_MISMATCH` |
| No facts supplied at all | `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | `EVIDENCE_FACT_NOT_FOUND` |

## Tests

268 total, up from 214. 54 added.

| File | Tests | Change |
| --- | --- | --- |
| `tests/domain/test_decisions.py` | 72 | Was 47. Added status derivation, the bypasses, `verify_decision`, `DecisionCandidate` |
| `tests/domain/test_evidence.py` | 27 | New. The verification boundary and the fact index |
| `tests/domain/test_schema_export.py` | 29 | Was 27. Two more models published |
| `tests/domain/test_invariants.py` | 40 | Unchanged, still green |
| `tests/domain/test_money.py` | 36 | Unchanged, still green |
| `tests/domain/test_facts.py` | 25 | Unchanged, still green |
| `tests/domain/test_codes.py` | 15 | Unchanged, still green |
| `tests/domain/test_lifecycle.py` | 14 | Unchanged, still green |

The invariant catalogue tests and the schema drift tests are unchanged and pass.
The drift test did its job during this work: it failed the moment the models
changed, and again when the schema directory moved.

## Version and schema

2.0.0, a major step. Closing the gaps made decisions unconstructible that 1.0.0
accepted, which is breaking by the contract's own rule even though the change
only ever removes wrong answers.

The schema moved to `docs/schema/v2/` and `v1` was removed rather than left in
place. This departs from the guidance in ADR-002, and the reason is recorded in
ADR-003: v1 existed for a single commit, nothing consumed it because there is
still no persistence, and republishing it would mean publishing a contract with
a known correctness gap. The general rule stands for any version that has
actually been used.

## Documentation corrected

`docs/domain-contract.md` previously stated the central rule as "every required
evidence reference exists", which implied a constructor could establish
existence. It now states the rule as citations resolved against real source
facts, and has a section separating structural validation from source-fact
verification.

ADR-002 section 2 carries an amendment note pointing at ADR-003, and both link
to each other. ADR-002 is not rewritten, per the rule in `docs/adr/README.md`.

## Deferred, unchanged from Phase 1

Ingestion, normalisation, matching, persistence, the AI provider interface, the
benchmark harness, API endpoints beyond `/health`, the frontend, and the
settlement window rule. Phase 1.1 added no capability and deferred nothing new.

One item did change shape. Phase 2 must supply `verify_decision` with a complete
fact index. A partial index yields `INSUFFICIENT_EVIDENCE` rather than a wrong
resolution, which is the safe direction, but completeness is now Phase 2 work
with a name.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| `git diff --check` | Passed | Exit 0 |
| `make schema` | Passed | Exit 0, committed set matches |
| `make ci` | Passed | Exit 0, all nine checks |
| 100 percent coverage retained | Passed | `Total coverage: 100.00%` |
| Valid resolution verifies against matching facts | Passed | Test and measured run |
| Nonexistent evidence ID cannot resolve | Passed | Yields `INSUFFICIENT_EVIDENCE` |
| Hash mismatch cannot resolve | Passed | Yields `EXCEPTION` |
| Source system mismatch cannot resolve | Passed | Yields `EXCEPTION` |
| Four status bypasses rejected | Passed | Each raises at construction, each tested |
| Four valid status paths still work | Passed | Each constructs, each tested |
| Catalogue and schema drift tests green | Passed | Unchanged and passing |
