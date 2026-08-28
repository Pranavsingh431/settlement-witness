"""Tests for settlement lines that no question can be asked about.

The defect these exist for: line outcomes were built from the requests, so a
line with no candidate page produced no request and therefore no outcome. A
snapshot holding one settlement line reported `line_count = 0`. The line was
there and the report said nothing about it, which is the worst way for something
not to be evaluated: silently.

A line has no candidate page when the snapshot holds no payment event and no
payout. The candidate universe is every event and payout in the snapshot, so
that is a property of the whole snapshot rather than of one line.
"""

import pytest

from app.ai.candidates import build_pages, build_requests, truth_for
from app.ai.evaluation import ExpectedProviderAction, evaluate
from app.ai.provider import FixtureProvider, always_abstains, selecting, selects_everything
from app.reconciliation.snapshot import FactSnapshot
from tests.reconciliation.conftest import index_of, payment_event, payout, settlement_line


@pytest.fixture
def only_lines() -> FactSnapshot:
    """Return a snapshot holding two settlement lines and nothing else."""
    return FactSnapshot.from_index(
        index_of(settlement_line("sl-1"), settlement_line("sl-2", payment_id="pay-2"))
    )


@pytest.fixture
def askable() -> FactSnapshot:
    """Return an ordinary snapshot where every line has candidates."""
    return FactSnapshot.from_index(
        index_of(payment_event("pe-1", payment_id="pay-1"), settlement_line("sl-1"), payout("po-1"))
    )


class TestALineWithNoCandidatesIsStillReported:
    """It appears, and it is marked as never having been asked about."""

    def test_the_line_count_matches_the_snapshot(self, only_lines: FactSnapshot) -> None:
        """The reproduction, kept as a test.

        Before this phase a snapshot with one settlement line reported
        `line_count = 0`.
        """
        report = evaluate(only_lines, FixtureProvider(always_abstains()))

        assert report.line_count == len(only_lines.settlement_lines) == 2
        assert len(report.line_outcomes) == 2

    def test_no_page_is_built_for_it(self, only_lines: FactSnapshot) -> None:
        """There is nothing to offer, and an empty page is not a question."""
        assert build_requests(only_lines) == ()
        assert build_pages("line-sl-1", only_lines) == ()

    def test_the_report_says_how_many_were_unaskable(self, only_lines: FactSnapshot) -> None:
        """Named, so a reader can see why no request was made."""
        report = evaluate(only_lines, FixtureProvider(always_abstains()))

        assert report.unaskable_line_count == 2
        assert report.page_count == 0

    def test_each_such_line_is_marked_on_its_own_outcome(self, only_lines: FactSnapshot) -> None:
        """Per line, not only as a total."""
        report = evaluate(only_lines, FixtureProvider(always_abstains()))

        for outcome in report.line_outcomes:
            assert not outcome.askable
            assert outcome.page_count == 0
            assert outcome.selected == ()


class TestItIsNotCreditedForAnythingItDidNotDo:
    """The reason the flag exists rather than an empty outcome being enough."""

    def test_it_is_never_an_exact_selection(self, only_lines: FactSnapshot) -> None:
        """Its truth and its selection are both empty.

        Comparing them would say yes, and a corpus of lines with no candidates
        would score perfect exact-set accuracy for having asked nothing.
        """
        report = evaluate(only_lines, FixtureProvider(always_abstains()))

        assert all(not outcome.exact for outcome in report.line_outcomes)
        assert report.exact_set_accuracy.value == 0.0

    def test_it_is_never_a_safe_abstention(self, only_lines: FactSnapshot) -> None:
        """Nobody declined, because nobody was asked."""
        expected = dict.fromkeys(
            (line.settlement_line_id for line in only_lines.settlement_lines),
            ExpectedProviderAction.ABSTAIN,
        )

        report = evaluate(only_lines, FixtureProvider(always_abstains()), expected)

        assert report.safe_abstention_recall.value == 0.0
        assert report.unusable_expected_abstention_rate.value == 1.0

    def test_its_denominators_stay_explicit(self, only_lines: FactSnapshot) -> None:
        """No truth to recall, reported as unmeasurable rather than as zero."""
        report = evaluate(only_lines, FixtureProvider(always_abstains()))

        assert report.link_recall.value is None
        assert report.link_precision.value is None
        assert report.exact_set_accuracy.denominator == 2


class TestAMixedCorpus:
    """Askable and unaskable lines counted side by side."""

    def test_adding_an_unaskable_line_does_not_hide_it(self, askable: FactSnapshot) -> None:
        """A second line with nothing to link is still in the report.

        Constructed by taking an ordinary snapshot and reading it as though its
        candidates were gone, which is what a snapshot of lines alone looks
        like.
        """
        ordinary = evaluate(
            askable, FixtureProvider(selecting(lambda one: tuple(sorted(truth_for(one, askable)))))
        )

        assert ordinary.line_count == 1
        assert ordinary.unaskable_line_count == 0
        assert ordinary.exact_set_accuracy.value == 1.0

    def test_an_unaskable_corpus_does_not_inflate_exact_set_accuracy(
        self, only_lines: FactSnapshot, askable: FactSnapshot
    ) -> None:
        """The comparison that shows the credit is not being given.

        One snapshot is answered perfectly and scores 1.000. The other is never
        asked and scores 0.000, rather than scoring 1.000 for two empty
        selections matching two empty truths.
        """
        answered = evaluate(
            askable, FixtureProvider(selecting(lambda one: tuple(sorted(truth_for(one, askable)))))
        )
        unasked = evaluate(only_lines, FixtureProvider(always_abstains()))

        assert answered.exact_set_accuracy.value == 1.0
        assert unasked.exact_set_accuracy.value == 0.0

    def test_no_provider_behaviour_can_change_an_unaskable_line(
        self, only_lines: FactSnapshot
    ) -> None:
        """It was never asked, so what a provider would have said cannot matter."""
        reports = [
            evaluate(only_lines, FixtureProvider(behaviour)).model_dump_json()
            for behaviour in (always_abstains(), selects_everything())
        ]

        assert reports[0] == reports[1]
