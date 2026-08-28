# Phase 11.1: Make a review command retryable, and the whole queue reachable

- Date: 2026-08-28
- Exit gate: passed
- Domain contract 5.0.0, parser 3.0.0, baseline 1.0.0, review contract 1.0.0, all unchanged
- No backend change: no storage, no migration, no API, no contract
- Baseline output byte-identical before and after this phase

## Two defects, both in the screen

Phase 11 built a correct server and a client that could not use half of it. The
API was idempotent and the client could not retry; the API paged and the client
never asked for a second page. Both are corrected here and nothing behind the
API moved.

### 1. A fresh key on every click defeats the server's idempotency

```text
FAIL  reuses the idempotency key when unchanged input is retried
AssertionError: expected '396bfc00-1510-4507-b895-be21d4916e99'
                to be     '49492dcb-9b5b-4b1c-94a8-8bd206e6993f'
```

A request can fail after the server has written the row: the answer is lost on
the way back. The reviewer sees an error and presses the button again. Phase 11
sent a new key, so the server recorded a second event for one intended action,
and nothing on either side said so.

Phase 11 tested this behaviour and treated it as a feature: "sends a fresh
idempotency key for each submission". That test is the proof the client could
not retry safely, which is why the defect survived a phase whose whole point was
idempotency.

### 2. Only the first twenty items were reachable

```text
FAIL  offers a way to reach the twenty-first item
Unable to find an accessible element with the role "button" and name /next/i
```

The screen asked for `limit=20` with no offset, printed "20 of 21 shown", and
offered no control to go further. A run with 21 or more reviewable decisions hid
work from the reviewer, silently.

## A. The key belongs to the command, not to the click

```ts
interface PendingCommand {
  readonly action: ReviewAction;
  readonly note: string;        // normalised, matching what the server stores
  readonly decisionId: string;
  readonly fingerprint: string;
  readonly key: string;
}
```

One pending command is held while its outcome is unknown.

| Event | What happens to the pending command |
| --- | --- |
| Submit, no pending command matches | A new command with a new key, kept |
| Submit, the input matches the pending command | The same command, the same key |
| The request is confirmed | Cleared, so the next action is a new command |
| The request fails | Kept, because whether it was recorded is exactly what is unknown |
| The reviewer changes action, note, target or fingerprint | The next submission is a different command and gets a new key |

The comparison is over the **normalised** note, matching the server's own
`normalise_note`, so a note that differs only by surrounding space is the same
command on both sides.

Whether the next submission would be a retry is derived during render rather
than stored, so editing the note stops it being a retry the moment the input
changes rather than one state update later. The button reads **Retry this same
action** while it is a retry and **Record this action** otherwise, and a notice
beside it says what that means:

> **This retries the same action, not a second one.** The request above may have
> reached the server before the answer was lost, so it is sent again under the
> same key. If it was recorded, you get that event back rather than a duplicate.
> Change the action or the note and it becomes a different action instead.

The baseline-unchanged notice is untouched and is asserted to still be present
while retrying.

## B. Pagination

`offset` is component state and goes into the request and into the `useLoad`
key. Previous and next are ordinary buttons inside a `nav` labelled "Review
queue pages", disabled at the boundaries: previous at `offset === 0`, next when
`offset + items.length >= total`.

The selection is cleared in exactly one place, a render-time check that drops it
when the page on screen does not hold it. Doing it there rather than in the page
handlers means it also covers a reload after an action that comes back without
the selected item, and the fallback to the first item is unchanged.

Ordering did not change and could not: the API orders by settlement line ID, and
that does not move when somebody acts on an item. A test records an action on
the second page and asserts the next request is still `offset=20`.

The table caption and the pager both state the range. Only the caption is a live
region: two live regions competing on one screen is noise rather than access,
and the caption changes with the page.

## Verified behaviour

`ReviewQueuePage.test.tsx` grows from 28 cases to 50.

| Case | Proves |
| --- | --- |
| `reuses the key when unchanged input is retried` | The defect, from the other end |
| `sends an identical command payload on the retry` | Not only the key: the whole call is equal |
| `labels the retry as the same action rather than a second one` | The button text and the notice |
| `keeps the baseline-unchanged notice while retrying` | The Phase 11 guarantee is not lost in the new state |
| `stops being a retry when the action is changed` | The label goes back, before anything is sent |
| `sends a new key when the action is changed after a failure` | A changed command is a different command |
| `sends a new key when the note is changed after a failure` | Same, for the other field a reviewer edits |
| `treats a note that differs only by surrounding space as the same command` | Normalisation agrees with the server's |
| `sends a new key for the next action once one is confirmed` | Fail, retry, succeed, then a deliberate second action |
| `sends a fresh idempotency key for a deliberate second action` | Two confirmed actions are two events |
| `asks for the first page with an explicit offset` | `{ limit: 20, offset: 0 }` |
| `asks for offset twenty when the next page is chosen` | The request, not just the rendering |
| `reaches the twenty-first item, which the first page hides` | The defect, from the other end |
| `returns to offset zero on previous` | |
| `disables previous on the first page`, `disables next on the last page` | Both boundaries |
| `says which items are on screen and how many there are in total` | "Items 1 to 20 of 21", then "Items 21 to 21 of 21" |
| `offers the pager under a named landmark` | Keyboard and screen reader reachable |
| `selects the first item of a page the previous selection is not on` | |
| `keeps the baseline status and the workflow state apart on every page` | The Phase 11 distinction, on page two |
| `stays on the same page after an action is recorded` | An action does not reorder or reset paging |
| `offers no pager on a queue that fits one page` | Both buttons disabled, no dead ends |
| `orders the timeline by sequence, whatever order it arrived in` | The component sorts rather than trusting the server's order |

