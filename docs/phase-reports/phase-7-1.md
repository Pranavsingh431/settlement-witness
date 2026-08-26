# Phase 7.1: Remove the remaining false definition of EXCEPTION

- Date: 2026-08-26
- Exit gate: passed. See "Exit gate status".
- Domain contract 5.0.0, parser 3.0.0, baseline 1.0.0, harness 2.0.0, all unchanged

## Scope

Interface copy and the tests that hold it. No domain rule, API response, schema,
dependency, stylesheet or component structure changed. `make schema` was run and
produced a byte identical result.

## The defect

Phase 7 found this claim on the decision certificate and fixed it there:

> The evidence resolved, and at least one rule about it did not hold.

It did not fix the same claim on the dashboard, which kept describing the
exception state as:

> The evidence is there and a rule about it does not hold.

That is false, and the demo corpus contains the counterexample. `line-0001` is
an `EXCEPTION` carrying `PARTIAL_REFUND` with every invariant result `PASSED`.
Read from the running application:

```text
!  Exception   line-0001
   Invariant certificate
   Every invariant recorded here held or did not apply.
   ✓ INV-001 · Held    ✓ INV-002 · Held    ✓ INV-003 · Held
   ✓ INV-004 · Held    – INV-009 · Does not apply
   Exceptions raised: PARTIAL_REFUND
```

A failed invariant means an exception. An exception does not mean a failed
invariant. `derive_status` reaches `EXCEPTION` by two routes: an exception code
raised while examining the case, and a failed invariant. The first needs no
failed check at all.

Fixing the detailed view and leaving the summary wrong is arguably worse than
leaving both wrong. A reader who takes the overview at its word has no reason to
open the certificate that contradicts it, and the overview is the screen a judge
sees first.

## The fix

### The exception card

| | |
| --- | --- |
| Was | The evidence is there and a rule about it does not hold. A real finding, reported rather than smoothed away. |
| Now | The records needed to judge this line were there, and the baseline reports a finding instead of resolving it. Its certificate says whether a required check failed or a lifecycle state was reported. |

Three things changed. It no longer asserts a failed check. It says what an
exception actually is, which matches the contract's own words for the status:
"something is demonstrably wrong, and the records needed to say so were
present". And it points at the certificate rather than guessing on its behalf,
because the certificate is where the two routes are distinguishable.

### The insufficient-evidence card

Tightened in the same pass, because it was narrower than the rule.

| | |
| --- | --- |
| Was | The line cites something that is not in the store, so no judgement is possible. |
| Now | The evidence needed to judge this line did not all resolve, so no judgement is possible. |

A missing fact is one route to that status. A required invariant that reached
`INSUFFICIENT_INPUT` is another, and so is a decision citing nothing at all. The
old wording described the first and read as a definition.

The distinction from an exception is now carried by the two cards together: an
exception is a finding made *because* the records were there, and insufficient
evidence is an inability to judge because they were not.

### Markup

The three answers are now a `ul` of `li` rather than nested divs. They are a
list, so they are marked up as one, and it gives the regression test a semantic
handle on each card instead of a class name.

## Search for equivalent wording

Every active surface was searched for the same claim.

| Surface | Result |
| --- | --- |
| `frontend/src/routes/DashboardPage.tsx` | The defect. Fixed |
| `frontend/src/components/DecisionCertificate.tsx` | Already correct, fixed in Phase 7 |
| Other frontend screens and components | No description of what an exception means |
| `README.md` | "A line is resolved only when its citations resolved and its required invariants held" states the RESOLVED condition, which is true in both directions. No claim about exceptions |
| `docs/api.md` | Names the codes and counts, defines neither status |
| `docs/reconciliation-baseline.md` | Lists `line-0001` as `PARTIAL_REFUND` without claiming a check failed |
| `docs/domain-contract.md` | "Any failed invariant means `EXCEPTION`" is the true direction, not its converse |
| Phase reports 0 to 6.1 | Historical, left as written |
| Phase report 7 | Correction note added, exit gate row marked |

