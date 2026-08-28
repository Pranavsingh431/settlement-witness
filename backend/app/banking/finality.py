"""Deciding whether a payout reached the merchant's bank account.

The rule is one sentence: a payout is verified when exactly one bank statement
row carries its reference, that row is a credit, and its amount and currency
equal the payout's exactly. Everything else is reported as the specific thing it
is.

**Nothing here is approximate.** There is no tolerance band, no rounding, no
nearest-amount search, no date window, no case folding of references, no
"probable match". One minor unit of difference is a mismatch. Those omissions
are the design: a finality claim that was right most of the time would be worse
than no claim, because a merchant would act on it.

**Nothing here reads a reconciliation decision.** A settlement line can be
internally `RESOLVED` and its payout can have no bank evidence at all. Both are
true, they answer different questions, and collapsing them into one word would
lose the only one a merchant actually cares about.

**Nothing here writes anything.** An audit is a pure function of a snapshot, so
auditing the same facts twice produces identical certificates, and a stored
certificate can be recomputed and compared rather than trusted.
"""

from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from app.banking.snapshot import BankFinalitySnapshot
from app.domain.banking import BankDirection, BankTransaction
from app.domain.evidence import EvidenceRef, EvidenceVerification, verify_against_index
from app.domain.lifecycle import PayoutBatch
from app.domain.primitives import CurrencyCode, Identifier, SourceRecordId, UtcTimestamp

BANK_FINALITY_VERSION: Final = "1.0.0"
"""Semantic version of the bank finality rules and certificate shape.

Its own version, deliberately separate from the domain contract, the baseline
and the parser. No reconciliation decision reads a certificate and no invariant
reads a bank fact, so a change here cannot change a decision, and moving the
domain contract for it would rewrite the declared version of every recorded
decision and invalidate every recorded run for a change none of them can see.

Patch: wording. Minor: a new outcome, or a new optional certificate field.
Major: a change to what an existing outcome means, or to what verifies."""

BANK_STATEMENT_SCHEMA_VERSION: Final = "1.0.0"
"""Version of the bank statement CSV layout, recorded on every audit.

Separate from `PARSER_VERSION` for the same reason. `PARSER_VERSION` is in the
reconciliation run key because a parser change can change a conclusion about the
payment records; adding a layout for a record type no invariant reads cannot,
and bumping it would have created a new run for every existing database with no
change of meaning. This version is what moves when these columns change."""


class BankFinalityOutcome(StrEnum):
    """What the records say about one payout reaching the bank.

    Seven outcomes, and none of them is a maybe. Each names a different thing
    that is true of the evidence, and they are kept apart because the action a
    person takes differs for every one: chase the bank, chase the provider for a
    reference, ask which of two rows is the payout, or investigate a real
    discrepancy.
    """

    VERIFIED_BANK_CREDIT = "VERIFIED_BANK_CREDIT"
    """Exactly one credit carrying this reference, for this exact amount and
    currency. The only outcome that says money arrived."""

    MISSING_BANK_EVIDENCE = "MISSING_BANK_EVIDENCE"
    """The payout names a reference and this snapshot holds no statement row
    carrying it.

    Says nothing about whether the money arrived. It says this system has not
    been shown that it did, which is a different and more honest claim: the
    statement may simply not have been imported."""

    UNLINKABLE_PAYOUT = "UNLINKABLE_PAYOUT"
    """The payout carries no bank reference, so no exact association is possible.

    Not a failure of the bank and not a discrepancy. It is a gap in the
    provider's own record, and it is reported rather than guessed around."""

    AMBIGUOUS_BANK_EVIDENCE = "AMBIGUOUS_BANK_EVIDENCE"
    """More than one statement row carries this reference.

    Choosing one would be inventing a fact about which transfer this was. Every
    candidate is cited so a person can see what has to be resolved."""

    BANK_DIRECTION_MISMATCH = "BANK_DIRECTION_MISMATCH"
    """The one row carrying this reference is a debit.

    Money moving out of the account under a payout's reference is not weaker
    evidence that the payout arrived. It is evidence of something else."""

    BANK_AMOUNT_MISMATCH = "BANK_AMOUNT_MISMATCH"
    """The credit is for a different number of minor units. Any difference."""

    BANK_CURRENCY_MISMATCH = "BANK_CURRENCY_MISMATCH"
    """The credit is in a different currency.

    Checked before the amount, because two amounts in different currencies
    cannot be compared and reporting them as a mismatch of size would invite
    somebody to look for a missing hundred rather than a wrong currency."""


