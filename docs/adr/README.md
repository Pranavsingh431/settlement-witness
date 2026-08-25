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
