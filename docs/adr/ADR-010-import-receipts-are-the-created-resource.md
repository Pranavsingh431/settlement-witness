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

## Consequences

- A caller must read `outcome`, not the status code, to know whether facts were
  written. `wrote_facts` is also on the receipt, derived from `accepted_count`
  and validated against it so it cannot become a second opinion.
- Retrying a rejected upload writes a second receipt. That is correct: two
  attempts were made, and an audit trail that recorded one would be wrong.
- The 4xx cases are the ones a client can fix by changing the request rather
  than the file. That is a useful line for a caller and it is the line drawn.
