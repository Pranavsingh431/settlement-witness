# Phase 8.1: Honest shadow metrics, and server-owned proposal metadata

- Date: 2026-08-27
- Exit gate: passed. See "Exit gate status".
- Domain contract 5.0.0, parser 3.0.0, baseline 1.0.0, harness 2.0.0, all unchanged
- Shadow link harness 1.0.0 to 2.0.0

## Scope

The AI boundary and its metrics. No hosted model, API key, SDK, network call,
endpoint, persistence or database schema was added, and no domain,
reconciliation, storage, ingestion, API or baseline behaviour changed. Baseline
decisions were diffed against the previous commit and are byte identical.

## Defect 1: what was called recall was not recall

`link_recall` was measured over the lines that produced a selection. A line the
provider abstained on, failed on, or answered invalidly was removed from the
denominator instead of counting against it, so declining to answer raised the
score. Reproduced on the two-line fixture:

```text
line-sl-1 true links: 2   line-sl-2 true links: 2   total 4

perfect on line 1 + ABSTAIN on line 2:
  link_recall = 1.0  (2/2)
  true links in corpus = 4, true links actually returned = 2
perfect on line 1 + malformed on line 2:
  link_recall = 1.0  (2/2)
perfect on line 1 + out-of-set on line 2:
  link_recall = 1.0  (2/2)
```

Perfect recall while half the corpus's true links were never returned, by three
different routes.

### The fix

`link_recall` is now true positives over every true link in the corpus. Every
line asked about is in the denominator whatever became of it, so an abstention,
a malformed response, an out-of-set selection and a provider failure all miss
their true links and all cost the same as answering wrongly. It is null only
when the corpus contains no true link at all.

The old measure is kept, because it answers a real and different question: how
well the provider did when it did answer. It is reported as
`answered_link_recall`, under a name that says which lines it covers, and never
as recall alone.

Precision is unchanged: selected true links over all selected links, null when
nothing was selected. Exact-set accuracy, abstention rate, invalid-output rate
and false-link rate are unchanged and still reported separately.

### The controls afterwards

| Provider | `link_recall` | `answered_link_recall` | Precision | Exact set |
| --- | --- | --- | --- | --- |
| Perfect on both lines | 1.000 (4/4) | 1.000 (4/4) | 1.000 | 1.000 |
| Perfect + abstain | **0.500** (2/4) | 1.000 (2/2) | 1.000 | 0.500 |
| Perfect + malformed | **0.500** (2/4) | 1.000 (2/2) | 1.000 | 0.500 |
| Perfect + out of set | **0.500** (2/4) | 1.000 (2/2) | 1.000 | 0.500 |
| Always abstains | **0.000** (0/4) | null | null | 0.000 |
| Selects everything | 1.000 (4/4) | 1.000 (4/4) | 0.667 | 0.000 |

Abstaining on everything is now 0.000 rather than unmeasurable: there are true
links and the provider returned none of them. The conditional measure and
precision stay null, because there is no answered line and nothing was selected.

Selecting everything is unaffected by the redefinition, which is the point of
including it: it still scores perfect recall and is still caught by precision
and exact-set accuracy.

### The version bump

`SHADOW_HARNESS_VERSION` is `2.0.0`. A report written under `1.0.0` used the
word to mean the conditional measure, so the two must not be compared as though
they measured the same thing. The Phase 8 report carries a correction note
rather than having its figures rewritten.

## Defect 2: the provider signed its own answers

`LinkProposal` was a single type that the provider returned, so it supplied its
own identity and its own proposal ID. An otherwise valid payload naming another
provider validated, and the forged values were what got recorded:

```text
forged payload validates today: True
  recorded provider   : attacker v999
  recorded proposal id: anything-i-like
  the derived id would be: 0a4f149c09c6e0501c1758bdbb64cfd1
```

### The fix: two layers

**`RawLinkSelection`** is what a provider returns. Two fields: `outcome` and
`selected_source_record_ids`. That is the whole of what it may say.

