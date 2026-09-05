# Submission and demo guide

Use this as a fact-checked outline for a hackathon submission or a live demo.
It describes the repository as it is; do not turn the measurements below into
claims about production merchants or hiring outcomes.

## One-sentence pitch

**Settlement Witness is an evidence-to-closure finance controller: it reconciles
a generated 59-scenario payment batch, proves every conclusion from source
records, and turns every unresolved line into a bounded action with an owner,
required evidence and a verifier-enforced closure gate.**

## What to show in a three-minute demo

1. **Start with the problem.** Payment, settlement and payout records can
   appear internally consistent while still needing an auditable explanation.
   A green match without its evidence is not enough for a finance operator.
2. **Start a working batch.** On Overview, choose *Explore a sample business*.
   This imports four fixed sample documents (236 records) into the shared
   application workspace, reconciles 59 settlement lines, and records a separate
   bank-credit check. Previously accepted sample records are reused safely.
   If a batch already exists, the desk opens its real recorded results immediately.
3. **Read the results.** Show the 32/59 auto-match rate and the full list of
   27 unresolved lines. The desk's rates describe this recorded batch, not
   accuracy against an independent oracle. For throughput and measured contract
   agreement, open *Benchmark* and run its isolated 59-scenario evaluation.
   That benchmark uses a fresh temporary database and does not change the shared
   workspace. Both use generated data, not a representative production dataset.
4. **Make the next action testable.** Open an issue in the attention inbox. Its
   close plan names the owning finance lane, the next action, the exact proof
   required and whether the current verifier can check it. Download its plain-text
   evidence request, or open *Record a follow-up* on that exact case. Open *Data sources*,
   download the four seeded CSVs and import them in the displayed order: 180
   provider records plus 56 matching bank credits. Before choosing each file,
   press its matching **Use source → type** button: it explicitly declares
   `PSP_API` for the first three files and `BANK_STATEMENT` for the last,
   without guessing from the filename or contents. Never upload real merchant
   data to the public preview.
5. **Inspect a run.** Open the decision audit workspace and select an
   exception. The certificate shows citations, payload hashes, exception codes,
   each invariant independently, and the new-run gate that prevents an action
   from being mistaken for resolution.
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
immutable decision ------------------------------> deterministic close plan
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
- Every unresolved decision carries a deterministic close plan with an owner,
  evidence acceptance test and a new-run gate; the plan cannot edit its source
  decision.
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
