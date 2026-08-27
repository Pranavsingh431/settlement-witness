# Phase 8: Bounded AI link proposals in shadow mode

- Date: 2026-08-27
- Exit gate: passed. See "Exit gate status".
- Domain contract 5.0.0, parser 3.0.0, baseline 1.0.0, harness 2.0.0, all unchanged
- Shadow link harness 1.0.0, new

## What this phase is, stated plainly

**No hosted model is called anywhere in it.** There is no API key, no provider
SDK, no network access and no model output in any number reported here. The only
provider is a deterministic fake whose answers the calling test writes.

So this is not an AI performance result, and nothing in it should be read as
one. What it is: the contract for what a model may say, the deterministic code
that enforces it, and a harness that can score a provider once one exists.

The numbers below measure the boundary and the harness. They measure a fixture
selecting from a list.

## The rule

**The model can point. It cannot assert.**

A provider is shown a finite list of source records and may select from it. It
may not say anything about them. Recorded as
[ADR-012](../adr/ADR-012-the-model-points-the-verifier-decides.md).

Concretely:

| | |
| --- | --- |
| It may | Select record IDs from a set it was given, or abstain |
| It may not | Assign a status, report an exception code, write a reason code, claim an invariant result, supply a payload hash, attach a confidence, or write a sentence |

Those fields do not exist on `LinkProposal`. `extra="forbid"` means a provider
that sends one is rejected rather than trimmed, and a test asserts the declared
field set so one cannot be added unnoticed.

### Why a separate type

`DecisionCandidate` was not reused. It carries exception codes, invariant
results and evidence references with payload hashes, and each of those is
something the verifier derives or deterministic code builds from real facts.
Model output arriving in that shape would put a generated value one
`model_validate` away from a stored conclusion.

### Why exception codes specifically

Phase 7.2 recorded an open question: the contract turns a bare exception code
with no citations into an `EXCEPTION`. A model that could emit a code without
citing anything would be a path from a generated assertion to a reported finding
with nothing behind it.

This phase does not answer that question. It removes the model's ability to walk
the path, which is a narrower and stricter rule than any answer would need. The
open question in `docs/domain-contract.md` now says so.

## The candidate environment

For one settlement line, the candidates are every payment event and every payout
in the snapshot, ordered by source record ID.

Not a shortlist. Narrowing by payment ID would do the linking before the
provider was asked, and the exercise would then measure a filter. The subject
line's own record is excluded: it is what the question is about, it is linked by
construction, and offering it back would let a provider score by selecting the
thing it was asked about.

Each candidate carries reference fields only: record ID, record type, payment
ID, payout ID, event type, occurred-at. **No money.** Linking here is by exact
reference and never by amount, and an amount in the request would invite
reasoning from a number the provider cannot check.

A provider has no database handle, no filesystem, no tools, no follow-up query
and no access to the documents the facts were parsed from. It cannot ask for
more records, and the validator checks membership against the same set the
request carried, so an unknown ID is an invalid proposal rather than a discovery.

## What the harness found

Run against the two-payment fixture snapshot:

| Provider | Precision | Recall | Exact set | False link | Abstention | Invalid |
| --- | --- | --- | --- | --- | --- | --- |
| Selects the linked set | 1.000 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 |
| Selects everything | 0.381 | **1.000** | **0.000** | 0.619 | 0.000 | 0.000 |
| Always abstains | null | null | 0.000 | null | 1.000 | 0.000 |
| Returns malformed output | null | null | 0.000 | null | 0.000 | 1.000 |

The second row is the paired control and the reason the metrics are reported
separately. Selecting everything scores **perfect recall** while linking records
that do not belong. A single headline number, or recall alone, would call that a
success. Precision, exact-set accuracy and the false-link rate each catch it.

The third row is the other degenerate strategy. Abstaining on everything makes
no false links, and precision and recall are reported as **null** rather than
1.0, because a provider that answered nothing has not earned a perfect score by
having made no mistake. The abstention rate is where declining shows up.

