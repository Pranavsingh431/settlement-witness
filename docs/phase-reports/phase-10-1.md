# Phase 10.1: Bounded reads, corpus-only in the adapter, typed failures in the receipt

- Date: 2026-08-28
- Exit gate: passed. No hosted run occurred, because no credentials are configured.
- Domain contract 5.0.0, parser 3.0.0, baseline 1.0.0, all unchanged
- Shadow link harness 5.0.0, shadow corpus 1.0.0, both unchanged
- Live receipt 1.0.0 to 2.0.0
- `ADR-014` amended; `docs/phase-reports/phase-10.md` annotated, not rewritten
- **One claim below was corrected in [Phase 10.2](phase-10-2.md).** See "Corrected later".

## No hosted run occurred

Still no credentials on this machine, so still no score.

```text
hosted-model variables in this environment:
  SETTLEMENT_WITNESS_AI_BASE_URL: not set
  SETTLEMENT_WITNESS_AI_API_KEY: not set
  SETTLEMENT_WITNESS_AI_MODEL: not set
  SETTLEMENT_WITNESS_AI_TIMEOUT_SECONDS: not set
  SETTLEMENT_WITNESS_AI_MAX_RESPONSE_BYTES: not set
  SETTLEMENT_WITNESS_AI_MAX_REQUESTS: not set
```

No number in this repository describes a hosted model's reconciliation ability.

## The defects, reproduced first

### 1. The byte budget measured the spend instead of limiting it

A recording byte stream, a 1000 byte budget, a 200 kilobyte answer:

```text
DEFECT 1: the whole body is delivered before the budget is applied
  budget                 : 1000 bytes
  body size              : 200117 bytes
  bytes actually consumed: 200117
  outcome                : RESPONSE_TOO_LARGE
  VERDICT: consumed 200x the budget before deciding it was too large
```

The failure kind was right and arrived after the download. `client.post()` reads
the body before returning, so `len(response.content) > budget` is a report of
what was already spent. Phase 10's own report described this as "a byte budget
on each response" and the enum said the body was "abandoned unread". Neither was
true. A host answering with a gigabyte would have been paid in full.

### 2. Corpus-only was a property of the caller, not of the adapter

A `LinkProposalRequest` built from an ordinary hand-made snapshot, handed
straight to `propose()`:

```text
DEFECT 2: a non-corpus request is sent to the host
  the page above is built from a hand-made snapshot, not build_corpus()
  corpus pages            : 24
  this page is one of them: False
  requests sent           : 1 to ['/v1/chat/completions']
  outcome                 : {'outcome': 'ABSTAIN', 'selected_source_record_ids': []}
  VERDICT: the adapter accepted a request from outside the corpus and put it on
  the wire
```

ADR-014 argued for "no handle to misuse rather than a rule against misusing
one", then relied on the CLI having no `--database` flag. The adapter accepted
anything. The guarantee held for exactly as long as there was one caller.

### 3. The receipt could not tell a rate limit from a dead socket

```text
DEFECT 3: the receipt loses the adapter's typed failure
  429 from the host:
    receipt failure_counts : {'PROVIDER_FAILED': 24}
    adapter knew it was    : not recorded anywhere
  dead socket:
    receipt failure_counts : {'PROVIDER_FAILED': 24}
    adapter knew it was    : not recorded anywhere
  VERDICT: a rate limit and an unreachable host are indistinguishable in the
  receipt
```

Identical output for two problems with nothing in common. Phase 10 recorded this
as a limitation and reasoned that fixing it would mean changing the shared
harness contract. The reasoning about the harness was right; the conclusion was
wrong. The report did not need to change.

### 4. The endpoint could carry a secret into the receipt

Found while writing the above, and refused rather than left for later:

```text
DEFECT 4: accepted a URL carrying credentials
  provenance: https://admin:hunter2@api.example.test/v1?token=sk-leaky
```

`provenance()` copies the base URL verbatim into every receipt.

## A. The response is read under budget

`client.post` is replaced with `client.stream`, so the response arrives unread.

| Case | Behaviour |
| --- | --- |
| Non-2xx | Returned as `REFUSED_BY_PROVIDER` with the body never requested |
| `Content-Length` above the budget | Refused before a byte is asked for |
| `Content-Length` absent | Streamed, stopped when the buffer passes the budget |
| `Content-Length` forged small | Same. The declared size is a convenience, never the thing relied on |
| `Content-Length` not a number | Ignored, streaming decides |

The buffer holds at most the budget plus the one chunk that crossed it, and an
abandoned buffer is not parsed, stored or reported. Typed failures and the
no-retry rule are unchanged: one attempt per page, still.

Measured, with a 1000 byte budget and 4096 byte chunks:

