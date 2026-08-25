# Domain contract, version 1.0.0

This document explains the contract. It does not define it. The Pydantic models
in `backend/app/domain/` are the definition, and the JSON Schema in
`docs/schema/v1/` is generated from them. Where this page and the code ever
disagree, the code is right and this page is a bug.

Start with `backend/app/domain/decisions.py`. The central rule lives there.

## The central rule

> A decision may be `RESOLVED` only when every required evidence reference
> exists and every required deterministic invariant reached a determinate,
> passing answer.

Model output can never satisfy either half. There is no field on a decision, on
an evidence reference or on an invariant result that accepts prose, a confidence
value or a chain of reasoning, and every model in the contract sets
`extra="forbid"`. So there is nothing to weigh against an invariant, because
there is nowhere for it to be written down.

The rule is enforced by the model itself, in a validator, not by a separate step
a caller has to remember. A decision that claims `RESOLVED` without the backing
is not flagged after the fact. It cannot be constructed.

## Money

Every amount is an integer in the minor unit of its currency, held with an ISO
4217 alpha-3 currency string. Floating point appears nowhere. Binary floating
point cannot represent most decimal amounts exactly, and a reconciliation system
that compares amounts would then report differences that are artefacts of the
representation rather than real breaks.

The published schema contains no `"number"` type anywhere, and a test asserts
that.

The signed formula, used everywhere:

```text
net_minor = gross_minor - fee_minor - tax_minor + adjustment_minor
```

`gross_minor`, `fee_minor` and `tax_minor` are non-negative magnitudes.
`adjustment_minor` is signed, because it covers both credits and debits. Fees
are stored as positive numbers and subtracted by the formula, so a source that
reports fees as positive and a source that reports them as negative cannot both
look correct.

Two amounts in different currencies cannot be added, subtracted or ordered.
Attempting it raises `CurrencyMismatchError`. Equality is structural and simply
returns `False`, which mirrors how Python treats naive and aware datetimes:
raising there would break dictionary lookups and containment checks for no gain,
because `False` is already the honest answer.

## Source facts are append-only

A source fact is one immutable observation of one record from one source. It is
never updated in place. Correcting an observation means recording a later fact,
so the history of what was seen and when stays intact and a decision can be
replayed against the evidence that existed at the time.

A fact carries the source record ID, the source system and record type, a
locator that points at the exact file row or API resource, the provider event
ID, an observed-at and an occurred-at timestamp, the payload hash, and the
canonical payload.

Both timestamps are timezone aware. Any offset is accepted and stored as the
same instant in UTC, because the offset a timestamp arrived with is presentation
and not fact. A naive timestamp is rejected: guessing a timezone for a financial
record would make ordering and settlement windows wrong in ways that are hard to
see later.

Observed-at and occurred-at are separate fields because webhook delivery is
unreliable. A fact can be observed days after it occurred, and that is normal.

### What is validated where

This split matters and is easy to get wrong.

| Kind of field | Checked by | Why |
| --- | --- | --- |
| Derived by this system, such as `payload_hash` | The model, at construction | The hash is ours. A fact whose hash disagrees with its own payload is incoherent, not bad source data. |
| Declared by the source, such as a settlement line's `net_minor` | An invariant | Sources really do publish inconsistent records. If the model refused them, a broken record would be unrepresentable and the system could never report the break. |

`SettlementLine.net_minor` is therefore stored rather than computed. If it were
a computed property, INV-002 would compare a number against itself and always
pass.

### Canonical payloads hold no floats

A canonical payload accepts strings, integers, booleans, null, lists and
objects. Floats are rejected, including integral ones like `1.0`. Ingestion
converts to minor units or to a string before it builds a fact. Allowing a float
here would let a rounding error enter the system and then be reported later as a
real settlement difference.

### Idempotency

Identity is `(source_system, provider_event_id)`. The same provider event ID
seen through two different systems is two observations, not one, because the
systems can disagree and that disagreement is worth keeping.

`classify_ingestion` returns one of three outcomes:

| Outcome | When | Meaning |
| --- | --- | --- |
| `NEW` | No stored fact with this identity | Store it |
| `DUPLICATE_NO_OP` | Same identity, same payload hash | Harmless. Providers resend webhooks and files get loaded twice. Store nothing. |
| `DUPLICATE_CONFLICT` | Same identity, different payload hash | The source contradicts itself. One observation is wrong and neither may be silently preferred. |

