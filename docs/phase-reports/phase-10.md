# Phase 10: A real hosted model, against the shadow corpus only

- Date: 2026-08-28
- Exit gate: passed, with one part not performed. See "Exit gate status".
- Domain contract 5.0.0, parser 3.0.0, baseline 1.0.0, all unchanged
- Shadow link harness 5.0.0, shadow corpus 1.0.0, both unchanged
- New: `ADR-014`, adapter `openai-compatible`

## No hosted run occurred

**No hosted-model credentials are configured on this machine, so no hosted model
was called and this report contains no live score.**

```text
hosted-model variables in this environment:
  SETTLEMENT_WITNESS_AI_BASE_URL: not set
  SETTLEMENT_WITNESS_AI_API_KEY: not set
  SETTLEMENT_WITNESS_AI_MODEL: not set
  SETTLEMENT_WITNESS_AI_TIMEOUT_SECONDS: not set
  SETTLEMENT_WITNESS_AI_MAX_RESPONSE_BYTES: not set
  SETTLEMENT_WITNESS_AI_MAX_REQUESTS: not set
```

The adapter, the command and every mock-backed check are complete and verified.
No number in this repository describes a hosted model's reconciliation ability,
and none is estimated, projected or borrowed from a published benchmark. When
somebody runs the command with a key, the receipt it writes will be the first
such number, and it will be one run over a generated corpus.

This section is separate from the next two on purpose. What follows is verified
adapter behaviour. It is not evidence about any model.

## What was built

### `app/ai/hosted.py`, 429 lines

An OpenAI-compatible chat-completions adapter implementing the existing
`LinkProposalProvider` protocol, so it is interchangeable with the fixtures and
is judged by the same validator.

`HostedProviderConfig` is read from six environment variables, all required,
with no default endpoint, model or key. A blank value is treated as missing. The
key is a `SecretStr`. Timeouts, response size and the request budget are bounded
above as well as below, so a typo cannot ask for an unbounded read. Plain HTTP is
refused unless the host is the local machine, which exists so a fake server can
be run in front of the adapter.

`presentation_payload` builds the only structure that leaves the process:

| Sent | Not sent |
| --- | --- |
| Settlement line id and its two rendered references | Every canonical fact |
| Candidate source-record ids | All three fingerprints |
| Candidate record type, references, event type, timestamp | Amounts, currencies, payload hashes |
| The page ordinal and the page count | Any imported document, row or filename |

Every identifier in the corpus is a digest of a seeded generator's output, so
the ids are opaque tokens rather than merchant references.

The instruction and the data are separate messages, and the data is JSON rather
than interpolated text. Structured output is requested where the host offers it.

### `app/ai/live_shadow.py`, 213 lines

The command. Its entire option set is `--allow-network` and `--output`.

`--allow-network` is checked before the environment is read. There is no
argument that selects what is evaluated: `run` calls `build_corpus()` and
nothing else. Two seams exist for the tests, `environment` and `transport`, and
neither is reachable from the command line.

The receipt records the harness version, corpus version, provider name, model
id, the non-secret configuration, the request count, the failure counts, the
report and a timestamp. It contains no prompt, no response, no header and no
key.

### Changes to existing files

`FailureKind` gained four members for the conditions a network provider has and
a fixture does not: `CONNECTION_FAILED`, `REFUSED_BY_PROVIDER`,
`RESPONSE_TOO_LARGE`, `UNREADABLE_RESPONSE`. `httpx2` moved from the dev
dependencies to the runtime dependencies, because the adapter now imports it in
application code. It was already present as the transport behind the FastAPI
test client, so this adds no new library.

## Verified behaviour

131 new test cases, none of which reach the network. All 1484 backend tests pass at
100 percent coverage.

### The key is never written down

`TestTheKeyIsNeverWrittenDown`, 10 cases. Absent from the config repr, the model
dump, the JSON dump, `provenance()`, the receipt, the report, the artifact on
disk, and every raised exception including a missing-variable error, which names
variables and never values. Present in exactly one place: the `Authorization`
header of the request.

