# Phase 0: Repository foundation

- Date: 2026-08-24
- Exit gate: passed, locally and on the pipeline. See "Exit gate status".

## Objective

Create a reproducible repository that every later phase can extend safely. No application
functionality is required at this phase.

## What was built

### Repository scaffold

```text
backend/          FastAPI service, Python tooling and tests
frontend/         React and TypeScript workspace
benchmark/        Reserved for the generator and evaluator, documented but empty
data/fixtures/    Small committed fixtures
data/generated/   Generated output, ignored by git
docs/adr/         Architecture decision records
docs/phase-reports/
scripts/          Setup and developer scripts
.github/workflows/
```

### Backend toolchain

- Python 3.12, pinned in `backend/.python-version` and installed by uv, so Python itself is not a
  prerequisite.
- Ruff formats and lints. Line length 100. Rule sets: pycodestyle, pyflakes, isort, bugbear,
  comprehensions, pyupgrade, annotations, bandit, builtins, ruff, pytest style, simplify and tidy
  imports.
- mypy in strict mode over both `app` and `tests`.
- pytest with branch coverage, a 90 percent gate, and warnings treated as errors.
- `backend/uv.lock` is committed and every install uses `--frozen`.

### Backend code

Deliberately minimal. It exists so the toolchain, the image and the pipeline are exercised against
real code rather than against an empty directory.

- `app/config.py`: settings from `SW_` prefixed environment variables and the repository root
  `.env`. Environment variables win. Invalid values fail at startup.
- `app/main.py`: application factory and a `/health` endpoint.
- `app/__main__.py`: the entry point that starts the server on the configured address. Reload is
  on only when `SW_APP_ENV` is `local`.

The settings module is anchored to the repository root, so the backend reads the same `.env`
whether it starts from the repository root, from `backend/`, or from an editor. This was verified
directly rather than assumed. See "Commands run and observed results".

### Frontend toolchain

- Node range `^20.19.0 || ^22.13.0 || >=24`, pinned in `frontend/.nvmrc` and declared in
  `engines`.
- Vite 8, React 19, TypeScript 6 in strict mode with `noUncheckedIndexedAccess` and
  `exactOptionalPropertyTypes`.
- ESLint 10 flat config with the type aware strict and stylistic rule sets, the React hooks rules
  and the React refresh rule. `--max-warnings 0`, so a warning fails the build.
- Prettier for formatting, with `eslint-config-prettier` so the two never disagree.
- Vitest 4 with jsdom, React Testing Library and a 90 percent coverage gate.
- `frontend/pnpm-lock.yaml` is committed and every install uses `--frozen-lockfile`.

TypeScript is held below the 7 line on purpose. `typescript-eslint` 8.67 declares a peer range of
`>=4.8.4 <6.1.0`, so TypeScript 7 cannot be used yet without giving up type aware linting. 6.0.3
is the newest version inside that range, and it was checked against format, lint, typecheck, test
and build before being adopted.

### Make targets

`setup`, `dev`, `test`, `lint`, `format`, `typecheck` and `ci` are all present, as required. Also
added: `help`, `build`, `audit`, `clean`, `docker-build`, `docker-up`, `docker-down`, and a
`-backend` and `-frontend` variant of each check target.

`make ci` runs lint, typecheck, test and build. The Makefile avoids `.SHELLFLAGS` and `.ONESHELL`,
so it works on the GNU Make 3.81 that ships with macOS.

### Continuous integration

`.github/workflows/ci.yml` runs five jobs on every push to `main` and every pull request:

1. Backend checks: format, lint, mypy, pytest.
2. Frontend checks: format, lint, typecheck, test, build.
3. Secret scan with gitleaks over the full history.
4. Dependency audit with pip-audit and `pnpm audit`.
5. Container build for both images, then a live health check against the running backend image.

Every action is pinned to an exact version rather than a floating major tag. A floating tag can
move underneath the pipeline, which makes a run non-reproducible, and not every action publishes
one. `astral-sh/setup-uv` has no `v10` tag at all, which is how the first pipeline run failed.

`.github/dependabot.yml` opens weekly update pull requests for uv, npm, GitHub Actions and the
container base images. Three version pins are excluded, because they have to match a version that
is pinned elsewhere, and bumping one alone would break the build rather than improve it:

| Excluded | Reason |
| --- | --- |
| `python` image, major and minor | Has to match `backend/.python-version` and the `requires-python` range |
| `node` image, major | Has to match `frontend/.nvmrc`, so the bundle is built on a supported Node |
| `@types/node`, major | Has to match the Node version the project runs |

This was not written from theory. Dependabot's first run opened pull requests to move the Python
image to 3.14, the Node image to 26 and `@types/node` to 26, each of which contradicts a pin. The
ignore rules were added in response.

### Containers

- `backend/Dockerfile`: installs only the locked runtime dependencies, runs as an unprivileged
  user, carries a health check, and starts through the same `python -m app` entry point the
  developer commands use.
- `frontend/Dockerfile`: two stages. The build stage produces the bundle. The runtime stage is
  nginx with only the static files, so no build tooling reaches the running container.
