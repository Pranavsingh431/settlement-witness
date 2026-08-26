# Backend API

The reconciliation run API. Examples below are real responses, taken from a
database holding the example documents in `data/fixtures/ingestion/`.

## Before anything else: this is a local backend

**There is no authentication and no multi-tenancy.** It assumes one merchant's
data and one trusted operator, and it must not be exposed to a network where
either assumption fails.

That is a statement of fact, not a plan. Adding a token check without a tenancy
model would look like security and provide none, so nothing has been added.

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

The internal run key is not published either. It is an idempotency identity, and
publishing it would invite callers to depend on how it is computed.

## Running it

```bash
make db-setup
make import-fixtures
make api          # http://127.0.0.1:8000
```

The database is migrated to head before the server binds. A service that started
against an out of date schema would fail on its first real request rather than
on start, which is the harder failure to diagnose.

## `GET /health`

Unchanged from Phase 0.

```json
{ "status": "ok", "version": "0.0.0", "environment": "local" }
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

```json
{ "error": "not_found", "detail": "no run with id 'nope'" }
```

A code and a sentence. No stack trace, no SQL, and nothing about the shape of
the database. A test asserts that error bodies contain none of those.

| Status | When |
| --- | --- |
| 404 | Unknown run or decision |
| 409 | Nothing to reconcile |
| 422 | Invalid parameter, or a contract rule refused something |

A `ValueError` from the domain becomes a 422 rather than a 500. It means a rule
refused something, which is a problem with the request or the stored data, not a
crash. The message is the rule's own, written for people.

## Determinism

Two identical calls return identical bytes. Decisions are ordered by settlement
line ID, matching how the baseline emits them, and runs by created-at then run
ID. The persisted run agrees with what the CLI prints for the same snapshot, and
a test compares them.