### Only presentation leaves the process

`TestOnlyPresentationLeavesTheProcess`, 7 cases. The request body is compared
against the exact expected key set at every level. Separate assertions prove no
canonical fact, no fingerprint of any of the three kinds, and no amount appears
anywhere in the serialised body.

### Nothing is repaired

`TestNothingIsRepaired`, 20 cases. Markdown fences, prose around an object,
trailing commas, extra keys, missing keys, a bare array, a bare string, a null,
an unknown outcome, an id that is not in the environment, and an id invented
whole are each rejected rather than fixed. Every one becomes a typed failure or
an ordinary rejection.

### Nothing is retried

`TestNothingIsRetried`, 6 cases. A counting transport confirms exactly one
request per page for a timeout, a connection failure, a 500, a 429 and a
malformed body.

### Failures are typed and carry nothing

`TestFailuresAreTypedAndCarryNothing`, 21 cases. Each condition maps to its kind
and the failure detail carries no response body, no header and no provider
prose. A 401 and a 429 are indistinguishable in the output, which is intended: a
host's explanation of why it refused is not evidence.

### The budget stops the run

`TestTheRequestBudgetStops`, 3 cases. Verified end to end against the 24-page
corpus with `SETTLEMENT_WITNESS_AI_MAX_REQUESTS=5`: exactly 5 requests were
sent and the remaining 19 pages were counted invalid rather than skipped.

### It cannot be pointed at real data

`TestItCannotBePointedAtRealData`, 10 cases. The command's option set is asserted
whole. Both modules' import graphs are read with `ast.walk` and asserted to
contain nothing from `app.storage`, `app.api` or `app.ingestion`, so the test
checks the actual imports rather than the prose in a docstring.

### The adapter is inert

`TestTheHostedAdapterIsAlsoInert`, in the existing isolation suite. Nine hosted
behaviours, including every failure kind, are each run against a populated
database, and the store is unchanged after all of them.

## Observed results

### The gates

```text
$ uv run python -m app.ai.live_shadow
error: this command calls a hosted model over the network. Re-run with
--allow-network to allow that.
exit: 2

$ uv run python -m app.ai.live_shadow --allow-network        # empty environment
error: these environment variables are not set:
['SETTLEMENT_WITNESS_AI_API_KEY', 'SETTLEMENT_WITNESS_AI_BASE_URL',
 'SETTLEMENT_WITNESS_AI_MAX_REQUESTS', 'SETTLEMENT_WITNESS_AI_MAX_RESPONSE_BYTES',
 'SETTLEMENT_WITNESS_AI_MODEL', 'SETTLEMENT_WITNESS_AI_TIMEOUT_SECONDS']
exit: 2
```

Six names, no values.

### The whole network path, against a port with nothing behind it

Not a model run. This exercises configuration loading, client construction, all
24 requests, failure typing, the receipt and the artifact, with a real socket
and no model at the other end.

```text
model            : no-such-model
harness / corpus : 5.0.0 / 1.0.0
requests made    : 24
lines / pages    : 6 / 24
invalid pages    : 1.000 (24/24)
failures         : {'PROVIDER_FAILED': 24}
This is one run over a generated shadow corpus. It is not reconciliation
accuracy and not production performance.
exit: 0
```

The database before and after:

```text
db before: f59e27288bb674b948d4498435c2e0dff05eee2b7976b9b90044a5ec2008f296
db after : f59e27288bb674b948d4498435c2e0dff05eee2b7976b9b90044a5ec2008f296
DATABASE BYTE-IDENTICAL
```

