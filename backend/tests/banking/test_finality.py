"""The bank finality rule, one outcome at a time.

Every negative case is the verified case with exactly one field changed, so a
test that passes is evidence that the named field is what produced the outcome.
Building each case from its own facts would pass whether or not the rule under
test was doing the work.
"""

import json

import pytest
from pydantic import ValidationError

from app.banking.finality import (
    BANK_FINALITY_VERSION,
    BANK_STATEMENT_SCHEMA_VERSION,
    BankFinalityCertificate,
    BankFinalityOutcome,
    audit,
    audit_payout,
)
from app.banking.snapshot import BankFinalitySnapshot
from app.domain.banking import BankDirection
from app.domain.decisions import DecisionStatus
from app.domain.evidence import EvidenceOutcome, SourceFactIndex
from app.ingestion.projection import project_bank_transaction
from tests.banking.conftest import (
    PAYOUT_NET_MINOR,
    VERIFIED_REFERENCE,
    bank_transaction,
    facts_for,
    linkable_payout,
)
from tests.reconciliation.conftest import index_of, payout


def certificate_of(index: SourceFactIndex) -> BankFinalityCertificate:
    """Return the one certificate an index of a single payout produces."""
    return audit(BankFinalitySnapshot.from_index(index)).certificates[0]


class TestTheOneArrangementThatVerifies:
    """Exact reference, correct direction, exact amount, exact currency."""

    def test_an_exact_credit_verifies(self) -> None:
        """The happy path, and the only path to it."""
        certificate = certificate_of(facts_for())

        assert certificate.outcome is BankFinalityOutcome.VERIFIED_BANK_CREDIT
        assert certificate.is_verified is True

    def test_it_records_what_it_compared(self) -> None:
        """Expected and observed, both, so a reader can check the comparison."""
        certificate = certificate_of(facts_for())

        assert certificate.expected_amount_minor == PAYOUT_NET_MINOR
        assert certificate.expected_currency == "INR"
        assert certificate.observed_amount_minor == PAYOUT_NET_MINOR
        assert certificate.observed_currency == "INR"
        assert certificate.observed_direction is BankDirection.CREDIT

    def test_it_cites_the_payout_and_the_bank_row(self) -> None:
        """Both facts, both verified against the snapshot."""
        certificate = certificate_of(facts_for())

        assert len(certificate.evidence) == 2
        assert all(
            result.outcome is EvidenceOutcome.VERIFIED
            for result in certificate.evidence_verification
        )

    def test_it_names_the_bank_row_it_matched(self) -> None:
        """So the certificate points at a record rather than at a conclusion."""
        certificate = certificate_of(facts_for())

        assert certificate.matched_bank_transaction_ids == ("BANKTXN-bt-1",)
        assert certificate.bank_reference == VERIFIED_REFERENCE


