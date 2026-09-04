# Backend API

The import, reconciliation, bank finality and human review API. Examples below are real
responses, taken from a database holding the example documents in
`data/fixtures/ingestion/`.

## Before anything else: this is a single-workspace demo backend

**There is no authentication and no multi-tenancy.** It assumes one workspace
and one trusted operator. The submitted Vercel preview is a shared synthetic
demo, not a merchant-data service: anyone with its public URL can use its write
routes, so do not upload merchant data there.

That is a statement of fact, not a plan. Adding a token check without a tenancy
model would look like security and provide none, so nothing has been added.

It follows that a review event records **no reviewer**. There is nobody to
attribute it to. The review API is a workflow record and not an accountability
one, and nothing in it answers "who decided this".

**There is no endpoint that changes anything already stored.** Every route is a
`GET` apart from the four that create something: an import receipt, a
reconciliation run, a bank finality audit and a human review event. The public
Track 04 batch endpoint is a `GET` that uses a fresh temporary database and
does not create anything in the application workspace. Facts,
receipts, runs, decisions, bank finality audits and review events are all
append-only in the database, and the API adds no exception.

**A settlement decision is not a claim that money arrived.** `RESOLVED` means
the provider's own records agree with each other and with the invariants over
them. Whether a bank shows the payout arriving is a separate conclusion from
separate evidence, served under `/v1/bank-finality`, and a line can be
`RESOLVED` with no bank evidence at all. The two vocabularies share no value, so
neither can be rendered as the other. See
[ADR-016](adr/ADR-016-settlement-agreement-is-not-bank-finality.md).

**There is no endpoint that changes a stored decision.** Every reconciliation
route is a `GET`, apart from the one that creates a run, and a test asserts no
other verb exists there.

The review API appends human workflow events *beside* a decision and cannot
alter one. There is no field in its command that could carry a status, no code
path that writes to the decision tables, and the table it does write to refuses
UPDATE and DELETE at the database. Closing a review does not resolve a line: the
line keeps the `EXCEPTION` or `INSUFFICIENT_EVIDENCE` the baseline gave it, and
every review response says so. Actual resolution would mean a source record
supporting the line, imported and reconciled into a new run. See
[ADR-015](adr/ADR-015-review-events-annotate-they-do-not-decide.md).

## What responses contain, and what they do not

A response carries evidence *references* and the invariant certificate. A
citation names a source record and its payload hash, which is what makes a
conclusion checkable by anyone holding the same facts.

It does not carry the canonical payloads behind those citations. Those are
merchant records, and an endpoint that exists to explain a conclusion has no
reason to serve them. A test asserts no response body contains one.

The import endpoints follow the same rule. A receipt explains what happened to a
document. It does not return the uploaded bytes, a canonical payload, a header
row or a single CSV cell, and tests assert that none of those appear in any
import response.

The internal run key is not published either. It is an idempotency identity, and
publishing it would invite callers to depend on how it is computed.

## Running it

```bash
make db-setup
make api          # http://127.0.0.1:8000
```

The database is migrated to head before the server binds. A service that started
against an out of date schema would fail on its first real request rather than
on start, which is the harder failure to diagnose.

Then load the example documents. Either front door works and both write the same
facts, which a test asserts:

```bash
make import-fixtures        # through the command line, into $(DB)
```

```bash
make import-fixtures-http   # through the running API, then reconciles
```

The HTTP target posts all three documents, lists the receipts and creates a run:

```text
==> Importing the three example documents through http://127.0.0.1:8000
  PAYMENT_EVENT    ACCEPTED   rows=5 accepted=5 receipt=6aaba5817420433981506f01de3e0a7d
  SETTLEMENT_LINE  ACCEPTED   rows=3 accepted=3 receipt=ae72fb55339e4b98be0fa4bafad9eefc
  PAYOUT           ACCEPTED   rows=2 accepted=2 receipt=111e5f1551bb4aca8a90cdb448e698ad
==> Reconciling what was imported
  run fd0c9443bb7e4e5fb4eee88a79b6dc74  facts=10 decisions=3
```

One document by hand:

