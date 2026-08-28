# Phase 11: An evidence-locked human review queue

- Date: 2026-08-28
- Exit gate: passed
- Domain contract 5.0.0, parser 3.0.0, baseline 1.0.0, all unchanged
- New: review contract 1.0.0, migration `0003_review_events`, `ADR-015`
- Baseline output byte-identical before and after this phase

## What this phase is, in one paragraph

The baseline produces exceptions and unknowns, and somebody has to work through
them. This adds the queue and the workflow record for that, and adds no way to
change what the baseline concluded. There is no approve, no resolve and no
override, because a click cannot make a settlement line supported: a line is
resolved when the records it cites are present and the invariants over them
hold, and if those records are absent the only thing that changes it is the
records arriving.

## The boundary, and how it is held

| Claim | Held by |
| --- | --- |
| A review event is not evidence and not a decision | Its own table, its own contract version, its own API prefix; nothing in a decision refers to one |
| No action alters a status, code, invariant, evidence, fingerprint or run key | Byte-for-byte comparison of every stored decision before and after all four actions, and of the recomputed baseline |
| There is no approve, resolve or override action | Four-member enum, asserted as a set; a database CHECK constraint; a request model with no status field |
| Closing does not resolve | `CLOSED_WITHOUT_OVERRIDE`, `baseline_status` served beside `workflow_state`, and a sentence in every response |
| No reviewer identity is claimed | No actor column, asserted against the schema and the model; the UI says so on every timeline entry |

The last row is the uncomfortable one and it is stated as a limitation rather
than dressed up. See "What is missing".

## Storage

`review_events`, the fifth append-only table.

| Column | Why |
| --- | --- |
| `sequence` | The database assigns it. The only thing ordering uses |
| `event_id`, `run_id`, `decision_id`, `subject_settlement_line_id` | Identity, and the binding to one recorded conclusion |
| `decision_fingerprint` | A digest of the whole decision the reviewer was looking at |
| `action` | One of four, enforced by a CHECK constraint |
| `note` | Optional, at most 500 characters, plain text |
| `idempotency_key`, `command_fingerprint` | A retry returns the original; a reuse is refused |
| `recorded_at` | Recorded, never sorted on |

**No status column.** The workflow state is folded from the events every time it
is served, because a stored status and an event log can disagree and then
somebody has to decide which one is true.

**No actor column.** There is no authentication, so there is nobody to record.

Ordering is by `sequence` and never by `recorded_at`. Two events in the same
millisecond still have an order, and a clock correction cannot reorder history.
Both are tested directly.

Migration `0003_review_events` creates the table and its two triggers, and
touches neither earlier revision. `review_events` was added to
`APPEND_ONLY_TABLES`, so the three existing trigger tests cover it without being
edited.

## The projection

```text
no events                 -> OPEN
ACKNOWLEDGED              -> ACKNOWLEDGED
REQUEST_EVIDENCE          -> WAITING_FOR_EVIDENCE
ESCALATED                 -> ESCALATED
CLOSED_WITHOUT_OVERRIDE   -> CLOSED_WITHOUT_OVERRIDE
```

The last event in sequence order wins. The projection sorts its input rather
than trusting the caller to have sorted it, because one that depended on
pre-sorted input would report the wrong state silently on the day somebody
passed it a set.

An event after a close reopens the item. Closing says no further work is
planned, not that nothing may follow, and the whole history stays visible either
way. A queue that could never be reopened would be worked around outside the
system.

None of the five workflow states shares a value with any of the four decision
statuses. A test asserts the two vocabularies are disjoint, so neither can be
rendered where the other belongs.

## API

Under `/v1/review`, not `/v1/reconciliation`. The separation keeps the existing
claim that every reconciliation route is a read exactly as checkable as it was.

| Route | Does |
| --- | --- |
| `GET /v1/review/runs/{run_id}/queue` | A page of the queue, `limit`/`offset`, ordered by settlement line ID |
| `GET /v1/review/runs/{run_id}/queue/{decision_id}` | One item, its certificate and its timeline |
| `POST /v1/review/runs/{run_id}/queue/{decision_id}/events` | Append one event |

Every response carries `baseline_unchanged_note` and every item carries
`baseline_status` beside `workflow_state`. The status is repeated deliberately:
a client reading only the workflow state would otherwise have to go looking for
the conclusion, and showing a closed review as though the line were settled is
the one mistake this endpoint must not make possible.

