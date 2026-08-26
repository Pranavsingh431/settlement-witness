# Backend API

The import and reconciliation API. Examples below are real responses, taken from
a database holding the example documents in `data/fixtures/ingestion/`.

## Before anything else: this is a local backend

**There is no authentication and no multi-tenancy.** It assumes one merchant's
data and one trusted operator, and it must not be exposed to a network where
either assumption fails.

That is a statement of fact, not a plan. Adding a token check without a tenancy
model would look like security and provide none, so nothing has been added.

**There is no endpoint that changes anything already stored.** Every route is a
`GET` apart from the two that create something: an import receipt and a
reconciliation run. Facts, receipts, runs and decisions are all append-only in
the database, and the API adds no exception.

**There is no endpoint that changes a stored decision.** Every reconciliation
route is a `GET`, apart from the one that creates a run. Human override is a
real need and it is deferred deliberately: the contract rests on conclusions
being immutable and replayable, and a mutable resolve endpoint would end that.
A test asserts no other verb exists.

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
  "parser_version": "3.0.0",
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
| `record_type=BANK_TRANSACTION`, which has no CSV schema | 422 |
| Body or document over `SW_MAX_UPLOAD_BYTES` | 413 |

```json
{
  "detail": {
    "error": "unsupported_record_type",
    "detail": "BANK_TRANSACTION is a source record type this contract defines and this parser has no CSV schema for; supported types are ['PAYMENT_EVENT', 'PAYOUT', 'SETTLEMENT_LINE']"
  }
}
```

### The size limit

`SW_MAX_UPLOAD_BYTES` defaults to 8 MiB and is enforced twice. A request whose
declared `Content-Length` cannot hold an allowed document is turned away before
the server reads it. The document itself is then read in 64 KiB pieces and
abandoned one piece past the limit, so an oversized upload is never held whole.

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
  "parser_version": "3.0.0",
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
      "verified_evidence_count": 3
    }
  ]
}
```

A citation and whether it resolved are joined into one object, because they are
the same fact about the same record and making a caller line up two parallel
arrays invites mistakes.

## `GET /v1/reconciliation/runs/{run_id}/decisions/{decision_id}`

One decision, in the same shape as it appears in the run detail.

The decision is rebuilt through the domain model on the way out, so a row that
no longer satisfies the contract fails here rather than being served as though
it did.

404 if either the run or the decision is unknown. The run is checked first, so
the message names the right thing.

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
| 404 | Unknown run, decision or import receipt |
| 409 | Nothing to reconcile |
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
line ID, matching how the baseline emits them, and runs by created-at then run
ID. The persisted run agrees with what the CLI prints for the same snapshot, and
a test compares them.
