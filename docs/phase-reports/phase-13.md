# Phase 13: Pre-registered hosted-model shadow evaluation

- Date: 2026-08-29
- Exit gate: **protocol passed; hosted evaluation not run**
- Hosted score: **none**
- ADR amendment: none. No hosted result exposed a new design limitation.

## Result on this machine

The prerequisite local `.env.ai` file was absent. The Phase 13 launcher stopped
cleanly before it read a provider configuration, wrote a plan, created a
receipt, opened a socket, or made a hosted request. There is therefore no model
score, no partial score, no aggregate and no claim about model performance in
this report.

That is an intentional terminal state of the protocol, not a failed result
silently presented as one. A credentialed run can be made later only through
the committed `make phase-13` command; it must append its observed, safe summary
to this report rather than replace this record of why no result exists today.

## What is now pre-registered

`scripts/run-phase-13.sh` is the execution protocol. It has one model-call
command and no alternative provider path:

```text
python -m app.ai.live_shadow --allow-network
```

Before that command can run, the local helper writes a plan under ignored
`results/phase-13/<UTC timestamp>/` containing:

- the full checked-out commit SHA;
- corpus version and canonical corpus hash, harness version, rendered request-set
  fingerprint and page count;
- provider **hostname only**, adapter/model identity, timeout, response-byte
  budget and request budget; and
- `declared_run_count: 3`.

The count is a source constant, not an argument. After preflight, the script
performs exactly three independent `live_shadow` executions. It never retries a
poor run. Typed provider failures remain in their own run receipt and mark that
run `INCOMPLETE`; an aggregate is withheld if any one of the three is incomplete.
An unexpected local interruption gets a safe `LIVE_COMMAND_DID_NOT_COMPLETE`
record and stops the protocol without an unplanned retry.

## What a later result may publish

Each run is kept separately. Only three complete runs may have a pooled
aggregate, calculated by adding the component numerators and denominators — not
by averaging rounded rates. The allowed fields are strict link recall,
answered-link recall, precision, exact-set accuracy, false-link rate, safe
abstention, unsafe selection, invalid-page rate, typed failure counts and
request count. There is no generic headline score.

No summary may contain a key, endpoint path, prompt, response body, raw
source-record identifier or provider error prose. Raw hosted receipts remain
only in ignored `results/`; the protocol record contains the safe projection
needed to report a run. The provider receives only the generated corpus request
fingerprints that the existing adapter allow-list approves. No imported merchant
document, application database content, live payout, API input or frontend input
is part of the provider payload.

## Local state proof

Before and after each planned attempt, the protocol records a read-only hash of
the SQLite database file plus `-wal` and `-shm` sidecars. It also records local
row-count and content hashes for every protected table:

- `source_facts` and `import_receipts`;
- `reconciliation_runs` and `reconciliation_decisions`;
- `review_events`; and
- `bank_finality_audits` and `bank_finality_certificates`.

The post-run record must match the pre-run record exactly. The host call does not
receive these hashes or any database-derived value; the proof is local evidence
of non-interference.

## Verification completed

- `make phase-13` exited 2 at the absent `.env.ai` gate with the safe message
  that no hosted evaluation started and no score exists. It did not reach `uv`,
  the provider configuration, the network or `results/`.
- 34 new focused tests; `app.ai.phase13` is 100% statement and branch covered.
- Full backend suite: **1,892 passed, 100.00% coverage**. Backend formatting,
  lint and strict mypy passed across 150/145 files respectively.
- `make schema` produced no tracked schema diff. The public synthetic benchmark
  was generated and evaluated twice; both reports hashed to
  `27747836964f0231210b486d9f127a9b8af8462a86e51d52ed477dc47a2b1819`.
- `make verify-containers` passed: backend `/health`, frontend route fallback
  and same-origin proxy checks all returned 200; the backend ran as UID 999 and
  frontend as UID 101.
- The host Node runtime is 23.10.0 and correctly refused `make test-frontend`
  at the project’s Node 24 engine gate. An isolated pinned Node 24 container
  then ran the locked frontend format, lint, typecheck, test and build commands:
  **305 frontend tests passed** at 98.94% statements / 92.9% branches.
- The tests prove all three predeclared attempts are required, a subset or
  duplicate cannot be summarised, incomplete runs withhold the aggregate, and a
  changed database proof stays visible even beside a complete hosted receipt.
- The helper has no `HostedLinkProposalProvider` import; the only adapter caller
  remains `app.ai.live_shadow`.
- The raw-receipt and protocol-summary tests assert that a configured test key,
  database row text and endpoint path do not appear in publishable artifacts.
- Existing deterministic corpus tests remain the fixture path used by the
  protocol. No baseline, bank-finality, review, storage or frontend code was
  changed.

The host Node runtime is 23.10.0 while this repository deliberately requires
Node 24. The project’s engine gate correctly prevented a host-native run; the
isolated pinned Node 24 result above is the frontend verification for this
phase, without weakening that contract.

## Corpus limitation

This is a generated regression/shadow corpus. It checks whether a hosted model
can select and abstain under the deliberately bounded request environment; it is
not a representative production dataset, not a measure of real-merchant
performance, and not a claim about reconciliation quality.
