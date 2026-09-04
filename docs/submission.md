# Submission and demo guide

Use this as a fact-checked outline for a hackathon submission or a live demo.
It describes the repository as it is; do not turn the measurements below into
claims about production merchants or hiring outcomes.

## One-sentence pitch

**Settlement Witness is an evidence-first AI finance controller that closes a
multi-source reconciliation loop across a generated 59-scenario batch: it
auto-matches supported lines, publishes an honest exception ledger, and keeps
AI suggestions outside the decision path.**

## What to show in a three-minute demo

1. **Start with the problem.** Payment, settlement and payout records can
   appear internally consistent while still needing an auditable explanation.
   A green match without its evidence is not enough for a finance operator.
2. **Run the Track 04 batch.** On the landing page, choose *Run the 59-case
   batch*. It generates 59 synthetic payment, settlement and payout scenarios,
   imports them through the real strict parser into a fresh temporary database,
   and reconciles them through the real baseline. No reviewer file is uploaded
   and the shared application database is not changed.
3. **Read the measures.** Show the throughput, the 32/59 auto-match rate, the
   full list of 27 lines that did not auto-resolve, the strict contract
   agreement and the zero false-resolution count. State the limitation on the
   page: this is a generated regression corpus, not real-merchant or
   production performance.
4. **Make the next action visible.** The exception ledger names the finance
   follow-up for each finding category. Open *Evidence*, download the four
   seeded CSVs and import them in the displayed order: 180 provider records
   plus 56 matching bank credits. Before choosing each file, press its matching
   **Use source → type** button: it explicitly declares `PSP_API` for the first
   three files and `BANK_STATEMENT` for the last, without guessing from the
   filename or contents. Never upload real merchant data to the public preview.
5. **Inspect a run.** Open the decision audit workspace and select an
   exception. The certificate shows citations, payload hashes, exception codes
   and each invariant independently.
6. **Separate operations from truth.** Open the review queue and record an
   acknowledgement or request for evidence. The baseline decision stays the
   same; a human workflow event cannot convert an exception into a resolution.
7. **Show finality separately.** Explain that provider reconciliation and a
   bank showing a credit are different conclusions. A bank finality audit is
   append-only and exact-reference only; it does not guess from amounts or
   dates.
8. **Explain the AI boundary.** The model can propose opaque record links only
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
- Do not say the public preview is a production deployment. It has no
  authentication or multi-tenancy, and it is for synthetic demo data only; see
  [deployment.md](deployment.md).

## Reviewer setup

For a local run, use `make docker-up`, open <http://127.0.0.1:5173>, and run
the Track 04 batch above. Run `make verify` before recording a demo. For a
remote reviewer, use the public Vercel preview only for the generated synthetic
batch described in [deployment.md](deployment.md).