The receipt from that run contains the string `not-a-real-key` nowhere, and the
built backend image carries zero `SETTLEMENT_WITNESS_AI` variables.

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
```

Identical to Phase 9.2. Regenerating the published JSON Schema produced no diff.

## Commands run

```text
uv run ruff format --check .                    124 files already formatted
uv run ruff check .                             All checks passed
uv run mypy                                     no issues in 121 source files
uv run pytest                                   1484 passed, 100.00% coverage
pnpm run format:check                           Prettier clean
pnpm run lint                                   eslint, 0 warnings
pnpm run typecheck                              tsc clean
pnpm run test                                   98.5% statements, 92.09% branches
pnpm run build                                  built in 281ms
make schema                                     no diff
make benchmark-evaluate                         unchanged from Phase 9.2
./scripts/verify-containers.sh                  passed, both non-root
```

The migration and legacy-adoption suites run inside `uv run pytest`, unchanged
and passing.

## Limitations

**No hosted model has been evaluated.** Everything above is adapter behaviour
against mocks and a dead socket. Nothing here says whether any model is good at
this task.

**A hosted run will not be reproducible.** Temperature is zero and is recorded,
and that is not the same thing. Batching, routing, hardware and silent updates
behind a model alias all move an answer, and none of them is controllable from
here. A receipt records which alias was asked, not which weights replied.

**The corpus is small and synthetic.** 6 settlement lines, 24 pages, one seed.
One run over it produces a number with wide variance and no confidence interval,
and the corpus was written by the same project that would be scored on it.

**Failure counts are coarse in the receipt.** The report counts rejection codes,
so 24 connection failures appear as `PROVIDER_FAILED: 24`. The adapter typed
each one as `CONNECTION_FAILED`, but the harness report shape is shared with
fixture runs and versioned, so distinguishing a timeout from a 401 in the
receipt would change that contract. Left as it is, and recorded here rather than
changed quietly.

**JSON framing is not a solution to prompt injection.** Sending data as
structured JSON in a message separate from the instruction removes the string
concatenation a value could escape from. It does not make a model immune to text
that reads like an instruction. The real defence remains the one from Phase 8:
the model returns identifiers, the verifier decides, and no model output can
reach a decision.

**No claim is made about production data.** No production data has been through
this path, because no path exists. That is the guarantee, and it is not a
measurement.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| Reachable only through a new CLI that evaluates `build_corpus()` | Passed | `run` takes no data argument; option set asserted whole |
| No database URL, document, CSV, repository or snapshot accepted | Passed | `TestItCannotBePointedAtRealData`, import graphs read with `ast` |
| No API route or frontend page invokes it | Passed | No import of it outside `app.ai` and its tests |
| Fails before reading credentials without `--allow-network` | Passed | Gate output above; asserted with a complete environment present |
| No default key, endpoint or model | Passed | `test_there_is_no_default_endpoint_model_or_key` |
| Key never printed, serialised, logged or raised | Passed | `TestTheKeyIsNeverWrittenDown`, 10 cases; image carries none |
| HTTPS required except for local test endpoints | Passed | 7 cases over remote http, local http, non-http and hostless URLs |
| No retries; every failure is typed | Passed | `TestNothingIsRetried` counts requests; 8 `FailureKind` members |
| No raw bodies, prose, headers or error bodies retained | Passed | `TestFailuresAreTypedAndCarryNothing` |
| Temperature zero, recorded, not claimed as reproducible | Passed | In the receipt; limitation stated above and in ADR-014 |
| Nothing repaired, nothing inferred | Passed | `TestNothingIsRepaired`, 20 cases |
| Unit tests never contact the network | Passed | Every test uses `MockTransport` or a dead port only in this report |
| Result artifacts untracked | Passed | `results/` in `.gitignore`, confirmed with `git check-ignore` |
| Database byte-identical before and after a run | Passed | Hashes above |
| No AI endpoint, no persistence, no path to a decision | Passed | Isolation suite; no route, no model, no writer added |
| A live hosted run with a real score | **Not performed** | No credentials configured. No number invented. |

## Unresolved

Nothing blocking. The one open item is the coarse failure counting in the
receipt, described above, which is a deliberate choice to leave the versioned
harness contract alone rather than a defect.
