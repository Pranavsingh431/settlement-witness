# ADR-014: A hosted model sees the shadow corpus and nothing else

- Status: Accepted
- Date: 2026-08-27
- Supersedes: none
- Superseded by: none
- Related: [ADR-012](ADR-012-the-model-points-the-verifier-decides.md),
  [ADR-013](ADR-013-paged-environments-and-three-kinds-of-truth.md)

## Context

ADR-012 bounded what a model may say and ADR-013 bounded what it may see, but
every provider so far has been a deterministic fixture inside the process. This
phase adds the first call to a third party, which raises a question the earlier
two did not have to answer: what data may leave the machine.

The tempting design is a provider that works against any snapshot, so that the
same adapter can later be pointed at real data. That is the wrong default to
ship. It would mean the only thing standing between a merchant's imported
records and a third-party API is somebody remembering which snapshot they
passed, and a decision that important should not live in an argument.

## Decision

**A hosted model is reachable only from one CLI command, and that command
evaluates the generated corpus.**

### 1. Corpus only, structurally

`app.ai.live_shadow` builds `build_corpus()` inside `run` and has no argument
that changes what is evaluated: no database, no file, no snapshot, no document.
Both the command and the adapter import nothing from `app.storage`, `app.api` or
`app.ingestion`, and a test reads their syntax trees to prove it. There is no
handle to misuse rather than a rule against misusing one.

No API route calls it. No frontend page calls it. A test asserts the command's
whole option set is `--allow-network` and `--output`.

The corpus is generated in memory from a fixed seed and every identifier in it
is a digest. So what leaves the process is opaque tokens and their rendered
references: no canonical fact, no payload hash, no money, no CSV, no document
text, and nothing that was ever imported.

### 2. Explicitly opt in

Without `--allow-network` the command stops **before** the environment is read.
A run started by accident does not reach a credential, let alone send one. That
ordering is tested: with a complete, valid environment and no flag, nothing
about the configuration is touched.

### 3. Output stays bounded

The response goes through the same `RawLinkSelection` parsing and the same
deterministic validation as a fixture's. Nothing is repaired: Markdown fences
are not stripped, prose is not searched for an object, extra keys are not
trimmed, identifiers are not guessed at, and nothing is retried. A repaired
answer is partly the model's and partly ours, and a report over those cannot say
which part was whose.

Structured-output mode is requested where the host supports it. It is a
convenience and never a guarantee, and every response is validated as though it
had been ignored.

### 4. The key is never written down

Held in a `SecretStr`, read once when the request is built, sent in one header.
It appears in no repr, no serialisation, no log, no exception, no run receipt
and no test artifact, and a test asserts that across every configuration and
every failure path. Plain HTTP is refused except to the local machine, because
the header carrying it must be encrypted.

Provider error bodies are discarded. A rate limit and an authentication failure
are the same fact here: no answer. A host's own prose is the least trustworthy
text in the exchange and does not belong beside a reconciliation result.

## Consequences

- Pointing this at production data is not a configuration change. It would mean
  writing a new caller, and that caller would need its own ADR arguing why a
  merchant's records may leave the process.
- The adapter cannot be reused as a general provider without removing the
  guarantee its tests assert. That is deliberate friction.
- A run costs money, so the request budget is a configured maximum and a run
  that reaches it stops rather than continuing.
- Temperature is zero and is recorded, and that does **not** make a hosted run
  reproducible. Batching, routing, hardware and silent model updates all move an
  answer, and nothing available here controls any of them. A receipt records
  which model alias was asked, not which weights answered.
- Sending data as structured JSON in a separate message from the instruction is
  a precaution, not a solution to prompt injection. It removes the string
  concatenation a value could escape from; it does not make a model immune to
  text that reads like an instruction.
