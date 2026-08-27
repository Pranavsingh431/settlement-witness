"""Tests for the finite world a provider chooses from.

The inclusion policy is the thing under test. If the candidate set were narrowed
by payment ID, the linking would already be done and the provider would be
scored on a filter's work. If it leaked records from outside the snapshot, the
membership check downstream would be checking against the wrong set.
"""

import pytest

from app.ai.candidates import (
    CandidateRecord,
    build_request,
    build_requests,
    selectable_records,
    truth_for,
)
from app.domain.facts import SourceRecordType
from app.reconciliation.baseline import reconcile_line
from app.reconciliation.snapshot import FactSnapshot


class TestWhatIsOffered:
    """The inclusion policy, stated and checked."""

    def test_every_payment_event_and_payout_is_a_candidate(self, snapshot: FactSnapshot) -> None:
        """Not a shortlist. Narrowing would do the linking before asking."""
        request = build_request("line-sl-1", snapshot)

        assert {candidate.record_type for candidate in request.candidates} == {
            SourceRecordType.PAYMENT_EVENT,
            SourceRecordType.PAYOUT,
        }
        assert len(request.candidates) == 3

    def test_the_other_line_records_are_offered_too(self, snapshot: FactSnapshot) -> None:
        """The event belonging to the other payment is in the set.

        That is what makes a wrong answer possible, and therefore what makes a
        right one worth measuring.
        """
        request = build_request("line-sl-1", snapshot)

        assert "PAYMENT_EVENT:pe-2" in request.candidate_ids

    def test_the_subject_line_is_not_a_candidate(self, snapshot: FactSnapshot) -> None:
        """It is what is being asked about, and it is linked by construction.

        Offering it back would let a provider score by selecting the thing in
        the question.
        """
        request = build_request("line-sl-1", snapshot)

        assert "SETTLEMENT_LINE:sl-1" not in request.candidate_ids

    def test_no_settlement_line_is_a_candidate(self, snapshot: FactSnapshot) -> None:
        """Including any line would invite linking lines to each other."""
        request = build_request("line-sl-1", snapshot)

        assert all(
            candidate.record_type is not SourceRecordType.SETTLEMENT_LINE
            for candidate in request.candidates
        )

    def test_an_unknown_line_is_refused(self, snapshot: FactSnapshot) -> None:
        """Rather than returning an empty environment that looks answerable."""
        with pytest.raises(ValueError, match="no settlement line"):
            build_request("line-nope", snapshot)


class TestWhatACandidateCarries:
    """Reference fields only. No money, no prose."""

    def test_a_payment_event_carries_its_matching_reference(self, snapshot: FactSnapshot) -> None:
        """The payment ID is how a line is matched to its events."""
        request = build_request("line-sl-1", snapshot)
        event = next(
            candidate
            for candidate in request.candidates
            if candidate.source_record_id == "PAYMENT_EVENT:pe-1"
        )

        assert event.payment_id == "pay-1"
        assert event.event_type == "CAPTURE"

    def test_a_payout_carries_its_own_reference(self, snapshot: FactSnapshot) -> None:
        """And no payment ID, because a payout has none."""
        request = build_request("line-sl-1", snapshot)
        payout = next(
            candidate
            for candidate in request.candidates
            if candidate.record_type is SourceRecordType.PAYOUT
        )

        assert payout.payout_id == "payout-1"
        assert payout.payment_id is None

    def test_no_candidate_carries_money(self, snapshot: FactSnapshot) -> None:
        """Linking here is by exact reference and never by amount.

        An amount in the request would invite a provider to reason from a number
        it cannot check, and would put merchant money in front of a model for no
        purpose the task needs.
        """
        declared = set(CandidateRecord.model_fields)

        assert declared == {
            "source_record_id",
            "record_type",
            "payment_id",
            "payout_id",
            "event_type",
            "occurred_at",
        }
        assert not any("minor" in name or "amount" in name for name in declared)

    def test_a_candidate_forbids_extra_fields(self) -> None:
        """So nothing can be attached to a record on the way to a provider."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="extra"):
            CandidateRecord(
                source_record_id="rec-1",
                record_type=SourceRecordType.PAYOUT,
                note="anything",  # type: ignore[call-arg]
            )


class TestTheOrderIsStable:
    """Two runs must ask the same question the same way."""

    def test_candidates_are_ordered_by_record_id(self, snapshot: FactSnapshot) -> None:
        """Documented, and checked, because an unstable order would make a
        provider's behaviour vary for reasons nothing recorded."""
        request = build_request("line-sl-1", snapshot)

        ids = [candidate.source_record_id for candidate in request.candidates]
        assert ids == sorted(ids)

    def test_building_twice_produces_an_identical_request(self, snapshot: FactSnapshot) -> None:
        """Byte for byte."""
        first = build_request("line-sl-1", snapshot)
        second = build_request("line-sl-1", snapshot)

        assert first.model_dump_json() == second.model_dump_json()

    def test_requests_are_ordered_by_settlement_line(self, snapshot: FactSnapshot) -> None:
        """So a report lists lines in the same order every time."""
        requests = build_requests(snapshot)

        subjects = [request.subject_settlement_line_id for request in requests]
        assert subjects == sorted(subjects)
        assert subjects == ["line-sl-1", "line-sl-2"]


