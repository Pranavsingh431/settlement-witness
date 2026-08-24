# ADR-001: Stack and modular monolith

- Status: Accepted
- Date: 2026-08-24
- Supersedes: none
- Superseded by: none

## Context

Settlement Witness reconciles a payment-to-settlement lifecycle and returns one of three states
for every case: matched, exception, or insufficient evidence. An exception is only resolved when
the system can also return the source records and the accounting checks that support the
decision.

That shapes the engineering problem in three ways.

First, correctness has to be provable rather than asserted. The project needs deterministic
arithmetic, a held-out evaluation set, seeded runs that reproduce, and stored raw model output
that a score can be recomputed from. A number that cannot be regenerated from checked-in code is
not a result.

Second, the parts of the system are tightly coupled by data. Normalisation, the lifecycle index,
deterministic candidate matching, model diagnosis and the evidence verifier all read and write the
same records. The reasoning is sequential: each step depends on what the previous step observed.

Third, the project is built by one person, in numbered phases, and has to stay easy to run. Any
reviewer should be able to clone the repository and reproduce the results with one setup command.

## Decision

### The system is a modular monolith

One deployable backend process, with hard module boundaries inside it. Not microservices, and not
a swarm of independent agents.

The reconciliation pipeline is a single stateful orchestrator with a central verifier between any
proposal and any recorded result. Work is only run in parallel where the subproblems are genuinely
independent, such as ingesting separate source files.

The reason is that the pipeline is sequential and data heavy. Splitting it across services or
independent agents would add serialisation, network failure modes and coordination code without
removing any real coupling, and it would make an error in an early step harder to trace. The
module boundaries give the separation that matters. The process boundary would only add cost.

If a later phase measures a step that is both independent and slow enough to matter, that step can
be extracted. That will need its own ADR and a measurement showing the split pays for itself.

### Probabilistic proposals are separated from deterministic authority

A model may propose a schema mapping, a diagnosis or an explanation. Deterministic code owns
arithmetic, joins on stable identifiers, accounting invariants, evidence validation and the final
permission to record a result. No model confidence value can override a violated invariant.

This boundary is the product, not an implementation detail, so it is recorded here rather than
left to each phase.

### Backend: Python with FastAPI, Pydantic, SQLAlchemy and pytest

Python is the right language for a data and evaluation heavy project. Pydantic gives validated
typed models at every boundary, and it fails at startup on a bad setting instead of failing later.
FastAPI generates a schema from those same models. pytest is the standard runner and has the
plugin support the benchmark harness will need.

SQLAlchemy is the frozen choice for persistence. It is not installed yet, because phase 0 has no
persistence. It arrives in the phase that adds the data model.

Strict tooling is set from the start, because retrofitting it later is expensive:

- Ruff formats and lints, including the bugbear, security, annotation and pytest rule sets.
- mypy runs in strict mode over both the application and the tests.
- pytest fails below 90 percent branch coverage and treats warnings as errors.

### Frontend: React, TypeScript, Vite and Playwright

The interface is a workpaper view over reconciliation runs. React and TypeScript are the standard
choice and the largest supply of reviewable examples. Vite gives a fast dev loop and a small
production bundle.

Playwright is the frozen choice for end to end tests. It is not installed yet. Phase 0 has no user
interface to drive, and a Playwright suite with nothing real to assert would be a placeholder
dressed up as coverage. It arrives in the phase that adds the first real screen. Unit and
component tests run on Vitest with React Testing Library.

### Local persistence: SQLite

The evaluation runs on generated data on one machine. SQLite needs no server, and its file is easy
to snapshot, seed and delete between runs, which is what a replayable evaluation needs. A single
writer is enough because the batch controller is the only writer.

### Package management: uv and pnpm

uv resolves and locks Python dependencies and manages the Python version itself, so the only
Python prerequisite is uv. pnpm gives a strict `node_modules` layout that surfaces undeclared
dependencies instead of hiding them.

Both lockfiles are committed and every install uses the frozen lockfile, including in continuous
integration and in the container builds. Reproducibility is the point.