The hash is computed over JSON with sorted keys and no insignificant whitespace,
so two payloads differing only in key order are the same payload.

## Lifecycle

Nothing assumes one payment equals one payout.

- `PaymentIdentity` carries the payment ID, merchant ID and the currency every
  later event must match.
- `PaymentEvent` covers `CAPTURE`, `REFUND`, `REVERSAL` and `CHARGEBACK`. The
  amount is always a positive magnitude; direction comes from the type. Every
  event names the source fact it was read from.
- `SettlementLine` belongs to one payout and refers to one payment. It holds the
  component breakdown and the net the source declared.
- `PayoutBatch` holds many settlement line IDs, a declared total and an optional
  bank UTR.

A payout contains many lines, and a payment can be followed by refunds and
chargebacks long after it settles. Modelling either as one-to-one would make the
common cases look like exceptions.

## Decisions

A decision is about one settlement line. Its evidence and linked events may be
many, because a line's correctness can depend on a capture, later refunds, the
payout it belongs to and a bank credit, all at once.

| Field | Meaning |
| --- | --- |
| `decision_id` | Identity of this decision |
| `schema_version` | The contract version it was made under |
| `status` | `RESOLVED`, `EXCEPTION`, `PENDING` or `INSUFFICIENT_EVIDENCE` |
| `subject_settlement_line_id` | The line being decided |
| `linked_source_record_ids` | Every source record the decision rests on |
| `linked_event_ids` | Every lifecycle event it rests on |
| `evidence` | Pointers to source facts, each with its payload hash |
| `invariant_results` | Outcomes of the checks that were run |
| `exception_codes` | Codes raised, if any |
| `reason_codes` | Deterministic codes saying which rule fired |
| `created_at` | When the decision was made, in UTC |

An `EvidenceRef` holds a source record ID, the source system and the payload
hash. Nothing else. The hash is carried so a stored decision can be checked
against the fact it cited, and a later rewrite of that fact would be detectable.

### Status obligations

| Status | Must hold |
| --- | --- |
| `RESOLVED` | At least one evidence reference; no exception codes; a determinate, passing result for every required invariant; no failed invariant anywhere |
| `EXCEPTION` | At least one exception code |
| `PENDING` | No exception code other than `TIMING_PENDING` |
| `INSUFFICIENT_EVIDENCE` | Carries the `INSUFFICIENT_EVIDENCE` code |

Every decision carries at least one reason code, no invariant may appear twice,
and every evidence reference must name a record that is also in
`linked_source_record_ids`.

`derive_status` expresses the rule as a function, so a caller never picks a
status. `RESOLVED` is what remains when nothing else applies. That direction is
deliberate: a resolution is the absence of any reason not to resolve, not a
positive claim a caller may assert.

## Invariants

An invariant is a deterministic statement that is true, false, not applicable,
or impossible to evaluate with what is available.

| Outcome | Meaning | Blocks resolution |
| --- | --- | --- |
| `PASSED` | The statement holds | No |
| `FAILED` | The statement is false, and everything needed to say so was present | Yes |
| `NOT_APPLICABLE` | The statement does not apply here. A determinate answer | No |
| `INSUFFICIENT_INPUT` | Not enough was supplied to tell. Not a failure | Yes |

The fourth outcome is the important one. Missing information is not a mismatch.
A system that reports it as one manufactures breaks that finance teams then
waste time disproving.

### The catalogue

| ID | Statement | Missing input means | Required to resolve |
| --- | --- | --- | --- |
| INV-001 | Money is integer minor units and currencies are compatible | `INSUFFICIENT_EVIDENCE` | Yes |
| INV-002 | Settlement line net follows the signed formula | `INSUFFICIENT_EVIDENCE` | Yes |
| INV-003 | Payout net equals the sum of its settlement line nets | `PENDING` | Yes |
| INV-004 | Returned amounts do not exceed the captured amount | `INSUFFICIENT_EVIDENCE` | Yes |
| INV-005 | A source fact idempotency identity has one payload | `EXCEPTION` | No |
| INV-006 | A resolved decision has source-backed evidence | `INSUFFICIENT_EVIDENCE` | No |
| INV-007 | A resolved decision has passing required invariant results | `INSUFFICIENT_EVIDENCE` | No |
| INV-008 | Source facts are append-only and are never rewritten | `EXCEPTION` | No |