### The tests fail against the restored behaviour

Each defect was put back on its own, with everything else left in place:

```text
a new key on every click          4 of 50 failed
offset dropped from the request   9 of 50 failed
```

The four are the retry cases; the nine are every page-dependent case. The other
cases pass either way, correctly: they assert the ordinary path still works.

## Observed results

```text
frontend: 271 passed, 98.82% statements, 92.56% branches
backend : 1700 passed, 100.00% coverage, unchanged
```

Frontend coverage is above the Phase 11 numbers on both measures (98.53% and
92.13%). `ReviewQueuePage.tsx` and `ReviewWorkspace.tsx` are both at 100%
statements.

### The baseline is unmoved

```text
reconcile over the example documents: efc16896fdc7bf2cb0649312f07efae3fb4f9931bd7e7b2d5aed3d22c8b9d3dd
public benchmark report            : e5cff7b46a22c4d5b89ee0361ac1e373a4680f2f4a9ec268575b242cf60c4b5c
```

Both identical to Phase 11, and the benchmark hash is identical to Phase 10,
10.1 and 10.2 as well. Regenerating the published JSON Schema produced no diff.
`git diff --stat -- backend/` is empty: no storage, no migration, no baseline
logic and no contract changed, so there was nothing that could move.

## Changed files

| File | Change |
| --- | --- |
| `frontend/src/routes/ReviewQueuePage.tsx` | Pending command with a stable key; offset state and pager; single-place selection reset |
| `frontend/src/routes/ReviewQueuePage.test.tsx` | 22 new cases; the fresh-key test renamed to what it now proves |
| `frontend/src/styles.css` | `.pager` |
| `docs/phase-reports/phase-11.md` | "Corrected later"; original text left as written |

## Limitations

**A retry is still a decision the reviewer makes.** Nothing here retries
automatically. That is deliberate: an automatic retry on a network failure would
be right most of the time and would also hide from the reviewer that anything
went wrong, and this screen exists to not hide things.

**The pending command lives in the component.** Reloading the browser after a
failed submission loses it, and the next attempt gets a new key. Surviving that
would mean persisting the key in browser storage, which is a real feature with
its own questions about when to expire it. Recorded rather than built.

**Offset paging is still offset paging.** Stable here because a run's decisions
never change and the order is the settlement line ID, which is a stronger
guarantee than most offset paging has. It would not survive a queue whose
membership changed under the reader, and this one cannot.

**The queue still has no filters.** No filtering by workflow state, no
assignment, no bulk actions. Unchanged from Phase 11 and still deferred.

## Commands run

```text
uv run ruff format --check .                    135 files already formatted
uv run ruff check .                             All checks passed
uv run mypy                                     no issues in 131 source files
uv run pytest                                   1700 passed, 100.00% coverage
pnpm run format:check                           Prettier clean
pnpm run lint                                   eslint, 0 warnings
pnpm run typecheck                              tsc clean
pnpm run test                                   271 passed, 98.82% statements
pnpm run build                                  production bundle built
make schema                                     no diff
make benchmark-evaluate                         byte-identical to Phase 10
./scripts/verify-containers.sh                  passed, both non-root
```

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| Both defects reproduced first | Passed | The two failures quoted above, before any fix |
| One pending command holding action, note, fingerprint, target and key | Passed | `PendingCommand`, built before the request |
| A failure retains the command; unchanged input reuses its key | Passed | `reuses the key when unchanged input is retried` |
| Confirmed success clears it | Passed | `sends a new key for the next action once one is confirmed` |
| A changed command discards the pending one and gets a new key | Passed | Action and note cases, plus the whitespace case |
| A retry is clearly labelled as the same action | Passed | Button text and notice, both asserted |
| The baseline-unchanged notice is retained | Passed | Asserted in the retry state |
| Accessible previous and next controls | Passed | Buttons inside a labelled `nav` |
| Offset carried in state and requested | Passed | `{ limit: 20, offset: 20 }` asserted on the call |
| Previous disabled at 0, next disabled at the end | Passed | Both boundary cases |
| Selection preserved only when present, else the first item | Passed | `selects the first item of a page the previous selection is not on` |
| Ordering stable, actions do not reorder pages | Passed | `stays on the same page after an action is recorded` |
| The workflow and baseline distinction preserved | Passed | Asserted on page two as well as page one |
| The tests fail against the restored behaviour | Passed | 4 and 9 failures with each defect back |
| No storage, migration, baseline or contract change | Passed | `git diff -- backend/` is empty |
| Full CI, frontend tests, byte-identical baseline | Passed | Commands and hashes above |

## Unresolved

Nothing blocking. The open items are the per-session lifetime of the pending
command, the absence of automatic retry, offset paging's general weakness, and
the missing queue filters. All four are described above and none is hidden
behind a passing row.