```bash
curl --request POST http://127.0.0.1:8000/v1/imports \
  --form 'file=@data/fixtures/ingestion/payment_events.csv;type=text/csv' \
  --form 'source_system=PSP_API' \
  --form 'record_type=PAYMENT_EVENT'
```

## `GET /v1/demo/batch`

Run the public Track 04 demonstration. It accepts no input and never reads a
reviewer's file. Each call generates the committed 59-scenario synthetic corpus,
imports it through the ordinary strict parser into a fresh temporary database,
reconciles it through the ordinary deterministic baseline, evaluates it against
the independent manifest, and removes the temporary database before returning.

The response reports source-record and decision counts, elapsed processing time,
throughput, the operational auto-match rate, every exception class, its owning
finance-operations lane, next action and required proof, and the strict contract
measures. It contains no generic `accuracy` field. The reported
numbers are measurements on a generated regression/shadow corpus, **not**
reconciliation accuracy, production accuracy or evidence of real-merchant
performance. Repeating this `GET` leaves no facts, receipts, runs, bank audits
or review events in the shared application database.

## `GET /health`

Unchanged from Phase 0.

```json
{ "status": "ok", "version": "0.0.0", "environment": "local" }
```

## `POST /v1/imports`

Imports one CSV document as one record type from one source system, and records
a receipt.

`source_system` and `record_type` are declared by the caller and are never taken
from the file. A document read as the wrong record type fails loudly on its
headers. A document read as the wrong source system would import cleanly and be
wrong, which is why guessing is not offered.

The uploaded file name is used as `document_name` and nothing else. It is a
label for people, never an identifier: directory components and control
characters are stripped from it, it is shortened to 200 characters, and a name
with nothing usable left becomes `unnamed-upload.csv`.

**Every document the service reads returns 201.** That includes one that was
rejected and one that was an exact replay. The receipt is the created resource,
not the acceptance of the document, so `outcome` is the only field that says
what happened to it. See
[ADR-010](adr/ADR-010-import-receipts-are-the-created-resource.md).

| Field | Meaning |
| --- | --- |
| `outcome` | `ACCEPTED`, `DUPLICATE_NO_OP`, `REJECTED_CONFLICT` or `REJECTED_INVALID` |
| `row_count` | Rows examined, which equals the length of `row_outcomes` |
| `accepted_count` | Rows stored |
| `duplicate_count` | Rows already stored with the same payload |
| `conflict_count` | Rows contradicting a stored fact |
| `rejected_count` | Rows that could not be read |
| `not_applied_count` | Readable rows not stored, because the document was refused whole |
| `wrote_facts` | Derived from `accepted_count`, and validated against it |

The counts always add up to `row_count` and always agree with `row_outcomes`. A
response whose summary disagreed with its own rows is refused rather than
served.

An accepted document:

```json
{
  "receipt_id": "5895047e27a746f2b978775f86d54553",
  "document_hash": "2858d7ec1af5b652e3e9c7cac6c766a56023f6e97b08e0a9509305a8f8ec2618",
  "document_name": "payment_events.csv",
  "source_system": "PSP_API",
  "source_record_type": "PAYMENT_EVENT",
  "parser_version": "3.1.0",
  "received_at": "2026-08-26T11:42:23.299635Z",
  "outcome": "ACCEPTED",
  "row_count": 5,
  "accepted_count": 5,
  "duplicate_count": 0,
  "conflict_count": 0,
  "rejected_count": 0,
  "not_applied_count": 0,
  "wrote_facts": true,
  "failure_detail": null,
  "row_outcomes": [
    {
      "row_number": 2,
      "outcome": "ACCEPTED",
      "source_record_id": "2858d7ec...:PSP_API:PAYMENT_EVENT:2",
      "code": null,
      "detail": null
    }
  ]
}
```

A refused one, also 201. One row was readable and is reported as `NOT_APPLIED`
rather than accepted, because nothing was written and a receipt may not claim a
fact that does not exist:

```json
{
  "outcome": "REJECTED_INVALID",
  "row_count": 3,
  "accepted_count": 0,
  "rejected_count": 2,
  "not_applied_count": 1,
  "wrote_facts": false,
  "failure_detail": "2 row(s) could not be read",
  "row_outcomes": [
    { "row_number": 2, "outcome": "NOT_APPLIED", "code": null, "detail": null },
    {
      "row_number": 3,
      "outcome": "REJECTED",
      "source_record_id": null,
      "code": "INVALID_ENUM",
      "detail": "event_type must be one of ['CAPTURE', 'CHARGEBACK', 'REFUND', 'REVERSAL'], got 'NOT_A_REAL_TYPE'"
    },
    {
      "row_number": 4,
      "outcome": "REJECTED",
      "source_record_id": null,
      "code": "MISSING_VALUE",
      "detail": "amount_minor is required and was empty"
    }
  ]
}
```

A row outcome names the column and the rule it broke. It never repeats the cell
that broke it.

### What is refused before the service sees it

These leave no receipt, because no import was attempted.

| Request | Status |
| --- | --- |
| Missing `file`, `source_system` or `record_type` | 422 |
| A value that is not a member of its enum | 422 |
| Body or document over `SW_MAX_UPLOAD_BYTES` | 413 |

Every record type the contract defines now has a CSV schema, so nothing is
refused for want of one. The refusal below remains for a record type added to
the contract without a layout, so such a type fails as a clear 422 rather than
as a rejected receipt blaming the caller's file:

```json
{
  "detail": {
    "error": "unsupported_record_type",
    "detail": "BANK_TRANSACTION is a source record type this contract defines and this parser has no CSV schema for; supported types are ['PAYMENT_EVENT', 'PAYOUT', 'SETTLEMENT_LINE']"
  }
}
```

### The size limits

There are two, and they are different numbers doing different jobs.

**The request budget** bounds the whole multipart body at `SW_MAX_UPLOAD_BYTES`
plus 8 KiB for the envelope. It is enforced at the ASGI layer, before anything
parses the request, by counting the bytes that actually arrive. `Content-Length`
is not what decides: a request that declares nothing, or declares a false
figure, is counted the same way as an honest one. A body past the budget is
refused as `request_too_large` after at most the budget plus one chunk has been
read, so the multipart parser never sees it and it is never spooled.

An honest oversized `Content-Length` is still turned away without reading
anything. That is an optimisation on top of the count, not the check itself.

**The file limit** bounds the document inside at `SW_MAX_UPLOAD_BYTES` exactly.
The budget has to be the larger of the two, because a multipart body carries
boundaries and part headers as well as the file, and a document of exactly the
permitted size has to be sendable. A document in the gap between the two passes
the budget and is refused here as `document_too_large`, read in 64 KiB pieces
and abandoned one piece past the limit.

Neither refusal reaches the import service, writes a fact, or leaves a receipt.
Both say so in the same words: `no import was processed and no receipt was
written`. Neither claims that nothing was received, because by the time an
absent or false length is caught, some of the body has been.

## `GET /v1/imports`

The import history, newest attempt first.

Ordered by the database assigned sequence descending, which is the order the
attempts were made in, reversed. That sequence is unique, so a page boundary
lands in the same place on every call without needing a tie-breaker. Ordering by
`received_at` would need one, because two attempts can share a timestamp.

`limit` (1 to 100, default 20) and `offset` page the list. `outcome`,
`source_system` and `record_type` filter it. `total` counts the receipts matching
the filters rather than the whole history, and `filtered` says whether any filter
was applied, so the two cannot be confused.

```json
{
  "total": 5,
  "limit": 2,
  "offset": 0,
  "filtered": false,
  "receipts": [
    {
      "receipt_id": "9b9cd9175fac41dab66a2eda6091ecfa",
      "document_name": "payouts.csv",
      "source_record_type": "PAYOUT",
      "outcome": "DUPLICATE_NO_OP",
      "row_count": 2,
      "accepted_count": 0,
      "wrote_facts": false
    }
  ]
}
```

## `GET /v1/imports/{receipt_id}`

One receipt in full, in the same shape the upload returned. `receipt_id` is the
public identity; the database sequence is not published.

404 when there is no such receipt:

```json
{ "detail": { "error": "not_found", "detail": "no import receipt with id 'nope'" } }
```


## `POST /v1/reconciliation/runs`

Runs the deterministic baseline over every accepted source fact and records the
result.

**Idempotent.** Reconciling the same facts again under the same rule versions
returns the run already recorded, rather than writing a second row describing
the same conclusion.

