# Phase 9: Paged candidate environments and a non-trivial shadow corpus

- Date: 2026-08-27
- Exit gate: passed. See "Exit gate status".
- Domain contract 5.0.0, parser 3.0.0, baseline 1.0.0, all unchanged
- Shadow link harness 2.0.0 to 3.0.0; shadow corpus 1.0.0, new

## No hosted model was called

There is no API key, no SDK, no network access and no model output anywhere in
this phase. Every provider is a deterministic fixture, and every number below
measures the environment, the harness and the corpus. None of it is a claim
about AI performance, and a report that scored a real provider would need this
phase's corpus and a provider that does not yet exist.

## Problem 1: the environment could not be answered

A selection is bounded at 64 records. A settlement line can link more than that.
Reproduced before anything was changed:

```text
snapshot: 72 facts
candidate environment for line-sl-1 : 71 records
true linked records for line-sl-1   : 71
MAX_SELECTED_RECORDS                : 64

every true link IS offered as a candidate: True

a provider tries to return the complete correct answer:
  REFUSED -> a proposal selected 71 records, more than the 64 allowed

a PERFECT provider evaluated under the current design:
  link_recall       : 0.000 (0/71)
  exact_set_accuracy: 0.000 (0/1)
  invalid_output    : 1.000 (1/1)
```

A provider that knew the exact right answer scored zero on everything, because
the answer could not be expressed. That is a task with no passing answer, not a
model failing one.

### The fix: deterministic pages

The universe is unchanged: every payment event and payout in the snapshot, with
no prefilter. It is cut into consecutive blocks of at most 64, sorted by source
record ID, and each block is asked as its own request.

| Rule | Held by |
| --- | --- |
| Every candidate on exactly one page | Union size equals total page size |
| No page empty | Asserted, and refused by the request model |
| No page over 64 | Asserted, and refused by the request model |
| Union equals the universe exactly | Compared against `candidate_universe` |
| Page ordinals from 1 with no gaps | Asserted |
| Pages do not overlap in record ID order | Consecutive block boundaries compared |
| Independent of insertion order | Facts reversed; pages and report byte identical |

The partition is deliberately dull. Ordering the universe by likely relevance
would perform the linking inside the pager and leave the provider confirming a
shortlist somebody else built, and the evaluation would measure the pager.

A provider may select only from the page in front of it. A record on page two is
as unselectable on page one as one that is not in the snapshot at all, and the
rejection says which page did not offer it.

Page ordinal and environment fingerprint are server-owned, and both are part of
a proposal's derived identity so that two pages of one line cannot collide. Raw
model output still carries an outcome and a list of IDs; a response supplying a
page number or an environment fingerprint is refused as an extra, as one
supplying a provider identity already was.

The same line, after:

```text
universe: 71 candidates -> 2 pages
  page 1/2: 64 candidates, 64 true links
  page 2/2:  7 candidates,  7 true links

PERFECT provider, paged:
  link_recall        : 1.000 (71/71)
  exact_set_accuracy : 1.000 (1/1)
  invalid_page_rate  : 0.000 (0/2)
```

## Problem 2: the task was string equality

The demo fixtures show a provider the same reference strings the baseline
matches on, so selecting correctly means comparing two identical strings. A
report over that measures nothing about selection.

### Three kinds of truth, kept apart

**Canonical facts** are the source facts. The baseline links by exact reference
over these, and that is the oracle. **The presentation** is what a provider
sees: a rendering of a reference, chosen per record. **The expected action** is
what a provider ought to do, which is not always to select.

The oracle is computed from canonical facts and never read back from a
presentation field. Recorded in
[ADR-013](../adr/ADR-013-paged-environments-and-three-kinds-of-truth.md).

### The corpus

Six families, 217 generated facts, 6 lines, 24 pages. Every identifier is a
digest of a fixed seed, so nothing a provider sees names a scenario, an action,
a template or an answer.

| Family | True links | Pages holding them | Expected action | Matcher selected | Exact |
| --- | --- | --- | --- | --- | --- |
| `EXACT_CONTROL` | 2 | 2 | SELECT_EXACTLY | 2 | yes |
| `FORMAT_VARIANT` | 2 | 2 | SELECT_EXACTLY | 2 | yes |
| `NEAR_NEIGHBOR` | 2 | 2 | SELECT_EXACTLY | 2 | yes |
| `AMBIGUOUS_VISIBLE_REFERENCE` | 2 | 2 | ABSTAIN | 0 | no |
| `WITHHELD_VISIBLE_REFERENCE` | 2 | 2 | ABSTAIN | 0 | no |
| `MULTI_PAGE_TARGET` | 151 | 4 | SELECT_EXACTLY | 151 | yes |

`FORMAT_VARIANT` shows the line's reference upper-cased and its records'
underscored, with distractors spaced, so a reader must see through punctuation
and case while selecting everything still fails. `NEAR_NEIGHBOR` adds records
belonging to a payment whose reference is one digit from the line's, shown as
they are: they do not link, and they are the wrong answer that looks most like
the right one.