class TestTheTruthMatchesTheBaseline:
    """The oracle is the linker, not a second implementation of it."""

    def test_the_linked_set_is_what_the_baseline_links(self, snapshot: FactSnapshot) -> None:
        """Minus the line's own record, which is not a candidate.

        Derived from `reconcile_line` rather than restated, so a change to the
        linking rule cannot leave the oracle and the baseline disagreeing about
        what a correct answer is.
        """
        line = snapshot.settlement_lines[0]
        request = build_request(line.settlement_line_id, snapshot)

        candidate = reconcile_line(line, snapshot)
        expected = set(candidate.linked_source_record_ids) - {line.source_record_id}

        assert truth_for(request, snapshot) == expected

    def test_each_line_links_its_own_payment(self, snapshot: FactSnapshot) -> None:
        """The two lines have different correct answers, which is the point."""
        first = truth_for(build_request("line-sl-1", snapshot), snapshot)
        second = truth_for(build_request("line-sl-2", snapshot), snapshot)

        assert "PAYMENT_EVENT:pe-1" in first
        assert "PAYMENT_EVENT:pe-1" not in second
        assert first != second

    def test_the_truth_is_always_inside_the_candidate_set(self, snapshot: FactSnapshot) -> None:
        """Otherwise a correct answer would be unselectable."""
        for request in build_requests(snapshot):
            assert truth_for(request, snapshot) <= request.candidate_ids


class TestNothingOutsideTheSnapshotIsSelectable:
    """The environment bounds what any later selection can name."""

    def test_every_offered_record_is_a_fact_in_the_snapshot(self, snapshot: FactSnapshot) -> None:
        """A provider cannot be offered something that is not there."""
        offered = selectable_records(build_requests(snapshot))

        assert offered <= set(snapshot.facts_by_record_id)

    def test_no_settlement_line_record_is_offered_anywhere(self, snapshot: FactSnapshot) -> None:
        """Across every request, not only the one under test."""
        offered = selectable_records(build_requests(snapshot))

        assert not any(record_id.startswith("SETTLEMENT_LINE:") for record_id in offered)


class TestALineWhoseRecordsAreNotAllThere:
    """The snapshot decides what exists, and it can be short of a record."""

    @pytest.fixture
    def without_a_payout(self) -> FactSnapshot:
        """Return a snapshot holding a line and its event but no payout."""
        from tests.reconciliation.conftest import index_of, payment_event, settlement_line

        return FactSnapshot.from_index(index_of(payment_event("pe-1"), settlement_line("sl-1")))

    def test_the_truth_holds_only_what_is_present(self, without_a_payout: FactSnapshot) -> None:
        """A payout the line names and the snapshot lacks is not linkable.

        The oracle describes what the baseline links from these facts, not what
        a complete export would have contained. Counting an absent record as a
        correct answer would score a provider on something it was never shown.
        """
        request = build_request("line-sl-1", without_a_payout)

        assert truth_for(request, without_a_payout) == {"PAYMENT_EVENT:pe-1"}

    def test_no_payout_is_offered_either(self, without_a_payout: FactSnapshot) -> None:
        """So the candidate set and the oracle agree about what exists."""
        request = build_request("line-sl-1", without_a_payout)

        assert request.candidate_ids == {"PAYMENT_EVENT:pe-1"}

    def test_a_provider_can_still_be_exactly_right(self, without_a_payout: FactSnapshot) -> None:
        """A short snapshot is answerable, not unanswerable."""
        from app.ai.evaluation import evaluate
        from app.ai.provider import FixtureProvider, selecting

        provider = FixtureProvider(
            selecting(lambda req: tuple(sorted(truth_for(req, without_a_payout))))
        )
        report = evaluate(without_a_payout, provider)

        assert report.exact_set_accuracy.value == 1.0