### AI: a provider-neutral interface with a deterministic fake for tests

Model calls go through one internal interface. One real provider is configured. A deterministic
fake provider backs the tests, so the suite is offline, free and repeatable.

This is frozen but not built yet. It arrives in the phase that makes the first model call. The
reason to record it now is that the fake provider has to exist from the first model call onward,
not be added after the tests have already grown to depend on network access.

Only one real provider is configured. A second provider is a cost to maintain and is not justified
until an evaluation shows a task where routing between providers pays for itself.

### Local execution: a Makefile plus Docker

The Makefile is the one place that records how to run anything, so `make help` is the entry point
and `make ci` reproduces the pipeline. Docker gives a runnable image and proves the service starts
outside a developer machine. Docker is not required for daily work.

### Money is always an integer in minor units

Floating point is never used for a monetary amount. Binary floating point cannot represent most
decimal amounts exactly, and a reconciliation system that compares amounts would produce
differences that are artefacts of the representation. This rule is recorded here because it has to
hold from the first line of domain code.

## Consequences

Good:

- One process to run, one place to look when a decision is wrong, and a stack trace that crosses
  the whole pipeline.
- The setup story is two package managers and one command.
- Strict tooling from the start means the rules never have to be retrofitted across a grown
  codebase.
- Committed lockfiles mean a reviewer resolves the same versions that were tested.

Costs and risks:

- A modular monolith degrades into a tangle if the module boundaries are not enforced. Later
  phases have to keep them explicit and test across them.
- Strict linting, strict typing and coverage gates slow down the first version of any change. This
  is accepted, because the project is judged on evidence rather than on speed of first draft.
- Two toolchains means two lockfiles, two audit tools and two sets of continuous integration
  steps.
- SQLite will not survive a genuinely concurrent multi-writer workload. That workload is not in
  scope. If it ever is, it needs a new ADR.
- The frozen Node range is narrower than the latest release line, because locked dependencies
  declare their own supported versions. Contributors on an unsupported Node get a clear failure
  from `make setup`.

## Alternatives considered

**Microservices, one per pipeline stage.** Rejected. The stages share data and run in sequence.
Splitting them adds network failure modes and serialisation cost, and removes no real coupling.
The value would be independent scaling and independent deployment, neither of which this project
needs.

**A multi-agent swarm, one agent per stage.** Rejected. The reasoning here is sequential: each
step depends on what the previous step observed. Independent agents also make it much harder to
say which component produced a wrong result, which is exactly the question this project has to
answer. A single orchestrator with a central verifier keeps one reasoning locus and one place
where a result is approved. If a later phase can show a controlled comparison where a second agent
beats the orchestrator on the same budget, that will get its own ADR.

**A vector database for evidence retrieval.** Rejected for now. The evidence here is linked by
stable identifiers and transaction relationships, not by text similarity, so a relational or graph
index is the direct fit. A semantic retriever is still worth keeping as a comparison baseline in
the evaluation, but it does not need a separate database to serve that role. If evaluation shows a
case that relational retrieval cannot reach, that gets its own ADR.

**Kafka, Kubernetes and a message bus.** Rejected. The workload is batch reconciliation over
generated data on one machine. This infrastructure would add operational surface with no
measurable benefit, and it would make the project harder to reproduce.

**Postgres instead of SQLite.** Rejected for now. It would need a running server for every
contributor and every continuous integration job, for a single-writer batch workload. SQLAlchemy
keeps the move open if a later phase needs it.

**Node or Go for the backend.** Rejected. The evaluation harness, the deterministic data
generator and the numerical work all sit in Python, and splitting the language would mean two
places to look for the same bug.

**Black, isort and Flake8 instead of Ruff.** Rejected. Ruff covers all three, runs far faster, and
is one tool to configure rather than three that can disagree.

**Jest instead of Vitest.** Rejected. Vitest shares the Vite transform pipeline, so the tests and
the application see the same module resolution and the same TypeScript settings.