Refusals, each of which writes nothing:

| Status | `error` | When |
| --- | --- | --- |
| 404 | `not_found` | Unknown run, or unknown decision |
| 409 | `not_reviewable` | `RESOLVED` or `PENDING` target |
| 409 | `stale_certificate` | The fingerprint is not this decision's |
| 409 | `idempotency_conflict` | The key was used for a different command |
| 422 | `invalid_request` | Malformed body, unknown action, short key, over-long note |

A resolved decision is a 404 on the item endpoint rather than a 200 with an
empty timeline. It is not in this queue.

## Interface

A queue screen at `/runs/:runId/review`, reachable from the dashboard and from
the run audit page.

The layout carries the argument. The baseline status appears in the same badge
every other screen uses; the workflow state appears in a visibly different one,
an outline with a dot, borrowing no colour from the resolved, exception or
unknown palette. A reviewer glancing at a row cannot mistake "closed" for
"resolved" because the two happened to be the same shade of green. Between them
is a sentence saying the second does not change the first, and it is not
dismissible.

The dashboard reads the real queue for the latest run and shows three counts:
needing review, still open, closed without override. With no run it says a queue
is built from a recorded run and asks the backend for nothing.

Every string from the server is rendered as text. There is no
`dangerouslySetInnerHTML` anywhere in this application and no Markdown renderer,
so a note containing markup is shown as the characters somebody typed. An API
test records `<b>see</b> ticket #4` and asserts it comes back verbatim, and an
interface test renders it and asserts the page contains no `<b>` element.

Accessibility and states are the existing conventions: a labelled radio group
for the four actions with a sentence each, a labelled textarea, `role="status"`
for the confirmation, `role="alert"` for a refusal, keyboard-reachable row
buttons with `aria-pressed`, and explicit loading, error and empty states.

One defect was found and fixed while writing the tests: recording an action
reloaded the queue, and `useLoad` reports no data while a request is in flight,
so the workspace blanked at the exact moment somebody pressed a button and took
the confirmation with it. The page now keeps the last loaded queue across a
reload.

## Verified behaviour

124 new backend cases in four new suites, 2 more in the migration suite, and
65 new frontend cases.

| Suite | Cases | Proves |
| --- | --- | --- |
| `tests/review/test_events.py` | 29 | The four actions, the disjoint vocabularies, the projection, ordering under identical timestamps and a backwards clock, the fingerprints |
| `tests/review/test_storage.py` | 10 | Raw SQL UPDATE and DELETE refused, INSERT allowed, no status column, no actor column, the action CHECK constraint, database-assigned sequence |
| `tests/review/test_service.py` | 31 | Byte-for-byte decision immutability, stable ordering and paging, five refusals that write nothing, idempotent retry, conflicting reuse, the raced key |
| `tests/api/test_review.py` | 54 | Every action, every error, empty queue, stable pagination, the unchanged-baseline copy, no actor in any response |
| `ReviewQueuePage.test.tsx` | 28 | Four actions offered and no fifth, no approve/resolve/override control, fresh idempotency key per submission, closed item still showing `Exception` |
| `DashboardPage.test.tsx` (new cases) | 8 | The real queue read for the latest run, the counts, the copy, no request when there is no run |
| `RunAuditPage.test.tsx` (new case) | 1 | The audit screen links to the queue for that run |
| `parse.test.ts`, `client.test.ts` (new cases) | 28 | The wire shapes checked rather than cast; no status field is ever sent |

### The central test

```python
def test_every_action_leaves_every_decision_identical(...):
    before = stored_decisions_json(engine, run.run_id)
    for decision in reviewable:
        for action in ReviewAction:
            append(engine, run, decision, action, ...)
    assert stored_decisions_json(engine, run.run_id) == before
```

Every action against every reviewable decision, compared as one canonical string
over every stored decision, so a changed status, a dropped exception code, a
reordered evidence list or a moved timestamp would all fail. A second test does
the same against a freshly recomputed baseline, because a stored decision that
matched while a fresh reconciliation disagreed would mean the review path had
changed something the store was hiding.

### The guards fail against restored misleading behaviour

Each was reintroduced on its own, with everything else left in place:

