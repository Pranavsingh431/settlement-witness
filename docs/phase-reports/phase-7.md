# Phase 7: Evidence-first reconciliation dashboard

- Date: 2026-08-26
- Exit gate: passed. See "Exit gate status".
- Domain contract 5.0.0, parser 3.0.0, baseline 1.0.0, harness 2.0.0, all unchanged

## Scope

The frontend. The Phase 0 shell is replaced with four screens that carry a
person from a CSV file to a decision certificate without a terminal. No backend
domain rule, endpoint, response shape or version changed. `make schema` was run
and produced a byte identical result.

## What a judge can now do

Open `http://localhost:5173` and follow the evidence:

| Screen | What it is for |
| --- | --- |
| `/` | What the system claims, the three answers a line can get, and where the data stands |
| `/imports` | Upload a document, read the receipt the server recorded, page and filter the history |
| `/runs` | Reconcile, and see the run history |
| `/runs/:runId` | Every decision, and the certificate behind the selected one |

The full loop was run against the real stack and against the built containers:
three documents imported, a run created, a certificate read.

## The two things this interface has to get right

### A line is resolved only when the evidence supports it

The three states are explained on the overview before any number is shown, and
each is a badge carrying a glyph, a colour and the word. The palettes differ in
lightness as well as hue, so they survive being read in greyscale, and no status
is ever communicated by colour alone.

There is no accuracy figure, no success rate and no percentage anywhere. Tests
assert that, because the easiest way to ruin this product is to average its
three answers into one reassuring number.

An empty store shows an explanation and a next step rather than a dashboard of
zeroes. Zeroes in the layout of real counts read as a clean bill of health.

### A certificate must not flatten its own findings

A check that held, a check that broke and a check that could not be run are
three different answers, and they are drawn three different ways. So is a
citation that resolved against one that found nothing.

Real data caught a copy bug here during verification. The screen said "at least
one rule about it did not hold" for every exception, and `line-0001` of the
demo corpus is an exception where every invariant held: the baseline reported
`PARTIAL_REFUND` on its own. The headline is now derived from the decision's own
invariant results, and a test holds it there.

#### Corrected in Phase 7.1

That fix was applied to the certificate and not to the dashboard, which went on
saying the same false thing about the same decision:

> The evidence is there and a rule about it does not hold.

A failed invariant means an exception. An exception does not mean a failed
invariant, and `line-0001` is the case that proves it. Fixing the detailed view
while leaving the summary wrong is arguably worse than leaving both: a reader
who takes the overview at its word never opens the certificate that contradicts
it.

The card now says the records needed to judge the line were there, that the
baseline reports a finding instead of resolving it, and that the certificate is
where you find out whether a required check failed or a lifecycle state was
reported. The insufficient-evidence card was tightened in the same pass: it
described only the missing-fact route to that status, and a required invariant
with no input reaches it too.

See [phase-7-1.md](phase-7-1.md).

Money is shown only where the API sends it, which is the expected and observed
values on an invariant result. Those are minor units and the API sends no
currency with them, so they are grouped, labelled `minor units`, and given no
symbol. Inventing one would be inventing a fact.

## Architecture

### A fragility CI caught that local verification could not

The first version wrote the upstream host literally, as
`proxy_pass http://backend:8000`. nginx resolves a literal upstream while it is
starting and refuses to start when it cannot, so the frontend image would not
run at all unless a host called `backend` already existed:

```text
nginx: [emerg] host not found in upstream "backend"
```

`make verify-containers` uses Compose, where that name always resolves, so it
passed. The CI container job runs each image standalone with `docker run`, where
it does not, and the frontend container never started.

Local verification could not have found this, and the fix is worth having on its
own terms: an image that will not start without an unrelated service is the
wrong coupling. The upstream is now passed through a variable with a `resolver`
declared, which defers resolution to request time. The image starts either way,
and a request made with no backend behind it returns 502, which is the truthful
answer. Verified both ways afterwards: standalone serves the index and the
fallback and answers 502 on `/v1`, and under Compose every proxy check passes.

Two tests hold it: the config must declare a resolver, and it must not contain a
literal `proxy_pass http://backend`.

### Same-origin only

The browser only ever asks for relative paths such as `/v1/imports`. Vite
proxies those in development and nginx proxies them in the container. No host
appears in the client, in an environment variable or in the built bundle, and
the backend gained no CORS policy.

