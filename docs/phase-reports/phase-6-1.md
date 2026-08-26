# Phase 6.1: Enforce the upload limit before multipart parsing

- Date: 2026-08-26
- Exit gate: passed. See "Exit gate status".
- Domain contract 5.0.0, parser 3.0.0, baseline 1.0.0, harness 2.0.0, all unchanged

## Scope

Where the upload limit is enforced, and the wording of the two refusals. No
domain model, parser, reconciliation rule, published schema or version changed.
`make schema` was run and produced a byte identical result. The receipt
contract, the response sanitising and the normal upload path are untouched.

## The defect

Phase 6 said an oversized upload was "refused before it is parsed" and that "at
most the limit plus one chunk is ever held". Both were true only when the client
declared an honest `Content-Length`.

`read_bounded` runs inside the endpoint, and FastAPI reads a multipart body while
resolving that endpoint's arguments. The parser had therefore already consumed
and spooled the whole upload before the check ran. It bounded what the endpoint
held, not what the server accepted.

Measured against the Phase 6 code with a 64 KiB limit and a 4 MB body:

```text
no Content-Length (chunked):   413, but 4,194,590 of 4,194,590 bytes delivered
forged Content-Length: 100:    413, but 4,194,590 of 4,194,590 bytes delivered
honest Content-Length:         413, 0 bytes delivered
```

Two smaller things followed from it. The 413 text claimed the body "was not
read" and that "nothing was read", which was untrue in the first two cases. And
the container check offered as verification used curl, which always sends an
honest length, so it only ever exercised the case that already worked.

## The fix

### Count the body at the ASGI layer

`app/api/body_limit.py` holds a pure ASGI middleware that counts `http.request`
chunks as they arrive and refuses the request once the total passes the budget.
It runs before the application is called, so the multipart parser never sees a
byte of a body that does not fit.

`Content-Length` is not what decides. It is checked first as an optimisation,
because a client that honestly declares too much can be turned away without
transferring anything, but nothing depends on the header being present or true.

The same measurement after the change:

```text
no Content-Length (chunked):   413, 131,072 of 4,194,590 bytes delivered
forged Content-Length: 100:    413, 131,072 of 4,194,590 bytes delivered
honest Content-Length:         413, 0 bytes delivered
```

131,072 is two 64 KiB chunks against a 73,728 byte budget: the first fits, the
second crosses, and reading stops there.

### Why it buffers rather than aborting mid-stream

The obvious alternative is to raise from `receive` while the parser is reading,
which would avoid holding the body at all. It does not work. Starlette catches
anything raised while it reads the stream and answers `400 There was an error
parsing the body`, so the caller gets the wrong status and a description of the
wrong problem. Verified directly rather than assumed:

```text
raising BodyTooLarge from receive
  -> 400 {"detail":"There was an error parsing the body"}
```

So the body is held while it is counted and handed on only once it is known to
fit. That bounds one request at the budget plus the chunk that crossed it, and
in exchange a permitted upload is buffered rather than streamed. At the sizes
this budget allows, that is the cheaper half of the trade, and it is the shape
the phase brief explicitly permits.

### Scoped to the upload route

The limiter matches POST to `/v1/imports` and passes everything else straight
through, body and all. Health, the run endpoints and the receipt reads keep
exactly the behaviour they had, including their own bodies not being buffered.

### Two limits, said plainly

| Limit | Value | Enforced | Refusal |
| --- | --- | --- | --- |
| Request budget | `max_upload_bytes` + 8 KiB | ASGI layer, by counting arriving bytes | `request_too_large` |
| File limit | `max_upload_bytes` exactly | In the endpoint, reading the upload in 64 KiB pieces | `document_too_large` |

The budget has to be the larger of the two, because a multipart body carries
boundaries and part headers as well as the file, and a document of exactly the
permitted size has to be sendable. A document in the gap passes the budget and is
refused by the file check, which is tested from both sides.

Both messages now end `no import was processed and no receipt was written`.
Neither claims nothing was received, because by the time an absent or false
length is caught, some of the body has been read. A message saying otherwise
would be the same kind of false boundary claim in smaller print.

## Changed files

| File | Change |
| --- | --- |
| `backend/app/api/body_limit.py` | New. The ASGI limiter, its route rule, and the replay stream |
| `backend/app/main.py` | Replaces the `Content-Length` middleware with the limiter, scoped to the upload route |
| `backend/app/api/imports.py` | Publishes `IMPORTS_PATH` so the limiter can be scoped to it |
| `backend/app/api/uploads.py` | Corrected 413 wording, and says which of the two checks it is |
| `backend/app/config.py` | Documents that two limits derive from the one setting |
| `backend/tests/api/test_body_limit.py` | New. 31 tests driven through the ASGI interface |
| `docs/adr/ADR-010-...md` | A section on the two limits and why the enforcement point matters |
| `docs/api.md`, `README.md` | The two limits, and what `Content-Length` does and does not do |
| `docs/phase-reports/phase-6.md` | The false claim marked and corrected in place |

## Commands run

