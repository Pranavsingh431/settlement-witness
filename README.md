# Settlement Witness

An evidence-first AI finance controller for auditable payment-to-settlement reconciliation.

The goal is a controller that reconciles a payment lifecycle in batches and refuses to resolve an
exception unless it can return the source records and the accounting checks that support the
decision. Every case ends in one of three states: matched, exception, or insufficient evidence.

## Status

Phase 13 plus the evidence-to-closure product layer. Seven append-only tables,
four documented CSV schemas, two independent evidence-backed conclusions, and
no model output that can reach either of them.

**Phases 0 to 2** built the foundation and the contract. `backend/app/domain/`
defines what a fact, an amount, a lifecycle event, an invariant, an exception
and a decision mean, and enforces those meanings: a decision's status is derived
from its backing and never chosen. Documented CSV documents become immutable
source facts in SQLite, an import is accepted whole or not at all, and every
attempt leaves a receipt whether it succeeded or not.

**Phases 3 to 5** built the deterministic baseline, the seeded evaluation
harness, and durable runs. The baseline matches on exact references only. On the
demo fixtures one of three settlement lines resolves, which is the honest
number: a baseline that resolved all three would be guessing at two of them.
Runs are persisted immutably and served by a typed HTTP API, and schema changes
go through real migrations.

**Phases 6 and 7** added the CSV import API and the evidence-first dashboard.
The interface shows what a decision rests on rather than a green tick.

**Phases 8 to 10** added bounded AI link proposals in shadow mode. A model may
point at candidate records and never decide: the verifier judges every proposal
by the same deterministic rules, and no model output can reach a decision, a run
or the database. A hosted model is reachable only from one command, only against
a generated corpus, and never with imported merchant data.

**Phase 11** added the human review queue. A person can acknowledge, request
evidence, escalate, or close an exception without override. There is no approve
and no resolve, because a click cannot make a line supported.

**Phase 12** added bank finality. A `RESOLVED` line means the provider's own
records agree; whether a bank shows the money arriving is a separate conclusion
from separate evidence, and both are shown without being conflated.

**Phase 13** ran a pre-registered hosted-model shadow protocol over the
generated corpus only. The boundary held: no model output reached a decision or
the application database. Two planned runs completed and one was incomplete
because the model exceeded the bounded response size, so no aggregate was
published. That is a model-and-protocol observation, not reconciliation or
production performance.

**The product layer** turns every immutable decision into a versioned
evidence-to-closure plan. An unresolved line now names its owner, next bounded
action, exact proof required and whether today's verifier can check that proof.
It still closes only through authoritative evidence and a new reconciliation
run—never through a recommendation or review button. The research-backed
rationale and next product steps are in
[docs/product-thesis.md](docs/product-thesis.md).

There is no authentication and no multi-tenancy. The submitted Vercel link is a
shared **Track 04** synthetic batch demonstration for reviewers, not a
merchant-data or production deployment. It evaluates 59 generated payment,
settlement and payout scenarios, reports its auto-match rate and keeps every
exception visible; see [docs/deployment.md](docs/deployment.md).

The Evidence screen also provides four download-ready synthetic CSVs for the
hands-on path: 65 payment events, 59 settlement lines, 56 payouts and 56 bank
credits. Import them in the displayed order, create an audit, inspect the
exception certificates and run the separate bank-finality check. Do not upload
real merchant data to the public preview.

See [docs/domain-contract.md](docs/domain-contract.md) for what the contract
says, and the [phase reports](docs/phase-reports/) for exactly what was built and
verified in each phase.

## Prerequisites

| Tool | Version | Notes |
| --- | --- | --- |
| Git | any recent version | |
| Node.js | `>=24 <25` | The Node 24 LTS line only. The version is named in `frontend/.nvmrc`. |
| uv | 0.12 or newer | `make setup` installs it with Homebrew or pipx if it is missing. |
| pnpm | 10 or newer | `make setup` enables it through corepack if it is missing. |
| Docker | optional | Needed for `make verify` and `make docker-up`, not for `make ci`. |

Python itself is not a prerequisite. uv downloads and manages the Python version named in
`backend/.python-version`.

