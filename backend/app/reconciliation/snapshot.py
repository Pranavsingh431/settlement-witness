"""The fact snapshot a reconciliation run is computed over.

A run is a statement about one set of facts at one moment. The fingerprint
identifies exactly which set, so two runs can be compared and a result can be
traced back to the evidence that produced it.

The word snapshot is load bearing. Everything this module groups is grouped from
the facts that happen to be in the index. It says nothing about whether the
provider's export was complete, and no result here may be read as though it did.
"""

import hashlib
from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.domain.evidence import SourceFactIndex
from app.domain.facts import SourceFact, SourceRecordType
from app.domain.lifecycle import PaymentEvent, PayoutBatch, SettlementLine
from app.ingestion.projection import project_payment_event, project_payout, project_settlement_line


def fingerprint(facts: Sequence[SourceFact]) -> str:
    """Return a stable digest of exactly which facts a run saw.

    Built from each fact's record ID and payload hash, sorted, so it changes when
    a fact is added, removed or replaced, and does not change when the same facts
    are read in a different order.
    """
    digest = hashlib.sha256()
    for record_id, payload_hash in sorted(
        (fact.source_record_id, fact.payload_hash) for fact in facts
    ):
        digest.update(record_id.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(payload_hash.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


class FactSnapshot(BaseModel):
    """Everything one reconciliation run is allowed to reason about.

    Built once from the index and never re-read, so a run cannot see a fact
    appear halfway through and produce a result that is true of no single moment.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    digest: str
    fact_count: int
    as_of: datetime
    """The latest observation time in the snapshot.

    Used as the created-at of every decision in the run, so re-running over the
    same facts produces identical decisions. A wall clock would make every run
    differ in a way that has nothing to do with the evidence.
    """

    settlement_lines: tuple[SettlementLine, ...]
    payment_events: tuple[PaymentEvent, ...]
    payouts: tuple[PayoutBatch, ...]
    facts_by_record_id: dict[str, SourceFact]

    @classmethod
    def from_index(cls, index: SourceFactIndex) -> "FactSnapshot":
        """Build a snapshot from the complete accepted fact index.

        Args:
            index: The complete index, as `SourceFactRepository.fact_index`
                returns it. A partial index produces a snapshot that abstains
                more often, never one that resolves wrongly.

        Returns:
            The snapshot, with every collection in a fixed order.

        Raises:
            ValueError: If the index is empty. There is nothing to reconcile, and
                returning an empty run would look like a clean result.
        """
        facts = sorted(index.values(), key=lambda fact: fact.source_record_id)
        if not facts:
            message = "cannot reconcile an empty fact index"
            raise ValueError(message)

        lines = tuple(
            sorted(
                (
                    project_settlement_line(fact)
                    for fact in facts
                    if fact.source_record_type is SourceRecordType.SETTLEMENT_LINE
                ),
                key=lambda line: line.settlement_line_id,
            )
        )
        events = tuple(
            sorted(
                (
                    project_payment_event(fact)
                    for fact in facts
                    if fact.source_record_type is SourceRecordType.PAYMENT_EVENT
                ),
                key=lambda event: event.event_id,
            )
        )
        payouts = tuple(
            sorted(
                (
                    project_payout(fact)
                    for fact in facts
                    if fact.source_record_type is SourceRecordType.PAYOUT
                ),
                key=lambda payout: payout.payout_id,
            )
        )

        return cls(
            digest=fingerprint(facts),
            fact_count=len(facts),
            as_of=max(fact.observed_at for fact in facts),
            settlement_lines=lines,
            payment_events=events,
            payouts=payouts,
            facts_by_record_id={fact.source_record_id: fact for fact in facts},
        )

    def events_for_payment(self, payment_id: str) -> tuple[PaymentEvent, ...]:
        """Return every event for one payment, matched on exact payment ID.

        Ordered by occurred-at and then event ID, so two events at the same
        instant still come back in a fixed order.
        """
        return tuple(
            sorted(
                (event for event in self.payment_events if event.payment_id == payment_id),
                key=lambda event: (event.occurred_at, event.event_id),
            )
        )

    def payout_for(self, payout_id: str) -> PayoutBatch | None:
        """Return the payout with this exact ID, or None if the snapshot has none."""
        return next((payout for payout in self.payouts if payout.payout_id == payout_id), None)

    def lines_for_payout(self, payout_id: str) -> tuple[SettlementLine, ...]:
        """Return the settlement lines in this snapshot that name this payout.

        This is a snapshot grouping, not the payout's own declaration of its
        contents. The payout document says what the batch totalled, not which
        lines composed it, so the only thing available is which lines say they
        belong to it. A line that was never imported is simply not here, and no
        result computed from this grouping can show that.
        """
        return tuple(line for line in self.settlement_lines if line.payout_id == payout_id)

    def fact_for(self, source_record_id: str) -> SourceFact:
        """Return the fact behind a projected record."""
        return self.facts_by_record_id[source_record_id]
