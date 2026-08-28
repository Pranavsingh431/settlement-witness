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

## Amendment, Phase 10.1

Three of the decisions above were argued correctly and implemented weakly. The
original text is left as written, because a record of what was decided is worth
more than a record edited to look right afterwards.

### Corpus-only is now enforced by the adapter, not only by the caller

Section 1 argued that there should be no handle to misuse rather than a rule
against misusing one, then relied on the CLI having no data argument. The
adapter itself accepted any `LinkProposalRequest` and sent it. So the guarantee
held for exactly as long as there was one caller, which is a rule about
discipline dressed as a structural property.

`HostedLinkProposalProvider` now requires a non-empty immutable allow-list of
request fingerprints at construction and refuses anything outside it as
`REQUEST_NOT_AUTHORIZED`, before a header is built or a socket is opened. The
command derives that list from `build_corpus()` with the corpus styling. A
second caller has to state which pages it may ask about, in code, and cannot
state "anything".

The allow-list is over request fingerprints rather than record IDs, so the same
corpus rendered differently is a different set of questions and is not
authorised by the first set.

### The response budget is enforced while reading, not after

Section 3 described a byte budget. The adapter asked httpx for the whole body
and then measured it, which reports what was already spent rather than limiting
it: a 200 kilobyte answer against a 1 kilobyte budget was fully downloaded
before being refused.

The response is now streamed. A non-2xx body is never read at all. An honest
`Content-Length` above the budget ends the exchange before the body is
requested. An absent or forged one is caught by the read itself, which stops as
soon as the buffer passes the budget, so the most an oversized answer costs is
the budget plus the one chunk that crossed it.

### Typed failures survive into the receipt

Section 3 said a host's error prose is the least trustworthy text in the
exchange, which is still true, and the adapter still keeps none of it. But the
run receipt recorded only the shared report's generic `PROVIDER_FAILED`, so a
rate limit and an unreachable host were the same word. Those are different
problems with different fixes, and neither is the host's prose.

The adapter now keeps its own counter of `FailureKind` outcomes, bounded by the
enum, values are integers, and the receipt carries it as `typed_failure_counts`
beside the report's `report_rejection_counts`. `ShadowReport` is unchanged:
generic is the right word for a report that also scores fixtures.

### The endpoint is held to the key's standard

Not in the original text at all. The base URL is copied verbatim into every
receipt's provenance, and user-info credentials, a query string and a fragment
are the three places a token is put in a URL. All three are now refused, and the
refusal names the variable rather than quoting what it read.

That last part is why `MissingConfiguration` is a `RuntimeError` rather than a
`ValueError`: pydantic wraps a `ValueError` raised inside a validator into a
message that quotes the input it was given, which for this field is the thing
that must not be quoted.

## Amendment, Phase 10.2

The amendment above says the adapter "requires a non-empty immutable allow-list
of request fingerprints at construction". It required a non-empty one and
checked that. Immutable was a type annotation, `frozenset[str]`, and Python does
not check annotations. A caller who passed an ordinary `set` still held the
object the provider was reading, and adding a fingerprint to it after
construction widened the provider's scope mid-run.

This is the third time the same error has been made in this design. Phase 10
made corpus-only a property of which arguments the CLI offered. Phase 10.1 moved
it into the adapter and made it a property of what the annotation said. Both
times the guarantee lived somewhere the runtime never looked.

**The provider now copies the allow-list into a `frozenset` it owns.** The
parameter accepts any `Collection[str]`, the copy is taken before anything else
in the constructor runs, and what the caller passed is never consulted again. A
caller cannot widen the scope after construction because nothing the caller
holds is the scope.

Two guards go with the copy, because a snapshot of the wrong thing is still
immutable and still wrong:

- A `str` or `bytes` is refused rather than snapshotted. A string is a
  collection of characters, so copying one would build a non-empty immutable
  allow-list of single letters, which would refuse every real page while looking
  like a working provider. mypy does not catch this, because a `str` genuinely
  is a `Collection[str]`.
- A member that is not a string is refused, for the same reason: it would never
  match, and a run where every page came back unauthorised would look like a
  scope problem rather than a typo.

The no-permissive-default rule is unchanged and now applies to every shape of
emptiness, not only to an empty `frozenset`.

The general lesson, written down because it has now cost three phases: in this
codebase a guarantee is whatever the runtime enforces. A docstring, an
annotation and a caller's good manners are three ways of not enforcing it.
