# Submission and demo guide

Use this as a fact-checked outline for a hackathon submission or a live demo.
It describes the repository as it is; do not turn the measurements below into
claims about production merchants or hiring outcomes.

## One-sentence pitch

**Settlement Witness is an evidence-first reconciliation controller that
resolves a settlement only when its stored source records and required
invariants support it, preserves every import and review action append-only,
and keeps AI suggestions outside the decision path.**

## What to show in a three-minute demo

1. **Start with the problem.** Payment, settlement and payout records can
   appear internally consistent while still needing an auditable explanation.
   A green match without its evidence is not enough for a finance operator.
2. **Import evidence.** In *Import evidence*, load the three committed CSVs in
   `data/fixtures/ingestion/` using the declared record types. Show the import
   receipts, including that an identical replay is a no-op rather than a second
   write.
3. **Create and inspect a run.** In *Runs*, reconcile the snapshot. Open the
   run audit and select an exception. The certificate shows citations, payload
   hashes, exception codes and each invariant independently.
4. **Separate operations from truth.** Open the review queue and record an
   acknowledgement or request for evidence. The baseline decision stays the
   same; a human workflow event cannot convert an exception into a resolution.
5. **Show finality separately.** Explain that provider reconciliation and a
   bank showing a credit are different conclusions. A bank finality audit is
   append-only and exact-reference only; it does not guess from amounts or
   dates.
6. **Explain the AI boundary.** The model can propose opaque record links only
   on a generated shadow corpus. Deterministic validation decides whether a
   proposal is admissible; no model output can create or change evidence,
   decisions, runs, reviews or bank audits.

## Architecture in one screen

```text
CSV/API import -> append-only source facts + receipt -> deterministic baseline
                                                  -> immutable run + certificate
human review event ------------------------------> separate workflow timeline
bank statement ----------------------------------> separate finality audit
hosted model (generated shadow corpus only) -----> validated link proposal only
```

The arrows intentionally do not lead from AI or human review to a settlement
decision. That constraint is the design, not an omitted feature.

## Claims that are supported

- Imports, receipts, source facts, reconciliation runs, decisions, review
  events and bank-finality audits are append-only.
- The baseline resolves only when cited facts verify and required invariants
  hold; otherwise it reports an exception or insufficient evidence.
- The UI keeps settlement status, human workflow state and bank finality
  visually and semantically separate.
- The hosted-model adapter is corpus-only, allow-listed, request-bounded and
  response-bounded. It has no database, API or frontend caller.
- The credentialed Phase 13 shadow protocol made three pre-registered attempts:
  two completed and one was incomplete after a bounded oversized response. No
  pooled metric was published.

## Claims to avoid

- Do not call the shadow-corpus metrics "reconciliation accuracy", "production
  accuracy", or real-merchant performance.
- Do not say AI resolves payments, improves a settlement decision, or has
  access to uploaded merchant data. It does none of those things.
- Do not say a `RESOLVED` settlement line proves money reached the merchant.
  Only a separate matching bank credit can support that statement.
- Do not say the application is a public production deployment. It has no
  authentication or multi-tenancy; see [deployment.md](deployment.md).

## Reviewer setup

For a local run, use `make docker-up`, open <http://127.0.0.1:5173>, and follow
the walkthrough above. Run `make verify` before recording a demo. For a remote
reviewer, follow the access-control and persistent-storage requirements in
[deployment.md](deployment.md) before sharing a URL.
