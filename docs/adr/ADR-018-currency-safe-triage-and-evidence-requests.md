# ADR-018: Triage is not a currency conversion, and a handoff is not closure

- Status: Accepted
- Date: 2026-09-05
- Supersedes: none
- Superseded by: none
- Related: [ADR-003](ADR-003-derived-status-and-source-fact-verification.md),
  [ADR-015](ADR-015-review-events-annotate-they-do-not-decide.md),
  [ADR-016](ADR-016-settlement-agreement-is-not-bank-finality.md),
  [ADR-017](ADR-017-closure-is-a-proof-obligation.md)

## Context

An exception ledger gives every unresolved line equal visual weight. That is
not how finance operations works: people need an honest answer to “where should
I start?” and a package they can send to the party holding the next proof.

Both features carry a familiar failure mode. A product can add values in INR
and USD and label the result “cash at risk”, or make a download look like a
resolution workflow. Neither statement follows from the reconciliation
certificate. The first requires an explicit FX policy and a definition of
exposure. The second requires authoritative source evidence and a new run.

## Decision

**Serve a deterministic currency-separated workboard and non-authoritative
evidence-request packages as read models over immutable decisions.**

### 1. The workboard shows only a cited declared settlement net

For every non-resolved decision, the service seeks the cited settlement-line
fact in the current append-only fact index. It uses the value only when record
ID, payload hash, record type and settlement-line identity all agree. It then
orders work by absolute `net_minor` within a single source currency. Currency
queues are never converted or summed.

If the cited settlement evidence cannot be re-read, the line stays visible in
`unpriced_items`. A blank amount is more honest than a guessed one. The
workboard is a priority read model, not a financial conclusion, a bank-finality
audit, or a claim of cash at risk.

### 2. A package is a request, not a source fact

The package is generated only for an unresolved decision from its versioned
closure plan. It carries record IDs, source systems, payload hashes,
verification outcomes, owner lane, bounded actions, evidence required and the
closure gate. It excludes raw payloads, free-form model text, a financial
adjustment, an actor action and every field that could alter a decision.

The endpoint is a read and returns an attachment header with a fixed filename.
It stores neither a package nor a workflow event. A resolved decision returns
an explicit conflict rather than a fictional task.

## Consequences

- Operators get a useful starting queue without currency arithmetic hidden in a
  dashboard total.
- An exception can travel to the party holding evidence without losing its
  exact identity or acceptance condition.
- A returned package has no authority. The only closure path remains import of
  authoritative evidence and reconciliation into a new immutable run.
- A future cash-exposure feature must introduce a documented FX source, timing
  rule and exposure definition. A future package-delivery feature must add
  authenticated recipients and audit that delivery; this public synthetic demo
  does neither.

## Alternatives rejected

**Publish one “cash at risk” number.** Rejected: the current sources do not
provide a justified conversion policy or a single exposure semantic.

**Let an operator fill in a number while downloading.** Rejected: it would turn
a handoff into an unverified financial assertion.

**Store package state as closure state.** Rejected: generating or sharing a
request does not prove evidence exists or that any invariant now holds.
