# Architecture decision records

Each record captures one decision that is hard to reverse: the context, the decision, the
consequences and the alternatives that were rejected.

Rules:

- Number files in sequence, for example `ADR-002-transaction-lifecycle-schema.md`.
- Once a record is accepted, do not rewrite it. Write a new record that supersedes it, and link
  the two in both directions.
- Record the decision when it is made, not after the code is written.

| Record | Title | Status |
| --- | --- | --- |
| [ADR-001](ADR-001-stack-and-modular-monolith.md) | Stack and modular monolith | Accepted |
| [ADR-002](ADR-002-domain-contract-and-verifier-authority.md) | Domain contract and verifier authority | Accepted |
| [ADR-003](ADR-003-derived-status-and-source-fact-verification.md) | Derived status and source-fact verification | Accepted |
| [ADR-004](ADR-004-append-only-import-and-atomicity.md) | Append-only import, audit trail and atomicity | Accepted |
| [ADR-005](ADR-005-exact-reference-matching-and-snapshot-payouts.md) | Exact-reference matching and snapshot-relative payout grouping | Accepted |
| [ADR-006](ADR-006-settlement-gross-must-match-its-capture.md) | A settled gross must equal the capture it settles | Accepted |
| [ADR-007](ADR-007-payment-event-amounts-are-strictly-positive.md) | Payment event amounts are strictly positive | Accepted |
| [ADR-008](ADR-008-seeded-generation-and-independent-oracle.md) | Seeded generation, paired controls and an independent oracle | Accepted |
| [ADR-009](ADR-009-immutable-runs-and-migrations.md) | Immutable reconciliation runs, idempotent run keys and real migrations | Accepted |
| [ADR-010](ADR-010-import-receipts-are-the-created-resource.md) | A processed upload returns 201, whatever the document turned out to be | Accepted |
| [ADR-011](ADR-011-same-origin-api-instead-of-cors.md) | The browser reaches the API through its own origin, not through CORS | Accepted |
| [ADR-012](ADR-012-the-model-points-the-verifier-decides.md) | The model points, the verifier decides | Accepted |