| Status | Meaning |
| --- | --- |
| 201 | A new run was recorded |
| 200 | An identical run already existed and was returned |
| 409 | The store holds no accepted facts to reconcile |

The distinction between 201 and 200 matters: a caller retrying after a timeout
needs to know whether it created something.

```json
{
  "run_id": "8b5831ee02874efe98daf6d3c1e75bc4",
  "snapshot_fingerprint": "7092df18a31c4b9386a3120e3134d6867f8728c1674a367bc73018f887cb84dc",
  "baseline_version": "1.0.0",
  "domain_schema_version": "5.0.0",
  "parser_version": "3.1.0",
  "created_at": "2026-08-26T05:53:51.035407Z",
  "as_of": "2026-08-26T05:53:44.863742Z",
  "fact_count": 10,
  "settlement_line_count": 3,
  "decision_count": 3,
  "status_counts": {
    "EXCEPTION": 2, "INSUFFICIENT_EVIDENCE": 0, "PENDING": 0, "RESOLVED": 1
  },
  "exception_counts": { "PARTIAL_REFUND": 1, "UNSUPPORTED_STATE": 1 }
}
```

Every rule version is on the run. A conclusion without the rules behind it
cannot be interpreted later, and a changed rule version produces a new run over
the same facts rather than overwriting the old answer.

`as_of` is the snapshot time, from the latest observed fact. `created_at` is
when the run was persisted. They differ on purpose: one describes the state the
decisions are about, the other when someone asked.

## `GET /v1/reconciliation/runs`

Paginated, newest first, ordered by `created_at` descending then `run_id` so a
page boundary lands in the same place on every call.

| Parameter | Default | Bounds |
| --- | --- | --- |
| `limit` | 20 | 1 to 100 |
| `offset` | 0 | 0 or more |

```json
{ "runs": [ { "run_id": "8b5831ee...", "...": "..." } ], "total": 1, "limit": 20, "offset": 0 }
```

`total` is the whole collection, not the page, so a caller can tell how much
more there is. An offset past the end returns an empty page and a 200, not a
404: the collection exists, that page of it is empty.

Out of range values return 422 rather than being silently clamped.

## `GET /v1/reconciliation/runs/{run_id}`

A run and its decisions, ordered by settlement line ID.

| Parameter | Effect |
| --- | --- |
| `status` | Only decisions with this status |
| `exception_code` | Only decisions carrying this code |

Filters narrow the decisions and never the summary counts, which always describe
the whole run. `filtered` says which view you are looking at, so a narrowed list
cannot be mistaken for the complete one. An unknown filter value is a 422, not
an empty list that looks like a result.

```json
{
  "run": { "run_id": "8b5831ee02874efe98daf6d3c1e75bc4", "decision_count": 3, "...": "..." },
  "filtered": false,
  "decisions": [
    {
      "decision_id": "7092df18a31c4b93:line-0002",
      "schema_version": "5.0.0",
      "status": "RESOLVED",
      "subject_settlement_line_id": "line-0002",
      "linked_source_record_ids": [
        "2690c9f0...:PSP_API:PAYOUT:2",
        "2858d7ec...:PSP_API:PAYMENT_EVENT:3",
        "3fe81276...:PSP_API:SETTLEMENT_LINE:3"
      ],
      "linked_event_ids": ["evt-0002"],
      "evidence": [
        {
          "source_record_id": "2690c9f0...:PSP_API:PAYOUT:2",
          "source_system": "PSP_API",
          "payload_hash": "2c1d4bb217febc17c0d4bfb33d869c27abb6fed7ee787943effa408b3d61d596",
          "verification_outcome": "VERIFIED"
        }
      ],
      "invariant_results": [
        { "invariant_id": "INV-001", "outcome": "PASSED",
          "reason_code": null, "expected_minor": null, "observed_minor": null }
      ],
      "exception_codes": [],
      "reason_codes": ["ALL_REQUIRED_INVARIANTS_PASSED"],
      "created_at": "2026-08-24T12:00:00Z",
      "verified_evidence_count": 3,
      "closure_plan": {
        "plan_version": "1.0.0",
        "baseline_status": "RESOLVED",
        "disposition": "NO_ACTION",
        "primary_owner": "NONE",
        "headline": "No finance-ops follow-up is required for this decision.",
        "blocking_codes": [],
        "actions": [],
        "requires_new_run": false,
        "resolution_gate": "Already resolved by the recorded certificate. Later evidence creates a new run; it never edits this one."
      }
    }
  ]
}
```