The project supports the Node 24 LTS line and nothing else. Node 26 and any other line are
rejected on purpose. One supported line means the version you run, the version the container
builds on and the version CI uses are always the same, so a problem cannot appear on one of them
and hide on the others. The same range is written in `frontend/.nvmrc`, in the `engines` field of
`frontend/package.json`, and in the check inside `scripts/setup.sh`.

If your Node is outside that line, `make setup` stops, names the supported range, and prints the
exact fix.

## Quick start

```bash
make setup
```

That single command checks your toolchain, installs the locked backend and frontend dependencies,
and creates a local `.env` from `.env.example`. Then run the checks:

```bash
make ci
```

To start both dev servers:

```bash
make dev
```

The backend serves `http://127.0.0.1:8000/health` and the frontend serves
`http://127.0.0.1:5173`. Press Ctrl-C to stop both.

## Commands

Run `make help` to see this list in your terminal.

| Command | What it does |
| --- | --- |
| `make setup` | Install every toolchain and dependency this repository needs |
| `make dev` | Run the backend and frontend dev servers together |
| `make test` | Run every test suite with its coverage gate |
| `make lint` | Check formatting and lint rules everywhere |
| `make format` | Rewrite files into the project style |
| `make typecheck` | Type check the backend and the frontend |
| `make build` | Produce the frontend production bundle |
| `make schema` | Regenerate the published JSON Schema from the domain models |
| `make db-setup` | Migrate the SQLite schema to head. Safe to run again. |
| `make api` | Run the backend API at `http://127.0.0.1:8000` |
| `make import-fixtures` | Import the documented example CSV documents through the command line |
| `make import-fixtures-http` | Import the same documents through the running API, then reconcile them |
| `make reconcile-fixtures` | Reconcile the imported facts and print JSON |
| `make benchmark-generate` | Write the public synthetic corpus and its manifest |
| `make benchmark-evaluate` | Score the baseline against the public corpus |
| `make ci` | Run the core local checks that CI mirrors. No Docker, no network. |
| `make verify` | Run the core checks plus the dependency audit and the container checks |
| `make audit` | Report known vulnerabilities in the locked dependencies. Needs network. |
| `make verify-containers` | Build the images, start them, and check they serve and run as non-root. Needs Docker. |
| `make clean` | Remove build output, caches and coverage reports |
| `make docker-up` | Build and start both services in containers |
| `make docker-down` | Stop the containers and remove their volumes |

Each of `test`, `lint`, `format` and `typecheck` also has a `-backend` and a `-frontend` variant,
for example `make test-backend`.

## Try it in a browser

Two terminals, then one page. No command line after the first step.

```bash
make db-setup
```

```bash
make dev
```

`make dev` starts the backend on `http://127.0.0.1:8000` and the interface on
`http://127.0.0.1:5173`. Open the second address.

The browser only ever asks for same-origin paths such as `/v1/imports`. Vite
carries those to the backend in development and nginx does the same job in the
container, so nothing in the bundle names a host and the backend needs no CORS
policy.

Then follow the evidence from a CSV file to a decision:

1. **Import evidence.** On *Import evidence*, download the four public synthetic
   samples and import them in the displayed order. Before choosing each CSV,
   press its matching **Use source → type** button: it declares `PSP_API` →
   `PAYMENT_EVENT`, `SETTLEMENT_LINE`, or `PAYOUT` for the provider files, and
   `BANK_STATEMENT` → `BANK_TRANSACTION` for the statement. The declaration is
   explicit rather than guessed from a filename or header row.
2. **Read the receipts.** Each upload returns the receipt the server recorded,
   showing what happened to every row. Upload the same file twice to see a
   `DUPLICATE_NO_OP` that writes nothing, or upload
   `data/fixtures/ingestion/invalid_mixed_rows.csv` to see a rejection whose
   receipt is kept even though no facts were written.
3. **Create a run.** Go to *Runs* and reconcile. Do it twice: the second attempt
   says the snapshot already had a run rather than writing a duplicate.
4. **Read a certificate.** Open the run and select a settlement line. The panel
   shows every invariant that held, broke or could not be checked, and every
   source record the decision cited with the hash of its payload.

