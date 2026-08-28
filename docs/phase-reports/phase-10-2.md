# Phase 10.2: Make the corpus allow-list immutable at runtime, not in an annotation

- Date: 2026-08-28
- Exit gate: passed. No hosted run occurred, because no credentials are configured.
- Domain contract 5.0.0, parser 3.0.0, baseline 1.0.0, all unchanged
- Shadow link harness 5.0.0, shadow corpus 1.0.0, live receipt 2.0.0, all unchanged
- `ADR-014` amended again; `docs/phase-reports/phase-10-1.md` annotated, not rewritten

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

## The defect, reproduced first

Build a provider with a mutable `set` holding one authorised corpus page. Add a
second page's fingerprint to that same set. Ask about the second page.

```text
  built with a set holding 1 fingerprint
  page two authorised at construction : False
  caller widened its own set to       : 2 fingerprints
  requests that reached the transport : 1 ['/v1/chat/completions']
  outcome for page two                : {'outcome': 'ABSTAIN',
                                         'selected_source_record_ids': []}
  requests_made                       : 1
```

A page the provider was never authorised for reached the wire. The parameter was
annotated `frozenset[str]`, Python does not check annotations, and the provider
stored the caller's object rather than a copy. So the allow-list was whatever
the caller's set happened to contain at the moment `propose` looked at it.

Phase 10.1 called this "immutable, so a caller cannot widen it after
construction" and tested it by asserting that a `frozenset` has no `.add`. That
tests the standard library, not the provider.

**This is the third time the same error has been made here.** Phase 10 made
corpus-only a property of which arguments the CLI offered. Phase 10.1 moved it
into the adapter and made it a property of what the annotation said. Both times
the guarantee lived somewhere the runtime never looked.

## The fix

The provider copies the allow-list into a `frozenset` it owns. The copy is taken
before anything else in the constructor runs, so a provider never exists holding
an object somebody else can change, and what the caller passed is never
consulted again.

The parameter is now `Collection[str]`, which is what the code actually accepts
and what the docstring now says. The alternative was to check `isinstance(...,
frozenset)` and refuse everything else; the copy was chosen because it makes the
guarantee true regardless of what arrives, rather than making it the caller's
job to arrive correctly.

Two guards go with it, because a snapshot of the wrong thing is still immutable
and still wrong:

| Input | Result | Why |
| --- | --- | --- |
| `str` | `NotFingerprints` | A string is a collection of characters. Copying one builds a non-empty immutable allow-list of single letters, which refuses every real page while looking like a working provider |
| `bytes` | `NotFingerprints` | The same trap, one type along |
| A member that is not a `str` | `NotFingerprints` | It would never match, and a run where every page came back unauthorised would read as a scope problem rather than a typo |
| Any empty collection | `NothingAuthorised` | The no-permissive-default rule, unchanged and now covering every shape of emptiness |
| Omitted | `TypeError` | Keyword-only and required, unchanged |

mypy catches the `bytes` case and does not catch the `str` case, because a `str`
genuinely is a `Collection[str]`. That is the argument for the runtime guard in
one line: the type checker agrees with the annotation, and the annotation was
never the guarantee.

After the fix, the same reproduction:

```text
  requests that reached the transport : 0 []
  outcome for page two                : ProviderFailure(kind=REQUEST_NOT_AUTHORIZED)
  requests_made                       : 0
```

## Verified behaviour

`TestOnlyAuthorisedPagesAreAsked` grows from 8 cases to 18.

| Case | Proves |
| --- | --- |
| `test_a_caller_cannot_widen_the_scope_through_a_mutable_set` | The regression. A `set`, widened after construction, second page never reaches `MockTransport`, `requests_made == 0` |
| `test_the_page_it_was_built_with_still_works_afterwards` | The copy is a copy of what was passed. Removing the first fingerprint from the caller's set afterwards does not un-authorise it |
| `test_a_list_is_snapshotted_too` | Appending to a `list` after construction changes nothing |
| `test_a_frozenset_authorises_every_canonical_corpus_page` | All 24 pages reach the mocked host, `requests_made == 24` |
| `test_an_empty_collection_of_any_kind_is_refused` | 5 cases: `frozenset()`, `set()`, `[]`, `()`, `{}` |
| `test_a_bare_string_is_refused_rather_than_split_into_letters` | Refused, not snapshotted into letters |
| `test_bytes_are_refused_for_the_same_reason` | Same guard |
| `test_a_member_that_is_not_a_fingerprint_is_refused` | A non-string member is named rather than silently never matching |
| `test_it_cannot_be_built_without_an_allow_list_at_all` | Unchanged from Phase 10.1 |

The Phase 10.1 cases are unchanged and still pass: a page from another snapshot
is refused, the same corpus under different styling is refused, an unauthorised
page costs no budget, and authorisation is checked before the budget. All three
refusals are asserted with zero transport calls.

The test that was replaced asserted that a `frozenset` has no `.add` method. It
is gone rather than kept: a test that passes against the defect it was written
for is worse than no test, because it reads as coverage.

