# Phase 7.2: Do not treat EXCEPTION as proof of complete evidence

- Date: 2026-08-26
- Exit gate: passed. See "Exit gate status".
- Domain contract 5.0.0, parser 3.0.0, baseline 1.0.0, harness 2.0.0, all unchanged

## Scope

Descriptions, and the tests that hold them. `derive_status`, the exception
precedence, persisted decisions, the schema shape, baseline behaviour and every
version are untouched. `make schema` was run and produced a byte identical
result.

## The defect

Phase 7.1 removed the claim that every exception has a failed rule and replaced
it with a claim that every exception has complete evidence:

> The records needed to judge this line were there, and the baseline reports a
> finding instead of resolving it.

That is false. `derive_status` decides on the highest precedence exception code
before it reaches the branch that answers `INSUFFICIENT_EVIDENCE` for a decision
citing nothing, so an ordinary code with no citations behind it derives
`EXCEPTION`:

```text
no evidence at all, one reported finding:
  AMOUNT_MISMATCH        -> EXCEPTION
  PARTIAL_REFUND         -> EXCEPTION
  UNSUPPORTED_STATE      -> EXCEPTION
  MISSING_PAYMENT        -> EXCEPTION

the two codes that do not derive EXCEPTION:
  TIMING_PENDING         -> PENDING
  INSUFFICIENT_EVIDENCE  -> INSUFFICIENT_EVIDENCE

no evidence and no code, for contrast:
  (nothing)              -> INSUFFICIENT_EVIDENCE
```

It is not a corner of the function either. A whole decision takes that shape:

```text
a full decision with no citations and one code:
  status        : EXCEPTION
  evidence      : 0
  verified count: 0
  exception     : ['AMOUNT_MISMATCH']
  reasons       : ['EVIDENCE_MISSING', 'REQUIRED_INVARIANT_NOT_EVALUATED']
```

Two things make this worse than the Phase 7.1 defect.

**The wording came from the domain source.** `DecisionStatus.EXCEPTION` was
documented as "something is demonstrably wrong, and the records needed to say so
were present". Phase 7.1 quoted that as authority for the interface copy. The
false claim was in the contract's own description of itself, so correcting only
the screen would have left the source to seed it again.

**A test defended it.** Phase 7.1 added
`expect(exception).toHaveTextContent(/the records needed to judge this line were there/i)`.
The suite asserted the overclaim, so it would have failed had anyone written the
truth. A test that pins a false statement is worse than no test.

## The fix

### The domain source

`DecisionStatus.EXCEPTION` now says what the status carries rather than what it
implies about the backing: the backing carries a reported finding or a failed
invariant, and that is not an assertion the records were present. It names the
ordering that makes the difference, notes that `EVIDENCE_MISSING` still appears
in the reason codes so the gap is recorded, and points at the open question.

### The two cards

| Card | Now |
| --- | --- |
| Exception | The baseline reports a finding and does not resolve this line. Its certificate shows the citations and the checks recorded for that finding, including any that are missing. |
| Insufficient evidence | The backing does not support a determinate judgement, so none was made. Not a failure, and not a pass either. |

The distinction is now drawn where the contract draws it. An exception is a
backing carrying a reported finding or a failed invariant. Insufficient evidence
is a backing that does not support a determinate judgement. Neither is stated in
terms of what evidence was present, because the status does not tell a reader
that.

The exception card ends by pointing at the certificate "including any that are
missing", which is the honest place for the variation: a decision with no
citations shows none, and the reader sees it.

## The open question, recorded not patched

Whether a bare exception code with no citations should stay constructible is a
question about the contract, and `docs/domain-contract.md` now carries it under
"An open question about bare exception codes" with the argument on both sides.

It is deliberately not answered here. Changing it would change which status
existing decisions derive, which is a major contract version with a migration
story, not a wording fix, and this phase was scoped to descriptions.

The reason it is written down rather than left implicit: **it has to be decided
before a model is allowed to propose exception codes.** Today every code comes
from deterministic baseline code that cites what it looked at. A component that
can emit a code without citing anything, against a contract that turns a bare
code into an `EXCEPTION`, is a path from a generated assertion to a reported
finding with nothing behind it. That is the shape this system exists to make
impossible, and it should be closed before the component exists.

## Search for equivalent claims

| Surface | Result |
| --- | --- |
| `backend/app/domain/decisions.py` | The origin of the wording. Corrected |
| `frontend/src/routes/DashboardPage.tsx` | The active copy. Corrected |
| `frontend/src/routes/DashboardPage.test.tsx` | Asserted the claim. Replaced with a guard against it |
| `frontend/src/components/DecisionCertificate.tsx` | Speaks about one decision from its own results, and asserts nothing about the status in general |
| `docs/domain-contract.md` | Derivation order stated correctly. The open question added |
| `docs/reconciliation-baseline.md`, `docs/api.md`, `README.md` | No definition of the status |
| Phase reports 7 and 7.1 | Correction notes added, exit gate rows marked, wording left as written |

## Changed files

