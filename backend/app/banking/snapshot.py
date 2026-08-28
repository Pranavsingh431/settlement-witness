"""The facts one bank finality audit is computed over.

The same idea as `app.reconciliation.snapshot`, and deliberately the same
fingerprint: an audit and a run over the same accepted facts carry the same
snapshot digest, so the two conclusions about one moment can be put beside each
other without either being re-derived.

That is why `fingerprint` is imported rather than reimplemented. A second
definition of "which facts did this see" would be a second answer to that
question, and the whole point of putting a decision and an audit side by side is
that they saw the same thing.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.domain.banking import BankTransaction
from app.domain.evidence import SourceFactIndex
from app.domain.facts import SourceFact, SourceRecordType
from app.domain.lifecycle import PayoutBatch
from app.ingestion.projection import project_bank_transaction, project_payout
from app.reconciliation.snapshot import fingerprint


class BankFinalitySnapshot(BaseModel):
    """Every payout and every bank statement row one audit may reason about.

    Only two collections, because only two kinds of record bear on the question.
    A settlement line does not say whether money arrived and a payment event
    does not either, so neither is here: an audit that could read them could be
    tempted to infer from them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    digest: str
    """The same snapshot fingerprint a reconciliation run over these facts has.

    Computed over every accepted fact, not only the two kinds below, so an audit
    and a run agree about which moment they describe."""

    fact_count: int
    as_of: datetime
    """The latest observation time in the snapshot.

    Used as the recorded time of every certificate, so auditing the same facts
    twice produces identical certificates. A wall clock would make every audit
    differ in a way that has nothing to do with the evidence."""

    payouts: tuple[PayoutBatch, ...]
    bank_transactions: tuple[BankTransaction, ...]
    facts_by_record_id: dict[str, SourceFact]

    @classmethod
    def from_index(cls, index: SourceFactIndex) -> "BankFinalitySnapshot":
        """Build a snapshot from the complete accepted fact index.

        Args:
            index: The complete index. A partial one produces an audit that
                reports missing evidence more often, never one that verifies
                something it should not.

        Returns:
            The snapshot, with every collection in a fixed order.

        Raises:
            ValueError: If the index is empty. There is nothing to audit, and an
                empty audit would look like a clean result.
        """
        facts = sorted(index.values(), key=lambda fact: fact.source_record_id)
        if not facts:
            message = "cannot audit an empty fact index"
            raise ValueError(message)

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
        transactions = tuple(
            sorted(
                (
                    project_bank_transaction(fact)
                    for fact in facts
                    if fact.source_record_type is SourceRecordType.BANK_TRANSACTION
                ),
                key=lambda transaction: transaction.bank_transaction_id,
            )
        )

        return cls(
            digest=fingerprint(facts),
            fact_count=len(facts),
            as_of=max(fact.observed_at for fact in facts),
            payouts=payouts,
            bank_transactions=transactions,
            facts_by_record_id={fact.source_record_id: fact for fact in facts},
        )

    def transactions_for_reference(self, reference: str) -> tuple[BankTransaction, ...]:
        """Return every statement row carrying this exact reference.

        Exact string equality, and nothing else. No case folding, no whitespace
        normalisation, no prefix matching, no "starts with the UTR". Every one of
        those would be a rule about what a bank probably meant, and this audit
        exists to report what the records say.

        A tuple rather than one row, because two rows carrying one reference is a
        real thing that happens and the honest answer to it is ambiguity, not a
        choice.
        """
        return tuple(
            transaction
            for transaction in self.bank_transactions
            if transaction.bank_reference == reference
        )

    def fact_for(self, source_record_id: str) -> SourceFact:
        """Return the fact behind a projected record."""
        return self.facts_by_record_id[source_record_id]