The two abstaining families are the point of the corpus. `AMBIGUOUS` truncates
the shown reference so two records render identically while only one links;
`WITHHELD` does not show the linked record's reference at all. In both, the
oracle knows the answer and nothing shown does.

## Strict linking against safe abstention

| Provider | recall | answered | precision | exact set | false link | safe abstain | unsafe select |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Reads visible refs | 0.975 | 1.000 | 1.000 | 0.667 | 0.000 | **1.000** | **0.000** |
| Canonical oracle | **1.000** | 1.000 | 1.000 | **1.000** | 0.000 | **0.000** | **1.000** |
| Selects everything | 1.000 | 1.000 | 0.127 | 0.000 | 0.873 | 0.000 | 1.000 |
| Always abstains | 0.000 | null | null | 0.000 | null | 1.000 | 0.000 |

Denominators: 161 true links, 6 lines, 2 lines expecting an abstention, 24 pages.

**The second row is the finding.** A provider that selects the canonical answer
everywhere is perfect on every linking metric and links records on both cases
where nothing shown identified them. The matcher gives up 0.025 of recall and is
safe on both.

Neither is simply better, and that is why the two sets of metrics are reported
side by side and never averaged. Which is preferable is a judgement about the
cost of a wrong link against the cost of a missing one, and that belongs to
whoever reads the report rather than to an average computed before they see it.

Selecting everything still scores perfect recall and is still caught by
precision at 0.127 and a false-link rate of 0.873. Abstaining everywhere reports
recall 0.000 where truth exists, with precision null rather than perfect.

## Corrected in Phase 9.1

Three claims in this report were wrong, and the numbers in the table above were
computed under all three.

**Safe abstention did not require abstaining.** It counted any expected-ABSTAIN
line where the provider selected nothing, so a provider returning malformed
output on every page reported `safe_abstention_recall` 1.000 alongside
`invalid_page_rate` 1.000. Both cannot be true of one line. The matcher's 1.000
in the table is genuine, because it did abstain; the metric was simply unable to
tell that apart from failing.

**Identity did not distinguish what the provider saw.** Canonical, truncated and
withheld renderings of one snapshot produce visibly different requests and
shared one environment fingerprint, one set of proposal IDs, and reports that
looked directly comparable.

**A settlement line with no candidates disappeared.** Line outcomes were built
from the requests, so a snapshot holding one settlement line and nothing else
reported `line_count = 0`.

See [phase-9-1.md](phase-9-1.md).

## Harness version

`SHADOW_HARNESS_VERSION` is `3.0.0`. A 2.0.0 report asked one question per line;
this asks one per page, counts abstention and invalid output per page under
names that say so, and requires exact-set accuracy to hold across every page of
a line. The two are not comparable and the version says so.

`abstention_page_rate` and `invalid_page_rate` are named for what they count.
`abstained_line_rate` is kept as the line-level measure, defined as lines where
the provider selected nothing on any page.

## Changed files

| File | Change |
| --- | --- |
| `backend/app/ai/candidates.py` | The pager, the environment fingerprint, the canonical line oracle, presentation wiring |
| `backend/app/ai/presentation.py` | New. Eight reference styles and the equivalence rule |
| `backend/app/ai/corpus.py` | New. Six families, opaque identifiers, the private manifest |
| `backend/app/ai/evaluation.py` | Page-aware, harness 3.0.0, safe-abstention metrics |
| `backend/app/ai/proposals.py` | Page and environment on the envelope and in the derived identity |
| `backend/app/ai/provider.py` | The visible-reference matcher |
| `backend/app/ai/validation.py` | Membership is against the page |
| `backend/tests/ai/` | 268 tests, up from 157 |
| `docs/adr/ADR-013-...md` | New |
| `docs/adr/ADR-012-...md` | The `bind` wording corrected |
| `docs/phase-reports/phase-8.md` | The same correction |

Nothing under `app/domain/`, `app/reconciliation/`, `app/ingestion/`,
`app/storage/` or `app/api/` was touched.

## Commands run

| Command | Exit | Observed |
| --- | --- | --- |
| `git diff --check` | 0 | No whitespace errors |
| `make ci` | 0 | All nine core checks passed |
| `uv run pytest` | 0 | `1270 passed`, `Total coverage: 100.00%` |
| `uv run mypy` | 0 | `Success: no issues found in 113 source files` |
| `pnpm run test` | 0 | `184 passed`, frontend untouched |
| `make schema` | 0 | Byte identical |
| Migration and adoption suite | 0 | 95 passed |
| `make verify-containers` | 0 | Including the proxy checks |
| Baseline diffed against Phase 8.1 | 0 | Byte identical |
| Two corpus builds, two reports | 0 | Byte identical |
| Facts reversed, pages and report rebuilt | 0 | Byte identical |

## Tests

1270 backend, up from 1159. 268 in `tests/ai/`, up from 157.

