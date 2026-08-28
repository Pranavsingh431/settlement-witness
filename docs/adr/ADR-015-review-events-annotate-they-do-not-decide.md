# ADR-015: A review event annotates a conclusion, it does not become one

- Status: Accepted
- Date: 2026-08-28
- Supersedes: none
- Superseded by: none
- Related: [ADR-002](ADR-002-domain-contract-and-verifier-authority.md),
  [ADR-004](ADR-004-append-only-import-and-atomicity.md),
  [ADR-009](ADR-009-immutable-runs-and-migrations.md)

## Context

The baseline produces `EXCEPTION` and `INSUFFICIENT_EVIDENCE` decisions, and
somebody has to work through them. Until now the product had nothing to say
about that: the API served conclusions and the interface displayed them, and
what happened next happened in a spreadsheet.

The obvious feature is a Resolve button. It is also the one feature that would
undo the rest of the system. A conclusion a person can edit is not replayable, a
status that can be set by a click is not derived from evidence, and an audit
trail whose most interesting rows were written by a UI is not an audit trail.

The harder truth is that a click cannot resolve anything even in principle. A
settlement line is resolved when the records it cites are present and every
required invariant over them holds. If those records are absent, the only thing
that changes that is the records arriving. A button that set the status to
`RESOLVED` would be asserting something about the world on no evidence, which is
precisely the failure mode this project exists to make impossible.

## Decision

**A review event is an append-only operational annotation, stored beside a
decision and never inside it.**

### 1. Four actions, and no fifth

`ACKNOWLEDGED`, `REQUEST_EVIDENCE`, `ESCALATED`, `CLOSED_WITHOUT_OVERRIDE`.

There is no approve, no resolve and no override. The last action is named the
way it is because the name is the guarantee: an item can leave the working
queue, and the line it points at is still whatever the baseline found it to be.
The API and the interface both say so, in the same sentence, carried in every
response as `baseline_unchanged_note`.

The request shape carries no status field. An override is unexpressible rather
than refused, which is a stronger property: there is no code path that rejects
it, because there is no way to ask.

### 2. State is derived, never stored

There is no current-status column on the review table. The workflow state is
folded from the event history every time it is served, because a stored status
and an event log can disagree and then somebody has to decide which one is true.

Ordering comes from the sequence the database assigns on insert. Not from
timestamps: two events recorded in the same millisecond still have an order, and
a clock correction cannot reorder a history.

### 3. Bound to one conclusion

Every command echoes back the `decision_fingerprint` the server served with the
item, which is a digest of the whole decision rather than of its identifier. A
command carrying another decision's fingerprint, or a stale one, is refused
before anything is written. Decisions are immutable, so this is not a
change detector: what it catches is a reviewer acting on a different item from
the one they were looking at.

Idempotency is on a caller-supplied key. The same command under the same key
returns the original event; a different command under a used key is refused,
because answering with the first event would tell a caller its second action had
been recorded.

### 4. Append-only in the database, not by convention

`review_events` has the same UPDATE and DELETE triggers as the four tables
before it. The asymmetry it would otherwise create is the sharpest reason for
them: an editable workflow history sitting beside an immutable decision would
let somebody rewrite what was known and when, while the thing it was known about
stayed fixed.

### 5. No reviewer identity

This application has no authentication. There is therefore nobody to attribute
an event to, and no actor column exists. A field filled with a name typed into a
box, or with a constant like "operator", would look like accountability and
provide none, and it would be read as an audit trail by the first person who
needed one.

This is a limitation and it is recorded as one. It is also why this is a
workflow record and not an accountability record, and why nothing in it should
be relied on to answer "who decided this".

### 6. Its own contract version

`REVIEW_CONTRACT_VERSION` starts at 1.0.0. The domain schema version does not
move. Nothing in a decision refers to a review event, the baseline would produce
identical output if this package did not exist, and bumping the domain contract
would tell every reader of a stored decision that the meaning of their record
had changed when it had not.

## Consequences

- **Actual manual resolution is not built, and cannot be built this way.** A
  line becomes resolved when a source record supporting it is imported and
  reconciled into a new run. Doing that from human judgement would need a new
  auditable source-record type carrying who asserted what, on what authority,
  with what external verification, and that record would then be evidence like
  any other and be verified like any other. It would never be a button.
- A closed item stays in the queue's `total` and leaves its `open_total`.
  Closing removes work, not findings.
- Reopening is possible: an event after a close moves the state again. Closing
  says no further work is planned, not that nothing may follow, and the whole
  history stays visible either way.
- The review API lives under `/v1/review`, not under `/v1/reconciliation`. The
  claim that no reconciliation route changes anything stays exactly as true and
  exactly as checkable as it was.
- Review events are scoped to one recorded run. A new run over new facts starts
  with an empty queue, because its decisions are new conclusions and the
  annotations on the old ones were about the old ones.
