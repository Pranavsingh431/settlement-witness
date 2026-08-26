# Phase 6: Auditable CSV import HTTP API

- Date: 2026-08-26
- Exit gate: passed. See "Exit gate status".
- Domain contract 5.0.0, parser 3.0.0, baseline 1.0.0, harness 2.0.0, all unchanged

## Scope

Three endpoints over the Phase 2 import service, and a typed read model to serve
receipts from. No domain model, parser, reconciliation rule or published schema
changed. `make schema` was run and produced a byte identical result.

A user can now load payment events, settlement lines and payouts through the
application and then create a run, without touching the command line.

## The endpoints

| Route | Purpose |
| --- | --- |
| `POST /v1/imports` | Import one CSV document, return its receipt |
| `GET /v1/imports` | Paginated, filtered import history, newest attempt first |
| `GET /v1/imports/{receipt_id}` | One receipt in full |

### Every processed upload returns 201

`ACCEPTED`, `DUPLICATE_NO_OP`, `REJECTED_CONFLICT` and `REJECTED_INVALID` all
return 201 with a receipt. The receipt is the created resource, not the
acceptance of the document, and `outcome` is the only field that says what
happened to it.

Returning 422 for a parser rejection would say no resource was created when one
was, and a caller retrying on that basis would write a second receipt for the
same refusal. Returning 200 for a duplicate would say the same about a replay.
The reasoning and the cost of this choice are in
[ADR-010](../adr/ADR-010-import-receipts-are-the-created-resource.md).

The line is not whether the document was good. It is whether an import was
attempted at all:

| Request | Status | Receipt |
| --- | --- | --- |
| Any document the service read | 201 | Written |
| Missing `file`, `source_system` or `record_type` | 422 | None |
| A value that is not a member of its enum | 422 | None |
| `record_type=BANK_TRANSACTION` | 422 | None |
| Body or document over the limit | 413 | None |

`BANK_TRANSACTION` is a source record type the contract defines and the parser
has no schema for. Passed through it would reach the service and become a
`REJECTED_INVALID` receipt saying the document could not be read, sending a
caller to look at a file whose real problem is that the type is not importable.
It is refused at the boundary instead, with a message naming the three types
that are, which is what the CLI already does through its argument choices.

### Reusing the importer rather than reimplementing it

The endpoint bounds the request, names the document, and hands the exact received
bytes to `ImportService.import_document`. It reads no CSV, inspects no headers,
and decides nothing about a document. A second reader of the same file would be a
second set of rules that eventually disagrees with the first.

Four tests hold the two front doors together. The same documents through HTTP and
through the CLI produce facts that are equal field for field including payload
hashes; the same document produces receipts equal in everything but the generated
uuid and the clock; a rejection is rejected identically and the CLI still exits 1;
and a document loaded by the CLI and then posted to the API comes back as
`DUPLICATE_NO_OP`, which only happens if the CLI wrote exactly what the API would
have.

### Bounding the upload

`SW_MAX_UPLOAD_BYTES` defaults to 8 MiB and is enforced in two places.

A request whose declared `Content-Length` cannot hold an allowed document is
refused by middleware before the server reads the body, so an oversized upload is
never spooled to disk. The allowance for multipart framing is a named constant,
so the guard refuses a request only when the document inside it cannot fit.

The document is then read from the upload in 64 KiB pieces and abandoned one
piece past the limit, which is the exact check and covers a client that sends no
`Content-Length` or lies about it. At most the limit plus one chunk is ever held.

Both were verified against the running container, not only in tests: a 9 MB
upload returned 413 and the receipt count did not move.

#### Corrected in Phase 6.1

The second paragraph above is false, and so is the 413 message it describes.

`read_bounded` runs inside the endpoint, and FastAPI reads a multipart body
while resolving an endpoint's arguments. So the parser had already consumed and
spooled the whole upload by the time that check ran. It bounded what the
endpoint held, not what the server accepted. The `Content-Length` middleware was
the only thing that stopped a body early, and it stopped only a body whose
client honestly declared it.

Measured against the Phase 6 code with a 64 KiB limit and a 4 MB body:

```text
no Content-Length (chunked):   413, but 4,194,590 of 4,194,590 bytes delivered
forged Content-Length: 100:    413, but 4,194,590 of 4,194,590 bytes delivered
honest Content-Length:         413, 0 bytes delivered
```

The 413 text also said the body "was not read" and "nothing was read", which was
untrue in the first two cases.

The container check that was cited as verification used curl, which sends an
honest `Content-Length`, so it exercised only the case that already worked.