- `docker-compose.yml` wires the two together, with the frontend waiting for the backend to report
  healthy.

### Documentation

- `README.md`: prerequisites, quick start, the command table, the layout, the settings table, the
  container instructions and the pipeline description.
- `CONTRIBUTING.md`: the workflow, the phase rules, the style rules and the dependency policy.
- `SECURITY.md`: how to report a vulnerability, plus the concrete measures in place.
- `docs/adr/ADR-001-stack-and-modular-monolith.md`: the stack, why the system is a modular
  monolith, the consequences and eight rejected alternatives.
- `benchmark/README.md` and `data/README.md`: what those directories are for and why they are
  still empty.

## Files added

56 tracked files.

| Area | Files |
| --- | --- |
| Root | `.dockerignore`, `.editorconfig`, `.env.example`, `.gitignore`, `CONTRIBUTING.md`, `LICENSE`, `Makefile`, `README.md`, `SECURITY.md`, `docker-compose.yml` |
| Backend | `.dockerignore`, `.python-version`, `Dockerfile`, `pyproject.toml`, `uv.lock`, `app/__init__.py`, `app/__main__.py`, `app/config.py`, `app/main.py`, `tests/__init__.py`, `tests/conftest.py`, `tests/test_config.py`, `tests/test_entrypoint.py`, `tests/test_health.py` |
| Frontend | `.dockerignore`, `.npmrc`, `.nvmrc`, `.prettierignore`, `.prettierrc.json`, `Dockerfile`, `eslint.config.js`, `index.html`, `nginx.conf`, `package.json`, `pnpm-lock.yaml`, `vite.config.ts`, `tsconfig.json`, `tsconfig.app.json`, `tsconfig.node.json`, `src/App.css`, `src/App.test.tsx`, `src/App.tsx`, `src/main.tsx`, `src/setupTests.ts`, `src/vite-env.d.ts` |
| CI | `.github/workflows/ci.yml`, `.github/dependabot.yml` |
| Scripts | `scripts/setup.sh`, `scripts/dev.sh` |
| Docs and data | `docs/adr/README.md`, `docs/adr/ADR-001-stack-and-modular-monolith.md`, `docs/phase-reports/README.md`, `docs/phase-reports/phase-0.md`, `benchmark/README.md`, `data/README.md`, `data/fixtures/.gitkeep`, `data/generated/.gitkeep` |

No files were changed or deleted. This is the first phase.

## Commands run and observed results

Host: macOS on arm64, GNU Make 3.81, uv 0.12.5, Node 24.19.0, pnpm 10.15.0, Docker 28.0.4.

### Clean copy rehearsal

The 56 tracked files were copied into an empty directory, with no `.venv`, no `node_modules` and
no `.env`, to reproduce what a reviewer gets from `git clone`.

| Command | Exit | Observed |
| --- | --- | --- |
| `make setup` | 0 | Checked Node, uv and pnpm, installed both locked dependency sets, created `.env` from `.env.example` |
| `make ci` | 0 | All nine checks passed. See the breakdown below. |

### Check breakdown

| Command | Exit | Observed |
| --- | --- | --- |
| `uv run ruff format --check .` | 0 | `9 files already formatted` |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy` | 0 | `Success: no issues found in 9 source files` |
| `uv run pytest` | 0 | `10 passed`, `Total coverage: 100.00%` against the 90 percent gate |
| `pnpm run format:check` | 0 | `All matched files use Prettier code style!` |
| `pnpm run lint` | 0 | No output, so no findings under `--max-warnings 0` |
| `pnpm run typecheck` | 0 | No output from `tsc -b` |
| `pnpm run test` | 0 | `Tests 2 passed (2)`, coverage 100 percent against the 90 percent gate |
| `pnpm run build` | 0 | Bundle produced. `index.js` 190.93 kB, 60.23 kB gzipped |

### Pipeline runs

| Run | Result | Detail |
| --- | --- | --- |
| First push of the phase 0 commit | Failed | `Backend checks` and `Dependency audit` could not resolve `astral-sh/setup-uv@v10`. The action publishes no floating major tag. |
| After pinning all seven actions to exact versions | Passed | All five jobs green: backend checks, frontend checks, secret scan, dependency audit, container images |

The container job builds both images on `ubuntu-latest`, starts the backend image and polls
`/health` until it answers, so the image is verified running rather than only building.

### Other checks

| Command | Exit | Observed |
| --- | --- | --- |
| `make audit` | 0 | `No known vulnerabilities found` from pip-audit and from `pnpm audit` |
| `docker compose build` | 0 | Both images built |
| `docker compose build` after the nginx bump to 1.31-alpine | 0 | Rebuilt, then verified running |
| `docker compose up -d` | 0 | Backend reported `healthy`, then frontend reported `healthy` |
| `curl http://127.0.0.1:8000/health` | 0 | `{"status":"ok","version":"0.0.0","environment":"local"}` |
| `curl http://127.0.0.1:5173/` | 0 | HTTP 200, the built `index.html` |
| `curl http://127.0.0.1:5173/some/deep/route` | 0 | HTTP 200, confirming the single page app fallback |
| `bash -n scripts/setup.sh scripts/dev.sh` | 0 | Both parse under bash 3.2, the macOS system bash |

