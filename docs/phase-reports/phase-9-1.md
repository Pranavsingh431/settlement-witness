# Phase 9.1: Real safe abstention, presentation-bound identity, and unaskable lines

- Date: 2026-08-27
- Exit gate: passed. See "Exit gate status".
- Domain contract 5.0.0, parser 3.0.0, baseline 1.0.0, all unchanged
- Shadow link harness 3.0.0 to 4.0.0; shadow corpus 1.0.0, unchanged

## No hosted model was called

No API key, no SDK, no network access, no model output. Every provider is a
deterministic fixture and nothing here is a claim about AI performance.

## The three defects, reproduced first

```text
=== DEFECT 1: malformed output earns safe-abstention credit ===
  valid ABSTAIN everywhere   safe=1.0  unsafe=0.0  invalid_pages=0.0
  malformed everywhere       safe=1.0  unsafe=0.0  invalid_pages=1.0

=== DEFECT 2: identity does not distinguish what the provider saw ===
  canonical  shown payment=pay-1   env=55382919c5…
  truncated  shown payment=pay     env=55382919c5…
  withheld   shown payment=None    env=55382919c5…
  provider-visible requests differ : True
  environment fingerprints differ  : False

=== DEFECT 3: a line with no candidates vanishes ===
  settlement lines in snapshot : 1
  candidate pages built        : 0
  report line_count            : 0
```

The first is the worst of the three. A provider that returned nothing usable on
every page was credited with abstaining safely, and the same report said its
pages were 100% invalid. Both cannot be true of one line, and the metric
flattered exactly the providers that deserve it least: the safe-abstention pair
exists to reward declining over guessing, and it was rewarding failing.

## A. Safe abstention requires abstaining

An expected-abstention line now falls into exactly one of three outcomes.

| Outcome | When |
| --- | --- |
| `SAFE` | At least one page, and a valid abstention on every page |
| `UNSAFE_SELECTION` | Any record selected on any page |
| `UNUSABLE` | Neither: a malformed page, a provider failure, a partial answer, or no pages at all |

Declining is an answer; failing is not answering. `unusable_expected_abstention_rate`
is reported beside the other two, and a test requires the three numerators to
sum to the denominator for five different provider behaviours.

Strict link recall is untouched. A safe abstention still misses every true link
on that line, and a test asserts both at once, because that tradeoff is the
thing the pair exists to keep visible.

| Behaviour | safe | unsafe | unusable |
| --- | --- | --- | --- |
| Valid abstention on every page | 1.000 | 0.000 | 0.000 |
| Malformed on every page | 0.000 | 0.000 | 1.000 |
| One abstained page, one malformed | 0.000 | 0.000 | 1.000 |
| Any record selected | 0.000 | 1.000 | 0.000 |
| Provider failure, all three kinds | 0.000 | 0.000 | 1.000 |

## B. Identity binds to what the provider saw

The environment fingerprint identifies **which records** a universe holds. It
says nothing about **what was shown** about them, and the same universe rendered
canonically, truncated and withheld is three different tasks.

`request_fingerprint` is built from the styled subject references, every
rendered field of every candidate in page order, the page ordinal and count, the
environment fingerprint and the snapshot fingerprint, in that order. It carries
nothing private, because it is built from a request and a request holds nothing
private. A withheld field contributes a distinct absence marker rather than an
empty string, so a reference that was withheld and one that rendered empty do
not collide.

It joins `proposal_id_for`, so the same provider, line, snapshot and page under
two renderings produce two proposal IDs. `ShadowReport.request_set_fingerprint`
is the ordered digest of every page request, so two reports over one canonical
snapshot under different renderings are visibly non-comparable.

Five presentations of one snapshot now produce five request fingerprints, five
sets of proposal IDs and five report fingerprints. The report fingerprint does
not depend on the provider, which is what makes it usable for deciding whether
two reports are comparable at all.

Raw model output still carries an outcome and a list of IDs. A response
supplying `request_fingerprint` is refused as an extra, as the other five pieces
of metadata already were.

## C. Unaskable lines are reported

Line outcomes are now built from the snapshot's settlement lines rather than
from the requests, so a line with no candidate page appears with
`page_count = 0` and `askable` false, and `unaskable_line_count` says how many
there are.