That is a decision with a cost, recorded in
[ADR-011](../adr/ADR-011-same-origin-api-instead-of-cors.md): the interface does
not work without a proxy, and a missing one fails confusingly. It is paid for
with tests on both proxy configurations and with container checks that request
`/v1/health` and `/v1/imports` through the frontend's own port.

### One typed client, nothing cast

`src/api/` holds the response types, a client, and validators. Every response is
read field by field before it is returned. A cast would let a missing field
arrive as `undefined` and be rendered as an empty cell, which looks like a fact
about the data rather than a defect in the plumbing.

Enum values are deliberately not checked against a fixed list. The backend owns
those vocabularies and can add to them, and losing a whole run because one
decision carries an unfamiliar code would be worse than showing the code.

Three failure kinds are told apart because the screen has to say something
different about each: a refusal from the API, whose message is passed through as
the backend wrote it; a backend that cannot be reached, which gets a retry; and
a response that did not make sense, which is reported rather than treated as
empty data.

### One dependency added

`react-router-dom`, for four routes. Hand-rolling `<Link>`, history handling,
route parameters and the current-page marking is a small amount of code and a
large amount of accessibility to get wrong. Nothing else was added to the
runtime; `@testing-library/user-event` was added to the dev dependencies for
keyboard tests.

## Accessibility

- A skip link and one `main` landmark in the shell, so the navigation is passed
  once rather than on every route.
- Every control has a real label. Both selects, the file input and every filter
  are labelled elements, not placeholder text.
- Every table has a caption and scoped headers, and the caption says whether the
  view is filtered.
- Status is a glyph plus a word plus a colour, never a colour alone.
- Upload results, run results and errors are in live regions, and errors carry
  `role="alert"`.
- Decision selection is a real button with `aria-pressed`, so a reader who
  cannot see the highlight is still told which line is open.
- Statistic groups are named, so the numbers can be addressed as a unit.
- One focus ring style for everything, at 2px with an offset.

Tests cover the keyboard paths directly: completing the upload form by tabbing
and pressing Enter, and selecting a decision by focus and Enter.

## Changed files

| File | Change |
| --- | --- |
| `frontend/src/api/{types,errors,parse,client}.ts` | New. The typed client and its validators |
| `frontend/src/{format,hooks}.ts` | New. Deterministic formatting, and loading with a stale-answer guard |
| `frontend/src/components/{ui,ReceiptView,DecisionCertificate}.tsx` | New. Badges, panels, states, and the two domain views |
| `frontend/src/routes/*.tsx` | New. The four screens |
| `frontend/src/{App,main}.tsx`, `frontend/src/styles.css` | The shell, the router, and one stylesheet |
| `frontend/vite.config.ts` | The `/v1` development proxy |
| `frontend/nginx.conf` | The `/v1` production proxy with a resolver and a variable upstream, the health passthrough, and a body limit above the nginx default |
| `frontend/package.json` | `react-router-dom`, and `@testing-library/user-event` for tests |
| `frontend/tsconfig.app.json` | Includes `vite.config.ts`, which the proxy test reads |
| `scripts/verify-containers.sh` | Four new checks on the proxy |
| `README.md` | The browser demo path |
| `docs/adr/ADR-011-...md` | New |

`frontend/src/App.css` and the Phase 0 shell test were deleted.

## Commands run

| Command | Exit | Observed |
| --- | --- | --- |
| `git diff --check` | 0 | No whitespace errors |
| `make ci` | 0 | All nine core checks passed |
| `pnpm run lint` | 0 | Clean at `--max-warnings 0` |
| `pnpm run typecheck` | 0 | Clean under the existing strict settings |
| `pnpm run test` | 0 | `177 passed`, statements 98.5%, branches 92.1%, functions 98.3%, lines 98.8% |
| `pnpm run build` | 0 | Production bundle built |
| `uv run pytest` | 0 | `990 passed`, backend unchanged |
| `make schema` | 0 | Byte identical |
| `make verify-containers` | 0 | Including four new proxy checks |
| Frontend image standalone, as CI runs it | 0 | Starts with no backend, serves index and fallback, 502 on `/v1` |
| Full flow in a browser against `make dev` | 0 | Upload through the form, run, certificate |
| Full flow through the container frontend origin | 0 | Three documents, run with 10 facts and 3 decisions |

## Tests

177 frontend tests, up from 2. The backend suite is unchanged at 990.