**`LinkProposal`** is the envelope the server builds, and `bind` is the only
thing that makes one. It writes the subject line and the snapshot fingerprint
from the request it holds, takes the provider identity from the provider object
it called, and derives the proposal ID itself.

`extra="forbid"` on the raw type now refuses four fields that have correct
values the provider does not own: `provider`, `proposal_id`,
`subject_settlement_line_id` and `snapshot_fingerprint`. A response carrying any
of them is malformed, because a provider supplying one has misunderstood what it
is being asked and the rest of its answer is not worth salvaging.

`ProviderFailure` lost its identity field for the same reason. Which provider
failed is read from the provider object by whatever records the failure, not
from the failure itself.

Afterwards:

```text
forged payload -> RejectedProposal: MALFORMED
valid selection -> provider recorded as: the-real-provider v7
                   subject: line-sl-1
                   snapshot: 0a3892241ec6… (matches request: True)
                   proposal id: 717ba880f35c29c3e73986ad4636f689
```

### Two failure modes became impossible

`WRONG_SNAPSHOT` and `WRONG_SUBJECT` are gone as rejection codes. A response
carries neither field, so it cannot be about the wrong line or the wrong
snapshot; the nearest attempt is a payload carrying one of those keys, which is
refused as an extra. A test asserts the remaining code set, so reintroducing
either field would have to reintroduce a code and be visible.

That is a stronger position than checking for them. A check can be forgotten or
reordered; a field that does not exist cannot be filled in.

## Defect 3, found while doing the above

`docs/domain-contract.md` stated that `DOMAIN_SCHEMA_VERSION` is `2.0.0`. It has
been `5.0.0` for three major steps. The document's own heading and its version
table both said `5.0.0`, so one sentence contradicted the two places a reader
looks first.

Corrected, and held by `tests/domain/test_documented_version.py`, which checks
the sentence, the heading and the version table against the constant. Verified
by reverting the sentence to `2.0.0` and watching the test fail. Nothing had
been checking, and a document that names the wrong version is worse than one
that names none: a reader has no reason to doubt it.

## Changed files

| File | Change |
| --- | --- |
| `backend/app/ai/proposals.py` | `RawLinkSelection` added, `LinkProposal` becomes a server-built envelope, `bind` added |
| `backend/app/ai/validation.py` | Parses a raw selection, binds it, two rejection codes removed as impossible |
| `backend/app/ai/provider.py` | Fixture returns two fields; `ProviderFailure` carries only its kind |
| `backend/app/ai/evaluation.py` | Corpus-wide recall, `answered_link_recall` added, harness 2.0.0, identity from the provider object |
| `backend/tests/ai/` | 157 tests, up from 123 |
| `backend/tests/domain/test_documented_version.py` | New |
| `docs/adr/ADR-012-...md` | The two layers, and why metadata is not the provider's |
| `docs/domain-contract.md` | The version corrected |
| `docs/phase-reports/phase-8.md` | Correction note, two exit gate rows marked |

## Commands run

| Command | Exit | Observed |
| --- | --- | --- |
| `git diff --check` | 0 | No whitespace errors |
| `make ci` | 0 | All nine core checks passed |
| `uv run pytest` | 0 | `1159 passed`, `Total coverage: 100.00%` |
| `uv run mypy` | 0 | `Success: no issues found in 109 source files` |
| `pnpm run test` | 0 | `184 passed`, frontend untouched |
| `make schema` | 0 | Byte identical |
| Migration and adoption suite | 0 | 95 passed |
| `make verify-containers` | 0 | Including the proxy checks |
| Baseline diffed against the pre-Phase-8.1 commit | 0 | Byte identical |
| Two evaluations of one snapshot | 0 | Byte-identical JSON |
| Version guard against a reverted document | 1 | Fails, which is the point of it |

## Tests

1159 backend, up from 1121. 157 in `tests/ai/`, up from 123.