### Checks that were verified on purpose, not assumed

Three things were tested directly because getting them wrong would have been invisible:

1. **The coverage gate actually fails.** A temporary uncovered file was added to `frontend/src`.
   Vitest reported 25 percent and four threshold errors, then the file was removed. The gate is
   real, not decoration.
2. **The settings really resolve to the repository root `.env`.** The root `.env` was set to
   `SW_APP_ENV=production` and `SW_API_PORT=9999`, and the backend started from `backend/` read
   those values. The original file was restored afterwards.
3. **The test suite is isolated from that same file.** With the root `.env` still set to
   production values, the full backend suite still passed, including the test that asserts the
   documented defaults. Tests do not depend on the developer machine.

A fourth check confirmed the lint configuration is live rather than silently inert:
`eslint --print-config` reports 16 React hooks rules, 1 React refresh rule and 108
typescript-eslint rules for `src/App.tsx`.

### Content checks

| Check | Result |
| --- | --- |
| Em dash (U+2014) in any tracked file | None |
| En dash (U+2013) in any tracked file | None |
| Any non-ASCII character in tracked text files | None |
| `.env` tracked by git | No, correctly ignored |

## Limitations

These are deliberate. Each one is a later phase's work, and none of it is stubbed, because a stub
described as finished is worse than an empty directory.

1. **No reconciliation logic.** There is no lifecycle schema, no invariant catalogue, no exception
   taxonomy and no evidence contract. Phase 0 is foundation only.
2. **No metrics.** No accuracy, throughput, precision or recall number exists yet, because nothing
   has been measured. The evaluator is built before the interface, in a later phase.
3. **SQLAlchemy is not installed.** It is the frozen choice in ADR-001, but phase 0 has no
   persistence, and an unused dependency in a lockfile is noise.
4. **Playwright is not installed.** It is the frozen end to end choice, but there is no user
   interface to drive. A Playwright suite asserting nothing would be a placeholder pretending to
   be coverage.
5. **The AI provider interface does not exist.** ADR-001 freezes the design, including the
   deterministic fake for tests. It is built in the phase that makes the first model call.
6. **`benchmark/` is empty apart from its README.** The generator needs the schema and the
   exception taxonomy first.
7. **The frontend is a shell.** One page that names the project and says plainly that it is the
   phase 0 shell. It exists to give the build, lint, type and test tooling real React to run
   against.
8. **The backend exposes only `/health`.** Same reason.
9. **Coverage gates are set at 90 percent against a tiny codebase.** They are currently easy to
   satisfy. Their value is that they are in place before the code grows.
10. **Local verification is macOS arm64 only.** The Linux result comes from the pipeline, which is
    green, rather than from this machine.

## Known friction for contributors

`make setup` fails on a Node version outside `^20.19.0 || ^22.13.0 || >=24`. This is not a
preference. Locked frontend dependencies declare that range in their own `engines` field, and
`engine-strict` is on, so pnpm stops rather than producing a broken tree. This was hit during
phase 0 on Node 23.10.0.

The failure was made explicit rather than left as a confusing pnpm error. `make setup` names the
supported range, and if a supported Node is already installed under Homebrew it prints the exact
`export PATH` line to use.

## Exit gate status

> From a clean clone, one documented setup command succeeds and `make ci` passes. No application
> functionality is required yet.

| Requirement | Status | Evidence |
| --- | --- | --- |
| One documented setup command | Passed | `make setup`, documented in the README quick start |
| Succeeds from a clean clone | Passed | Rehearsed on a copy of the 56 tracked files with no `.venv`, no `node_modules` and no `.env`. Exit 0. |
| `make ci` passes | Passed | Exit 0 from that same clean copy, all nine checks green |
| No application functionality required | Met | Only `/health` and a shell page, both present to exercise the toolchain |

The gate asks about a clean clone, and that is verified above. The pipeline was also pushed with
this phase and is green on `main` across all five jobs, so the same checks are confirmed on
`ubuntu-latest` and not only on macOS.

## Unresolved decisions

None block phase 1. Two are worth flagging early:

1. **TypeScript 7 is blocked by `typescript-eslint`.** The 8.x line caps TypeScript below 6.1,
   so the project sits at 6.0.3, the newest version inside that range. Revisit when
   `typescript-eslint` supports the 7 line. Keeping type aware linting is worth more than being on
   the newest compiler.
2. **`pnpm audit` and `pip-audit` can fail the pipeline for a reason unrelated to a change.** That
   is the correct behaviour for a security check, but it means a red pipeline is sometimes not the
   contributor's fault. Both were clean at the time of writing. If this becomes noisy, the fix is
   to move the audit to a scheduled run, and that will need an ADR.

## Next phase

Phase 1 should freeze the transaction lifecycle schema, the invariant catalogue, the exception
taxonomy and the evaluator-private evidence contract, before any product interface is written.
