"""Tests for the paged candidate environment.

The defect these exist for: a selection is bounded at `MAX_SELECTED_RECORDS`, so
a line with more true links than that had no expressible correct answer and a
perfect provider scored zero. That was an environment that could not be
answered, not a provider that failed.

The partition rules are the fix, and they are only worth having if they hold
exactly: every candidate on exactly one page, no page empty, no page too large,
and the union equal to the universe. A pager that dropped a candidate would make
a correct answer unreachable again, quietly.
"""

from itertools import pairwise

import pytest
from pydantic import ValidationError

from app.ai.candidates import (
    MAX_CANDIDATE_PAGE,
    CandidateRecord,
    LinkProposalRequest,
    build_pages,
    build_request,
    build_requests,
    candidate_universe,
    environment_fingerprint,
    line_truth_for,
    truth_for,
)
from app.ai.evaluation import evaluate
from app.ai.proposals import MAX_SELECTED_RECORDS, ProposalOutcome, RawLinkSelection
from app.ai.provider import FixtureProvider, selecting
from app.ai.validation import RejectionCode, parse_proposal
from app.domain.facts import SourceRecordType
from app.reconciliation.snapshot import FactSnapshot
from tests.ai.conftest import FIXTURE, payload_for
from tests.reconciliation.conftest import index_of, payment_event, payout, settlement_line

LINKED_EVENTS = 150
"""Enough that one line's true links span three pages."""


def wide_snapshot(events: int = LINKED_EVENTS, reverse: bool = False) -> FactSnapshot:
    """Return a snapshot whose single line links more records than one page holds."""
    facts = [
        payment_event(f"pe-{index:04d}", payment_id="pay-1", amount_minor=1000 + index)
        for index in range(events)
    ]
    facts += [settlement_line("sl-1", payment_id="pay-1"), payout("po-1")]
    if reverse:
        facts.reverse()
    return FactSnapshot.from_index(index_of(*facts))


class TestTheDefectThisFixes:
    """A line with more true links than a selection may carry."""

    def test_a_complete_selection_was_unexpressible(self) -> None:
        """The reproduction, kept as a test.

        The universe offered every true link and the contract refused to carry
        them, so no correct answer existed for the question as it was asked.
        """
        too_many = tuple(f"rec-{index}" for index in range(MAX_SELECTED_RECORDS + 1))

        with pytest.raises(ValueError, match="more than the 64 allowed"):
            RawLinkSelection(outcome=ProposalOutcome.PROPOSE, selected_source_record_ids=too_many)

    def test_a_perfect_provider_now_scores_perfectly(self) -> None:
        """The same line, answered a page at a time.

        Before paging this was 0.000 recall and 0.000 exact-set, because the
        one correct answer could not be returned.
        """
        snapshot = wide_snapshot()
        provider = FixtureProvider(
            selecting(lambda request: tuple(sorted(truth_for(request, snapshot))))
        )

        report = evaluate(snapshot, provider)

        assert report.link_recall.value == 1.0
        assert report.exact_set_accuracy.value == 1.0
        assert report.invalid_page_rate.value == 0.0
        assert report.link_recall.denominator == LINKED_EVENTS + 1

    def test_every_page_is_completely_answerable(self) -> None:
        """Selecting a whole page is always within the bound.

        The property that makes the environment answerable. A page offering more
        than a provider may select would be the same defect again.
        """
        snapshot = wide_snapshot()

        for page in build_pages("line-sl-1", snapshot):
            whole_page = tuple(sorted(page.candidate_ids))

            assert len(whole_page) <= MAX_SELECTED_RECORDS
            RawLinkSelection(outcome=ProposalOutcome.PROPOSE, selected_source_record_ids=whole_page)