A line is resolved only when its citations resolved and its required invariants
held. Exceptions and insufficient evidence are shown as what they are, not
folded into a success rate.

## Bank finality: did the money actually arrive

A `RESOLVED` settlement line means the provider's own records agree with each
other and with the invariants over them. **It does not mean the merchant has the
money.** A provider can be internally consistent while the transfer fails,
bounces, goes to a closed account, or was never made.

The only record that can say money arrived is a bank statement, so this system
reads one and audits it separately. Import a bank statement as
`BANK_TRANSACTION`, then record an audit from the run screen or with:

```bash
curl -X POST http://127.0.0.1:8000/v1/bank-finality/audits
```

A payout verifies when exactly one statement row carries its reference, that row
is a credit, and its amount and currency equal the payout's exactly. There is no
tolerance band, no rounding, no nearest-amount search, no date window and no
probable match. One minor unit of difference is a mismatch.

Seven outcomes, kept apart because the action a person takes differs for each:

| Outcome | Means |
| --- | --- |
| `VERIFIED_BANK_CREDIT` | A bank shows this exact credit arriving |
| `MISSING_BANK_EVIDENCE` | This system has not been shown it arriving. Not a claim that it did not |
| `UNLINKABLE_PAYOUT` | The payout carries no bank reference, so nothing can be matched exactly |
| `AMBIGUOUS_BANK_EVIDENCE` | Two or more rows carry the reference, and choosing one would invent a fact |
| `BANK_DIRECTION_MISMATCH` | The row is a debit |
| `BANK_AMOUNT_MISMATCH` | The credit is for a different amount |
| `BANK_CURRENCY_MISMATCH` | The credit is in a different currency |

None of those is a `DecisionStatus`, the two vocabularies share no value, and
the interface shows them in visibly different badges with a sentence between
them saying they are separate conclusions. Audits are immutable: importing a
statement later records a new audit beside the old one rather than rewriting
what was known before.

**The standing limitation.** Exact-reference matching cannot verify a payout
whose provider record and bank record share no reference. That is not a defect
to be fixed with a cleverer matcher; closing it needs a shared reference in the
data. See
[ADR-016](docs/adr/ADR-016-settlement-agreement-is-not-bank-finality.md).

## The human review queue

The lines a run did not resolve, and what people are doing about them. Reachable
from the overview and from any run's audit screen, at `/runs/<run id>/review`.

A reviewer can record four things: acknowledge, request evidence, escalate, and
close without override. There is no approve, no resolve and no override, and the
last action is named the way it is because the name is the guarantee. A closed
item still carries the `EXCEPTION` or `INSUFFICIENT_EVIDENCE` the baseline gave
it, and both the API and the screen say so.

That is not a missing feature. A settlement line is resolved when the records it
cites are present and the invariants over them hold. If those records are
absent, the only thing that changes it is the records arriving, imported and
reconciled into a new run. A button that set the status would be asserting
something about the world on no evidence.

Review events are append-only, ordered by a sequence the database assigns, and
stored beside a decision rather than inside it. They change no status, no code,
no invariant result and no evidence, and a test compares every stored decision
byte for byte before and after every action to prove it.

**There is no reviewer recorded**, because this application has no
authentication. That is a limitation and not a design choice: the log answers
what happened and cannot answer who is accountable. See
[ADR-015](docs/adr/ADR-015-review-events-annotate-they-do-not-decide.md).

## Running the shadow corpus against a hosted model

Optional, and off by default. This is the only place the project calls a third
party, and it evaluates a generated corpus rather than anything imported.

```bash
cp .env.ai.example .env.ai
```

Fill in all six required settings in `.env.ai`, which is ignored by git, then
run the fixed Phase 13 protocol:

```bash
make phase-13
```

It loads the local file without echoing it, freezes a plan before any hosted
call, then performs **exactly three** independent calls through
`python -m app.ai.live_shadow --allow-network`. The run count is a committed
constant, not a flag. A missing or invalid configuration stops before a plan,
request or score exists. A typed provider failure is recorded as an incomplete
run; it is not retried and it is not blended into a successful aggregate.