A citation and whether it resolved are joined into one object, because they are
the same fact about the same record and making a caller line up two parallel
arrays invites mistakes.

## `GET /v1/reconciliation/runs/{run_id}/decisions/{decision_id}`

One decision, in the same shape as it appears in the run detail.

Every `DecisionView` also carries `closure_plan`, a deterministic read model of
that immutable decision. For unresolved lines it contains the primary owner,
all blocking codes, bounded actions, the evidence required for each action,
whether today's contract can verify that evidence, and the new-run closure
gate. A resolved decision carries `NO_ACTION`. The plan has no override status
and writing or reading it changes no stored decision.

The decision is rebuilt through the domain model on the way out, so a row that
no longer satisfies the contract fails here rather than being served as though
it did.

404 if either the run or the decision is unknown. The run is checked first, so
the message names the right thing.

## `GET /v1/review/runs/{run_id}/queue`

The decisions of one recorded run that need a person: `EXCEPTION` and
`INSUFFICIENT_EVIDENCE`, and nothing else. A resolved line is not work, and a
pending one is waiting for information that is expected to arrive.

Query parameters: `limit` (1 to 100, default 20) and `offset`. Ordered by
settlement line ID, which is how the baseline emits decisions. The order does
not move when somebody acts on an item, so a page boundary lands in the same
place on every call.

```json
{
  "run_id": "fd0c9443bb7e4e5fb4eee88a79b6dc74",
  "review_contract_version": "1.0.0",
  "items": [
    {
      "run_id": "fd0c9443bb7e4e5fb4eee88a79b6dc74",
      "decision": { "status": "EXCEPTION", "...": "the same DecisionView as every other endpoint" },
      "decision_fingerprint": "b31c1a2f4d0e5c6a7b8c9d0e1f2a3b4c5d6e7f8091a2b3c4d5e6f7081920a3b4c",
      "workflow_state": "OPEN",
      "baseline_status": "EXCEPTION",
      "baseline_unchanged_note": "A review event records human workflow only. ...",
      "events": []
    }
  ],
  "total": 2,
  "open_total": 2,
  "limit": 20,
  "offset": 0,
  "baseline_unchanged_note": "A review event records human workflow only. ..."
}
```

`total` is the size of the queue, not of the run. `open_total` is how many of
those are not closed. Both are derived from the events, like `workflow_state`
itself, which is folded from the timeline every time it is served rather than
stored.

`baseline_status` repeats `decision.status` deliberately. A client that reads
only the workflow state would otherwise have to go looking for the conclusion,
and the one mistake this endpoint must not make possible is showing a closed
review as though the line were settled.

404 if the run is unknown.

## `GET /v1/review/runs/{run_id}/queue/{decision_id}`

One queue item, in the same shape as it appears in the list.

404 if the run is unknown, if the decision is unknown, **or if the decision is
not one this queue holds**. A resolved line is a 404 here rather than a 200 with
an empty timeline.

## `POST /v1/review/runs/{run_id}/queue/{decision_id}/events`

Append one human workflow event beside a decision.

```json
{
  "action": "REQUEST_EVIDENCE",
  "decision_fingerprint": "b31c1a2f4d0e5c6a7b8c9d0e1f2a3b4c5d6e7f8091a2b3c4d5e6f7081920a3b4c",
  "idempotency_key": "line-0001-request-evidence-0001",
  "note": "need the 3 March bank statement"
}
```

`action` is one of `ACKNOWLEDGED`, `REQUEST_EVIDENCE`, `ESCALATED`,
`CLOSED_WITHOUT_OVERRIDE`. There is no fifth, and there is no field that could
carry a status: an override is unexpressible here rather than refused.

`decision_fingerprint` is the one this API served with the item. Echoing it back
is what stops an action aimed at another conclusion being recorded against this
one.

`idempotency_key` is the caller's own, at least 8 characters. The same key with
the same command returns the original event with status 200 instead of 201. The
same key with a different command is refused with 409 and writes nothing.