class BankFinalityCertificate(BaseModel):
    """What one payout's bank evidence was, and what it showed.

    Carries the citations and the comparison rather than a summary of them, so a
    reader holding the same facts can check the outcome instead of believing it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    payout_id: Identifier
    payout_source_record_id: SourceRecordId
    """The fact the payout was projected from."""

    bank_reference: Identifier | None
    """The reference the payout declared, or None when it declared none."""

    outcome: BankFinalityOutcome
    evidence: tuple[EvidenceRef, ...]
    """The payout fact, and every statement row carrying its reference.

    Every candidate, not only the one that decided the outcome. An ambiguous
    result that cited one of two rows would be describing a choice it did not
    make."""

    evidence_verification: tuple[EvidenceVerification, ...]
    """Each citation resolved against the snapshot, by the same verifier every
    reconciliation decision uses."""

    matched_bank_transaction_ids: tuple[Identifier, ...]
    """The statement rows carrying the reference, in a fixed order."""

    expected_amount_minor: int | None = None
    expected_currency: CurrencyCode | None = None
    observed_amount_minor: int | None = None
    observed_currency: CurrencyCode | None = None
    observed_direction: BankDirection | None = None
    """Filled in only when exactly one row was found, because a comparison
    against two rows or none is not a comparison."""

    recorded_at: UtcTimestamp
    schema_version: str = BANK_FINALITY_VERSION

    @property
    def is_verified(self) -> bool:
        """Return True only for a verified credit. There is no partial credit."""
        return self.outcome is BankFinalityOutcome.VERIFIED_BANK_CREDIT


class BankFinalityBatch(BaseModel):
    """Every payout in one snapshot, audited together."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_fingerprint: str
    bank_finality_version: str = BANK_FINALITY_VERSION
    bank_statement_schema_version: str = BANK_STATEMENT_SCHEMA_VERSION
    as_of: UtcTimestamp
    fact_count: int
    payout_count: int
    bank_transaction_count: int
    certificates: tuple[BankFinalityCertificate, ...]
    outcome_counts: dict[str, int] = Field(default_factory=dict)


def _citation(record_id: str, snapshot: BankFinalitySnapshot) -> EvidenceRef:
    """Return a citation of one fact the snapshot holds."""
    fact = snapshot.fact_for(record_id)
    return EvidenceRef(
        source_record_id=fact.source_record_id,
        source_system=fact.source_system,
        payload_hash=fact.payload_hash,
    )


def _compare(payout: PayoutBatch, transaction: BankTransaction) -> BankFinalityOutcome:
    """Return the outcome for one payout against exactly one statement row.

    The order is the order a reader needs. Direction first, because a debit is
    not a weaker version of a credit. Currency next, because two amounts in
    different currencies cannot be compared. Amount last, exactly.
    """
    if transaction.direction is not BankDirection.CREDIT:
        return BankFinalityOutcome.BANK_DIRECTION_MISMATCH
    if transaction.currency != payout.currency:
        return BankFinalityOutcome.BANK_CURRENCY_MISMATCH
    if transaction.amount_minor != payout.net_minor:
        return BankFinalityOutcome.BANK_AMOUNT_MISMATCH
    return BankFinalityOutcome.VERIFIED_BANK_CREDIT


def audit_payout(payout: PayoutBatch, snapshot: BankFinalitySnapshot) -> BankFinalityCertificate:
    """Return the finality certificate for one payout.

    Args:
        payout: The payout to audit.
        snapshot: The facts it may be audited against.

    Returns:
        The certificate, citing the payout fact and every statement row carrying
        its reference.
    """
    citations = [_citation(payout.source_record_id, snapshot)]
    matches: tuple[BankTransaction, ...] = ()
    observed: BankTransaction | None = None

    if payout.utr is None:
        outcome = BankFinalityOutcome.UNLINKABLE_PAYOUT
    else:
        matches = snapshot.transactions_for_reference(payout.utr)
        citations.extend(_citation(one.source_record_id, snapshot) for one in matches)
        if not matches:
            outcome = BankFinalityOutcome.MISSING_BANK_EVIDENCE
        elif len(matches) > 1:
            outcome = BankFinalityOutcome.AMBIGUOUS_BANK_EVIDENCE
        else:
            observed = matches[0]
            outcome = _compare(payout, observed)

    evidence = tuple(citations)
    return BankFinalityCertificate(
        payout_id=payout.payout_id,
        payout_source_record_id=payout.source_record_id,
        bank_reference=payout.utr,
        outcome=outcome,
        evidence=evidence,
        evidence_verification=tuple(
            verify_against_index(reference, snapshot.facts_by_record_id) for reference in evidence
        ),
        matched_bank_transaction_ids=tuple(one.bank_transaction_id for one in matches),
        expected_amount_minor=payout.net_minor if observed is not None else None,
        expected_currency=payout.currency if observed is not None else None,
        observed_amount_minor=observed.amount_minor if observed is not None else None,
        observed_currency=observed.currency if observed is not None else None,
        observed_direction=observed.direction if observed is not None else None,
        recorded_at=snapshot.as_of,
    )


def audit(snapshot: BankFinalitySnapshot) -> BankFinalityBatch:
    """Audit every payout in one snapshot.

    Ordered by payout ID, matching how the snapshot holds them, so two audits of
    the same facts produce byte-identical output.
    """
    certificates = tuple(audit_payout(payout, snapshot) for payout in snapshot.payouts)
    counts: dict[str, int] = {outcome.value: 0 for outcome in BankFinalityOutcome}
    for certificate in certificates:
        counts[certificate.outcome.value] += 1
    return BankFinalityBatch(
        snapshot_fingerprint=snapshot.digest,
        as_of=snapshot.as_of,
        fact_count=snapshot.fact_count,
        payout_count=len(snapshot.payouts),
        bank_transaction_count=len(snapshot.bank_transactions),
        certificates=certificates,
        outcome_counts=counts,
    )