class TestThePartitionRules:
    """Every rule stated in the design, checked."""

    @pytest.fixture
    def pages(self) -> tuple[object, ...]:
        """Return the pages of the wide line."""
        return build_pages("line-sl-1", wide_snapshot())

    def test_the_union_is_exactly_the_universe(self) -> None:
        """Nothing added and nothing lost by paging."""
        snapshot = wide_snapshot()
        universe = {one.source_record_id for one in candidate_universe("line-sl-1", snapshot)}

        union: set[str] = set()
        for page in build_pages("line-sl-1", snapshot):
            union |= page.candidate_ids

        assert union == universe

    def test_every_candidate_appears_exactly_once(self) -> None:
        """A record on two pages could be selected twice and counted twice."""
        pages = build_pages("line-sl-1", wide_snapshot())

        total = sum(len(page.candidates) for page in pages)
        distinct = len({one for page in pages for one in page.candidate_ids})

        assert total == distinct

    def test_no_page_is_empty(self) -> None:
        """A question about nothing has no useful answer."""
        assert all(page.candidates for page in build_pages("line-sl-1", wide_snapshot()))

    def test_no_page_exceeds_the_maximum(self) -> None:
        """The bound that makes each page answerable."""
        pages = build_pages("line-sl-1", wide_snapshot())

        assert all(len(page.candidates) <= MAX_CANDIDATE_PAGE for page in pages)

    def test_page_ordinals_run_from_one_without_gaps(self) -> None:
        """So a reader can tell which page a result came from."""
        pages = build_pages("line-sl-1", wide_snapshot())

        assert [page.page_ordinal for page in pages] == list(range(1, len(pages) + 1))
        assert all(page.page_count == len(pages) for page in pages)

    def test_candidates_are_ordered_within_a_page(self) -> None:
        """Sorted, because the partition is consecutive blocks of a sorted list."""
        for page in build_pages("line-sl-1", wide_snapshot()):
            shown = [one.source_record_id for one in page.candidates]

            assert shown == sorted(shown)

    def test_pages_do_not_overlap_in_record_id_order(self) -> None:
        """Consecutive blocks, so page two starts after page one ends."""
        pages = build_pages("line-sl-1", wide_snapshot())

        boundaries = [(min(page.candidate_ids), max(page.candidate_ids)) for page in pages]
        for earlier, later in pairwise(boundaries):
            assert earlier[1] < later[0]

    def test_a_universe_that_fits_gets_one_page(self) -> None:
        """Paging is invisible when it is not needed."""
        snapshot = wide_snapshot(events=3)

        assert len(build_pages("line-sl-1", snapshot)) == 1

    def test_a_universe_one_over_gets_two_pages(self) -> None:
        """The boundary, from the other side."""
        snapshot = wide_snapshot(events=MAX_CANDIDATE_PAGE)

        pages = build_pages("line-sl-1", snapshot)

        assert len(pages) == 2
        assert [len(page.candidates) for page in pages] == [MAX_CANDIDATE_PAGE, 1]


class TestDeterminism:
    """The same facts must produce the same questions, every time."""

    def test_building_twice_produces_identical_pages(self) -> None:
        """Byte for byte."""
        snapshot = wide_snapshot()

        first = [page.model_dump_json() for page in build_pages("line-sl-1", snapshot)]
        second = [page.model_dump_json() for page in build_pages("line-sl-1", snapshot)]

        assert first == second

    def test_reversing_insertion_order_changes_nothing(self) -> None:
        """The partition is by sorted record ID, not by arrival.

        A pager that depended on insertion order would cut different pages from
        the same facts depending on the order they were imported, and two
        reports over one snapshot would differ for a reason nothing recorded.
        """
        forward = [page.model_dump_json() for page in build_pages("line-sl-1", wide_snapshot())]
        backward = [
            page.model_dump_json() for page in build_pages("line-sl-1", wide_snapshot(reverse=True))
        ]

        assert forward == backward

    def test_reversing_insertion_order_produces_an_identical_report(self) -> None:
        """The whole way through, not only at the pager."""
        forward_snapshot = wide_snapshot()
        backward_snapshot = wide_snapshot(reverse=True)
        provider = FixtureProvider(
            selecting(lambda request: tuple(sorted(truth_for(request, forward_snapshot))))
        )

        forward = evaluate(forward_snapshot, provider).model_dump_json()
        backward = evaluate(backward_snapshot, provider).model_dump_json()

        assert forward == backward

    def test_the_environment_fingerprint_is_the_same_on_every_page(self) -> None:
        """It identifies the universe, not the page."""
        pages = build_pages("line-sl-1", wide_snapshot())

        assert len({page.environment_fingerprint for page in pages}) == 1

    def test_the_environment_fingerprint_ignores_order(self) -> None:
        """Built from sorted record IDs."""
        assert environment_fingerprint(["b", "a"]) == environment_fingerprint(["a", "b"])

    def test_the_environment_fingerprint_moves_when_a_candidate_does(self) -> None:
        """So two reports cannot be compared as though they saw one universe."""
        assert environment_fingerprint(["a", "b"]) != environment_fingerprint(["a", "b", "c"])

    def test_two_pages_of_one_line_get_different_proposal_ids(self) -> None:
        """Otherwise a report would carry several records claiming to be one."""
        snapshot = wide_snapshot()
        pages = build_pages("line-sl-1", snapshot)
        first, second = pages[0], pages[1]

        one = parse_proposal(payload_for((next(iter(first.candidate_ids)),)), first, FIXTURE)
        two = parse_proposal(payload_for((next(iter(second.candidate_ids)),)), second, FIXTURE)

        assert one.proposal.proposal_id != two.proposal.proposal_id  # type: ignore[union-attr]