```text
closed review served as RESOLVED       2 failed in TestNothingHereResolvesAnything
"closing resolves the line" copy       1 failed in TestTheQueue
same copy in the interface             2 failed in ReviewQueuePage.test.tsx
```

## Observed results

A live run through the API, over the example documents:

```text
queue: total=2 open=2 contract=1.0.0
  line-0001  baseline=EXCEPTION  workflow=OPEN
  line-0003  baseline=EXCEPTION  workflow=OPEN

  ACKNOWLEDGED             -> 201  workflow=ACKNOWLEDGED             baseline=EXCEPTION
  REQUEST_EVIDENCE         -> 201  workflow=WAITING_FOR_EVIDENCE     baseline=EXCEPTION
  ESCALATED                -> 201  workflow=ESCALATED                baseline=EXCEPTION
  CLOSED_WITHOUT_OVERRIDE  -> 201  workflow=CLOSED_WITHOUT_OVERRIDE  baseline=EXCEPTION

timeline: [(1, 'ACKNOWLEDGED'), (2, 'REQUEST_EVIDENCE'), (3, 'ESCALATED'),
           (4, 'CLOSED_WITHOUT_OVERRIDE')]
closed item baseline status: EXCEPTION
queue after: total=2 open=1
reconciliation response byte-identical: True
```

The baseline status is `EXCEPTION` on every line of that output, including after
the close. The reconciliation endpoint returns the same bytes before and after.

### The baseline is unmoved

The baseline over the example documents, canonically serialised and hashed, with
and without this phase applied:

```text
with Phase 11     : efc16896fdc7bf2cb0649312f07efae3fb4f9931bd7e7b2d5aed3d22c8b9d3dd
without (stashed) : efc16896fdc7bf2cb0649312f07efae3fb4f9931bd7e7b2d5aed3d22c8b9d3dd
snapshot fingerprint identical, status counts identical
```

The public benchmark report is byte-identical to Phase 10, 10.1 and 10.2:

```text
sha256 e5cff7b46a22c4d5b89ee0361ac1e373a4680f2f4a9ec268575b242cf60c4b5c
```

Regenerating the published JSON Schema produced no diff. The domain contract,
baseline and parser versions did not move, so the run key is unchanged and a
database recorded before this phase still finds its runs.

## Versioning

| Version | Before | After | Why |
| --- | --- | --- | --- |
| Domain schema | 5.0.0 | 5.0.0 | Nothing in a decision changed, or refers to a review event |
| Baseline | 1.0.0 | 1.0.0 | The reconciliation engine is untouched |
| Parser | 3.0.0 | 3.0.0 | Ingestion is untouched |
| Review contract | none | 1.0.0 | A new independent contract with its own shape |
| Migration head | 0002 | 0003 | A new table |

Bumping the domain contract for an independent feature would tell every reader
of a stored decision that the meaning of their record had changed when it had
not. A test asserts the two version constants differ.

## What is missing

**There is no authentication, so there is no reviewer.** Every event records
what was done and not who did it. This is a limitation, not a design choice with
a silver lining: without it the review log answers "what happened" and cannot
answer "who is accountable". A field holding a name typed into a box would be
worse than the gap, because it would be read as an audit trail. Adding real
identity means an authentication and tenancy model, which is genuine work that
has not been done.

**Actual manual resolution is not built, and would not be a button.** Nothing in
this phase can make a settlement line resolved, and no future version of this
screen should. A line becomes resolved when a source record supporting it is
imported and reconciled into a new run. Doing that from human judgement would
require a new auditable source-record type carrying who asserted what, on what
authority, and with what external verification, and that record would then be
evidence like any other and be verified like any other. Until such a record type
exists, an exception stays an exception and a person's decision about it stays
an annotation.

**Review events are scoped to one run.** A new run over new facts starts with an
empty queue. That is correct, because its decisions are new conclusions and the
annotations on the old ones were about the old ones, and it is also a real
operational cost: somebody who acknowledged twenty items yesterday sees twenty
open items after the next import. Carrying annotations forward would mean
deciding when two decisions about the same line are "the same item", which is a
judgement this phase declined to make silently.

**Paging is offset-based.** Stable here because the order is the settlement line
ID and a run's decisions never change, which is a stronger guarantee than most
offset paging has. It would not survive a queue whose membership changed under
the reader, and this one cannot.

