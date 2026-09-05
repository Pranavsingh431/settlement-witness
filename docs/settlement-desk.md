# Settlement desk: an operator workspace

The default screen is now work, not a benchmark pitch. Persistent navigation
separates Overview, Attention inbox, Data sources, Bank credits, Audit history
and Benchmark. White surfaces, restrained indigo actions, readable type and
progressive disclosure take visual cues from Razorpay without claiming affiliation.

## The useful path

1. With no recorded batch, **Explore a sample business** imports the four fixed
   synthetic sources through the existing HTTP importer, then records a run and
   a separate bank check. Each file is atomic, not the entire sequence. If a
   later step fails, earlier receipts remain visible. Duplicate import semantics
   make retrying unchanged samples safe.
2. With a batch, see actual counts and its auto-match rate immediately. Bank
   figures are queried for the exact same snapshot. An unavailable query is not
   reported as zero credits; a newer bank audit is not attributed to an older run.
3. Open work is ordered by verified declared settlement net **within currency**.
   This is not an estimate of loss or recoverable money. Unlike currencies are
   neither converted nor added. Unknown currency precision stays in minor units.
4. Open a case: plain-language finding, suggested team, every next action,
   required evidence and current-rule limits. Download a human-readable request
   or follow up on the exact decision, including cases beyond the queue's first
   page. Keyboard focus follows the case and returns on close.
5. Export a batch brief; inspect source citations, hashes and checks when needed.
   Audit metadata is expandable. Financial decisions retain their existing
   immutable authority; a human follow-up does not resolve a settlement.

The Bank credits screen separately filters payouts with and without verified
credits and exposes each certificate. Data sources uses human-readable options
with unchanged wire values. If a selected filename matches a supplied sample but
its declared settings differ, the screen offers an explicit preset. A filename
is not accepted as proof of source identity and never silently changes settings.

## Deployment and verification

The Vite service owns a filesystem-first SPA fallback in the root `vercel.json`.
Top-level `/health` and `/v1/*` routing remains assigned to the backend. This
allows refreshes of nested audit/review routes as well as the new workspace pages.

Regression tests cover partial sample failures, replay, snapshot selection,
bank-query failure, old/new snapshot separation, per-currency ordering, paging,
search, exact-case follow-up, request exports and zero denominators. The backend
domain, schema, reconciliation rules and hosted-model authority are unchanged.

## The boundary still matters

This public deployment is a shared synthetic reviewer workspace, not a tenant-safe
merchant service. It does not connect to a bank, move money, assign real employees,
send evidence requests, or let an AI alter financial conclusions. Requests are
downloads for an operator to share. Authentication, tenant isolation and operating
controls remain prerequisites to accepting real company records.