Such a line is never credited. Its truth and its selection are both empty, so
comparing them would call it an exact selection, and a corpus of lines with no
candidates would have scored perfect exact-set accuracy for having asked
nothing. It is not a safe abstention either: nobody declined, because nobody was
asked.

**A line is unaskable only when the whole snapshot holds no payment event and no
payout.** The candidate universe is every event and payout in the snapshot, so
this is a property of the snapshot rather than of one line. A snapshot of two
settlement lines and nothing else reports two lines, zero pages and two
unaskable lines.

## The corpus under the corrected harness

| Provider | recall | answered | precision | exact | false | safe | unsafe | unusable |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Reads visible refs | 0.975 | 1.000 | 1.000 | 0.667 | 0.000 | 1.000 | 0.000 | 0.000 |
| Canonical oracle | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | 0.000 | 1.000 | 0.000 |
| Selects everything | 1.000 | 1.000 | 0.127 | 0.000 | 0.873 | 0.000 | 1.000 | 0.000 |
| Always abstains | 0.000 | null | null | 0.000 | null | 1.000 | 0.000 | 0.000 |
| Malformed everywhere | 0.000 | null | null | 0.000 | null | **0.000** | 0.000 | **1.000** |

Denominators: 161 true links, 6 lines, 2 expecting an abstention, 24 pages.

The matcher's numbers are unchanged, because it genuinely abstained on both
cases where abstaining was safe. What changed is the last row: a provider that
returned nothing usable moved from 1.000 safe abstention to 1.000 unusable. The
correction does not alter what a working provider scores; it stops a failing one
scoring as though it had behaved well.

The finding from Phase 9 stands and is unaffected: the canonical oracle is
perfect on every linking metric and unsafe on both abstaining cases, while the
matcher gives up 0.025 of recall to be safe on both.

## Corrected in Phase 9.2

This phase fixed `safe_abstention_recall` and left the same defect in the field
beside it. `abstained_line_rate` went on counting any line where nothing was
selected, so it reported 1.000 for a corpus no provider was ever called on, and
1.000 next to an invalid page rate of 1.000 for a provider whose every page was
refused.

The limitation this report records, that safe abstention is scored per line
rather than per page, is unrelated and still stands. What it did not say is that
the general line-level measure was still crediting failure, which is the exact
mistake the phase was written to remove.

`abstained_line_rate` is replaced by `fully_abstained_askable_line_rate`. See
[phase-9-2.md](phase-9-2.md).

## Versioning

`SHADOW_HARNESS_VERSION` is `4.0.0`. Report fields changed, and both proposal
and report identity changed meaning. A 3.0.0 report is not comparable with one
from here, and its safe-abstention figure was computed under a rule that
credited failure.

The corpus is unchanged at 1.0.0. No fact and no scenario moved.

## Changed files

| File | Change |
| --- | --- |
| `backend/app/ai/candidates.py` | The request fingerprint, the absence marker, `truth_for_line` |
| `backend/app/ai/evaluation.py` | `LineAbstention`, unaskable lines, the request-set fingerprint, harness 4.0.0 |
| `backend/app/ai/proposals.py` | `request_fingerprint` on the envelope and in the derived identity |
| `backend/app/ai/validation.py` | Binds the request fingerprint |
| `backend/tests/ai/test_safe_abstention.py` | New. 18 tests |
| `backend/tests/ai/test_request_identity.py` | New. 12 tests |
| `backend/tests/ai/test_unaskable_lines.py` | New. 10 tests |
| `docs/adr/ADR-013-...md` | Canonical environment identity against provider-visible request identity |
| `docs/phase-reports/phase-9.md` | Correction note, two exit-gate rows marked |

Nothing under `app/domain/`, `app/reconciliation/`, `app/ingestion/`,
`app/storage/` or `app/api/` was touched, and `app/ai/corpus.py` and
`app/ai/presentation.py` are unchanged.

## Commands run

