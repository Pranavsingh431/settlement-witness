# Phase 0: Repository foundation

- Date: 2026-08-24
- Last corrected: 2026-08-24, by the phase 0.1 correction pass
- Exit gate: passed, locally and on the pipeline. See "Exit gate status".

Phase 0.1 was a correction pass over this foundation. It changed no product behaviour. The body of
this report describes the state after that pass, and the "Phase 0.1 correction pass" section near
the end records exactly what was wrong and what was done about it.

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

- Node 24 LTS line only, `>=24 <25`. Written in three places that have to agree:
  `frontend/.nvmrc`, the `engines` field of `frontend/package.json`, and the check in
  `scripts/setup.sh`. The frontend image builds on `node:24-bookworm-slim` and CI reads
  `.nvmrc`, so all four agree.
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

Local checking is split in two. `make ci` runs the core checks, which are lint, typecheck, test
and build. It needs neither Docker nor network access. `make verify` runs those, then the
dependency audit, then `make verify-containers`, which builds both images, starts them, checks
they answer on their published ports, and reads the UID inside each container to confirm neither
runs as root.

Neither target claims to run the whole pipeline. The secret scan has no local equivalent, because
it scans the pushed history.

The Makefile avoids `.SHELLFLAGS` and `.ONESHELL`, so it works on the GNU Make 3.81 that ships
with macOS.

### Continuous integration

`.github/workflows/ci.yml` runs five jobs on every push to `main` and every pull request:

1. Backend checks: format, lint, mypy, pytest.
2. Frontend checks: format, lint, typecheck, test, build.
3. Secret scan with gitleaks over the full history.
4. Dependency audit with pip-audit and `pnpm audit`.
5. Container checks: both images built, then both started and checked for behaviour and for the
   user they run as.

Every action is pinned to a full 40 character commit SHA, with the release it corresponds to in a
trailing comment.

A git tag is a mutable reference. Whoever controls the action repository can move it to point at
different code, and the pipeline would run that code with nothing in this repository changing. A
commit SHA names the exact tree that was reviewed. Floating major tags have the same problem and
are also not always published: `astral-sh/setup-uv` has no `v10` tag at all, which is how the
first pipeline run failed.

Each SHA was resolved from its release tag and then verified a second time with `git ls-remote`
against the action repository, so the pin is confirmed against the source rather than against one
API call. Dependabot reads the trailing comment and bumps the SHA and the comment together.

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
  the unprivileged nginx image with only the static files, so no build tooling reaches the
  running container. It runs as UID 101 and listens on 8080, because a process without root
  cannot bind a port below 1024. Compose maps host port 5173 onto it.
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

58 tracked files, after the phase 0.1 pass added one script.

| Area | Files |
| --- | --- |
| Root | `.dockerignore`, `.editorconfig`, `.env.example`, `.gitignore`, `CONTRIBUTING.md`, `LICENSE`, `Makefile`, `README.md`, `SECURITY.md`, `docker-compose.yml` |
| Backend | `.dockerignore`, `.python-version`, `Dockerfile`, `pyproject.toml`, `uv.lock`, `app/__init__.py`, `app/__main__.py`, `app/config.py`, `app/main.py`, `tests/__init__.py`, `tests/conftest.py`, `tests/test_config.py`, `tests/test_entrypoint.py`, `tests/test_health.py` |
| Frontend | `.dockerignore`, `.npmrc`, `.nvmrc`, `.prettierignore`, `.prettierrc.json`, `Dockerfile`, `eslint.config.js`, `index.html`, `nginx.conf`, `package.json`, `pnpm-lock.yaml`, `vite.config.ts`, `tsconfig.json`, `tsconfig.app.json`, `tsconfig.node.json`, `src/App.css`, `src/App.test.tsx`, `src/App.tsx`, `src/main.tsx`, `src/setupTests.ts`, `src/vite-env.d.ts` |
| CI | `.github/workflows/ci.yml`, `.github/dependabot.yml` |
| Scripts | `scripts/setup.sh`, `scripts/dev.sh`, `scripts/verify-containers.sh` |
| Docs and data | `docs/adr/README.md`, `docs/adr/ADR-001-stack-and-modular-monolith.md`, `docs/phase-reports/README.md`, `docs/phase-reports/phase-0.md`, `benchmark/README.md`, `data/README.md`, `data/fixtures/.gitkeep`, `data/generated/.gitkeep` |