| File | Tests | Covers |
| --- | --- | --- |
| `tests/ai/test_corpus.py` | 66 | Determinism, composition, the leak scan, oracle isolation, perturbation |
| `tests/ai/test_proposals.py` | 55 | Both layers, forbidden metadata including the two new fields |
| `tests/ai/test_validation.py` | 42 | Every adversarial case, membership against the page |
| `tests/ai/test_evaluation.py` | 38 | Both recall measures and every paired control |
| `tests/ai/test_paging.py` | 35 | The reproduced impossibility, every partition rule, determinism |
| `tests/ai/test_candidates.py` | 21 | Inclusion policy and the canonical oracle |
| `tests/ai/test_isolation.py` | 11 | Fifteen provider behaviours, and the corpus |

The leak scan is parametrised over every scenario family, every expected action,
every reference style, five action words and seven template names, against the
whole rendered input. The oracle-isolation tests render every reference in the
corpus as a near miss, and then withhold every reference entirely, and require
canonical truth to be unmoved by both.

The perturbation test requires four provider behaviours to produce four distinct
reports. A corpus that scored every provider the same would be measuring
nothing.

## Limitations

1. **No model has been called.** Every number comes from a fixture. The claim is
   that the environment is answerable and the corpus can tell providers apart,
   not that anything performs well at it.
2. **One case per family.** Six lines makes every rate coarse, which is why
   every denominator is reported. More cases would smooth the numbers and would
   not make any of them mean anything different.
3. **The matcher is not a model and does not resemble one.** It compares
   rendered references, ignoring case and separators, and declines when what it
   was given to match on is less specific than what it is matching against. It
   is a stand-in that behaves sensibly, included so the corpus has something
   between the oracle and a degenerate strategy.
4. **The corpus tests reference matching, not judgement.** The difficulties are
   formatting, near neighbours, ambiguity and absence. A model asked to reason
   about lifecycle or timing would need different scenarios.
5. **The candidate universe is still every event and payout in the snapshot.**
   Right for hundreds of facts and wrong for millions. Bounding it without doing
   the linking in the filter remains the open design problem, and paging does
   not solve it: it makes a large universe answerable, not small.
6. **Safe abstention is scored per line, not per page.** A provider that
   abstained on one page of an ambiguous line for the wrong reason would count
   as safe. With one ambiguous case spanning two pages, nothing distinguishes
   them yet.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| 64-record impossibility reproduced | Passed | Perfect provider scored 0.000 before |
| Deterministic pages, maximum 64 | Passed | Seven partition rules asserted |
| Every candidate on exactly one page | Passed | Union size equals total page size |
| Union equals the old environment | Passed | Compared against `candidate_universe` |
| No empty page, no oversized page | Passed | Asserted and refused by the model |
| Stable ordering, independent of insertion | Passed | Reversed facts, byte-identical output |
| Partition by sorted ID, not by ranking | Passed | Consecutive blocks, boundaries asserted |
| Page ordinal and environment fingerprint server-owned | Passed | Refused as extras in raw output |
| Proposal identity includes page and environment | Passed | Two pages, two distinct IDs |
| A record from another page is refused | Passed | And accepted on its own page |
| Evaluator asks per page, aggregates per line | Passed | Page and line outcomes both reported |
| `link_recall` over the full corpus and all pages | Passed | Missing a page lowers it |
| `answered_link_recall` retained under that name | Passed | Stays 1.000 when a page is skipped |
| Exact set over the aggregate selection | Passed | Missing a page fails it |
| `page_count` reported | Passed | On the report |
| Every settlement line accounted for | Wrong | A line with no candidates was absent. Corrected in Phase 9.1 |
| `abstention_page_rate` and `invalid_page_rate` | Passed | Named for pages; line measure named separately |
| Harness version bumped | Passed | 3.0.0, with the reason in the constant |
| Control: three pages, perfect provider | Passed | 1.000 recall and exact set |
| Control: misses the final page | Passed | Recall falls, exact set fails |
| Control: selects everything | Passed | Recall 1.000, precision 0.127 |
| Control: abstains everywhere | Passed | Recall 0.000 where truth exists |
| Control: page 2 record on page 1 | Passed | Out of candidate set |
| Control: reversed insertion order | Passed | Byte-identical pages and report |
| Six scenario families | Passed | All present, composition reported |
| Opaque identifiers, no leaked labels | Passed | Leak scan over the whole rendered input |
| Private oracle from canonical facts | Passed | Manifest and baseline compared |
| Oracle isolation under corrupted presentation | Passed | Near-miss and withheld renderings |
| `ExpectedProviderAction` private to the evaluator | Passed | Never in a request; asserted |
| Safe-abstention metrics reported separately | Partly wrong | Reported separately, and credited any line with no selection. Corrected in Phase 9.1 |
| Perturbation changes the score | Passed | Four behaviours, four distinct reports |
| No money, CSV, prose, codes, statuses or rationale | Passed | Field sets asserted |
| Nothing persisted, no endpoint | Passed | Schema and module source asserted |
| Isolation across every page result | Passed | Fifteen behaviours, store compared |
| Baseline byte identical | Passed | Diffed against Phase 8.1 |
| CI, typing, coverage, schema, migrations, containers | Passed | All exit 0 |