| File | Tests | Covers |
| --- | --- | --- |
| `tests/ai/test_proposals.py` | 51 | Both layers, the forbidden metadata, and binding |
| `tests/ai/test_validation.py` | 40 | Every adversarial case, and the metadata seam |
| `tests/ai/test_evaluation.py` | 38 | The two recall measures and every paired control |
| `tests/ai/test_candidates.py` | 20 | Unchanged |
| `tests/ai/test_isolation.py` | 8 | Twelve provider behaviours, including three forgery attempts |

The metadata tests are the ones worth naming. Four fields are each proved to be
refused as an extra; a valid proposal is proved to carry the request's subject
and fingerprint and the provider object's identity; the derived proposal ID is
compared against `proposal_id_for`; and a custom-named provider is proved to
bind correctly rather than inheriting the fixture's name.

The isolation suite gained the three forgery attempts and lost the two behaviours
that became inexpressible. All twelve leave facts, receipts, runs, baseline
decisions and the snapshot fingerprint identical.

## Limitations

1. **Still no model has been called.** Every number comes from a fixture. This
   phase corrected how the harness measures and what a provider may say; neither
   is a claim about AI performance.
2. **`answered_link_recall` can still be quoted on its own** by someone reading
   the report carelessly. The name says which lines it covers, and that is the
   whole defence. Nothing stops a person quoting the wrong number.
3. **The bare exception code question remains open**, and Phase 8's prohibition
   remains the way this system avoids it.
4. **The envelope's shape validator is now unreachable through `bind`.** It is
   kept as a second check on the type that gets handed onward and is tested by
   constructing the envelope directly, which is what a future caller that
   forgot `bind` would do.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| `link_recall` over every true link in every requested line | Passed | 0.500 where it was 1.000, by three routes |
| Unanswered lines miss their true links | Passed | Abstain, malformed, out of set, stale, failure |
| Null only when the corpus has no true link | Passed | 0.000, not null, when truth exists |
| Conditional measure exposed separately, never called recall | Passed | `answered_link_recall`, asserted |
| Precision unchanged, null when nothing selected | Passed | Unchanged tests still passing |
| Exact set, abstention, invalid, false link kept separate | Passed | Six rates, none averaged |
| Paired control: perfect plus abstain | Passed | Overall 0.500, conditional 1.000 |
| Paired control: perfect plus invalid | Passed | Same, by two invalid routes |
| Paired control: always abstain | Passed | Overall 0.000, conditional and precision null |
| Paired control: select everything | Passed | Recall 1.000, precision 0.667, exact set 0.000 |
| Harness version bumped | Passed | `2.0.0`, with the reason in the constant's docstring |
| Phase 8 report corrected explicitly | Passed | Section added, two exit gate rows marked |
| Raw response type carries only outcome and IDs | Passed | Field set asserted |
| Envelope is server-constructed | Passed | `bind` is the only constructor |
| Provider cannot supply ID, identity, subject or fingerprint | Passed | Four fields, each refused as an extra |
| Identity and ID derived by application code | Passed | From the provider object and `proposal_id_for` |
| Provider failures take identity from the provider object | Passed | `ProviderFailure` carries only its kind |
| Custom-named providers bind correctly | Passed | Two tests, one at each layer |
| Raw output still forbids asserting fields | Passed | Twelve fields, parametrised |
| Isolation unchanged | Passed | Twelve behaviours, nothing altered |
| `DOMAIN_SCHEMA_VERSION` documented as `5.0.0` | Passed | Corrected, and guarded by a test |
| ADR-012 distinguishes the two layers | Passed | Rewritten decision 1 |
| Full CI, 100% coverage, schema byte identical | Passed | All exit 0 |
| Migration and adoption suites, containers | Passed | Both exit 0 |
| Baseline byte identical | Passed | Diffed against the previous commit |
| Evaluator deterministic and writes nothing | Passed | Identical JSON; isolation suite |
