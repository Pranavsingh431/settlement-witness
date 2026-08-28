# Phase 9.2: Make line-level abstention mean an actual abstention

- Date: 2026-08-27
- Exit gate: passed. See "Exit gate status".
- Domain contract 5.0.0, parser 3.0.0, baseline 1.0.0, all unchanged
- Shadow link harness 4.0.0 to 5.0.0; shadow corpus 1.0.0, unchanged

## No hosted model was called

No API key, no SDK, no network access, no model output. Every provider is a
deterministic fixture.

## The defect, reproduced first

```text
=== DEFECT 1: an unaskable corpus reports a full abstention ===
  provider called          : 0 times
  unaskable_line_count     : 2
  page_count               : 0
  abstained_line_rate      : 1.000

=== DEFECT 2: malformed output everywhere reports a full abstention ===
  invalid_page_rate        : 1.000
  abstention_page_rate     : 0.000
  abstained_line_rate      : 1.000
```

`abstained_line_rate` counted any line where nothing was selected. So a corpus
the provider was never called on reported a full abstention, and a provider
whose every page was refused reported one too. The second output is a report
saying, of one run, that no page abstained and that every line did.

This is the same mistake Phase 9.1 removed from `safe_abstention_recall`, left
in the field beside it. Phase 9.1 fixed the metric it was looking at and did not
check the neighbouring one for the defect it had just written a whole phase
about.

## The fix

`abstained_line_rate` is removed. It is not renamed onto the same meaning.

**`fully_abstained_askable_line_rate`**

| | |
| --- | --- |
| Numerator | Askable lines where every page returned a valid `ABSTAIN` |
| Denominator | Askable lines only |
| Null | When there are no askable lines |
| Never counted | Malformed, failed, rejected, partially abstained, any selection, or unaskable |

**`no_selection_line_rate`** is kept for the information the old field was
actually carrying, under a name that says what it counts: askable lines that
produced no record, for any reason. It is explicitly not an abstention measure,
and its docstring says so. It exists because reading recall needs to know which
lines contributed no links; which of declining, failing or being refused
happened is what the page rates and the abstention rate are for.

Both use the same denominator, so neither can be inflated by lines nobody was
asked about. `unaskable_line_count` reports those separately.

Both abstention measures now read one property, `LineOutcome.fully_abstained`,
so the line rate and the expected-abstention outcome cannot drift into
disagreeing. A test asserts they agree across five provider behaviours.

## Every required control

| Case | fully abstained | no selection |
| --- | --- | --- |
| Valid `ABSTAIN` on every page | 1.000 | 1.000 |
| Malformed everywhere | **0.000** | 1.000 |
| Provider failure, all three kinds | **0.000** | 1.000 |
| One abstained page plus one malformed | **0.000** | 1.000 |
| Any selected record | 0.000 | 0.000 |
| All-unaskable corpus | **null** (0/0) | null (0/0) |
| One abstained askable line plus one unaskable | 1.000 (1/1) | 1.000 (1/1) |

The second row is the pair working: no records were produced and nothing was
declined, and a single number would have to be wrong about one of those.

The last row is built from line outcomes directly rather than from a snapshot.
The candidate universe is every payment event and payout in the snapshot, so
either every line has candidates or none does, and no snapshot produces that
mixture. The arithmetic is still worth pinning, because it is what stops an
unaskable line diluting the rate.

## The corpus under the corrected harness

| Provider | fully abstained | no selection | abstention pages | invalid pages |
| --- | --- | --- | --- | --- |
| Reads visible refs | 0.333 | 0.333 | 0.583 | 0.000 |
| Malformed everywhere | 0.000 | 1.000 | 0.000 | 1.000 |

The matcher declines two of six lines and produces no records for the same two,
so both line rates read 0.333 and agree, which is what should happen when a
provider only ever declines deliberately. It abstains on 58.3% of pages because
the two declined lines span four pages each.

Every other figure in the Phase 9.1 report is unchanged. This phase removed a
field and added two; it did not touch linking, safe abstention, paging,
identity or the corpus.

## Versioning

`SHADOW_HARNESS_VERSION` is `5.0.0`. A report field was removed and two added,
so a 4.0.0 report cannot be read against one from here, and its
`abstained_line_rate` was computed under a rule that counted failure and silence
as declining.