```text
  no content-length        consumed    4096 of 200113 -> RESPONSE_TOO_LARGE
  forged small length      consumed    4096 of 200113 -> RESPONSE_TOO_LARGE
  honest oversized length  consumed       0 of 200113 -> RESPONSE_TOO_LARGE
  429 body                 consumed       0 of 500000 -> REFUSED_BY_PROVIDER
```

## B. Corpus-only is enforced by the adapter

`HostedLinkProposalProvider` now takes a required, keyword-only,
non-empty `frozenset[str]` of authorised request fingerprints. An empty one
raises `NothingAuthorised`; a missing one is a `TypeError`. There is no
permissive default, in the adapter or in the test helper.

> Corrected in Phase 10.2. `frozenset[str]` was an annotation, and Python does
> not check annotations. A caller passing an ordinary `set` kept a live handle
> on the provider's own scope and could widen it mid-run. The provider now
> copies whatever it is given into a `frozenset` it owns.

`propose` checks membership first, before the budget, before any header is
built and before the client is touched, and returns the new
`FailureKind.REQUEST_NOT_AUTHORIZED`. An unauthorised page consumes no budget,
because nothing was sent.

The allow-list is over `request_fingerprint`, which covers what a provider was
shown rather than only which records existed. So the same corpus rendered under
different styling is a different set of questions and is not authorised by the
first set.

`live_shadow` derives the list from `build_corpus()` and the canonical corpus
styling, which is what `evaluate` builds its questions from, so the two sets are
the same set by construction.

```text
  non-corpus page -> REQUEST_NOT_AUTHORIZED, requests sent: 0, requests_made: 0
  corpus page     -> {'outcome': 'ABSTAIN', 'selected_source_record_ids': []},
                     requests sent: 1
  empty allow-list -> NothingAuthorised
```

## C. Typed failures reach the receipt

`ShadowReport` is untouched. `PROVIDER_FAILED` stays the right word for a report
that also scores fixtures.

The adapter keeps its own counter of `FailureKind` outcomes: bounded by the enum,
keys are kind names, values are integers, and nothing else can enter it. No
status text, no header, no error body, no provider prose.

The receipt carries both, under names that say what they hold:

| Field | Holds |
| --- | --- |
| `report_rejection_counts` | The shared report's rejection codes. Was `failure_counts` |
| `typed_failure_counts` | The adapter's `FailureKind` counts |
| `receipt_version` | New. `2.0.0` |

The old name is gone rather than kept as an alias. It held generic rejections
while reading as though it held typed ones, which is how the defect survived a
phase.

```text
  429          report: {'PROVIDER_FAILED': 24}  typed: {'REFUSED_BY_PROVIDER': 24}
  dead socket  report: {'PROVIDER_FAILED': 24}  typed: {'CONNECTION_FAILED': 24}
```

## D. The endpoint is held to the key's standard

User-info credentials, a query string and a fragment are refused. Not stripped:
quietly removing a secret would leave the operator believing they had configured
something they had not.

The refusal names the variable and never quotes what it read. That is why
`MissingConfiguration` is a `RuntimeError` rather than a `ValueError`: pydantic
wraps a `ValueError` raised inside a validator into a message that quotes the
input it was given, and the input here is the thing that must not be quoted. A
`RuntimeError` propagates out of the validator unchanged, so the only text
anyone sees is written in `hosted.py`.

## Verified behaviour

76 new test cases, none of which reach the network.

| Class | Cases | Proves |
| --- | --- | --- |
| `TestTheResponseIsReadUnderBudget` | 11 | Consumption stops at budget plus one chunk, never at the whole body |
| `TestOnlyAuthorisedPagesAreAsked` | 8 | Refusal before transport use, and no permissive default |
| `TestTheAdapterCountsItsOwnFailures` | 18 | One case per kind, plus a mixed run and a bound on the keys |
| `TestTheEndpointCannotSmuggleASecret` | 27 | Seven leaky URLs refused three ways, with nothing echoed |
| `TestTheProviderIsAuthorisedForTheCorpusOnly` | 4 | The command's allow-list is neither too narrow nor too wide |
| `TestTypedFailuresReachTheReceipt` | 8 | 429 is not a connection failure, and neither carries provider text |

Plus one more in the isolation suite: an unauthorised page refused against a
populated database leaves the store byte-identical, and an oversized streamed
response was added to the nine hosted behaviours already covered there.

### The new tests fail against the old implementations

Each defect was put back on its own, with everything else left in place, and the
matching tests run:

```text
eager read restored          8 of 11 failed in TestTheResponseIsReadUnderBudget
allow-list check removed     5 of 8  failed in TestOnlyAuthorisedPagesAreAsked
typed counter removed       20 of 26 failed across the two counting classes
endpoint checks removed     21 of 27 failed in TestTheEndpointCannotSmuggleASecret
```