Every local artifact goes under ignored `results/phase-13/`: the frozen plan,
the three raw receipts, a before/after proof around each call, three safe run
records and one summary. The publishable records include only the provider
hostname, model identity, settings, corpus/hash/version, metrics and counts.
They exclude the key, endpoint path, prompts, response bodies, source-record
identifiers and provider error prose.

The database proof hashes the complete SQLite file set (including WAL sidecars)
and separately hashes `source_facts`, `import_receipts`, reconciliation runs
and decisions, review events, bank audits and bank certificates. These hashes
are local proof only: the hosted request is still built solely from the
generated corpus, and no database value reaches the provider.

`--allow-network` remains required. Without it the hosted command stops before
it reads any credential, so a run started by accident cannot send one.

**What leaves the machine.** The corpus is generated in memory from a fixed seed
and every identifier in it is a digest, so the request carries opaque tokens and
their rendered reference fields. No canonical fact, no payload hash, no money, no
CSV, no document text, and nothing that was ever imported. The command has no
database, file or snapshot argument, and neither it nor the adapter imports
anything that could read the store.

**What comes back.** Only a selection, judged by the same validator a fixture's
answer meets. Nothing is repaired and nothing is retried. The model cannot
produce a decision, a run, or a row in any table.

The protocol reports strict link recall, answered-link recall, precision,
exact-set accuracy, false-link rate, safe abstention, unsafe selection,
invalid-page rate, typed failure counts and request count. It deliberately has
no generic headline score. This is a generated regression/shadow corpus, not a
representative production dataset or evidence of real-merchant performance.

See [ADR-014](docs/adr/ADR-014-hosted-models-are-corpus-only.md) for why this is
corpus only.

## Repository layout

```text
backend/          FastAPI service, Python tooling and tests
frontend/         React and TypeScript workspace
benchmark/        Dataset generation and evaluation harness (later phases)
data/fixtures/    Small committed fixtures
data/generated/   Generated datasets and run output, ignored by git
docs/adr/         Architecture decision records
docs/phase-reports/  What each phase built and what was verified
scripts/          Setup and developer scripts
.github/workflows/   Continuous integration
```

## Configuration

Copy `.env.example` to `.env` and edit it. `make setup` does this for you. The file is ignored by
git.

Every backend setting is read with the `SW_` prefix. The backend reads `.env` from the repository
root no matter which directory you start it from, so the same file applies to `make dev`,
`make dev-backend` and an editor run configuration. Environment variables take priority over the
file.