class TestEachFailureIsIsolatedByOneField:
    """One field changed from the verified case, one outcome each."""

    @pytest.mark.parametrize(
        ("overrides", "expected"),
        [
            ({"direction": "DEBIT"}, BankFinalityOutcome.BANK_DIRECTION_MISMATCH),
            ({"currency": "USD"}, BankFinalityOutcome.BANK_CURRENCY_MISMATCH),
            (
                {"amount_minor": PAYOUT_NET_MINOR + 1},
                BankFinalityOutcome.BANK_AMOUNT_MISMATCH,
            ),
            ({"bank_reference": "UTR-SOMETHING-ELSE"}, BankFinalityOutcome.MISSING_BANK_EVIDENCE),
        ],
    )
    def test_one_changed_field_produces_one_outcome(
        self, overrides: dict[str, object], expected: BankFinalityOutcome
    ) -> None:
        """The control verifies; the case does not; nothing else differs."""
        assert certificate_of(facts_for()).outcome is BankFinalityOutcome.VERIFIED_BANK_CREDIT

        assert certificate_of(facts_for(**overrides)).outcome is expected

    def test_one_minor_unit_over_is_a_mismatch(self) -> None:
        """There is no tolerance band, and this is what that means."""
        certificate = certificate_of(facts_for(amount_minor=PAYOUT_NET_MINOR + 1))

        assert certificate.outcome is BankFinalityOutcome.BANK_AMOUNT_MISMATCH
        assert certificate.expected_amount_minor == PAYOUT_NET_MINOR
        assert certificate.observed_amount_minor == PAYOUT_NET_MINOR + 1

    def test_one_minor_unit_under_is_a_mismatch_too(self) -> None:
        """A short payment is not a rounding difference."""
        certificate = certificate_of(facts_for(amount_minor=PAYOUT_NET_MINOR - 1))

        assert certificate.outcome is BankFinalityOutcome.BANK_AMOUNT_MISMATCH

    def test_a_debit_never_verifies_however_exact_it_is(self) -> None:
        """Same reference, same amount, same currency, opposite direction.

        Money leaving the account under a payout's reference is not weaker
        evidence that the payout arrived. It is evidence of something else.
        """
        certificate = certificate_of(facts_for(direction="DEBIT"))

        assert certificate.outcome is BankFinalityOutcome.BANK_DIRECTION_MISMATCH
        assert certificate.observed_direction is BankDirection.DEBIT
        assert certificate.observed_amount_minor == certificate.expected_amount_minor

    def test_a_currency_mismatch_is_not_reported_as_an_amount_mismatch(self) -> None:
        """Two amounts in different currencies cannot be compared.

        Reporting it as a size difference would send somebody looking for a
        missing hundred rather than for a wrong currency.
        """
        certificate = certificate_of(facts_for(currency="USD"))

        assert certificate.outcome is BankFinalityOutcome.BANK_CURRENCY_MISMATCH
        assert certificate.observed_amount_minor == certificate.expected_amount_minor

    def test_a_direction_mismatch_wins_over_an_amount_mismatch(self) -> None:
        """Both wrong, and the direction is the one worth saying first."""
        certificate = certificate_of(
            facts_for(direction="DEBIT", amount_minor=PAYOUT_NET_MINOR + 500)
        )

        assert certificate.outcome is BankFinalityOutcome.BANK_DIRECTION_MISMATCH


class TestMissingAndUnlinkable:
    """Two different kinds of nothing, kept apart."""

    def test_no_bank_row_is_missing_evidence(self) -> None:
        """The payout names a reference and no statement row carries it."""
        certificate = certificate_of(index_of(linkable_payout()))

        assert certificate.outcome is BankFinalityOutcome.MISSING_BANK_EVIDENCE
        assert certificate.bank_reference == VERIFIED_REFERENCE
        assert certificate.matched_bank_transaction_ids == ()

    def test_missing_evidence_compares_nothing(self) -> None:
        """A comparison against nothing is not a comparison."""
        certificate = certificate_of(index_of(linkable_payout()))

        assert certificate.expected_amount_minor is None
        assert certificate.observed_amount_minor is None
        assert certificate.observed_direction is None

    def test_a_payout_with_no_reference_is_unlinkable_not_missing(self) -> None:
        """The distinction the whole outcome exists for.

        Missing evidence means the statement has not been shown. Unlinkable
        means it could not be associated even if it had been, because the
        provider's own record carries nothing to match on.
        """
        certificate = certificate_of(index_of(payout("po-1", utr=None), bank_transaction("bt-1")))

        assert certificate.outcome is BankFinalityOutcome.UNLINKABLE_PAYOUT
        assert certificate.bank_reference is None

    def test_an_unlinkable_payout_is_not_matched_to_a_lone_credit(self) -> None:
        """One payout, one credit, the right amount, and no shared reference.

        The most tempting guess in the whole system, and it is refused. Nothing
        in the records says this credit is that payout.
        """
        index = index_of(
            payout("po-1", utr=None, net_minor=PAYOUT_NET_MINOR), bank_transaction("bt-1")
        )
        certificate = certificate_of(index)

        assert certificate.outcome is BankFinalityOutcome.UNLINKABLE_PAYOUT
        assert certificate.matched_bank_transaction_ids == ()

    def test_an_unlinkable_payout_cites_only_itself(self) -> None:
        """Citing a credit it did not match would imply an association."""
        certificate = certificate_of(index_of(payout("po-1", utr=None), bank_transaction("bt-1")))

        assert len(certificate.evidence) == 1
        assert certificate.evidence[0].source_record_id == certificate.payout_source_record_id


