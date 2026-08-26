# ADR-010: A processed upload returns 201, whatever the document turned out to be

- Status: Accepted
- Date: 2026-08-26
- Supersedes: none
- Superseded by: none
- Related: [ADR-004](ADR-004-append-only-import-and-atomicity.md),
  [ADR-009](ADR-009-immutable-runs-and-migrations.md)

## Context

Phase 2 established that every import attempt leaves a receipt. A document that
was rejected writes no facts and still records what was tried, what was wrong
with it, and when. That receipt is stored in an append-only table and is as much
a part of the audit trail as an accepted one.

Phase 6 puts that service behind `POST /v1/imports`, which forces a question the
CLI never had to answer: what status code describes an upload the parser
refused, or one that turned out to be an exact replay of a document already
imported?

The obvious mapping is wrong in both cases. A rejected document looks like a 422,
and a duplicate looks like a 200. Both would describe the document rather than
what the request did to the system.

## Decision

**Every upload the import service processes returns 201, and the body is the
receipt.** That covers `ACCEPTED`, `DUPLICATE_NO_OP`, `REJECTED_CONFLICT` and
`REJECTED_INVALID` alike. The `outcome` field is the only thing that says what
happened to the document.

A status code describes what happened to the request. Each of those four
requests created a receipt: a stored resource, with an identity, that
`GET /v1/imports/{receipt_id}` will return afterwards. Saying 422 would say no
resource was created when one was, and a caller retrying on that basis would
write a second receipt for the same refusal. Saying 200 for a duplicate would
say the same thing about a replay.

There is a real cost to this. A caller that checks only the status code will
read a rejected import as a success, and that is a mistake this design makes
easy to hit. It is accepted because the alternative hides an audit record behind
a code that says nothing was recorded, and a silently missing receipt is worse
than a caller that has to read one field. The endpoint documentation and the
OpenAPI description for 201 both say plainly that the receipt is the created
resource and not the acceptance of the document.

**A request the service never processes is a 4xx and leaves nothing behind.**
That is the line: not whether the document was good, but whether an import was
attempted at all.

| Request | Status | Receipt |
| --- | --- | --- |
| Any document the service read | 201 | Written |
| Missing `file`, `source_system` or `record_type` | 422 | None |
| A value that is not a member of its enum | 422 | None |
| `record_type` the parser has no schema for | 422 | None |
| Body or document over the size limit | 413 | None |

The last two are worth naming. `BANK_TRANSACTION` is a source record type the
domain contract defines and the CSV parser has no schema for. Passed through, it
would become a `REJECTED_INVALID` receipt saying the document could not be read,
which would send a caller to look at their file when the problem is that the
type is not importable. It is refused at the boundary instead, with a message
naming the types that are.

An oversized upload is refused before it is parsed, so it never reaches the
service and no receipt is written for it. That is deliberate: the receipt
records what the parser made of a document, and nothing was made of this one.

## Two limits, doing two jobs

"Over the size limit" is two separate checks, and conflating them is how the
first version of this got it wrong.

**Bounded request handling.** The whole multipart body is bounded at
`SW_MAX_UPLOAD_BYTES` plus a small envelope allowance, counted at the ASGI layer
before anything parses the request. The count is over the bytes that actually
arrive. `Content-Length` does not decide it, because the client controls that
header and a client that omits it or lies about it is precisely the one this has
to stop. An honest oversized length is still refused without reading, but as an
optimisation on top of the count rather than as the check.

This has to happen before the application is called. FastAPI reads a multipart
body while resolving an endpoint's arguments, so a check written inside the
endpoint runs after the parser has already consumed and spooled the upload. It
also cannot be done by raising from the stream: Starlette catches an error
raised while it is reading and answers `400 There was an error parsing the
body`, which is the wrong status and a description of the wrong problem. So the
body is counted and held until it is known to fit, and only then handed on. That
bounds one request at the budget plus the chunk that crossed it, and it means a
permitted upload is buffered rather than streamed. At these sizes that is the
cheaper half of the trade.

**Exact file validation.** The document inside the envelope is then checked
against `SW_MAX_UPLOAD_BYTES` exactly. The budget must be the larger number, or
a document of exactly the permitted size could not be sent, so a document in the
gap between them passes the budget and is refused here.

Both are 413 and neither leaves a receipt. Both say `no import was processed and
no receipt was written`. Neither says nothing was received, because by the time
an absent or false length is caught, some of the body has been read, and a
message claiming otherwise would be the same kind of false boundary claim in
smaller print.

## Consequences

- A caller must read `outcome`, not the status code, to know whether facts were
  written. `wrote_facts` is also on the receipt, derived from `accepted_count`
  and validated against it so it cannot become a second opinion.
- Retrying a rejected upload writes a second receipt. That is correct: two
  attempts were made, and an audit trail that recorded one would be wrong.
- The 4xx cases are the ones a client can fix by changing the request rather
  than the file. That is a useful line for a caller and it is the line drawn.
