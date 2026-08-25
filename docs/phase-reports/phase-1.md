# Phase 1: Freeze the reconciliation contract

- Date: 2026-08-24
- Exit gate: passed. See "Exit gate status".
- Domain contract version: 1.0.0

## Scope

Define what a fact, an amount, a lifecycle event, an invariant, an exception and
a decision mean, as code that enforces those meanings. Nothing that consumes the
contract was built.

The contract is frozen now rather than later because the parts of this system
that are hard to reverse are the meanings, not the code. Once ingestion,
matching and a model call exist, each of them has an opinion about what a
decision is, and the contract quietly becomes whatever those three agree on.

## What was built

### Domain package

`backend/app/domain/`, eight modules, no dependency beyond Pydantic.

| Module | Holds |
| --- | --- |
| `primitives.py` | Identifier, currency, hash, amount and UTC timestamp types, and the canonical JSON payload type |
| `money.py` | `Money`, `MoneyBreakdown`, `compute_net_minor`, `CurrencyMismatchError` |
| `facts.py` | `SourceFact`, `SourceLocator`, `IdempotencyKey`, `compute_payload_hash`, `classify_ingestion` |
| `lifecycle.py` | `PaymentIdentity`, `PaymentEvent`, `SettlementLine`, `PayoutBatch` |
| `codes.py` | `ExceptionCode`, `ReasonCode`, precedence order and helpers |
| `invariants.py` | `InvariantId`, outcomes, the catalogue, and the checks for INV-001 to INV-005 and INV-008 |
| `decisions.py` | `DecisionStatus`, `EvidenceRef`, `ReconciliationDecision`, `derive_status`, and the checks for INV-006 and INV-007 |
| `version.py` | `DOMAIN_SCHEMA_VERSION` and its `Literal` type |

Every model is frozen and sets `extra="forbid"`.

### Published schema

`docs/schema/v1/` holds 13 JSON Schema files generated from the models by
`make schema`. A test regenerates and compares, so a model change that is not
reflected in the committed artifact fails the build.

### Documentation

- `docs/domain-contract.md` explains the contract.
- `docs/evaluation-contract.md` defines how the system will be graded, written
  before the system exists.
- `docs/adr/ADR-002-domain-contract-and-verifier-authority.md` records the seven
  decisions and the seven rejected alternatives.

## Decisions worth naming

Full reasoning is in ADR-002. The four that shaped everything else:

1. **The verifier is enforced by construction.** A decision claiming `RESOLVED`
   without backing cannot be built. The alternative, a `verify()` function
   callers are expected to run, is a convention, and conventions are followed
   until someone is in a hurry.
2. **Model output is excluded structurally.** No model in the contract has a
   field for prose, confidence or reasoning. There is nothing to weigh against
   an invariant because there is nowhere to write it down.
3. **Invariants have four outcomes, not two.** `NOT_APPLICABLE` is determinate
   and does not block a resolution. `INSUFFICIENT_INPUT` blocks one. A boolean
   would force missing information to be reported as a mismatch, which is the
   exact failure this project exists to beat.
4. **Structural validity and source consistency are checked in different
   places.** Fields this system derives, such as `payload_hash`, are validated at
   construction. Fields a source declares, such as `SettlementLine.net_minor`,
   are left to invariants, because a model that refused inconsistent records
   would make a broken record unrepresentable and the break unreportable.

## Invariant IDs

| ID | Statement | Missing input means | Required to resolve | Implemented in |
| --- | --- | --- | --- | --- |
| INV-001 | Money is integer minor units and currencies are compatible | `INSUFFICIENT_EVIDENCE` | Yes | `invariants.py` |
| INV-002 | Settlement line net follows the signed formula | `INSUFFICIENT_EVIDENCE` | Yes | `invariants.py` |
| INV-003 | Payout net equals the sum of its settlement line nets | `PENDING` | Yes | `invariants.py` |
| INV-004 | Returned amounts do not exceed the captured amount | `INSUFFICIENT_EVIDENCE` | Yes | `invariants.py` |
| INV-005 | A source fact idempotency identity has one payload | `EXCEPTION` | No | `invariants.py` |
| INV-006 | A resolved decision has source-backed evidence | `INSUFFICIENT_EVIDENCE` | No | `decisions.py` |
| INV-007 | A resolved decision has passing required invariant results | `INSUFFICIENT_EVIDENCE` | No | `decisions.py` |
| INV-008 | Source facts are append-only and are never rewritten | `EXCEPTION` | No | `invariants.py` |