No files were changed or deleted. This is the first phase.

## Commands run and observed results

Host: macOS on arm64, GNU Make 3.81, uv 0.12.5, Node 24.19.0, pnpm 10.15.0, Docker 28.0.4.

### Clean clone verification

Run against a real `git clone` of the pushed repository into an empty directory, not against the
working tree. The clone had 57 tracked files, no `.venv`, no `node_modules` and no `.env`, which
is exactly what a reviewer gets.

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
| After the phase 0.1 pass repinned all seven actions to commit SHAs | Passed | All five jobs green again, so the SHA pins resolve and run |

The container job builds both images on `ubuntu-latest`, then starts both and checks them, so an
image is verified running rather than only building. Phase 0.2 extended this to the frontend. See
"Phase 0.2: closing the CI frontend runtime gap".

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

`make setup` fails on any Node outside the 24 LTS line. Part of that is forced: locked frontend
dependencies declare their own supported range and `engine-strict` is on, so pnpm stops rather
than producing a broken tree. This was hit during phase 0 on Node 23.10.0. Narrowing it further to
`>=24 <25` is a deliberate choice, so that the version a contributor runs, the version the image
builds on and the version CI uses cannot drift apart.

The failure was made explicit rather than left as a confusing pnpm error. `make setup` names the
supported range, and if a supported Node is already installed under Homebrew it prints the exact
`export PATH` line to use.

## Exit gate status

> From a clean clone, one documented setup command succeeds and `make ci` passes. No application
> functionality is required yet.

| Requirement | Status | Evidence |
| --- | --- | --- |
| One documented setup command | Passed | `make setup`, documented in the README quick start |
| Succeeds from a clean clone | Passed | Run against a real `git clone` of the pushed repository, with no `.venv`, no `node_modules` and no `.env`. Exit 0. |
| `make ci` passes | Passed | Exit 0 from that same clone, all nine checks green. The pipeline is also green on `main`. |
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
3. **Three Dependabot pull requests are open and should not be merged.** They are expected to
   close on their own. See "Dependabot pull requests" for the numbers and the reasoning.

## Phase 0.1 correction pass

A review of the phase 0 foundation found five things that were either internally inconsistent or
stated more confidently than the evidence supported. None of them were product behaviour. All five
were corrected before phase 1 started, because a foundation that contradicts itself is worse than
one that is merely incomplete.

### 1. The Node contract disagreed with itself

`frontend/package.json` declared `^20.19.0 || ^22.13.0 || >=24`, `frontend/.nvmrc` named 24, the
frontend image built on `node:24-bookworm-slim`, and `scripts/setup.sh` accepted anything from
20.19 upward. So Node 26 was accepted by the setup check and by `engines`, was never used by the
image or by CI, and was never tested. Node 20 and 22 were the same: allowed on paper, exercised
nowhere.

Fixed by supporting the Node 24 LTS line only, `>=24 <25`, in all four places. Node 26 is now
rejected explicitly rather than tolerated silently. The setup failure is still explicit: it names
the range and prints the exact fix.

### 2. Actions were pinned to tags, not commit SHAs

Phase 0 pinned each action to an exact release tag and described that as making a run
reproducible. That claim was wrong. A tag is a mutable reference, and whoever controls the action
repository can move it to point at different code, which the pipeline would then run with nothing
in this repository changing.

Fixed by pinning all seven actions to a full 40 character commit SHA with the release named in a
trailing comment. Every SHA was resolved from its tag and then verified again with `git ls-remote`
against the action repository. The wording in the workflow, the README, `CONTRIBUTING.md` and
ADR-001 no longer claims that a tag is immutable. Dependabot still updates these, because it
understands the SHA plus trailing comment form and rewrites both together.