| File | Change |
| --- | --- |
| `backend/app/domain/decisions.py` | `DecisionStatus.EXCEPTION` documented as what it carries |
| `backend/tests/domain/test_decisions.py` | Eight tests pinning the derivation |
| `frontend/src/routes/DashboardPage.tsx` | Both cards, and a comment recording both wrong directions |
| `frontend/src/routes/DashboardPage.test.tsx` | The evidence guard, a specificity test, and the distinction test rewritten |
| `docs/domain-contract.md` | The open question |
| `docs/phase-reports/phase-7.md`, `phase-7-1.md` | Correction notes and marked exit gate rows |

## Commands run

| Command | Exit | Observed |
| --- | --- | --- |
| `git diff --check` | 0 | No whitespace errors |
| `make ci` | 0 | All nine core checks passed |
| `uv run pytest` | 0 | `998 passed`, `Total coverage: 100.00%` |
| `pnpm run lint` | 0 | Clean at `--max-warnings 0` |
| `pnpm run test` | 0 | `184 passed`, statements 98.5%, branches 92.1%, functions 98.3%, lines 98.8% |
| `make schema` | 0 | Byte identical |
| `make verify-containers` | 0 | Including the proxy checks |
| New guards against five evidence claims | 1 | All five fail, which is the point of them |

## Tests

998 backend, up from 990. 184 frontend, up from 183.

### Domain

`TestExceptionDoesNotImplyCompleteBacking`, eight tests:

| Test | Pins |
| --- | --- |
| A bare code with no citations derives `EXCEPTION` | Five ordinary codes, parametrised |
| The two codes that do not derive it still do not | The precedence map, not the evidence, is what makes them different |
| The code is read before the evidence | The same empty backing goes two ways, decided only by the code |
| Such a decision is constructible and records the gap | The whole shape, and `EVIDENCE_MISSING` in its reasons |

These are what make the descriptions checkable. Any future change to the
precedence now fails a test that says what it is changing.

### Frontend

The new guard was checked against the copy it exists to catch and four other
ways of asserting the same thing, plus vague filler for the specificity test:

```text
evidence=yes rule=-   vague=yes :: The records needed to judge this line were there, and ...
evidence=yes rule=-   vague=yes :: The evidence is there and the baseline reports a finding.
evidence=yes rule=-   vague=yes :: All the records were present, and the baseline will not resolve this line.
evidence=yes rule=-   vague=yes :: Every citation resolved, and the baseline reports a finding.
evidence=yes rule=-   vague=yes :: The evidence was verified, and the baseline reports a finding.
evidence=yes rule=yes vague=yes :: The evidence is there and a rule about it does not hold.
evidence=NO  rule=-   vague=yes :: Something happened here.
```

The last row is the specificity test doing its job: filler avoids every false
claim and is caught for saying nothing.

Writing that table found a real hole. "Every citation resolved" was initially
**not** caught, because the card's `textContent` runs the badge straight into the
copy as `ExceptionEvery citation resolved...`, and a pattern anchored with a
word boundary at the start of the sentence never matched. The guards now read
the explanation paragraph instead of the whole card. A guard that is not
executed against the thing it forbids is a comment.

The Phase 7 certificate test for `PARTIAL_REFUND` with all invariants passing,
and the Phase 7.1 test forbidding the broken-rule claim, are both unchanged and
both still passing.

## Limitations

1. **The contract still permits the shape.** This phase corrected what is said
   about it and did not change it. A decision citing nothing can still be an
   `EXCEPTION`, and the open question is recorded rather than answered.
2. **The guards are on wording.** Five assertive forms are covered and a sixth
   could be written. The specificity test is the other half: the card has to say
   something checkable, so replacing it with vagueness fails too.
3. **Three wrong descriptions in three phases.** Two of them were written while
   fixing the previous one. The durable part of this phase is the domain tests
   and the corrected docstring, because those are what a future description will
   be written from.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| Dashboard exception card asserts no evidence completeness | Passed | Guard checked against five assertive forms |
| The useful distinction kept | Passed | Backing carries a finding, versus backing does not support a judgement |
| Nothing says all required records were present | Passed | Source, copy and tests all corrected |
| `DecisionStatus.EXCEPTION` documentation corrected | Passed | Says what the status carries, names the ordering |
| Active documentation searched | Passed | Table above; two active instances found |
| Correction notes on Phase 7 and 7.1 | Passed | Sections added, exit gate rows marked, wording preserved |
| Domain regression test for a bare code | Passed | Eight tests, five codes parametrised |
| Frontend regression test for the evidence claim | Passed | Reads the explanation paragraph, not the card |
| `PARTIAL_REFUND` with passing invariants test kept | Passed | Unchanged and passing |
| Broken-check wording test kept | Passed | Unchanged and passing |
| The replacement stays specific | Passed | A test fails on filler |
| No change to derivation, precedence, decisions, schema or versions | Passed | One docstring, two strings, and tests |
| Deferred architecture question recorded | Passed | `docs/domain-contract.md`, with why it precedes any AI component |
| `make ci`, schema, container checks | Passed | All exit 0 |