The cases that pass either way are the ones asserting the ordinary path still
works: a body inside the budget, a clean endpoint, an answered page counting
nothing. Those are regression guards and not defect detectors, which is what
they should be.

## Observed results

### Every gate

```text
$ uv run python -m app.ai.live_shadow
error: this command calls a hosted model over the network. Re-run with
--allow-network to allow that.
exit: 2

$ uv run python -m app.ai.live_shadow --allow-network        # empty environment
error: these environment variables are not set: [all six names]
exit: 2

$ SETTLEMENT_WITNESS_AI_BASE_URL='https://admin:hunter2@api.example.test/v1?token=abc' \
  uv run python -m app.ai.live_shadow --allow-network
error: SETTLEMENT_WITNESS_AI_BASE_URL must not carry user-info credentials,
because a secret put there would be copied into every run receipt. Give the
endpoint alone and put the key in SETTLEMENT_WITNESS_AI_API_KEY.
exit: 2
```

The third refusal quotes neither the host, the user, the password nor the token.

### The whole network path, against a port with nothing behind it

Not a model run. Configuration, client construction, 24 real socket attempts,
failure typing, the receipt and the artifact.

```text
requests made    : 24
lines / pages    : 6 / 24
invalid pages    : 1.000 (24/24)
report rejections: {'PROVIDER_FAILED': 24}
typed failures   : {'CONNECTION_FAILED': 24}
exit: 0
```

The second line is the whole of defect 3, fixed and visible. The receipt:

```json
{
  "receipt_version": "2.0.0",
  "harness_version": "5.0.0",
  "corpus_version": "1.0.0",
  "provider_name": "openai-compatible",
  "model_id": "no-such-model",
  "requests_made": 24,
  "report_rejection_counts": {"PROVIDER_FAILED": 24},
  "typed_failure_counts": {"CONNECTION_FAILED": 24}
}
```

The database before and after:

```text
db before: f59e27288bb674b948d4498435c2e0dff05eee2b7976b9b90044a5ec2008f296
db after : f59e27288bb674b948d4498435c2e0dff05eee2b7976b9b90044a5ec2008f296
DATABASE BYTE-IDENTICAL
```

### The baseline is unmoved

```text
scenario_count                    : 59
decision_accuracy                 : 1.0 (59/59)
evidence_completeness             : 1.0 (59/59)
evidence_verification_completeness: 1.0 (59/59)
exact_exception_set_accuracy      : 1.0 (27/27)
exception_recall                  : 1.0 (33/33)
false_resolution_rate             : 0.0 (0/27)
harness 2.0.0, parser 3.0.0, baseline 1.0.0, domain 5.0.0

sha256 e5cff7b46a22c4d5b89ee0361ac1e373a4680f2f4a9ec268575b242cf60c4b5c
```

Byte-identical to the Phase 10 report file. Regenerating the published JSON
Schema produced no diff.

## Changed files

| File | Change |
| --- | --- |
| `backend/app/ai/hosted.py` | Streaming bounded read, allow-list, typed counter, endpoint checks |
| `backend/app/ai/provider.py` | `REQUEST_NOT_AUTHORIZED`; corrected the `RESPONSE_TOO_LARGE` docstring |
| `backend/app/ai/live_shadow.py` | Allow-list derivation, `LIVE_RECEIPT_VERSION`, both count fields |
| `backend/tests/ai/test_hosted.py` | Four new classes; `serving` now demands the pages it may ask about |
| `backend/tests/ai/test_live_shadow_cli.py` | Two new classes; receipt field assertions updated |
| `backend/tests/ai/test_isolation.py` | Unauthorised page and oversized stream added |
| `docs/adr/ADR-014-...md` | Phase 10.1 amendment |
| `docs/phase-reports/phase-10.md` | "Corrected later" section; original text left as written |
| `README.md`, `.env.ai.example` | The endpoint rule and the two sets of counts |

## Commands run

```text
uv run ruff format --check .                    124 files already formatted
uv run ruff check .                             All checks passed
uv run mypy                                     no issues in 121 source files
uv run pytest                                   1564 passed, 100.00% coverage
pnpm run format:check                           Prettier clean
pnpm run lint                                   eslint, 0 warnings
pnpm run typecheck                              tsc clean
pnpm run test                                   98.5% statements, 92.09% branches
pnpm run build                                  built in 218ms
make schema                                     no diff
make benchmark-evaluate                         byte-identical to Phase 10
./scripts/verify-containers.sh                  passed, both non-root
```

Migration and legacy-adoption suites run inside `uv run pytest`, unchanged and
passing.

## Limitations