**The queue has no filters.** No filtering by workflow state, no assignment, no
due dates, no bulk actions. Deferred rather than forgotten.

**One clock.** `recorded_at` comes from the process that served the request.
Nothing depends on it, because ordering does not, but it is not a trusted
timestamp and should not be read as one.

## Commands run

```text
uv run ruff format --check .                    135 files already formatted
uv run ruff check .                             All checks passed
uv run mypy                                     no issues in 131 source files
uv run pytest                                   1700 passed, 100.00% coverage
pnpm run format:check                           Prettier clean
pnpm run lint                                   eslint, 0 warnings
pnpm run typecheck                              tsc clean
pnpm run test                                   249 passed, 98.53% statements
pnpm run build                                  production bundle built
make schema                                     no diff
make benchmark-evaluate                         byte-identical to Phase 10
./scripts/verify-containers.sh                  passed, both non-root
```

Migration and legacy-adoption suites run inside `uv run pytest`. Both were
extended: the new revision is asserted to create the table, to leave it empty,
and to protect it with the same two triggers as every other append-only table.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| Append-only review event bound to one recorded decision and run | Passed | `review_events`, FK to the run, decision ID and fingerprint on every row |
| Storage-assigned monotonic sequence, not timestamp ordering | Passed | `test_the_sequence_is_assigned_by_the_database`, `test_identical_timestamps_still_order_deterministically` |
| Only the four named actions | Passed | Enum asserted as a set, CHECK constraint, 422 on anything else |
| Workflow state derived, no stored status column | Passed | `test_there_is_no_status_column`; the projection is a pure function |
| Idempotent retry, conflicting reuse fails and writes nothing | Passed | 4 service cases and 3 API cases, including the raced key |
| RESOLVED targets refused | Passed | 409 `not_reviewable`, and absent from the queue and the item endpoint |
| Bound to the exact certificate representation | Passed | `stale_certificate` for a wrong or another item's fingerprint |
| Database-level UPDATE/DELETE refusal, INSERT allowed | Passed | `TestTheTableRefusesToBeRewritten`, raw SQL |
| A proper migration, no historical file modified | Passed | `0003_review_events`; 0001 and 0002 untouched |
| Stable queue endpoint, EXCEPTION and INSUFFICIENT_EVIDENCE only | Passed | `TestTheQueue`, 3 page sizes, identical repeated pages |
| Each item exposes certificate, codes, invariants, evidence, state, timeline | Passed | `test_every_item_carries_its_certificate` and the item endpoint tests |
| Command endpoint, project error envelope and conventions | Passed | Same `ErrorEnvelope`, same 404/409/422 vocabulary |
| No model endpoint, model call or hosted-provider dependency | Passed | No import of `app.ai` anywhere in `app/review` or `app/api/review.py` |
| Dashboard connected to the real API | Passed | 8 new dashboard cases |
| Review workspace with certificate, timeline and one permitted action | Passed | `ReviewQueuePage.test.tsx` |
| The distinction visually unavoidable | Passed | Different badge shape and palette, plus non-dismissible copy on both screens |
| A closed review still shows the original status prominently | Passed | Frontend and API tests, and the live output above |
| Server text rendered as text | Passed | A note containing markup round-trips verbatim and renders no element |
| Keyboard, loading, error, empty states preserved | Passed | Existing conventions, tests for each |
| No fake login, reviewer name or audit identity | Passed | No actor in the schema, the model, the API or the interface |
| Decisions byte-identical before and after all action types | Passed | Stored and recomputed, both compared canonically |
| No EXCEPTION or INSUFFICIENT_EVIDENCE becomes RESOLVED anywhere | Passed | API and UI tests, and both fail against restored misleading behaviour |
| Documentation, ADR, absence of auth recorded as a limitation | Passed | ADR-015, `docs/api.md`, "What is missing" above |
| Versioning applied only where required | Passed | Review contract 1.0.0; domain, baseline and parser unmoved |
| Full CI, migrations, schema, frontend, containers, byte-identical baseline | Passed | Commands and hashes above |

## Unresolved

Nothing blocking. The open items are the absence of authentication, the absence
of a source-record type for externally verified human assertions, the per-run
scoping of annotations, and the missing queue filters. All four are described
above and none is hidden behind a passing row.
