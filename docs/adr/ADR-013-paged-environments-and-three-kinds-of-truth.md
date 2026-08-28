# ADR-013: Paged finite environments, and three kinds of truth

- Status: Accepted
- Date: 2026-08-27
- Supersedes: none
- Superseded by: none
- Related: [ADR-012](ADR-012-the-model-points-the-verifier-decides.md)

## Context

ADR-012 bounded what a model may say: it selects from a finite set of records
the application showed it, and asserts nothing. Two problems with that
arrangement only appeared once it was measured.

**The environment could be unanswerable.** A selection is bounded at 64 records,
and a settlement line can link more than that. On a line with 71 linked events
the candidate set offered every one of them and the contract refused to carry
them, so no correct answer existed. A provider that knew the exact right answer
scored zero recall and zero exact-set accuracy. That is not a model failing a
task; it is a task with no passing answer.

**The task was string equality.** The provider was shown the same reference
strings the deterministic baseline matches on, so selecting correctly meant
comparing two identical strings. A report over that measures nothing about
selection, and a good score on it would mean nothing.

## Decision

### 1. The universe is paged, dully

A line's candidate universe is unchanged: every payment event and every payout
in the snapshot, with no prefilter by payment ID, amount or similarity. It is
then cut into consecutive blocks of at most 64 records, sorted by source record
ID, and each block is asked as its own request.

Every candidate appears on exactly one page, no page is empty, no page exceeds
the selection bound, and the union of a line's pages is exactly its universe.
Selecting a whole page is therefore always expressible, so every question asked
has a correct answer.

**The partition is deliberately not a ranking.** Ordering the universe by likely
relevance would perform the linking inside the pager and leave the provider
confirming a shortlist somebody else built, and the evaluation would then be
measuring the pager. Consecutive blocks of a sorted list cannot do that.

A provider may select only from the page in front of it. A record on page two is
as unselectable on page one as one that is not in the snapshot, even though both
belong to the same line. That keeps the membership rule exactly as strong as it
was: the candidate set is the set that was offered.

Page ordinal and environment fingerprint join the server-owned metadata, and
both are part of a proposal's derived identity. Without them, two pages of one
line in one snapshot would be filed under one ID.

So does a third: the request fingerprint. The environment fingerprint identifies
**which records** a universe holds. It says nothing about **what was shown**
about them, and the same universe rendered canonically, truncated and withheld
is three different questions and three different tasks. Under the environment
fingerprint alone all three shared one identity, so a run over withheld
references looked like a replay of a run over canonical ones.

The request fingerprint is built from the styled subject references, every
rendered field of every candidate in page order, the page ordinal and count, the
environment fingerprint and the snapshot fingerprint. It carries nothing
private, because it is built from a request and a request holds nothing private.
`ShadowReport` carries the ordered digest of every page request for the same
reason at the level of a whole run. Raw model output still carries
an outcome and a list of IDs and nothing else; a response supplying a page
number or an environment fingerprint is refused as an extra, exactly as one
supplying a provider identity is.

### 2. Three kinds of truth, kept apart

Evaluating selection needs the task to be harder than string equality, and
making it harder means what a provider sees can no longer be what the baseline
matches on. So three things are separated, and the separation is the design.

**Canonical facts** are the source facts. The deterministic baseline links by
exact reference over these, and that linking is the oracle. Nothing about how a
case is presented changes it.

**The presentation** is what a provider sees: a rendering of a reference, chosen
per record. Reformatted, altered by one character, truncated so two records look
alike, or withheld entirely. Canonical values are never modified; rendering
happens on the way to a request and nowhere else.

**The expected action** is what a provider ought to do, which is not always to
select. Some cases cannot be answered safely from what is shown, and abstaining
on those is correct even though the oracle knows the link.

The oracle is computed from canonical facts and never read back from a
presentation field. A test renders every reference in a corpus as a near miss,
and another withholds every reference entirely, and requires canonical truth to
be unmoved by both. An oracle derived from the presentation would agree with
whatever the provider was shown, which is the opposite of an oracle.

### 3. Safe abstention is measured, and never averaged in

A case where nothing shown identifies a record has no safe selection. Abstaining
is right, and link recall counts it as a miss, because it is one: the true links
were not returned.

Both facts are true and neither should be hidden, so `safe_abstention_recall`
and `unsafe_selection_rate` are reported beside the linking metrics and never
folded into them. The corpus makes the tradeoff concrete: a provider that
selects the canonical answer everywhere scores 1.000 on every linking metric and
1.000 on unsafe selection, because it links records on both cases where nothing
shown identified them. A matcher that reads the visible fields and declines when
they are too coarse gives up 0.025 of recall and is safe on both.

Neither is simply better. Which is preferable is a judgement about the cost of a
wrong link against the cost of a missing one, and that judgement belongs to
whoever is reading the report, not to an average computed before they see it.

## Consequences

- A shadow report is not comparable across harness versions, and not comparable
  across presentations within one. The request-set fingerprint is what says so.
- The abstention and invalid rates are per page and are named for it. A line
  measure would have hidden a provider that declined one page of four.
- Exact-set accuracy is over a line's aggregate selection across every page, so
  answering three pages of four perfectly is not exact.
- The generated corpus is in-memory and never imported. A benchmark that seeded
  the database would corrupt every later reconciliation, and a test proves the
  two populations stay separate.
- Making the task harder means the presentation layer is now part of what has to
  be right. A bug there would change what a provider is scored on without
  changing what is true, which is why the oracle-isolation tests exist.