class TestAPageIsTheWholeWorld:
    """A provider may select only from the page in front of it."""

    def test_a_record_from_another_page_is_refused(self) -> None:
        """Even though it belongs to the same settlement line.

        The page is the candidate set. A record on page two was not offered on
        page one, and being linked to the same line does not make it selectable
        there.
        """
        snapshot = wide_snapshot()
        pages = build_pages("line-sl-1", snapshot)
        from_page_two = sorted(pages[1].candidate_ids)[0]

        result = parse_proposal(payload_for((from_page_two,)), pages[0], FIXTURE)

        assert result.code is RejectionCode.OUT_OF_CANDIDATE_SET  # type: ignore[union-attr]
        assert "page 1" in result.detail  # type: ignore[union-attr]

    def test_the_same_record_is_accepted_on_its_own_page(self) -> None:
        """So the refusal is about the page, not about the record."""
        snapshot = wide_snapshot()
        pages = build_pages("line-sl-1", snapshot)
        from_page_two = sorted(pages[1].candidate_ids)[0]

        result = parse_proposal(payload_for((from_page_two,)), pages[1], FIXTURE)

        assert result.proposal.selected_source_record_ids == (from_page_two,)  # type: ignore[union-attr]


class TestMissingOnePage:
    """Answering most of a line is not answering it."""

    def test_missing_the_final_page_lowers_strict_recall(self) -> None:
        """The links on that page were never returned."""
        snapshot = wide_snapshot()
        last = len(build_pages("line-sl-1", snapshot))

        def all_but_last(request: object) -> tuple[str, ...]:
            if request.page_ordinal == last:  # type: ignore[attr-defined]
                return ()
            return tuple(sorted(truth_for(request, snapshot)))  # type: ignore[arg-type]

        report = evaluate(snapshot, FixtureProvider(selecting(all_but_last)))

        assert report.link_recall.value is not None
        assert report.link_recall.value < 1.0

    def test_missing_the_final_page_fails_exact_set_accuracy(self) -> None:
        """A line is exact only if every page of it was answered."""
        snapshot = wide_snapshot()
        last = len(build_pages("line-sl-1", snapshot))

        def all_but_last(request: object) -> tuple[str, ...]:
            if request.page_ordinal == last:  # type: ignore[attr-defined]
                return ()
            return tuple(sorted(truth_for(request, snapshot)))  # type: ignore[arg-type]

        report = evaluate(snapshot, FixtureProvider(selecting(all_but_last)))

        assert report.exact_set_accuracy.value == 0.0

    def test_the_conditional_measure_stays_perfect(self) -> None:
        """Because the pages it answered were answered correctly.

        Reported separately for exactly this: it is true and it is not recall.
        """
        snapshot = wide_snapshot()
        last = len(build_pages("line-sl-1", snapshot))

        def all_but_last(request: object) -> tuple[str, ...]:
            if request.page_ordinal == last:  # type: ignore[attr-defined]
                return ()
            return tuple(sorted(truth_for(request, snapshot)))  # type: ignore[arg-type]

        report = evaluate(snapshot, FixtureProvider(selecting(all_but_last)))

        assert report.answered_link_recall.value == 1.0