## Changed files

| File | Change |
| --- | --- |
| `frontend/src/routes/DashboardPage.tsx` | Truthful exception and insufficient-evidence copy, and a list for the three cards |
| `frontend/src/routes/DashboardPage.test.tsx` | Six tests over the three-state explanation |
| `frontend/src/styles.css` | Bullets and indent removed from the card grid, now that it is a list |
| `docs/phase-reports/phase-7.md` | Correction note, and the exit gate row marked |

## Commands run

| Command | Exit | Observed |
| --- | --- | --- |
| `git diff --check` | 0 | No whitespace errors |
| `pnpm run lint` | 0 | Clean at `--max-warnings 0` |
| `pnpm run typecheck` | 0 | Clean |
| `pnpm run test` | 0 | `183 passed`, statements 98.5%, branches 92.1%, functions 98.3%, lines 98.8% |
| `make ci` | 0 | All nine core checks passed |
| `make schema` | 0 | Byte identical |
| `make verify-containers` | 0 | Including the proxy checks |
| New tests against the Phase 7 copy | 1 | Three fail, which is the point of them |

## Tests

183 frontend tests, up from 177. Six added, all on the three-state explanation.

| Test | Holds |
| --- | --- |
| Describes all three, and only three | The set does not grow or shrink silently |
| A resolved line needs both its citations and its invariants | The one state that is a conjunction stays one |
| Does not claim that every exception has a broken rule | The regression |
| An exception is a reported non-resolution, and points at the certificate | The replacement says something, not just less |
| Keeps an exception distinct from insufficient evidence | The two are not collapsed while fixing one |
| Insufficient evidence is not called a failure or a pass | It stays a third answer |

The regression test was checked against the copy it exists to catch, and against
three other ways of asserting the same thing:

```text
caught=yes :: The evidence is there and a rule about it does not hold.
caught=yes :: The evidence resolved and at least one required invariant did not hold.
caught=yes :: This line is an exception because a check failed.
caught=yes :: The records were there and one invariant failed.
```

It deliberately does not ban the words. "Its certificate says whether a required
check failed" mentions a failed check and asserts nothing, and is true of every
exception. What the test forbids is asserting the failure. An earlier version of
the pattern banned the mention as well and failed against the corrected copy,
which is how that distinction got drawn.

The Phase 7 certificate test for `PARTIAL_REFUND` with all invariants passing is
unchanged and still passing.

## Limitations

1. **The guard is on wording, and wording has more shapes than a regex.** Four
   assertive forms are covered and a fifth could be written. The positive tests
   are the other half of the protection: the card has to say what an exception
   is and point at the certificate, so replacing it with something vague fails
   too.
2. **`PENDING` is still not explained on the dashboard.** It is a real status
   and the demo corpus produces none, so the overview describes the three a
   reader will see. The audit screen filters by all four.
3. **No new coverage of the copy in other languages or themes**, because there
   are none.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| Dashboard exception copy replaced with truthful language | Passed | Both routes to the status named, neither asserted |
| Nothing says or implies every exception has a broken invariant | Passed | Regression test, checked against four assertive variants |
| Exception and insufficient evidence stay distinct | Passed | A dedicated test over both cards |
| All active surfaces searched and corrected | Passed | Table above; one active instance found and fixed |
| Historical phase reports preserved | Passed | Only Phase 7 changed, and only by adding a correction |
| Regression test on the three-state explanation | Passed | Fails against the Phase 7 copy |
| Phase 7 certificate test kept | Passed | Unchanged and passing |
| Phase 7 report carries a correction note | Passed | Section and exit gate row |
| No domain, API, schema, dependency or design-system change | Passed | Copy, one list element, and its list styling |
| Frontend lint, typecheck, tests and coverage | Passed | All exit 0, thresholds met |
| `make ci` and container checks | Passed | Both exit 0 |