The corpus is unchanged at 1.0.0. No fact and no scenario moved.

## Changed files

| File | Change |
| --- | --- |
| `backend/app/ai/evaluation.py` | `fully_abstained`, the two replacement rates, harness 5.0.0 |
| `backend/tests/ai/test_line_abstention.py` | New. 22 tests |
| `docs/phase-reports/phase-9.md` | The field's description marked as wrong |
| `docs/phase-reports/phase-9-1.md` | Correction note and a marked exit-gate row |

Nothing else was touched. `app/ai/candidates.py`, `corpus.py`, `presentation.py`,
`proposals.py`, `provider.py` and `validation.py` are unchanged, as is everything
under `app/domain/`, `app/reconciliation/`, `app/ingestion/`, `app/storage/` and
`app/api/`.

## Commands run

| Command | Exit | Observed |
| --- | --- | --- |
| `git diff --check` | 0 | No whitespace errors |
| `make ci` | 0 | All nine core checks passed |
| `uv run pytest` | 0 | `1335 passed`, `Total coverage: 100.00%` |
| `uv run mypy` | 0 | `Success: no issues found in 117 source files` |
| `pnpm run test` | 0 | `184 passed`, frontend untouched |
| `make schema` | 0 | Byte identical |
| Migration and adoption suite | 0 | 95 passed |
| `make verify-containers` | 0 | Including the proxy checks |
| Baseline diffed against Phase 9.1 | 0 | Byte identical |
| Two corpus builds | 0 | Byte identical |

## Tests

1335 backend, up from 1313. 333 in `tests/ai/`, up from 311. 22 added, all in
`tests/ai/test_line_abstention.py`, which opens with both reproductions kept as
tests.

| Class | Covers |
| --- | --- |
| `TestOnlyAnActualAbstentionCounts` | Seven behaviours, including partial and every failure kind |
| `TestUnaskableLinesCannotInflateIt` | Null on an all-unaskable corpus; the mixed denominator |
| `TestNoSelectionIsNamedForWhatItCounts` | Where the two rates agree and where they differ |
| `TestItAgreesWithTheSafeAbstentionOutcome` | The two abstention measures cannot drift |

## Limitations

1. **Three phases have now shipped an abstention metric that counted not
   answering as declining.** The durable change is that both measures read one
   property with the rule written in one place, and a test asserts they agree.
   That is a smaller surface to get wrong than three separate conditions.
2. **`no_selection_line_rate` combines declining, failing and being refused.**
   That is what it is for, and it is the number most easily misread. Its name
   and its docstring both say it is not an abstention measure, which is the most
   the field itself can do.
3. **The mixed askable and unaskable case cannot arise from a snapshot.** It is
   tested at the report level. If the inclusion policy ever narrows, that
   becomes a real case and the test already covers the arithmetic.
4. **Still no model.** Every number is a fixture's.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| Both defects reproduced before any change | Passed | Output quoted above, both kept as tests |
| `abstained_line_rate` removed, not renamed | Passed | Asserted absent from the report fields |
| `fully_abstained_askable_line_rate` added with the stated definition | Passed | Numerator, denominator and null case all tested |
| Malformed does not count | Passed | 0.000 beside an invalid page rate of 1.000 |
| Provider failure does not count | Passed | All three failure kinds |
| Partial abstention does not count | Passed | One declined page, one refused |
| Any selection does not count | Passed | Two selecting behaviours |
| Unaskable lines excluded from the denominator | Passed | Null on an all-unaskable corpus |
| Mixed askable and unaskable gives 1/1 | Passed | Built from line outcomes |
| `no_selection_line_rate` exposed with an exact definition | Passed | And documented as not an abstention measure |
| Harness version bumped | Passed | 5.0.0, with the reason in the constant |
| Phase 9.1 corrected, not rewritten | Passed | Correction section and a marked row |
| Corpus version unchanged | Passed | 1.0.0; no fact or scenario moved |
| Baseline byte identical | Passed | Diffed against Phase 9.1 |
| Isolation guarantees intact | Passed | Fifteen behaviours against a real database |
| CI, typing, coverage, schema, migrations, frontend, containers | Passed | All exit 0 |