Phase 6.1 counts the body at the ASGI layer before anything parses it. See
[phase-6-1.md](phase-6-1.md) and the corrected ADR-010.

### Naming the document

The uploaded file name is used as `document_name` and nothing else. It is a label
for people and explicitly not an identifier, which is what the import service
already documented.

It is also client supplied text that gets stored and later printed, so it is
reduced before it is used: directory components dropped on both separators, so
`../../etc/passwd` becomes `passwd`; non-printable characters dropped, so a
terminal escape cannot rewrite a screen that prints the receipt; shortened to the
200 characters the column holds, rather than being refused by the database after
the import already happened.

A name with nothing usable left becomes `unnamed-upload.csv`. A constant rather
than something derived from the content, because deriving it from the hash would
make two labels differ exactly when the identity differs, which is what an
identifier looks like, and someone would eventually rely on it.

A file part with no `filename` at all is read by Starlette as a plain form field
and rejected before any of this runs, so that case is covered by a direct test
rather than an HTTP one.

### The receipt cannot contradict itself

`ImportReceiptView` validates that its six counts equal the outcomes in
`row_outcomes`, and that `wrote_facts` equals `accepted_count > 0`. A summary
that can drift from the list beneath it is worse than no summary, because a
reader checks the cheap number and not the long list.

`wrote_facts` is not a success flag. A `DUPLICATE_NO_OP` import wrote no facts
and is the correct result. It is derived and checked, so it cannot become a
second opinion about what happened.

`not_applied_count` was added because Phase 2 has a fifth row outcome:
`NOT_APPLIED`, a readable row that was not stored because the document it
belonged to was refused whole. Without it the counts do not add up to
`row_count`, and the validator above would refuse every rejected receipt.

## Changed files

| File | Change |
| --- | --- |
| `backend/app/ingestion/receipts.py` | New. The receipt models, moved out of the service so storage can rebuild one without importing the service that writes one. Re-exported from `service.py`, so no existing import moved |
| `backend/app/api/imports.py` | New. The three endpoints |
| `backend/app/api/uploads.py` | New. Bounded reading and the safe document name |
| `backend/app/api/schemas.py` | `RowOutcomeView`, `ImportReceiptView`, `ImportReceiptPage`, `ErrorEnvelope` |
| `backend/app/api/dependencies.py` | Settings read from the application, so a test can build an app with a different limit |
| `backend/app/storage/repository.py` | `find`, `page` and `count` on the receipt repository, with one shared filter builder |
| `backend/app/config.py` | `max_upload_bytes` |
| `backend/app/main.py` | The imports router, the body-size middleware, and a validation handler that does not echo the request |
| `backend/app/api/reconciliation.py` | Declares `ErrorEnvelope` instead of `ErrorResponse`. Documentation only, no response changed |
| `backend/pyproject.toml` | `python-multipart`, which FastAPI requires for form parsing |
| `Makefile`, `.env.example`, `README.md`, `docs/api.md` | The setting, the HTTP import target, and the endpoint documentation |

## Two corrections to existing documentation

Both are documentation accuracy rather than behaviour changes, found while
writing the new endpoints.

1. **The error shape was documented wrongly.** `docs/api.md` and the OpenAPI
   `responses` both described a flat `{"error": ..., "detail": ...}` body.
   Starlette nests the detail of an `HTTPException`, so every error this API has
   ever returned was `{"detail": {"error": ..., "detail": ...}}`. Added
   `ErrorEnvelope` and corrected both. No response body changed.

2. **FastAPI's default validation handler echoes the offending input.** On an
   endpoint whose body is an uploaded document that is a way for document content
   to reach an error body. Replaced with a handler that returns the field and the
   rule it broke and nothing that was sent.

## Commands run