| Setting | Default | Meaning |
| --- | --- | --- |
| `SW_APP_ENV` | `local` | One of `local`, `test`, `ci`, `production`. Reload is on only in `local`. |
| `SW_LOG_LEVEL` | `INFO` | One of `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `SW_API_HOST` | `127.0.0.1` | Address the backend binds to. |
| `SW_API_PORT` | `8000` | Port the backend binds to. |
| `SW_MAX_UPLOAD_BYTES` | `8388608` | Largest CSV document `POST /v1/imports` accepts. The request carrying it is bounded at this plus 8 KiB, counted before anything parses it, so a client that sends no `Content-Length` or a false one is refused the same way. Either refusal is a 413 that leaves no receipt. |

An invalid value stops the service at startup instead of failing later.

## Containers

```bash
make docker-up
```

This builds both images and starts them. The backend listens on port 8000 and the frontend is
reachable on port 5173, the same addresses `make dev` uses.

Compose binds both ports to `127.0.0.1`, not the machine's network interfaces,
and keeps the SQLite audit trail in the named `settlement_witness_data` volume.
An ordinary restart preserves that volume; `make docker-down` removes it
deliberately so the next local demonstration starts clean.

Neither container runs as root. The backend runs as UID 999, from a system user created in its
Dockerfile. The frontend runs as UID 101, using the unprivileged nginx image rather than the
standard one. That distinction matters: the standard nginx image starts its master process as
root and drops only the workers, so a root process stays in the container. The unprivileged image
runs everything as UID 101, and because a process without root cannot bind a port below 1024, the
server listens on 8080 inside the container. Compose maps host port 5173 onto it, so nothing
changes for you.

`make verify-containers` checks all of this rather than assuming it. It builds the images, starts
them, confirms both answer on their published ports, confirms the single page fallback works, and
reads the UID inside each container to confirm it is not 0.

The container images are production style, so they do not reload on file changes. Use `make dev`
for that.

Read [docs/deployment.md](docs/deployment.md) before sharing a running instance
outside your machine. This project intentionally has no authentication, so the
local Compose configuration is safe for a local demo and is **not** a public
deployment recipe.

## Continuous integration

Every push to `main` and every pull request runs five jobs:

1. Backend checks: formatting, lint rules, strict type checking, tests with a coverage gate.
2. Frontend checks: formatting, lint rules, type checking, tests with a coverage gate, and the
   production build.
3. Secret scan across the full history.
4. Dependency audit for both the backend and the frontend lockfiles.
5. Container checks: both images are built, then both are started and checked. The backend must
   answer `/health`, the frontend must serve its index and an unknown client route, and neither
   container may run as UID 0.

`make ci` runs the core checks from jobs 1 and 2. It needs neither Docker nor network access,
which is what keeps it usable as the ordinary command you run before pushing.

`make verify` is the wider local pass. It runs `make ci`, then the dependency audit from job 4,
then the container build and health checks from job 5. Job 3, the secret scan, has no local
equivalent, because it scans the pushed history.

Every action in the workflow is pinned to a full commit SHA, with the release named in a trailing
comment. A git tag is a mutable reference. Whoever controls an action repository can move it to
point at different code, and the pipeline would pick that up with nothing in this repository
changing. A commit SHA names the exact tree that was reviewed. Dependabot reads the trailing
comment, bumps the SHA and the comment together, and opens a weekly pull request, so the pins stay
current.

## Money and correctness conventions

These rules apply from the first line of domain code in later phases:

These are no longer aspirations. Phase 1 turned each one into code, and
[docs/domain-contract.md](docs/domain-contract.md) explains how.

- All monetary amounts are integers in minor units. Floating point is never used for money, and
  the published schema contains no `"number"` type anywhere.
- Ingestion is idempotent, because duplicate events are expected. An identical replay is a no-op;
  the same identity with a different payload is a conflict.
- Events carry both an observed time and an occurred time, because they can arrive out of order.
- Source facts are append-only. A correction is a later fact, never an edit.
- The controller never edits financial records. Suggested repairs are reviewable work items.
- Secrets never reach the frontend or the logs.

## Documentation

- [docs/domain-contract.md](docs/domain-contract.md) explains the domain contract. The models in
  `backend/app/domain/` are the definition; that page describes them.
- [docs/ingestion-contract.md](docs/ingestion-contract.md) explains the four CSV schemas, the
  refusal rules, and how imports are made atomic and auditable.
- [docs/reconciliation-baseline.md](docs/reconciliation-baseline.md) explains what the baseline
  matches, what it refuses to match, and what a result does and does not mean.
- [ADR-016](docs/adr/ADR-016-settlement-agreement-is-not-bank-finality.md) explains why bank
  finality is a separate conclusion from a settlement decision, and why nothing here matches
  approximately.
- [docs/evaluation-harness.md](docs/evaluation-harness.md) explains the seeded generator, the
  independent oracle, and the public and private evaluation boundary.
- [docs/api.md](docs/api.md) documents the backend API, with real example responses and what it
  deliberately does not expose.
- [docs/deployment.md](docs/deployment.md) names the controls required before a remote demo.
- [docs/submission.md](docs/submission.md) is a fact-checked demo and submission outline.
- [docs/evaluation-contract.md](docs/evaluation-contract.md) defines how the system will be
  graded. It was written before the system it grades.
- [docs/schema/v5/](docs/schema/v5/) holds JSON Schema generated from the models by
  `make schema`. A test fails if it drifts from the code.
- [docs/adr/](docs/adr/) records the decisions and why they were made.
- [docs/phase-reports/](docs/phase-reports/) records what each phase built and verified.
- [CONTRIBUTING.md](CONTRIBUTING.md) explains the development workflow.
- [SECURITY.md](SECURITY.md) explains how to report a vulnerability.

## Licence

MIT. See [LICENSE](LICENSE).

This is an independent project. It is not affiliated with, endorsed by, or connected to any
payment provider.