class TestTheLineOracleSpansPages:
    """Strict recall is measured over the whole line."""

    def test_the_line_truth_is_the_whole_linked_set(self) -> None:
        """Not the part that fitted on one page."""
        snapshot = wide_snapshot()
        pages = build_pages("line-sl-1", snapshot)

        assert len(line_truth_for(pages[0], snapshot)) == LINKED_EVENTS + 1

    def test_a_page_oracle_is_the_part_on_that_page(self) -> None:
        """Because a page can only be answered with what it offers."""
        snapshot = wide_snapshot()
        pages = build_pages("line-sl-1", snapshot)

        per_page = sum(len(truth_for(page, snapshot)) for page in pages)

        assert per_page == len(line_truth_for(pages[0], snapshot))

    def test_every_page_of_every_line_is_asked(self) -> None:
        """So nothing is silently left out of the evaluation."""
        snapshot = wide_snapshot()

        requests = build_requests(snapshot)

        assert len(requests) == len(build_pages("line-sl-1", snapshot))


class TestThePagerRefusesItsOwnMistakes:
    """A page that could not be answered is caught where it is built.

    `build_pages` cannot produce any of these, which is the point: they are the
    shapes it must never produce, and the request refuses them so that a future
    change to the pager fails here rather than surfacing later as an invalid
    proposal nobody can explain.
    """

    def page(self, **overrides: object) -> LinkProposalRequest:
        """Return a valid page request, built directly."""
        candidate = CandidateRecord(source_record_id="rec-1", record_type=SourceRecordType.PAYOUT)
        fields: dict[str, object] = {
            "subject_settlement_line_id": "line-1",
            "subject_payment_id": "pay-1",
            "subject_payout_id": "po-1",
            "snapshot_fingerprint": "a" * 64,
            "environment_fingerprint": "e" * 64,
            "page_ordinal": 1,
            "page_count": 1,
            "candidates": (candidate,),
        }
        fields.update(overrides)
        return LinkProposalRequest(**fields)  # type: ignore[arg-type]

    def test_a_valid_page_is_accepted(self) -> None:
        """The baseline the rest of these change one field of."""
        assert self.page().page_ordinal == 1

    def test_an_empty_page_is_refused(self) -> None:
        """A question about nothing has no useful answer."""
        with pytest.raises(ValidationError, match="at least one record"):
            self.page(candidates=())

    def test_an_oversized_page_is_refused(self) -> None:
        """It would be a question with no expressible correct answer."""
        too_many = tuple(
            CandidateRecord(source_record_id=f"rec-{index}", record_type=SourceRecordType.PAYOUT)
            for index in range(MAX_CANDIDATE_PAGE + 1)
        )

        with pytest.raises(ValidationError, match="more than the 64"):
            self.page(candidates=too_many)

    def test_a_page_beyond_the_count_is_refused(self) -> None:
        """Page three of two is not a page."""
        with pytest.raises(ValidationError, match="page 3 of 2 is not a page"):
            self.page(page_ordinal=3, page_count=2)

    def test_a_page_ordinal_below_one_is_refused(self) -> None:
        """Ordinals count from one, so there is no page zero."""
        with pytest.raises(ValidationError):
            self.page(page_ordinal=0)


class TestLinesWithNothingToOfferAndTheSinglePageHelper:
    """Two edges of the pager, each with a reason to behave as it does."""

    def test_a_line_with_no_candidates_gets_no_pages(self) -> None:
        """Rather than one empty page, which would be a question about nothing."""
        snapshot = FactSnapshot.from_index(index_of(settlement_line("sl-1")))

        assert build_pages("line-sl-1", snapshot) == ()

    def test_the_single_page_helper_returns_the_only_page(self) -> None:
        """For callers working with snapshots where paging is not the subject."""
        snapshot = wide_snapshot(events=2)

        assert build_request("line-sl-1", snapshot).page_ordinal == 1

    def test_the_single_page_helper_refuses_a_multi_page_line(self) -> None:
        """A caller that assumed one page would evaluate a fraction of it."""
        snapshot = wide_snapshot()

        with pytest.raises(ValueError, match="candidate pages"):
            build_request("line-sl-1", snapshot)