class TestAmbiguity:
    """Two rows, one reference, and no arbitrary choice."""

    def test_two_matching_rows_are_ambiguous(self) -> None:
        """Not the first, not the closest, not the earliest. Ambiguous."""
        index = index_of(linkable_payout(), bank_transaction("bt-1"), bank_transaction("bt-2"))
        certificate = certificate_of(index)

        assert certificate.outcome is BankFinalityOutcome.AMBIGUOUS_BANK_EVIDENCE

    def test_both_candidates_are_cited(self) -> None:
        """An ambiguous result citing one of two would describe a choice it did
        not make."""
        index = index_of(linkable_payout(), bank_transaction("bt-1"), bank_transaction("bt-2"))
        certificate = certificate_of(index)

        assert certificate.matched_bank_transaction_ids == ("BANKTXN-bt-1", "BANKTXN-bt-2")
        assert len(certificate.evidence) == 3

    def test_ambiguity_wins_even_when_one_row_is_exact(self) -> None:
        """One perfect credit and one for the wrong amount, same reference.

        Picking the exact one would be choosing the evidence that gives the
        answer somebody wanted.
        """
        index = index_of(
            linkable_payout(),
            bank_transaction("bt-1"),
            bank_transaction("bt-2", amount_minor=PAYOUT_NET_MINOR + 5),
        )

        assert certificate_of(index).outcome is BankFinalityOutcome.AMBIGUOUS_BANK_EVIDENCE

    def test_ambiguity_compares_nothing(self) -> None:
        """There is no single observed value to report."""
        index = index_of(linkable_payout(), bank_transaction("bt-1"), bank_transaction("bt-2"))
        certificate = certificate_of(index)

        assert certificate.observed_amount_minor is None
        assert certificate.expected_amount_minor is None


class TestNothingIsApproximate:
    """The matching rule is exact string equality and nothing else."""

    @pytest.mark.parametrize(
        "reference",
        [
            "utr-2026-08-21-0001",
            "UTR-2026-08-21-000",
            "UTR-2026-08-21-00011",
            "UTR20260821 0001",
            "UTR-2026-08-21-0002",
        ],
    )
    def test_a_reference_that_is_nearly_right_does_not_match(self, reference: str) -> None:
        """No case folding, no prefix matching, no normalisation.

        Every one of these would be a rule about what a bank probably meant, and
        this audit reports what the records say. A near miss is missing
        evidence, which is a prompt to look, rather than a verification.
        """
        certificate = certificate_of(facts_for(bank_reference=reference))

        assert certificate.outcome is BankFinalityOutcome.MISSING_BANK_EVIDENCE

    @pytest.mark.parametrize(
        "reference", ["UTR-2026-08-21-0001 ", " UTR-2026-08-21-0001", "\tUTR-2026-08-21-0001"]
    )
    def test_a_padded_reference_cannot_exist_as_a_fact_at_all(self, reference: str) -> None:
        """Refused at the record boundary rather than at the match.

        A reference differing only by invisible characters never reaches the
        matcher, because the contract refuses it when the fact is projected and
        the parser refuses it when the document is read. That is a stronger
        guarantee than not matching it: there is no such record to match or
        mismatch.
        """
        with pytest.raises(ValidationError):
            project_bank_transaction(bank_transaction("bt-1", bank_reference=reference))

    def test_a_credit_on_a_later_date_still_verifies(self) -> None:
        """There is no date window either, in either direction.

        A bank credit is evidence the money arrived. When it arrived is a fact
        the certificate carries; it is not a condition on the match, because a
        window would be a guess about settlement timing.
        """
        certificate = certificate_of(facts_for(occurred_at="2030-01-01T00:00:00+00:00"))

        assert certificate.outcome is BankFinalityOutcome.VERIFIED_BANK_CREDIT