### 3. `make ci` was described as running the whole pipeline

It ran lint, typecheck, test and build, which is four of the five pipeline jobs partially and none
of the secret scan, the dependency audit or the container checks. The description oversold it.

Fixed by describing `make ci` as the core local checks that CI mirrors, and adding `make verify`
for the wider pass: core checks, then the dependency audit, then `make verify-containers`.
`make ci` still requires neither Docker nor network access, which was checked by running it with
`docker` removed from `PATH`. The secret scan is stated as having no local equivalent, because it
scans the pushed history.

### 4. The frontend container was not actually non-root

The README claimed both images ran as an unprivileged user. That was true of the backend and false
of the frontend. The standard `nginx` image starts its master process as root and drops only the
worker processes, so a root process stayed in the container. Measured before the fix:

```text
$ docker compose exec frontend id
uid=0(root) gid=0(root) groups=0(root),1(bin),2(daemon),...

$ docker compose exec frontend ps -o user,pid,comm
USER     PID   COMMAND
root         1 nginx
nginx       29 nginx
```

Fixed by switching the runtime stage to `nginxinc/nginx-unprivileged:1.31-alpine`, which runs
everything as UID 101. Because a process without root cannot bind a port below 1024, the server
listens on 8080 inside the container, `EXPOSE` and the health check follow, and compose maps host
port 5173 onto it. The published address is unchanged.

The claim is now tested rather than asserted. `make verify-containers` reads the UID inside each
running container and fails if it is 0.

### 5. Stale Dependabot pull requests

Reported rather than closed, because closing them is a change on GitHub rather than in this
repository. See "Dependabot pull requests" below.

### Verification of the correction pass

Host: macOS on arm64, GNU Make 3.81, uv 0.12.5, Node 24.19.0, pnpm 10.15.0, Docker 28.0.4.

| Command | Exit | Observed |
| --- | --- | --- |
| `git diff --check` | 0 | No whitespace errors |
| `make ci` | 0 | All nine core checks passed |
| `make ci` with `docker` removed from `PATH` | 0 | Confirms the core target does not need Docker |
| `make verify` | 0 | Core checks, then both audits clean, then every container check passed |
| `bash scripts/setup.sh` on Node 24.19.0 | 0 | Accepted |
| `bash scripts/setup.sh` on Node 23.10.0 | 1 | Rejected, naming `>=24 <25` and printing the exact `export PATH` fix |
| `pnpm install --frozen-lockfile` under the new `engines` | 0 | Lockfile still resolves |

Repeated against a fresh `git clone` of the pushed repository, with no `.venv`, no `node_modules`
and no `.env`:

| Command | Exit | Observed |
| --- | --- | --- |
| `make setup` | 0 | Toolchain checked, both locked dependency sets installed, `.env` created |
| `make ci` | 0 | All nine core checks passed |
| `make verify` | 0 | Core checks, both audits clean, every container check passed including both UID checks |

The pipeline is green on `main` for the same commit, across all five jobs.

Container checks, measured after the fix:

| Check | Observed |
| --- | --- |
| `docker exec settlement-witness-frontend-1 id` | `uid=101(nginx) gid=101(nginx) groups=101(nginx)` |
| Frontend PID 1 owner | `nginx`, not root |
| `docker exec settlement-witness-backend-1 id` | `uid=999(app) gid=999(app) groups=999(app)` |
| Published ports | `backend 0.0.0.0:8000->8000/tcp`, `frontend 0.0.0.0:5173->8080/tcp` |
| `curl http://127.0.0.1:8000/health` | HTTP 200, `{"status":"ok","version":"0.0.0","environment":"production"}` |
| `curl http://127.0.0.1:5173/` | HTTP 200 |
| `curl http://127.0.0.1:5173/an/unknown/client/route` | HTTP 200, so the single page fallback still works |

Action SHA pins, each resolved from its tag and then confirmed with `git ls-remote`:

| Action | Release | Commit SHA |
| --- | --- | --- |
| `actions/checkout` | v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| `astral-sh/setup-uv` | v10.0.1 | `20cfd1bf945f4377ade1205e4dbc17946fc9a30d` |
| `pnpm/action-setup` | v6.0.10 | `0977fd99725f1db4007ccb2928dbb4e90d06cc86` |
| `actions/setup-node` | v7.0.0 | `820762786026740c76f36085b0efc47a31fe5020` |
| `gitleaks/gitleaks-action` | v3.0.0 | `e0c47f4f8be36e29cdc102c57e68cb5cbf0e8d1e` |
| `docker/setup-buildx-action` | v4.3.0 | `37fe631027851001ddb9b187196cc803df7f5f0e` |
| `docker/build-push-action` | v7.3.0 | `53b7df96c91f9c12dcc8a07bcb9ccacbed38856a` |

### Files changed

12 files modified and 1 added, 13 in total.

| File | Change |
| --- | --- |
| `frontend/package.json` | `engines.node` narrowed to `>=24 <25` |
| `scripts/setup.sh` | Node check accepts major 24 only, message and hints updated |
| `frontend/Dockerfile` | Runtime stage switched to the unprivileged nginx image, `USER 101`, `EXPOSE 8080`, health check on 8080 |
| `frontend/nginx.conf` | Listens on 8080 |
| `docker-compose.yml` | Frontend port mapping is now `5173:8080` |
| `.github/workflows/ci.yml` | All seven actions pinned to commit SHAs, header rewritten |
| `.github/dependabot.yml` | Comment recording that the GitHub Actions updates keep the SHA pins current |
| `Makefile` | `ci` redescribed, `verify` and `verify-containers` added |
| `scripts/verify-containers.sh` | Added. Builds, starts and checks both containers, including the UID check |
| `README.md` | Node contract, command table, container section and pipeline section corrected |
| `CONTRIBUTING.md` | `make ci` against `make verify`, action pin policy, Node line policy |
| `docs/adr/ADR-001-stack-and-modular-monolith.md` | Node consequence corrected, local check split and container non-root decision recorded, action pin policy recorded |
| `docs/phase-reports/phase-0.md` | This section, plus inline corrections to statements that the pass made untrue |

`frontend/.nvmrc` was already `24` and did not need to change.

## Dependabot pull requests

Dependabot ran as soon as its config was added and opened five pull requests. Two were resolved in
phase 0 and Dependabot closed them itself. Three are still open and are listed here rather than
closed, because closing a pull request is a change on GitHub rather than a change in this
repository.

| PR | Title | State | Assessment |
| --- | --- | --- | --- |
| #1 | Bump python from 3.12-slim-bookworm to 3.14-slim-bookworm in /backend | Open | Do not merge. It contradicts `backend/.python-version` and the `requires-python` range in `backend/pyproject.toml`. Now covered by a docker ignore rule for the `python` image. |
| #2 | Bump node from 24-bookworm-slim to 26-bookworm-slim in /frontend | Open | Do not merge. It contradicts `frontend/.nvmrc` and the `>=24 <25` range. Now covered by a docker ignore rule for the `node` image. |
| #3 | Bump nginx from 1.27-alpine to 1.31-alpine in /frontend | Open | Obsolete. The runtime image is no longer `nginx` at all. It is `nginxinc/nginx-unprivileged:1.31-alpine`, so the dependency this pull request targets is not in the repository any more. |
| #4 | Bump @types/node from 24.13.3 to 26.2.0 in /frontend | Closed by Dependabot | Correctly closed once the npm ignore rule for `@types/node` majors landed. |
| #5 | Bump typescript from 5.9.3 to 6.0.3 in /frontend | Closed by Dependabot | Correctly closed once 6.0.3 was adopted directly. |

#4 closing on its own shows the ignore rules take effect. #1 and #2 are expected to close the same
way on the next docker ecosystem run. #3 will close once Dependabot notices the base image
changed. If any of them is still open after the next weekly run, close it by hand. None of them
should be merged.