The catalogue is data, in `INVARIANT_CATALOGUE`, and `REQUIRED_FOR_RESOLUTION`
is derived from it rather than written twice, so the two cannot drift.

`NOT_APPLICABLE` counts as determinate and does not block a resolution.
`INSUFFICIENT_INPUT` does block one. That distinction is the whole point: a
payment with no refunds genuinely has nothing for INV-004 to check, whereas a
payment whose capture was never observed has a ceiling nobody knows.

Four invariants are not required of a decision. INV-005 and INV-008 are checked
when a fact is ingested, long before a decision exists. INV-006 and INV-007 are
the verifier rule itself, so requiring a decision to carry them as evidence of
its own correctness would be circular. They are implemented in `decisions.py`
rather than `invariants.py`, because they read a decision and the alternative
was two modules importing each other.

### Notable cases

INV-003 answers `INSUFFICIENT_INPUT` when the settlement lines supplied are not
exactly the set the payout claims. A sum over some of the lines says nothing
about the total, and reporting it as a mismatch would manufacture a break for
every payout still being assembled. That is why its missing-input policy is
`PENDING`.

INV-004 answers `INSUFFICIENT_INPUT` when no capture is present, because the
ceiling is unknown, and `NOT_APPLICABLE` when a capture exists but nothing was
returned.

INV-001 answers `INSUFFICIENT_INPUT` for an empty input rather than `PASSED`.
Claiming that nothing is consistent would be a free pass.

## Exception taxonomy

Thirteen stable codes. A code is an identifier an evaluator can grade against
and a person can look up, never a label to be reworded.

| Code | Meaning |
| --- | --- |
| `MALFORMED_RECORD` | The source record could not be read into the canonical model |
| `DUPLICATE_CONFLICT` | One idempotency identity arrived with two payload hashes |
| `UNSUPPORTED_STATE` | Coherent records describing a case the contract does not yet cover |
| `CURRENCY_MISMATCH` | Records that must share a currency do not |
| `OUT_OF_ORDER_EVENT` | An occurred-at contradicts the sequence, such as a refund before its capture |
| `UNMAPPED_REFERENCE` | A reference exists but points at nothing known |
| `AMOUNT_MISMATCH` | Amounts that should agree do not, and both sides are known |
| `FEE_MISMATCH` | The fee or tax component disagrees with what the net implies |
| `MISSING_PAYMENT` | A line refers to a payment no source fact describes |
| `MISSING_SETTLEMENT` | A capture has no settlement line, past the point one was expected |
| `PARTIAL_REFUND` | A refund covers part of a capture, so a balance remains to account for |
| `INSUFFICIENT_EVIDENCE` | The evidence admits more than one explanation |
| `TIMING_PENDING` | Nothing is wrong yet. Inside the expected window |

### Precedence

The list above is the precedence order, strongest first. When several codes
apply, the strongest decides the status. Three rules are encoded in it:

1. **Malformed or conflicting source data outranks every interpretation of it.**
   A broken record must never be quietly reported as a clean match. If the
   source contradicts itself, comparing amounts is premature.
2. **A settlement that is merely late is the weakest signal.** Nothing is wrong
   with it yet, so everything else outranks `TIMING_PENDING`, and a delayed but
   plausible settlement belongs in `PENDING` rather than in an exception.
3. **Missing required evidence belongs in `INSUFFICIENT_EVIDENCE`, not in a
   mismatch.** It sits above only `TIMING_PENDING`, because a real, demonstrable
   mismatch is a stronger statement than not knowing, while not knowing is a
   stronger statement than merely waiting.

Only two codes map to a status other than `EXCEPTION`: `TIMING_PENDING` gives
`PENDING`, and `INSUFFICIENT_EVIDENCE` gives its own status.

## Versioning

`DOMAIN_SCHEMA_VERSION` is `1.0.0`. Every decision records the version it was
made under, so a stored decision stays readable after the contract moves on.

- Patch: wording and non-behavioural fixes.
- Minor: additions that leave existing decisions readable, such as a new
  exception code or a new optional field.
- Major: anything that changes the meaning of an existing field, invariant or
  code, or removes one.

The JSON Schema in `docs/schema/v1/` is generated by `make schema`. A test
regenerates it and compares, so a model change that is not reflected in the
committed artifact fails the build rather than leaving a stale file behind.