INV-006 and INV-007 sit in `decisions.py` because they read a decision, and the
alternative was two modules importing each other. INV-005 and INV-008 are checked
when a fact is ingested, long before a decision exists, so neither is required of
a decision. Requiring INV-006 or INV-007 of a decision would be circular.

`REQUIRED_FOR_RESOLUTION` is derived from the catalogue rather than written a
second time, and a test asserts the two agree.

## Exception codes

Thirteen, listed strongest first. This is also the precedence order.

`MALFORMED_RECORD`, `DUPLICATE_CONFLICT`, `UNSUPPORTED_STATE`,
`CURRENCY_MISMATCH`, `OUT_OF_ORDER_EVENT`, `UNMAPPED_REFERENCE`,
`AMOUNT_MISMATCH`, `FEE_MISMATCH`, `MISSING_PAYMENT`, `MISSING_SETTLEMENT`,
`PARTIAL_REFUND`, `INSUFFICIENT_EVIDENCE`, `TIMING_PENDING`.

Three rules are encoded in that order, and each is covered by a test:

1. Malformed or conflicting source data outranks every interpretation of it, so
   a broken record can never be reported as a clean match.
2. A settlement that is merely late is the weakest signal, so a delayed but
   plausible settlement lands in `PENDING` rather than in an exception.
3. Missing evidence sits above only lateness. A demonstrable mismatch is a
   stronger statement than not knowing; not knowing is stronger than waiting.

Only `TIMING_PENDING` and `INSUFFICIENT_EVIDENCE` map to a status other than
`EXCEPTION`.

## Tests added

204 domain tests, on top of the 10 from phase 0. 214 in total.

| File | Tests | Covers |
| --- | --- | --- |
| `tests/domain/test_decisions.py` | 47 | The central rule, status obligations, INV-006, INV-007, `derive_status` |
| `tests/domain/test_invariants.py` | 40 | The catalogue and every check, including each missing-input case |
| `tests/domain/test_money.py` | 36 | The signed formula, integer minor units, cross-currency refusal |
| `tests/domain/test_schema_export.py` | 27 | Schema drift, no floats in the published contract, determinism |
| `tests/domain/test_facts.py` | 25 | Append-only, hashing, UTC handling, idempotency outcomes |
| `tests/domain/test_codes.py` | 15 | The taxonomy and all three precedence rules |
| `tests/domain/test_lifecycle.py` | 14 | Event types, many-line payouts, declared-net storage |

Every invariant ID has tests for its passing, failing and missing-input paths.

### Demonstrating that an unbacked resolution cannot be built

The requirement was to show a decision cannot be constructed as `RESOLVED`
without evidence or with a failed invariant. Six routes were tested, and each
raises `ValidationError` at construction:

| Attempt | Result |
| --- | --- |
| `RESOLVED` with no evidence | Refused: "must cite at least one source fact" |
| `RESOLVED` with a failed required invariant | Refused: "cannot carry a failed invariant result" |
| `RESOLVED` with an `INSUFFICIENT_INPUT` required invariant | Refused: "pass or be not applicable" |
| `RESOLVED` with a required invariant absent | Refused: "must carry a result for every required invariant" |
| `RESOLVED` alongside an exception code | Refused: "cannot also carry exception codes" |
| `RESOLVED` citing evidence that is not linked | Refused: "must name a source record" |

A seventh check confirms `EvidenceRef` rejects an `explanation` field and a
`confidence` field, so model output has no route in.

### A note on the second line of defence

The INV-006 and INV-007 failure branches are unreachable through the models,
because the validator refuses to construct the input that would reach them. They
are kept for decisions that arrive without validation, such as one read back
from storage, and are tested through `model_construct`, which bypasses
validation deliberately. This is why coverage reaches those lines at all.

## Verification results

Host: macOS on arm64, uv 0.12.5, Python 3.12.12, Node 24.19.0, pnpm 10.15.0.