It is not a credential and nothing authenticates with it. Any string a caller
can regenerate for the same command works, and one describing the command, as
above, is easier to reason about than a random identifier.

A client should hold the key for as long as the outcome is unknown. A request
can fail after this endpoint has written the row, because the answer can be lost
on the way back, and retrying that command under a new key would append a second
event for one intended action. The key belongs to the command, not to the
attempt: retry unchanged input under the same key and a different command under
a different one.

`note` is optional, at most 500 characters, stored and served as plain text.
Blank is the same as absent.

201 when the event was recorded, 200 when a retry returned the original:

```json
{
  "event": {
    "event_id": "2a4b6c8d0e2f4a6b8c0d2e4f6a8b0c2d",
    "sequence": 1,
    "action": "REQUEST_EVIDENCE",
    "note": "need the 3 March bank statement",
    "recorded_at": "2026-08-27T09:15:00Z",
    "decision_fingerprint": "b31c1a2f..."
  },
  "workflow_state": "WAITING_FOR_EVIDENCE",
  "baseline_status": "EXCEPTION",
  "baseline_unchanged_note": "A review event records human workflow only. ..."
}
```

`sequence` is assigned by the database and is the only thing the timeline is
ordered by. Timestamps are recorded and never sorted on: two events in the same
millisecond still have an order, and a clock correction cannot reorder history.

`baseline_status` is the status after the event, which is the status before it.

There is no reviewer in this payload. See the note at the top of this document.

### What this endpoint refuses

| Status | `error` | When |
| --- | --- | --- |
| 404 | `not_found` | Unknown run, or unknown decision |
| 409 | `not_reviewable` | The decision is `RESOLVED` or `PENDING`, so it is not in this queue |
| 409 | `stale_certificate` | The fingerprint is not this decision's |
| 409 | `idempotency_conflict` | The key was used for a different command |
| 422 | `invalid_request` | Malformed body, unknown action, short key, over-long note |

Every one of them writes nothing.

## `POST /v1/bank-finality/audits`

Audit every payout against the imported bank statement rows.

A payout verifies when exactly one statement row carries its reference, that row
is a credit, and its amount and currency equal the payout's **exactly**. There
is no tolerance band, no rounding, no nearest-amount search, no date window, no
case folding of references and no probable match. One minor unit of difference
is a mismatch.

Idempotent, like a reconciliation run. The same facts under the same bank
finality rules return the audit already recorded, with status 200 rather than
201. Importing a statement later does not change an earlier audit: it makes a
new snapshot, and this endpoint then records a new audit beside the old one.

```json
{
  "audit_id": "a1f0c9443bb7e4e5fb4eee88a79b6dc7",
  "snapshot_fingerprint": "7092df18a31c4b9386a3120e3134d6867f8728c1674a367bc73018f887cb84dc",
  "bank_finality_version": "1.0.0",
  "bank_statement_schema_version": "1.0.0",
  "created_at": "2026-08-26T11:45:00Z",
  "as_of": "2026-08-24T12:00:00Z",
  "fact_count": 11,
  "payout_count": 2,
  "bank_transaction_count": 1,
  "outcome_counts": { "VERIFIED_BANK_CREDIT": 1, "UNLINKABLE_PAYOUT": 1, "...": 0 },
  "verified_payout_count": 1
}
```

`snapshot_fingerprint` is the same digest a reconciliation run over these facts
carries, which is how a run and its audit are put side by side.

`verified_payout_count` is a count and never a rate. A percentage would invite
reading ninety percent as nearly settled, and the ten percent is where a
merchant is missing money.

409 with `no_facts` when there is nothing to audit.

## `GET /v1/bank-finality/audits`

A page of audit summaries, newest first. `limit`, `offset`, and
`snapshot_fingerprint` to narrow to one snapshot. `filtered` says which view a
caller is looking at.

## `GET /v1/bank-finality/audits/{audit_id}`

One audit and its certificates, ordered by payout ID. `outcome` narrows the
certificates and never the summary counts.

