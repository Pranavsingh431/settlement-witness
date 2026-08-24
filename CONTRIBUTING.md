# Contributing

## Getting set up

```bash
make setup
```

Read the prerequisites in the [README](README.md) first. `make setup` is safe to run again at any
time.

## Before you push

```bash
make ci
```

This runs the same formatting, lint, type and test checks that the pipeline runs. Fix anything it
reports. `make format` rewrites files into the project style and applies the safe lint fixes.

## How the work is organised

The project is built in numbered phases. Each phase has an exit gate, and a phase is not finished
until that gate passes.

Rules that apply to every phase:

- Implement only the current phase. Do not add features that a later phase owns.
- Preserve behaviour that an earlier phase completed, unless an ADR justifies the change.
- Do not leave placeholder code described as finished. If something is a stub, say so.
- Do not report a metric that was not produced by code in this repository.
- Add tests for every new behaviour.
- Update the documentation in the same change.
- Write `docs/phase-reports/phase-N.md` with the changed files, the commands run, the observed
  results, the limitations and the exact exit gate status.

## Decisions

Anything that is hard to reverse gets an architecture decision record in `docs/adr/`. Copy the
shape of [ADR-001](docs/adr/ADR-001-stack-and-modular-monolith.md). Number files in sequence. Do
not edit an accepted record in place. Write a new record that supersedes it and link the two.

## Code style

The tools decide the style, so there is nothing to argue about.

Backend:

- Ruff formats and lints. Line length is 100.
- mypy runs in strict mode over `app` and `tests`.
- pytest fails if branch coverage of `app` drops below 90 percent.
- Warnings are errors in the test suite.

Frontend:

- Prettier formats. Line length is 100 and quotes are single.
- ESLint runs the type aware strict rule sets, plus the React hooks rules. Warnings fail the
  build, because `--max-warnings 0` is set.
- TypeScript runs in strict mode with `noUncheckedIndexedAccess` and `exactOptionalPropertyTypes`.
- Vitest fails if coverage of `src` drops below 90 percent.

## Writing style

- Use simple sentences.
- Do not use em dashes.
- Say what a thing does, not how impressive it is.

## Tests

Write the test with the behaviour, in the same change. A test name should state the behaviour, not
the function under test. Prefer a test that would catch a real regression over a test that only
raises the coverage number.

## Dependencies

The stack is frozen. See [ADR-001](docs/adr/ADR-001-stack-and-modular-monolith.md). Adding a
dependency needs a reason recorded in an ADR. Do not add a dependency that the current phase does
not use.

Both lockfiles are committed and installs use the frozen lockfile. If you change a dependency,
commit the updated lockfile in the same change.

## Commits

Write a short subject line in the imperative mood, for example `Add settlement lifecycle schema`.
Explain the reason in the body when the reason is not obvious from the diff.