**No hosted model has been evaluated.** Everything here is adapter behaviour
against mocks and a dead socket.

**Everything Phase 10 listed still stands.** A hosted run will not be
reproducible; the corpus is small, synthetic and written by this project; JSON
framing is not a solution to prompt injection; and no claim is made about
production data.

**The allow-list is only as good as what derives it.** The adapter now enforces
what it is told. `live_shadow` tells it the corpus, and a future caller could
tell it something else. What changed is that such a caller must say so in code,
in a required argument, rather than inheriting a permissive default. A test
asserts the two sets match today.

> And the adapter enforced what it was told only until the caller changed its
> mind. See the correction below.

**One chunk is still one chunk.** The bound is the budget plus whatever the
transport hands over in a single read. That is bounded and it is not exact, and
a host choosing enormous chunks makes the overshoot larger. Bounding it exactly
would mean reading byte by byte, which is a real cost for a case that is already
an error.

**A 401 and a 429 remain indistinguishable.** Deliberately. Telling them apart
means keeping the host's explanation, which is the one thing this adapter will
not store. `REFUSED_BY_PROVIDER` says the host said no; the operator has the
host's own dashboard for why.

**A 1.0.0 receipt is not readable as 2.0.0** and is not converted. There is no
reason to migrate a local artifact of a run that already happened.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| All three defects reproduced first | Passed | Output quoted above, before any fix |
| Streaming consumption, at most budget plus one chunk | Passed | `TestTheResponseIsReadUnderBudget`, 11 cases with a recording stream |
| Non-2xx body never read | Passed | 4 statuses, `consumed == 0` |
| Honest oversized `Content-Length` may fail early | Passed | `consumed == 0` |
| Absent or forged length still stops while streaming | Passed | `consumed == 4096` of 200113 |
| Nothing retained beyond the bounded buffer | Passed | Abandoned buffer is never parsed or reported |
| Typed failures and no-retry preserved | Passed | The Phase 10 classes still pass unchanged |
| New tests fail against the old implementation | Passed | 8, 5, 20 and 21 failures with each defect restored |
| Non-empty immutable allow-list required at construction | Passed | `NothingAuthorised`; `TypeError` when omitted |
| Refusal before headers or transport | Passed | Zero transport calls, `requests_made == 0` |
| Allow-list derived only from `build_corpus()` | Passed | `TestTheProviderIsAuthorisedForTheCorpusOnly` |
| Altered styling, page, or a real snapshot refused | Passed | 3 cases, each with zero transport calls |
| No permissive default | Passed | In the adapter and in the test helper |
| `ShadowReport` unchanged | Passed | Harness still 5.0.0; no field added or removed |
| Adapter counts its own kinds, bounded | Passed | `TestTheAdapterCountsItsOwnFailures`, 18 cases |
| Receipt distinguishes all seven conditions | Passed | One case per kind, plus a mixed run |
| No bodies, headers, prose or secrets in the counts | Passed | Asserted against a chatty 429 with a header |
| Generic counts kept separately, accurately named | Passed | `report_rejection_counts` |
| Receipt version added and bumped | Passed | `LIVE_RECEIPT_VERSION = "2.0.0"` |
| User-info, query and fragment refused | Passed | 7 endpoints, 3 ways each |
| Refusals echo nothing secret | Passed | `test_the_refusal_quotes_nothing` |
| No AI endpoint, persistence, production mode, retries, or path to reconciliation | Passed | Isolation suite; no route, table or writer added |
| Full CI, typing, 100% coverage, schema, containers, baseline | Passed | Commands above |
| A live hosted run with a real score | **Not performed** | No credentials configured. No number invented. |

## Corrected later

One claim here was true of the annotation and not of the code.

| Claimed here | What was true | Fixed in |
| --- | --- | --- |
| "a required, keyword-only, non-empty `frozenset[str]`", and "Immutable, so a caller cannot widen it after construction" | The parameter was annotated `frozenset[str]` and never checked. A caller passing a plain `set` kept a live reference to the provider's allow-list and could add to it at any point during a run, and the provider would then send the added page. | [Phase 10.2](phase-10-2.md) |

The exit-gate row "Non-empty immutable allow-list required at construction"
should be read as: non-empty was required and checked, immutable was written
down and not. Phase 10.2 reproduces the widening as a failing test first.

This is the same mistake as the one this phase was written to fix. Phase 10
relied on the CLI having no data argument and called that structural. Phase 10.1
moved the check into the adapter and relied on a type annotation, which Python
does not enforce, and called that structural too.

## Unresolved

Nothing blocking. The three open items are the chunk-sized overshoot, the
deliberate merging of 401 and 429, and the fact that the allow-list is only as
correct as the caller that derives it. All three are described above and none is
hidden behind a passing row.