| Command | Exit | Observed |
| --- | --- | --- |
| `git diff --check` | 0 | No whitespace errors |
| `make ci` | 0 | All nine core checks passed |
| `uv run mypy` | 0 | `Success: no issues found in 95 source files` |
| `uv run pytest` | 0 | `990 passed`, `Total coverage: 100.00%` |
| `make schema` | 0 | Byte identical, no domain model touched |
| `make verify-containers` | 0 | Both images build, serve and run unprivileged |
| Live server, chunked, no `Content-Length`, 4 MB body, 64 KiB limit | 0 | 413 after 688 KB sent, receipt count 0 |
| Live server, chunked, document that fits | 0 | 201 `ACCEPTED`, one fact written |
| Container, chunked, no `Content-Length`, 32 MB body | 0 | 413 at the 8 MiB budget, receipt count 0 |
| Container, ordinary upload | 0 | 201 `ACCEPTED`, two rows written |
| New tests against the Phase 6 code | 1 | Five fail, which is the point of them |

## Tests

990 total, up from 959. 31 added, all in `tests/api/test_body_limit.py`.

They are driven through the ASGI interface with scripted `receive` streams, not
through `TestClient`. That is not a style preference. `TestClient` always sends
an honest `Content-Length`, so a test written with it would have passed against
the Phase 6 code, which is exactly how the defect survived a suite with a
hundred percent coverage and a container check.

The harness records how many body bytes the server actually took, so the claim
being made is the one being asserted: not only that the answer is 413, but that
the parser could not have seen the whole body.

| Case | Asserted |
| --- | --- |
| Honest oversized `Content-Length` | 413, zero bytes read, no receipt |
| No `Content-Length` at all | 413, bounded read, no receipt, exact message |
| Forged `Content-Length` below the real size | 413, bounded read, no receipt |
| Chunk sizes 1, 7, budget minus one, budget, budget plus one | 413 at every boundary |
| A chunk ending exactly on the budget | Allowed through, refused by the file check |
| One chunk carrying the whole body | Refused after that one read |
| Document exactly at the file limit | 201, reaches the service, receipt written |
| Document one byte over the file limit | 413 `document_too_large`, no receipt |
| Normal chunked upload under both limits | 201 `ACCEPTED`, fact written |
| GET to the same path, POST elsewhere, a large body elsewhere | Unaffected |
| Health, run and receipt reads | Unchanged |
| The refusal body | Nested envelope, no document bytes, no boundary, no path, no SQL, no traceback, no parser detail |
| A client that hangs up mid-body | Not a 413; answered as the malformed request it is |

Reverting `main.py` to the Phase 6 guard fails five of them, including both
byte-count assertions. That check was run deliberately: a regression test that
passes against the code it was written to catch is not a regression test.

## Limitations

1. **A permitted upload is buffered, not streamed.** One request costs up to the
   budget in memory rather than being spooled by the parser. That is the trade
   for checking before anything parses, and the budget bounds it.
2. **A refused body is not drained.** The response is sent and the rest of the
   request is left unread, so a client may see a reset connection rather than a
   clean read of the 413. Reading megabytes in order to discard them would be
   paying the cost this exists to avoid.
3. **The budget is approximate, the file limit is not.** A body between the two
   passes the first check and is refused by the second. Both are 413 and both
   leave no receipt, but the error code differs, which is deliberate: they are
   different problems for a caller to fix.
4. **A forged smaller `Content-Length` is truncated by the server before this is
   reached.** Uvicorn frames the body by the declared length, so the surplus is
   not delivered as part of the request at all. The ASGI test covers the case
   where something upstream does deliver it; the live behaviour is a parse
   failure on a truncated body, which is also correct.
5. **Still no authentication**, unchanged and stated in the README and API docs.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| ASGI-level limiter on POST `/v1/imports` | Passed | `RequestBodyLimit`, scoped by `post_to` |
| Counts real body chunks, does not trust `Content-Length` | Passed | Refuses with the header absent, forged, or zero |
| Stops before the parser consumes or spools past the budget | Passed | Byte counts asserted, not just status codes |
| Bounded buffering, no parse-first-reject-later | Passed | At most budget plus one chunk, asserted |
| `Content-Length` check kept only as an optimisation | Passed | Honest oversized length reads zero bytes |
| Exact file-byte limit preserved | Passed | Document at the limit imports, one byte over is refused |
| Established 413 envelope | Passed | Nested `{"detail": {"error", "detail"}}`, declared JSON |
| Scoped so other routes are unaffected | Passed | GET, another POST, a large body elsewhere, and the read endpoints |
| Nothing over either limit reaches the service | Passed | Receipt and fact counts zero after every refusal |
| Inaccurate wording rewritten everywhere | Passed | No "nothing was read" left in code or docs |
| Real ASGI receive-stream tests, not only `TestClient` | Passed | 31 tests on scripted streams |
| Tests fail against the Phase 6 code | Passed | Five failures on revert |
| ADR-010, README, API docs, settings comments, Phase 6 report updated | Passed | All five, with the two limits distinguished |
| Live-server test without a trusted `Content-Length` | Passed | Chunked 4 MB refused, chunked small document imported, and the same against the container with a 32 MB body |
| `make ci` | Passed | Exit 0 |
| `make verify-containers` | Passed | Exit 0 |
| `make schema` | Met | Run, byte identical |
| 100 percent backend coverage | Passed | `Total coverage: 100.00%` |