**None of these is a reconciliation accuracy.** They measure whether a provider
picked the records the deterministic linker already picks. Reconciliation
correctness is what the verifier derives, and nothing here contributes to it. A
test asserts the report declares no field called `accuracy` on its own and
carries no status counts.

**pass@1 only.** One deterministic fake, asked once per line. No sampling and no
second attempt, so pass@k would be pass@1 reported k times.

## Nothing is persisted

No proposal is stored. The evaluator computes a report from a snapshot and
writes nothing, so no history is needed and none is kept. A stored proposal
would be a second thing in the database that looks like a decision, and the
safest amount of model output in this database is none.

No API endpoint was added. Nothing in the interface presents a proposal.

Two tests hold this: no table name contains `proposal`, and the evaluator module
mentions neither a repository nor a session.

## Verification that it changes nothing

The central claim, tested against a real database holding the example facts,
their receipts and a recorded run. Eleven provider behaviours were run against
it: correct, selects everything, abstains, malformed, out of set, another line,
stale snapshot, unknown field, timed out, raised, returned nothing.

| Checked | Result |
| --- | --- |
| Facts, receipts and runs compared record by record | Identical after every behaviour |
| Every fact's JSON including payload hashes | Identical |
| Every baseline decision, field for field | Identical |
| Snapshot fingerprint | Unmoved |
| Asking for a run again | Returns the existing run, so no rule version moved |
| Building evidence from a valid proposal | Reads facts, writes nothing |

Separately, the baseline was computed on this commit and on the commit before
Phase 8 and the output diffed:

```text
fingerprint: 7092df18a31c4b9386a3120e3134d6867f8728c1674a367bc73018f887cb84dc
BASELINE BYTE-IDENTICAL before and after Phase 8
```

## Changed files

| File | Change |
| --- | --- |
| `backend/app/ai/proposals.py` | New. `LinkProposal`, the outcome enum, derived identity |
| `backend/app/ai/candidates.py` | New. The candidate environment and the oracle |
| `backend/app/ai/validation.py` | New. The deterministic validator and evidence construction |
| `backend/app/ai/provider.py` | New. The provider protocol, typed failures, the fixture |
| `backend/app/ai/evaluation.py` | New. The shadow evaluator |
| `backend/tests/ai/` | New. 123 tests |
| `backend/pyproject.toml` | Coverage excludes a Protocol's `...` bodies, which never execute |
| `docs/adr/ADR-012-...md` | New |
| `docs/domain-contract.md` | Where Phase 8 stands on the open question |

Nothing under `app/domain/`, `app/reconciliation/`, `app/ingestion/`,
`app/storage/` or `app/api/` was touched.

## Commands run

| Command | Exit | Observed |
| --- | --- | --- |
| `git diff --check` | 0 | No whitespace errors |
| `make ci` | 0 | All nine core checks passed |
| `uv run pytest` | 0 | `1121 passed`, `Total coverage: 100.00%` |
| `uv run mypy` | 0 | `Success: no issues found in 108 source files` |
| `pnpm run test` | 0 | `184 passed`, frontend untouched |
| `make schema` | 0 | Byte identical |
| Migration and adoption suite | 0 | 95 passed |
| `make verify-containers` | 0 | Including the proxy checks |
| Baseline diffed against the pre-Phase-8 commit | 0 | Byte identical |
| Two evaluations of one snapshot | 0 | Byte-identical JSON |

## Tests

1121 backend, up from 998. 123 added.

| File | Tests | Covers |
| --- | --- | --- |
| `tests/ai/test_proposals.py` | 34 | The contract, and every field that must not exist |
| `tests/ai/test_validation.py` | 33 | Every adversarial case, and evidence construction |
| `tests/ai/test_evaluation.py` | 28 | The metrics, the paired controls, reproducibility |
| `tests/ai/test_candidates.py` | 20 | The inclusion policy, stable order, the oracle |
| `tests/ai/test_isolation.py` | 8 | That nothing changes, across eleven behaviours |

