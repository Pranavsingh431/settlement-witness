# Settlement Witness

An evidence-first AI finance controller for auditable payment-to-settlement reconciliation.

The goal is a controller that reconciles a payment lifecycle in batches and refuses to resolve an
exception unless it can return the source records and the accounting checks that support the
decision. Every case ends in one of three states: matched, exception, or insufficient evidence.

## Status

This repository is at phase 0. Phase 0 builds the foundation only. There is no reconciliation
logic yet. What exists is a working toolchain: dependency locking, formatting, linting, strict
type checking, tests with coverage gates, container images, and a continuous integration
pipeline. Later phases add the data model, the reconciliation core, the evaluator, and the user
interface on top of this base.

See [docs/phase-reports/phase-0.md](docs/phase-reports/phase-0.md) for exactly what was built and
verified.

## Prerequisites

| Tool | Version | Notes |
| --- | --- | --- |
| Git | any recent version | |
| Node.js | `^20.19.0 \|\| ^22.13.0 \|\| >=24` | The pinned version is in `frontend/.nvmrc`. |
| uv | 0.12 or newer | `make setup` installs it with Homebrew or pipx if it is missing. |
| pnpm | 10 or newer | `make setup` enables it through corepack if it is missing. |
| Docker | optional | Only needed for `make docker-up`. |

Python itself is not a prerequisite. uv downloads and manages the Python version named in
`backend/.python-version`.

The Node range is not a preference. Some locked frontend dependencies declare it in their own
`engines` field, and pnpm stops the install rather than producing a broken tree. If your default
Node is outside the range, `make setup` says so and prints the fix.

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
| `make ci` | Run the same checks the pipeline runs |
| `make audit` | Report known vulnerabilities in the locked dependencies |
| `make clean` | Remove build output, caches and coverage reports |
| `make docker-up` | Build and start both services in containers |
| `make docker-down` | Stop the containers and remove their volumes |

Each of `test`, `lint`, `format` and `typecheck` also has a `-backend` and a `-frontend` variant,
for example `make test-backend`.

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

An invalid value stops the service at startup instead of failing later.

## Containers

```bash
make docker-up
```

This builds both images and starts them. The backend listens on port 8000 and the frontend is
served by nginx on port 5173. Both images run as an unprivileged user and carry a health check.
The container images are production style, so they do not reload on file changes. Use `make dev`
for that.

## Continuous integration

Every push to `main` and every pull request runs five jobs:

1. Backend checks: formatting, lint rules, strict type checking, tests with a coverage gate.
2. Frontend checks: formatting, lint rules, type checking, tests with a coverage gate, and the
   production build.
3. Secret scan across the full history.
4. Dependency audit for both the backend and the frontend lockfiles.
5. Container build for both images, followed by a live health check against the backend image.

`make ci` runs jobs 1 and 2 locally. Jobs 3, 4 and 5 need network access or Docker, so they are
kept out of the default local target. Run `make audit` and `make docker-up` when you want them.

## Money and correctness conventions

These rules apply from the first line of domain code in later phases:

- All monetary amounts are integers in minor units. Floating point is never used for money.
- Ingestion is idempotent, because duplicate events are expected.
- Events carry both an event time and an ingestion time, because they can arrive out of order.
- The controller never edits financial records. Suggested repairs are reviewable work items.
- Secrets never reach the frontend or the logs.

## Documentation

- [docs/adr/](docs/adr/) records the decisions and why they were made.
- [docs/phase-reports/](docs/phase-reports/) records what each phase built and verified.
- [CONTRIBUTING.md](CONTRIBUTING.md) explains the development workflow.
- [SECURITY.md](SECURITY.md) explains how to report a vulnerability.

## Licence

MIT. See [LICENSE](LICENSE).

This is an independent project. It is not affiliated with, endorsed by, or connected to any
payment provider.