### The new tests fail against the old implementation

The retained reference was put back, with everything else left in place:

```text
6 of 18 failed in TestOnlyAuthorisedPagesAreAsked
```

The six are the mutable set, the list, the surviving first page, the bare
string, the bytes and the non-string member. The empty-collection cases pass
either way, correctly: emptiness was always checked.

## Observed results

```text
$ uv run python -m app.ai.live_shadow
error: this command calls a hosted model over the network. Re-run with
--allow-network to allow that.
exit: 2
```

The whole network path against a port with nothing behind it, unchanged from
Phase 10.1:

```text
invalid pages    : 1.000 (24/24)
report rejections: {'PROVIDER_FAILED': 24}
typed failures   : {'CONNECTION_FAILED': 24}
```

```text
db before: f59e27288bb674b948d4498435c2e0dff05eee2b7976b9b90044a5ec2008f296
db after : f59e27288bb674b948d4498435c2e0dff05eee2b7976b9b90044a5ec2008f296
DATABASE BYTE-IDENTICAL
```

The baseline benchmark report is byte-identical to Phase 10 and Phase 10.1:

```text
sha256 e5cff7b46a22c4d5b89ee0361ac1e373a4680f2f4a9ec268575b242cf60c4b5c
```

Regenerating the published JSON Schema produced no diff.

## Changed files

| File | Change |
| --- | --- |
| `backend/app/ai/hosted.py` | Snapshot into an owned `frozenset`; `NotFingerprints`; `Collection[str]`; docstrings corrected |
| `backend/tests/ai/test_hosted.py` | 10 new cases; the `.add` test replaced |
| `docs/adr/ADR-014-...md` | Phase 10.2 amendment |
| `docs/phase-reports/phase-10-1.md` | "Corrected later" section; original text left as written |

`live_shadow.py` is unchanged. It already passed a `frozenset` built from
`build_corpus()`, and `Collection[str]` accepts it.

## Commands run

```text
uv run ruff format --check .                    124 files already formatted
uv run ruff check .                             All checks passed
uv run mypy                                     no issues in 121 source files
uv run pytest                                   1574 passed, 100.00% coverage
pnpm run format:check                           Prettier clean
pnpm run lint                                   eslint, 0 warnings
pnpm run typecheck                              tsc clean
pnpm run test                                   98.5% statements, 92.09% branches
pnpm run build                                  built in 686ms
make schema                                     no diff
make benchmark-evaluate                         byte-identical to Phase 10
./scripts/verify-containers.sh                  passed, both non-root
```

Migration and legacy-adoption suites run inside `uv run pytest`, unchanged and
passing.

## Limitations

**No hosted model has been evaluated.** Everything here is adapter behaviour
against mocks and a dead socket.

**Everything Phase 10 and Phase 10.1 listed still stands.** A hosted run will
not be reproducible; the corpus is small, synthetic and written by this project;
the oversized-read bound is the budget plus one transport chunk; a 401 and a 429
stay deliberately indistinguishable; JSON framing is not a solution to prompt
injection; and no claim is made about production data.

**The allow-list is still only as good as what derives it.** That has not
changed and is not a defect. The provider now enforces exactly what it was
handed, and cannot be talked out of it afterwards. What it is handed is
`live_shadow`'s business, and a test asserts that set is the corpus.

**The guards are about type, not about content.** A 64-character string that is
not any real page's fingerprint is accepted into the allow-list and simply never
matches. Validating the format would not make the scope smaller, and validating
that each fingerprint belongs to a known corpus page would mean the adapter
building the corpus, which is the coupling this design exists to avoid.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| Defect reproduced first | Passed | Output above: 1 request sent, `requests_made == 1` |
| Runtime contract enforced, not merely annotated | Passed | The list is copied into an owned `frozenset` before anything else runs |
| A mutable collection cannot widen the scope | Passed | `set` and `list` cases, second page never reaches `MockTransport` |
| Refused before any client request can occur | Passed | Zero transport calls, `requests_made == 0` |
| Contract and docs updated honestly | Passed | `Collection[str]`, and the docstring says the value is copied |
| No permissive default preserved | Passed | 5 empty shapes, all `NothingAuthorised` |
| A valid frozenset still authorises every corpus page | Passed | 24 requests, 24 answers |
| Empty frozenset still refused | Passed | In the parametrised set |
| Wrong runtime type refused or safely snapshotted | Passed | `str` and `bytes` refused; `set`, `list`, `tuple` snapshotted |
| Restyled and non-corpus requests still refused before headers, budget or transport | Passed | Phase 10.1 cases unchanged and passing |
| New tests fail against the old implementation | Passed | 6 of 18 failed with the retained reference restored |
| Full CI, typing, 100% coverage, schema, baseline, containers | Passed | Commands above |
| A live hosted run with a real score | **Not performed** | No credentials configured. No number invented. |

## Unresolved

Nothing blocking. The open items are the ones Phase 10.1 listed, unchanged, plus
the note above that the guards check type rather than content. None is hidden
behind a passing row.