| Command | Exit | Observed |
| --- | --- | --- |
| `git diff --check` | 0 | No whitespace errors |
| `make ci` | 0 | All nine core checks passed |
| `uv run pytest` | 0 | `1313 passed`, `Total coverage: 100.00%` |
| `uv run mypy` | 0 | `Success: no issues found in 116 source files` |
| `pnpm run test` | 0 | `184 passed`, frontend untouched |
| `make schema` | 0 | Byte identical |
| Migration and adoption suite | 0 | 95 passed |
| `make verify-containers` | 0 | Including the proxy checks |
| Baseline diffed against Phase 9 | 0 | Byte identical |
| Two corpus builds | 0 | Byte identical |

## Tests

1313 backend, up from 1270. 311 in `tests/ai/`, up from 268.

| File | Tests | Covers |
| --- | --- | --- |
| `tests/ai/test_safe_abstention.py` | 18 | The three outcomes, the partition, recall left alone |
| `tests/ai/test_request_identity.py` | 12 | Five presentations, three identity levels, the absence marker |
| `tests/ai/test_unaskable_lines.py` | 10 | Reported, never credited, mixed corpora |

Each new file opens with the reproduction it exists for, kept as a test.

## Limitations

1. **Safe abstention is still scored per line, not per page.** A line where the
   provider abstained on every page for the wrong reason counts as safe. What
   changed is that failing on any page no longer counts as safe at all.
2. **`UNUSABLE` does not distinguish its causes.** A malformed page, a provider
   failure and a partial answer all land in one bucket. The page-level rates say
   which happened; the line-level outcome does not.
3. **Unaskable lines are all-or-nothing.** Because the universe is every event
   and payout in the snapshot, either every line has candidates or none does.
   A per-line notion would need a narrower inclusion policy, which is the open
   problem paging did not solve.
4. **The request fingerprint covers what a request contains.** If a future
   change put something in front of a provider outside the request object, it
   would not be in the fingerprint, and the fingerprint would quietly stop
   meaning what it says.
5. **Still no model.** Every number is a fixture's.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| Three defects reproduced before any change | Passed | Output quoted above, each kept as a test |
| Safe abstention requires a valid abstention on every page | Passed | Malformed, failed and partial all refused credit |
| Every abstention measure requires an abstention | Partly wrong | `abstained_line_rate` still credited failure. Corrected in Phase 9.2 |
| A selected record remains an unsafe selection | Passed | Including one record on one page of two |
| `unusable_expected_abstention_rate` added | Passed | Named and reported separately |
| The three outcomes partition the denominator | Passed | Asserted over five behaviours |
| Strict link recall unchanged | Passed | A safe abstention still scores 0.000 recall |
| Control: valid abstention everywhere | Passed | safe 1.000 |
| Control: malformed everywhere | Passed | safe 0.000, unusable 1.000 |
| Control: one abstained page plus one malformed | Passed | safe 0.000, unusable 1.000 |
| Control: any selected record | Passed | unsafe 1.000 |
| Request fingerprint over the stated inputs in fixed order | Passed | Documented and built in that order |
| It excludes private labels, answers and provider output | Passed | Built from a request, which holds none |
| Included in `proposal_id_for` | Passed | Five presentations, five ID sets |
| Report request-set fingerprint | Passed | Five presentations, five values |
| Raw output cannot supply it | Passed | Refused as an extra |
| Rebuilding a styled request is byte identical | Passed | Fingerprint and JSON both |
| Same selection under two presentations stays distinct | Passed | Same records, different proposal IDs |
| `line_count` equals the snapshot's lines | Passed | Including lines with no pages |
| `unaskable_line_count` reported | Passed | And `askable` per line outcome |
| An unaskable line is never exact or safe | Passed | Both asserted |
| `page_count` stays 0 and no empty page is built | Passed | `build_pages` returns none |
| Harness version bumped | Passed | 4.0.0, with the reason in the constant |
| Phase 9 corrected, not rewritten | Passed | Correction section and two marked rows |
| ADR-013 distinguishes the two identities | Passed | Section added |
| Corpus version unchanged | Passed | 1.0.0; no fact or scenario moved |
| Baseline byte identical | Passed | Diffed against Phase 9 |
| Corpus deterministic | Passed | Two builds compared |
| Isolation preserved | Passed | Fifteen behaviours against a real database |
| CI, typing, coverage, schema, migrations, frontend, containers | Passed | All exit 0 |