| Command | Exit | Observed |
| --- | --- | --- |
| `git diff --check` | 0 | No whitespace errors |
| `make ci` | 0 | All nine core checks passed |
| `uv run ruff format --check .` | 0 | `28 files already formatted` |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy` | 0 | `Success: no issues found in 28 source files` |
| `uv run pytest` | 0 | `214 passed`, `Total coverage: 100.00%` against the 90 percent gate |
| `pnpm run format:check` / `lint` / `typecheck` / `test` / `build` | 0 | Unchanged from phase 0.2 |
| `make schema` | 0 | 13 schema files written, byte identical to the committed set |

Coverage is 100 percent across every domain module, and the backend total rose
from 41 statements to 627 with no uncovered line or branch.

Three findings that mypy and ruff caught, and how they were resolved rather than
suppressed:

1. `Literal[DOMAIN_SCHEMA_VERSION]` is not valid, because a type checker cannot
   read a variable into `Literal`. Resolved by marking the constant `Final` and
   adding `DomainSchemaVersion` beside it, with a test asserting the two agree.
2. Recursive JSON type aliases would not resolve across modules with the older
   `TypeAlias` form. Resolved by using the Python 3.12 `type` statement.
3. Four negative tests pass a deliberately wrong type to prove the runtime check
   fires. They carry a narrow `type: ignore` with the reason, rather than being
   weakened to satisfy the checker.

### Contract properties checked mechanically

| Property | How |
| --- | --- |
| No floating point anywhere in the published contract | No `"number"` type in any of the 13 schema files |
| The catalogue holds exactly INV-001 to INV-008 | Set comparison against the catalogue |
| `REQUIRED_FOR_RESOLUTION` matches the catalogue flags | Derived, then asserted |
| Precedence covers all 13 codes exactly once | Set and length comparison |
| The catalogue cannot be mutated at runtime | Assignment raises `TypeError` |
| Committed schema matches the models | Regenerate and compare |

## Intentionally deferred to Phase 2

Nothing below is stubbed. A stub described as finished is worse than an absence.

1. **CSV and file ingestion.** No parser exists. `SourceLocator` describes where
   a record came from; nothing reads one.
2. **Normalisation into canonical payloads.** The contract rejects floats in a
   payload, which pushes conversion onto ingestion. That work is Phase 2's.
3. **A matching engine.** Nothing links a settlement line to a payment. The
   decision model describes the result of matching, not the act of it.
4. **Database and persistence.** No SQLAlchemy, no migrations, no storage. The
   append-only rule is defined and checked; nothing stores anything yet.
5. **The AI provider interface.** Not built, per ADR-001. The contract makes the
   boundary concrete by leaving model output no field to occupy.
6. **The benchmark harness and generator.** `docs/evaluation-contract.md`
   defines the obligations. `benchmark/` is still empty.
7. **API endpoints.** Still only `/health`.
8. **Frontend.** Untouched. Still the phase 0 shell.
9. **The remaining reason codes in practice.** `ReasonCode` includes codes such
   as `SETTLEMENT_WITHIN_EXPECTED_WINDOW` that no check emits yet, because the
   check that would emit them needs a settlement window rule that Phase 2 sets.
10. **Timing and window rules.** `TIMING_PENDING` is defined and ordered, but
    what counts as inside the expected window is not, because it depends on
    settlement schedules Phase 2 introduces.

## Exit gate status

| Requirement | Status | Evidence |
| --- | --- | --- |
| `git diff --check` | Passed | Exit 0, no whitespace errors |
| `make ci` | Passed | Exit 0, all nine checks |
| Unit tests cover each invariant | Passed | INV-001 to INV-008 each have passing, failing and missing-input tests |
| Unit tests cover the resolved-decision verifier | Passed | 47 decision tests, including six construction refusals |
| A decision without evidence cannot be `RESOLVED` | Passed | `ValidationError` at construction |
| A decision with a failed invariant cannot be `RESOLVED` | Passed | `ValidationError` at construction |
| No ingestion, matching, AI, database, dashboard or workflow | Met | See "Intentionally deferred" |
| Deterministic and dependency-light | Met | No new dependency; every check is a pure function |

## Unresolved decisions

None block Phase 2. Two are worth flagging:

1. **`ReasonCode` will grow.** Some codes are defined but not yet emitted. This
   is deliberate, since the taxonomy is easier to reason about whole, but it
   means the enum is currently wider than the behaviour. Phase 2 should either
   emit them or remove the ones that turn out to be wrong.
2. **The settlement window rule is undefined.** `PENDING` and `TIMING_PENDING`
   are specified, but not the T+N rule that decides when a settlement stops
   being plausibly late and starts being `MISSING_SETTLEMENT`. That needs real
   settlement schedule behaviour, and it should get its own ADR.

## Next phase

Phase 2 should build the deterministic generator and the ingestion path against
this contract, in that order, so that the first records the system reads are
records whose correct decisions are already known.