| Command | Exit | Observed |
| --- | --- | --- |
| `git diff --check` | 0 | No whitespace errors |
| `make ci` | 0 | All nine core checks passed |
| `uv run ruff format --check .` | 0 | `96 files already formatted` |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy` | 0 | `Success: no issues found in 93 source files` |
| `uv run pytest` | 0 | `959 passed`, `Total coverage: 100.00%` |
| `make schema` | 0 | Byte identical, no domain model touched |
| Migration and adoption suite | 0 | 95 tests, unchanged |
| `make verify-containers` | 0 | Both images build, serve and run unprivileged |
| Import through the running container | 0 | `ACCEPTED`, 5 rows, facts written |
| 9 MB upload through the running container | 0 | 413, receipt count unchanged |
| `make import-fixtures-http` | 0 | Three documents imported, history listed, run created with 10 facts and 3 decisions |

## Tests

959 total, up from 842. 117 added.

| File | Tests | Covers |
| --- | --- | --- |
| `tests/api/test_imports.py` | 73 | Accepting, every outcome that still writes a receipt, requests that never reach the service, document naming, listing, reading, what responses never contain, and agreement with the CLI |
| `tests/api/test_upload_handling.py` | 27 | The name reducer and the receipt view's self-consistency, including cases HTTP cannot produce |
| `tests/storage/test_repository.py` | +14 | Typed reconstruction, revalidation of stored enums and JSON, paging, and filters narrowing the page and the count together |
| `tests/test_config.py` | +2 | The upload limit and its lower bound |

Four tests assert that no import response contains a raw CSV line, a header row,
a canonical payload, a payload hash, or an amount from the document. One asserts
across the OpenAPI schema that the import surface has only `get` and `post`, so a
mutating route cannot arrive unnoticed. Three assert that `PUT`, `PATCH` and
`DELETE` on a receipt are 405.

The malformed-request tests all assert the receipt count afterwards, so "no
receipt where the service was never reached" is checked rather than assumed.

## Limitations

1. **A caller reading only the status code will misread a rejection.** 201 for a
   refused document is a deliberate trade recorded in ADR-010, and this is its
   cost. The endpoint documentation and the OpenAPI 201 description both say the
   receipt is the created resource and not the acceptance of the document.
2. **The multipart overhead allowance is approximate.** The request budget is the
   file limit plus 8 KiB, so a body between the two passes the budget and is then
   refused exactly by the file check. The document limit itself is exact.
3. **One document per request.** No batch upload, because a batch would need its
   own atomicity rule across documents and Phase 2's rule is per document.
4. **No upload progress or resumption.** A large document is one request that
   either completes or does not.
5. **Import is synchronous.** The request holds until the document is parsed and
   committed. At the sizes this limit allows that is fast, and an async job would
   need a job store and a status endpoint that nothing yet needs.
6. **Still no authentication.** Unchanged from Phase 5 and stated in the README
   and the API documentation. The import endpoint writes, which makes this more
   consequential than it was when every route was a read, and it is the reason
   this backend must not be exposed to an untrusted network.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| Upload creates facts and a receipt, and a run then sees them | Passed | Upload, then `POST /runs` with 10 facts |
| Duplicate replay returns a new `DUPLICATE_NO_OP` receipt, writes no facts | Passed | New receipt id, fact count unchanged |
| Conflicting upload returns `REJECTED_CONFLICT`, writes no facts | Passed | Using the existing conflicting fixture |
| Invalid document returns `REJECTED_INVALID`, writes no facts | Passed | Bad headers, bad encoding, blank, and mixed rows |
| Each parser-supported record type imports through HTTP | Passed | Parametrized over `SUPPORTED_RECORD_TYPES` |
| Unsupported type, missing fields, wrong media type, invalid enums, oversized body | Passed | Correct 4xx and receipt count zero for each |
| Bounded chunked read, one configured limit, 413 before parsing | Overclaimed | True only for an honest `Content-Length`. Corrected in Phase 6.1 |
| Safe deterministic document-name fallback | Passed | Traversal, escapes, length, and four unusable names |
| Counts exactly equal the returned row outcomes | Passed | Validated on the model, tested per count |
| No success boolean contradicting the outcome | Passed | `wrote_facts` derived and validated against `accepted_count` |
| List pagination, filtering, ordering, filtered totals | Passed | Bounds, offsets, three filters and combinations |
| Detail 404 with the established error shape | Passed | Nested `{"detail": {"error": ...}}` |
| Deterministic bytes, no payload or CSV leakage | Passed | Repeat reads byte identical, four leakage tests |
| API and CLI produce equivalent facts and receipts | Passed | Four equivalence tests including cross-front-door duplicate detection |
| The API mutates nothing | Passed | Reads leave the history identical, 405 on three verbs, OpenAPI surface asserted |
| Setting in typed settings, `.env.example`, README, API docs | Passed | All four |
| Make target for importing the fixtures over HTTP | Passed | `make import-fixtures-http`, run against a live server |
| ADR-010 written | Passed | With the cost of the decision stated, not only the case for it |
| Phase 0 to 5.2 tests remain green | Passed | 959 passed, none skipped |
| `make ci` | Passed | Exit 0 |
| `make schema` | Met | Run, byte identical |
| `make verify-containers` | Passed | Exit 0 |
| 100 percent backend coverage | Passed | `Total coverage: 100.00%` |