| File | Tests | Covers |
| --- | --- | --- |
| `src/api/client.test.ts` | 30 | Relative paths, every endpoint, 200 versus 201, the nested envelope, network failure, malformed bodies |
| `src/api/parse.test.ts` | 19 | Missing fields, wrong types, nullable versus absent, and an unfamiliar enum surviving |
| `src/routes/ImportsPage.test.tsx` | 33 | Four outcomes, 413, unreachable backend, drag and drop, filters, paging, keyboard, no CSV rendered |
| `src/routes/RunAuditPage.test.tsx` | 28 | Metadata, filters, keyboard selection, and the certificate telling held from broken from unknown |
| `src/format.test.ts` | 16 | Deterministic grouping and UTC timestamps, no currency symbol |
| `src/routes/RunsPage.test.tsx` | 16 | New versus reused run, the 409 refusal, in-flight guard |
| `src/routes/DashboardPage.test.tsx` | 14 | The empty state, real counts, no percentage anywhere |
| `src/proxy.test.ts` | 12 | Both proxy configurations, ordering against the fallback, and deferred upstream resolution |
| `src/App.test.tsx` | 9 | Routing, the skip link, current-page marking |

The fixtures are payloads copied from a running backend, not invented, so a test
that passes is a test against a shape the server actually sends.

## Limitations

1. **No screenshots.** They were left out rather than staged: generating them
   from the real application needs a capture step this repository does not have,
   and a hand-made image labelled as a demo would still be a picture of
   something other than the running system. The README describes the path
   instead.
2. **No end-to-end browser test in CI.** Playwright is in the frozen stack and is
   not installed. The container check exercises the proxy and the API through
   the frontend's own origin, which is the part that breaks silently; driving
   the interface itself is covered by component tests against mocked responses.
3. **The interface will not work without a proxy.** Deliberate, and the cost of
   ADR-011. A missing proxy returns the app shell for `/v1` paths and every
   screen reports a malformed response.
4. **Import history paging is offset based**, so a receipt written between two
   page requests can shift the boundary. Receipts are append-only and ordered by
   a sequence that only grows, so the effect is a repeated row rather than a
   skipped one.
5. **The upload posts the whole file in one request.** No chunking and no
   progress. The backend bounds the request at 8 MB by default, which is well
   past the demonstration corpus.
6. **Still no authentication.** The interface makes that more visible, not less
   true. Anyone who can reach the page can write facts and create runs, which is
   why this stack must not be exposed to an untrusted network.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| Same-origin relative paths, no hard-coded host | Passed | Asserted in the client tests and by the built bundle |
| Vite `/v1` proxy | Passed | Configured and tested |
| nginx `/v1` proxy to `backend:8000`, SPA fallback and non-root kept | Passed | Tested, and verified in the container |
| No permissive backend CORS added | Passed | Backend untouched |
| Typed client, no unchecked casts, errors parsed from the envelope | Passed | 49 client and validator tests |
| Backend-unavailable state with retry | Passed | On all four screens |
| No fake data, metrics or frontend-only decisions | Passed | No percentage or accuracy figure anywhere, asserted |
| Dashboard: claim, three states, latest run, recent imports, honest empty state | Partly overclaimed | The exception card equated an exception with a failed rule. Corrected in Phase 7.1 |
| Import: declared type and system, expected files, in-flight guard, receipt shown | Passed | 33 tests |
| Four import outcomes distinguished, rejections say no facts were written | Passed | One test each |
| Row outcomes shown, no raw CSV rendered | Passed | Asserted against real document bytes |
| History pagination, filters, filtered state | Passed | 6 tests |
| Runs: 201 versus 200 distinguished, 409 handled, no invented accuracy | Passed | 16 tests |
| Run audit: metadata, filters, distinct status badges, detail panel | Passed | 28 tests |
| Certificate separates passed, failed and missing | Passed | Three tests, one per outcome |
| Minor units shown only where present, labelled, no inferred currency | Passed | Two tests |
| No override, and no claim that a hash is a document | Passed | Asserted in the certificate panel |
| Accessible controls, labels, keyboard, captions, live regions, contrast | Passed | Including two keyboard-path tests |
| No external analytics, fonts, APIs or remote images | Passed | System font stack, no network references |
| Proxy configurations tested | Passed | 11 tests |
| Container check proxies a backend endpoint | Passed | Four new checks |
| Build, lint, typecheck, both suites, schema, containers | Passed | All exit 0 |