No dependency was upgraded during this pass. The only version change was the frontend runtime base
image, and that was a consequence of the non-root fix rather than an upgrade for its own sake.

## Phase 0.2: closing the CI frontend runtime gap

### The gap

Phase 0.1 changed the frontend runtime to a non-root nginx image listening on 8080. The container
job in `.github/workflows/ci.yml` built `settlement-witness-frontend:ci` but never started it. It
started only the backend and polled `/health`.

So the part of the system that phase 0.1 changed was the part CI did not run. A frontend image
that built but failed to start, failed to serve, or quietly reverted to root would have passed the
pipeline. The non-root claim was checked locally by `make verify-containers` and nowhere else.

### The fix

Only the container job changed. No application code, no dependency, no Node or Python contract, no
image choice, no port, and no action pin.

The job now starts both images it just built, from the same `:ci` tags, and checks five things:

| Check | Expected |
| --- | --- |
| `GET http://127.0.0.1:8000/health` | HTTP 200 |
| `GET http://127.0.0.1:8080/` | HTTP 200 |
| `GET http://127.0.0.1:8080/an/unknown/client/route` | HTTP 200, proving the single page fallback |
| `id -u` inside `backend-check` | Not 0 |
| `id -u` inside `frontend-check` | Not 0 |

The frontend is published on host port 8080 rather than 5173, because in CI there is no reason to
remap it and the container listens on 8080. The 5173 mapping belongs to `docker-compose.yml`,
which is a developer convenience and is unchanged.

Each HTTP check retries up to 30 times at 2 second intervals, so a slow start is tolerated but a
broken container fails within about a minute rather than hanging. On failure the check prints
which check failed, the last status code it saw, and the last 50 lines of that container's logs. A
separate `if: failure()` step prints the container table and the last 100 lines from both. A final
`if: always()` step removes both containers, each independently, so one missing container cannot
leave the other behind.

`docker compose` is deliberately not used here. The job validates the exact tagged images it
built, which is what the release artefact would be.

### Verification

The workflow was not trusted on inspection. Every `run:` block in the file was extracted and
checked with `bash -n`, and the container job's blocks were then extracted and executed verbatim
on a local Docker, against images built with the same tags the job builds.

Happy path, running the literal CI shell:

```text
NAMES            STATUS                     PORTS
frontend-check   Up (health: starting)      0.0.0.0:8080->8080/tcp
backend-check    Up (health: starting)      0.0.0.0:8000->8000/tcp

ok: backend health returned 200 from http://127.0.0.1:8000/health on attempt 2
ok: frontend index returned 200 from http://127.0.0.1:8080/ on attempt 1
ok: frontend single page fallback returned 200 from http://127.0.0.1:8080/an/unknown/client/route on attempt 1
ok: backend-check runs as UID 999, which is not root
ok: frontend-check runs as UID 101, which is not root
```

Failure path, checked rather than assumed. The frontend container was stopped and the same check
step was run again:

| Observed | Result |
| --- | --- |
| Exit status | 1 |
| Message | `failed: frontend index never returned 200 from http://127.0.0.1:8080/. Last status was '000'.` |
| Logs | Last 50 lines of `frontend-check` printed |
| Wall time | 1 minute 1 second, matching the 30 attempt budget at 2 second intervals |

Cleanup, checked in three states:

| State | Exit | Result |
| --- | --- | --- |
| One container running, one exited | 0 | Both removed |
| Neither container present | 0 | No error |
| Diagnostics step with neither container present | 0 | Prints the missing container errors and still succeeds, so it cannot fail the job by itself |

### Files changed

| File | Change |
| --- | --- |
| `.github/workflows/ci.yml` | Container job starts and checks both images, adds a failure diagnostics step, and always removes both containers |
| `README.md` | Pipeline job 5 described accurately |
| `docs/phase-reports/phase-0.md` | This section, plus the two earlier lines that described job 5 as a backend only check |

## Next phase

Phase 1 should freeze the transaction lifecycle schema, the invariant catalogue, the exception
taxonomy and the evaluator-private evidence contract, before any product interface is written.