```json
{
  "payout_id": "payout-0001",
  "payout_source_record_id": "9c2f1a4b:PSP_API:PAYOUT:2",
  "bank_reference": "UTR2026082100001",
  "outcome": "VERIFIED_BANK_CREDIT",
  "evidence": [
    { "source_record_id": "9c2f1a4b:PSP_API:PAYOUT:2", "source_system": "PSP_API",
      "payload_hash": "...", "verification_outcome": "VERIFIED" },
    { "source_record_id": "3e7d5c1f:PSP_API:BANK_TRANSACTION:2", "source_system": "PSP_API",
      "payload_hash": "...", "verification_outcome": "VERIFIED" }
  ],
  "matched_bank_transaction_ids": ["BANKTXN0001"],
  "expected_amount_minor": 1220500,
  "expected_currency": "INR",
  "observed_amount_minor": 1220500,
  "observed_currency": "INR",
  "observed_direction": "CREDIT",
  "recorded_at": "2026-08-24T12:00:00Z",
  "schema_version": "1.0.0"
}
```

There is no field called `status` and no boolean called `resolved`. The outcome
is one of seven, and none of them is a `DecisionStatus`:

| Outcome | What the records said |
| --- | --- |
| `VERIFIED_BANK_CREDIT` | Exactly one credit carrying this reference, for this exact amount and currency |
| `MISSING_BANK_EVIDENCE` | The payout names a reference and no imported statement row carries it. Not a claim the money failed to arrive: a claim this system has not been shown it arriving |
| `UNLINKABLE_PAYOUT` | The payout carries no bank reference, so no exact association is possible. A gap in the provider record, not a discrepancy |
| `AMBIGUOUS_BANK_EVIDENCE` | Two or more rows carry the reference. Every candidate is cited, because choosing one would invent a fact |
| `BANK_DIRECTION_MISMATCH` | The one row carrying the reference is a debit |
| `BANK_AMOUNT_MISMATCH` | The credit is for a different number of minor units. Any difference |
| `BANK_CURRENCY_MISMATCH` | The credit is in a different currency, so the amounts cannot be compared |

`expected_*` and `observed_*` are filled in only when exactly one row was found,
because a comparison against two rows or none is not a comparison.

Every response carries `settlement_and_finality_are_separate`, which says in one
sentence that these are two conclusions from two kinds of evidence.

## `GET /v1/bank-finality/audits/{audit_id}/payouts/{payout_id}`

One certificate, in the same shape as it appears in the audit detail. 404 if
either the audit or the payout is unknown; the audit is checked first, so the
message names the right thing.

## Errors

Every failure has one shape: a code and a sentence, nested under `detail`.

```json
{ "detail": { "error": "not_found", "detail": "no run with id 'nope'" } }
```

No stack trace, no SQL, nothing about the shape of the database, and nothing
echoed back from the request body. A test asserts that error bodies contain none
of those.

That last part matters on the import endpoint, where the request body is a
document. A malformed request is reported by naming the field and the rule it
broke, never by quoting what was sent:

```json
{
  "detail": {
    "error": "invalid_request",
    "detail": "body.source_system: Input should be 'PSP_API', 'PSP_WEBHOOK', 'BANK_STATEMENT' or 'MERCHANT_LEDGER'"
  }
}
```

| Status | When |
| --- | --- |
| 404 | Unknown run, decision, import receipt, queue item, audit or certificate |
| 409 | Nothing to reconcile or audit, or a review command refused (see above) |
| 413 | Body or uploaded document over `SW_MAX_UPLOAD_BYTES` |
| 422 | Invalid parameter or form field, an unsupported record type, or a contract rule refusing something |

A `ValueError` from the domain becomes a 422 rather than a 500. It means a rule
refused something, which is a problem with the request or the stored data, not a
crash. The message is the rule's own, written for people.

Note that a rejected **document** is not an error. It returns 201 with a receipt
explaining what was wrong with it. Only a request that never reached the import
service appears here. See
[ADR-010](adr/ADR-010-import-receipts-are-the-created-resource.md).

## Determinism

Two identical calls return identical bytes. Decisions are ordered by settlement
line ID, matching how the baseline emits them, runs by created-at then run ID,
review queue items by settlement line ID again, and bank finality certificates
by payout ID. A review timeline is ordered
by the sequence the database assigned. The persisted run agrees with what the CLI prints for the same snapshot, and
a test compares them.