The adversarial cases, each proved to be refused deterministically and to change
nothing: a record outside the environment, a settlement line record, another
line's answer, a stale snapshot, duplicate IDs, an abstention carrying records, a
proposal carrying none, five kinds of unknown field, eight malformed payloads, an
overlong selection, and three kinds of provider failure.

Prompt-injection-like text in a source field has its own class. It proves what
can honestly be proved with no model in the loop: the value is carried as a
field value, the request contains no instruction text for it to be appended to,
and a provider that did select everything in response is caught by the same
membership and scoring rules as any other. It is **not** evidence that a model
resists persuasion, and it is labelled as such in the test.

## Limitations

1. **No model has been called, so nothing here measures one.** Every number
   comes from a fixture. The honest claim is that the boundary is enforceable
   and the harness works, not that AI-assisted linking is accurate.
2. **The linking task on the demo corpus is easy.** Exact reference matching
   already solves it, and a provider is being scored on reproducing that. A
   corpus where linking is genuinely ambiguous would be a better test of a real
   provider, and this phase does not build one.
3. **The candidate set is every event and payout in the snapshot.** That is
   right for a corpus of ten facts and would not be for a million. Bounding it
   without doing the linking in the filter is a real design problem, deferred.
4. **Nothing is persisted, so there is no history of proposals.** Deliberate. If
   a shadow evaluation later needs to be compared across time, that record has
   to be designed as append-only and separate from runs, not added to them.
5. **The bare exception code question remains open.** Phase 8 avoids it rather
   than settling it.
6. **`ABSTAIN` is not distinguished from "no records apply".** A provider that
   correctly determines a line links to nothing abstains, and so does one that
   cannot tell. The corpus has no such line, so nothing forced the distinction.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| Separate strict model-output type, not `DecisionCandidate` | Passed | `LinkProposal`, declared field set asserted |
| Required fields present, extras forbidden | Passed | 34 contract tests |
| `ABSTAIN` carries no IDs, `PROPOSE` carries at least one and no duplicates | Passed | One test each |
| No status, code, hash, invariant, confidence, text, money or lifecycle claim | Passed | Twelve forbidden fields, parametrised |
| Model forbidden from proposing exception codes | Passed | No such field; refused as an extra |
| Deterministic candidate sets with a stated policy | Passed | Documented and tested, including exclusions |
| Stable, documented candidate order | Passed | Record ID ascending, asserted |
| Minimal structured fields, no prose, no money | Passed | Field set asserted |
| Source fields treated as data, never instructions | Passed | Injection class, honestly scoped |
| No raw CSV, no database, filesystem, tools or extra records | Passed | Request carries IDs and reference fields only |
| No out-of-set record can be selected | Passed | Membership checked against the request's own set |
| Validation against the exact request | Passed | Snapshot, subject, shape, membership, duplicates |
| Rejections are AI-proposal failures, never exceptions or mutations | Passed | Eleven behaviours, store compared record by record |
| Server-side code builds evidence from real facts | Passed | Hash read from the fact; no field to supply one |
| A valid proposal does not decide, run, or resolve | Passed | Nothing calls `verify_decision`; baseline unchanged |
| Narrow provider protocol with a deterministic fake | Passed | One method; no SDK, key or network |
| Typed failures, never repaired or retried | Passed | Three failure kinds, each recorded as invalid |
| Nothing persisted, no endpoint | Passed | Asserted over the schema and the module source |
| Shadow evaluator over the deterministic oracle | Passed | Oracle derived from the baseline linker |
| Six metrics reported separately | Passed | And none called reconciliation accuracy |
| pass@1 only | Passed | Asserted; no pass field on the report |
| Paired control against broad guessing | Passed | Perfect recall, 0.000 exact set, 0.619 false link |
| Every adversarial case proved harmless | Passed | Facts, receipts, runs, decisions, fingerprint |
| Determinism | Passed | Byte-identical report across two runs |
| Baseline byte-identical before and after | Passed | Diffed against the pre-Phase-8 commit |
| `make ci`, schema, migrations, containers | Passed | All exit 0 |
