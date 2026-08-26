# ADR-004: Append-only import, audit trail and atomicity

- Status: Accepted
- Date: 2026-08-25
- Supersedes: none
- Superseded by: none
- Amended: 2026-08-25, see "Amendment" below
- Followed by: [ADR-009](ADR-009-immutable-runs-and-migrations.md), which replaced
  `create_all` with migrations, as decision 5 anticipated
- Related: [ADR-001](ADR-001-stack-and-modular-monolith.md),
  [ADR-002](ADR-002-domain-contract-and-verifier-authority.md),
  [ADR-003](ADR-003-derived-status-and-source-fact-verification.md)

## Context

ADR-002 established that source facts are append-only and that a correction is a
later fact rather than an edit. ADR-003 left Phase 2 an explicit obligation:
storage must be able to supply `verify_decision` with a complete fact index.

Phase 2 makes both real, and doing so forces four decisions that are hard to
reverse. Once documents have been loaded and decisions cite the facts they
produced, changing how a fact is identified, or what happens to a half-valid
file, invalidates everything already stored.

## Decision

### 1. An import is accepted whole or not at all

One malformed row, or one conflict with a stored fact, rejects the entire
document. Nothing is written.

The alternative, loading the good rows and reporting the bad ones, is worse than
it sounds. A half-loaded file leaves a gap that nothing downstream can see. A
later reconciliation would report a missing settlement that was never missing,
only never loaded, and the investigation would look for a payment problem that
does not exist. A rejected import is visible; a partial one is not.

The cost is real: one bad row in a large file means fixing the file and
re-running. That is accepted, because the import is idempotent, so re-running is
cheap and safe.

### 2. Every attempt leaves a receipt, including the refused ones

Facts are written inside a savepoint that is rolled back when the import is
refused. The receipt is written outside it, so a rejection writes no facts and
still records what was tried and what was wrong with it.

An audit trail that only records successes is not an audit trail. The question a
person asks later is usually "was this file ever loaded", and "no, and here is
why" has to be answerable.

This required a documented workaround for pysqlite, whose legacy transaction
handling commits before a `SAVEPOINT` and would silently make the rollback a
no-op. Without it the all-or-nothing guarantee would appear to work while being
untrue. There is a test that fails if the workaround is removed.

### 3. A source record ID is derived from content, never from a path

The identity is `{document sha256}:{source system}:{record type}:{row number}`.

Not a file path: the same bytes imported from a different directory, or from a
stream with no path, are the same records, and a path would leak a local
directory layout into stored identifiers.

The source system is included because ADR-002 treats one event observed through
two systems as two observations. This was found by a test rather than by
reasoning: without the system in the identity, loading one document as a
provider feed and as a merchant ledger collided, and the second observation was
swallowed as a duplicate of the first.

Row numbers are one-based and count the header as row 1, so they match what a
person sees in a spreadsheet.

### 4. Lifecycle records are projections, not stored rows

Payment events, settlement lines and payout batches are derived from stored
facts on demand rather than written to their own tables.

Storing them would create a second copy that can disagree with the first, and a
second place a correction might come from. Deriving them means the fact is the
only source, and every projection carries the `source_record_id` it came from.

`PayoutBatch.settlement_line_ids` is left empty. A payout document says what the
batch totalled, not which lines composed it. That association is matching work,
and inventing it here would create evidence no document supports.

### 5. `create_all` rather than a migration tool

There is one released schema and no deployed database to migrate. A migration
framework would be ceremony without a subject.

This is explicitly provisional. The first schema change that has to preserve
existing rows needs a migration tool and a new ADR. Until then, setup is
`create_all`, which is safe to run again and touches no data.

## Amendment, 2026-08-25: append-only is enforced at the database

Decision 1 said facts are append-only and the repositories have no way to change
one. That was true and it was not enough.

A repository without an update or delete method stops the application from
rewriting history by mistake. It does nothing about a migration script, a
maintenance session, or anything else holding a connection to the same file. A
review confirmed it: a direct `UPDATE` rewrote five stored payload hashes and a
direct `DELETE` removed every fact and every receipt, with nothing objecting.

Append-only is a property of the data, not of one access path, so it is now
enforced where the data lives. Both `source_facts` and `import_receipts` carry
SQLite triggers that abort any `UPDATE` or `DELETE` with a message naming the
table and the operation. `INSERT` is unaffected.

The triggers are created by ordinary `create_schema` with `IF NOT EXISTS`, so a
clean database is protected without a separate hardening step and setup stays
safe to run again.

Two consequences worth recording. A trigger firing raises the same
`IntegrityError` that a unique constraint does, which is harmless here because
the import path only ever inserts, but it is worth knowing before anything else
starts catching that exception. And a future migration that has to rewrite these
tables will have to drop the triggers deliberately, which is the point: it makes
rewriting history an explicit act rather than an accident.

## Consequences

Good:

- A stored fact is trustworthy: it came from a document that was accepted whole,
  and the attempt that produced it is on the record.
- Re-running an import is safe, so an operator can retry without thinking.
- A refused import is as visible as a successful one.
- `verify_decision` has the complete index ADR-003 asked for.
- Facts and lifecycle records cannot drift apart, because there is only one of
  them.

Costs and risks:

- One bad row means re-running the whole file. Mitigated by idempotency, and by
  reporting every problem in a document at once rather than one per run.
- The pysqlite savepoint workaround is a piece of driver-specific knowledge that
  will look arbitrary to a future reader. It carries a comment saying what
  breaks without it.
- The audit table grows without bound. It is the record, so nothing prunes it. A
  retention policy is a later decision and needs its own ADR.
- A document hash is not a business key. Re-exporting the same data with a
  different byte layout produces new source record IDs and, if the provider
  event IDs match, a duplicate conflict rather than a silent duplicate. That is
  the safe direction, and it means an operator re-exporting a file will see a
  conflict they have to think about.
- `create_all` will not survive the first breaking schema change.

## Alternatives considered

**Accept valid rows, reject the rest.** Rejected. See decision 1. The invisible
gap is the failure this project exists to avoid.

**Write the receipt only on success.** Rejected. The most useful audit question
is about a file that did not load.

**One transaction covering both facts and the receipt.** Rejected. It makes a
rejection leave no trace, which is the same failure by a different route.

**Overwrite a fact when a document is re-imported with different content.**
Rejected outright. It contradicts ADR-002, and it would let the most recent
loader silently rewrite history.

**Identify a row by file path and line number.** Rejected. Two operators loading
the same file from different directories would produce different identities for
the same observation.

**Store lifecycle records in their own tables.** Rejected. See decision 4.

**Infer the record type from the file name or a sniffed header.** Rejected. A
file read as the wrong record type fails loudly on its headers, but a file read
as the wrong source system would import cleanly and be wrong. Since one has to
be declared, both are.

**Alembic from the start.** Rejected for now. See decision 5.