class TestAnAuditIsDeterministic:
    """The same facts produce the same bytes."""

    def test_two_audits_of_one_snapshot_are_identical(self) -> None:
        """Including the recorded time, which comes from the facts."""
        index = facts_for()
        first = audit(BankFinalitySnapshot.from_index(index))
        second = audit(BankFinalitySnapshot.from_index(index))

        assert first.model_dump_json() == second.model_dump_json()

    def test_certificates_are_ordered_by_payout_id(self) -> None:
        """A fixed order, so two audits list the same questions the same way."""
        index = index_of(
            linkable_payout("po-3"),
            linkable_payout("po-1"),
            linkable_payout("po-2"),
            bank_transaction("bt-1"),
        )
        batch = audit(BankFinalitySnapshot.from_index(index))

        payout_ids = [certificate.payout_id for certificate in batch.certificates]
        assert payout_ids == sorted(payout_ids)

    def test_the_batch_counts_every_outcome(self) -> None:
        """Zeroes included, so a missing key never means zero by accident."""
        batch = audit(BankFinalitySnapshot.from_index(facts_for()))

        assert set(batch.outcome_counts) == {member.value for member in BankFinalityOutcome}
        assert batch.outcome_counts[BankFinalityOutcome.VERIFIED_BANK_CREDIT.value] == 1

    def test_the_batch_carries_both_versions(self) -> None:
        """Which rules produced this, and which columns the statement was read
        under."""
        batch = audit(BankFinalitySnapshot.from_index(facts_for()))

        assert batch.bank_finality_version == BANK_FINALITY_VERSION
        assert batch.bank_statement_schema_version == BANK_STATEMENT_SCHEMA_VERSION

    def test_the_snapshot_digest_is_the_reconciliation_one(self) -> None:
        """So a run and an audit over one moment can be put side by side."""
        from app.reconciliation.snapshot import FactSnapshot

        index = facts_for()

        assert (
            BankFinalitySnapshot.from_index(index).digest == FactSnapshot.from_index(index).digest
        )

    def test_an_empty_index_is_refused(self) -> None:
        """An empty audit would look like a clean result."""
        with pytest.raises(ValueError, match="empty fact index"):
            BankFinalitySnapshot.from_index({})


class TestTheVocabulariesDoNotOverlap:
    """A finality outcome can never be rendered as a settlement status."""

    def test_no_outcome_is_a_decision_status(self) -> None:
        """Asserted as sets, so neither can be read as the other by a client
        that indexes a badge map by string."""
        outcomes = {member.value for member in BankFinalityOutcome}
        statuses = {member.value for member in DecisionStatus}

        assert outcomes.isdisjoint(statuses)

    def test_no_outcome_is_the_word_resolved(self) -> None:
        """The specific confusion this phase exists to prevent."""
        for outcome in BankFinalityOutcome:
            assert "RESOLVED" not in outcome.value

    def test_a_certificate_has_no_status_field(self) -> None:
        """It carries an outcome. A field called status would invite the join
        that must not happen."""
        assert "status" not in BankFinalityCertificate.model_fields

    def test_a_certificate_carries_no_free_text(self) -> None:
        """Every field is an identifier, an amount, a code or a time.

        A description field would be the first thing a fuzzy matcher reached
        for, and the second thing somebody put a model's opinion in.
        """
        rendered = json.loads(certificate_of(facts_for()).model_dump_json())

        assert not {"description", "note", "narrative", "detail", "reason"} & set(rendered)


class TestAuditingOnePayoutDirectly:
    """`audit_payout` is the unit the batch is built from."""

    def test_it_returns_the_same_certificate_the_batch_does(self) -> None:
        """So a test of one payout is a test of what a run records."""
        snapshot = BankFinalitySnapshot.from_index(facts_for())
        direct = audit_payout(snapshot.payouts[0], snapshot)

        assert direct == audit(snapshot).certificates[0]

    def test_the_recorded_time_comes_from_the_facts(self) -> None:
        """A wall clock would make every audit differ for no reason."""
        snapshot = BankFinalitySnapshot.from_index(facts_for())

        assert audit_payout(snapshot.payouts[0], snapshot).recorded_at == snapshot.as_of
