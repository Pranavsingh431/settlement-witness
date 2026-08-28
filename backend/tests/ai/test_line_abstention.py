"""Tests for what counts as a line the provider declined.

The defect these exist for: `abstained_line_rate` counted any line where
nothing was selected. It reported 1.000 for a corpus no provider was ever called
on, and 1.000 next to an invalid page rate of 1.000 for a provider whose every
page was refused. A report that says no page abstained and every line did is
describing two different runs.

It is the same mistake Phase 9.1 removed from `safe_abstention_recall`, left in
the field beside it. Abstaining is something a provider does; failing is not
doing it, and never being asked is not doing it either.
"""

import pytest

from app.ai.candidates import LinkProposalRequest, truth_for
from app.ai.evaluation import (
    ExpectedProviderAction,
    LineOutcome,
    ShadowReport,
    _report,
    evaluate,
)
from app.ai.proposals import ProviderIdentity
from app.ai.provider import (
    Behaviour,
    FailureKind,
    FixtureProvider,
    ProviderResult,
    always_abstains,
    fails_with,
    returns,
    selecting,
    selects_everything,
)
from app.reconciliation.snapshot import FactSnapshot
from tests.ai.conftest import FIXTURE
from tests.reconciliation.conftest import index_of, payment_event, payout, settlement_line


@pytest.fixture
def one_page() -> FactSnapshot:
    """Return a snapshot whose single line fits on one candidate page."""
    return FactSnapshot.from_index(
        index_of(payment_event("pe-1", payment_id="pay-1"), settlement_line("sl-1"), payout("po-1"))
    )


@pytest.fixture
def two_pages() -> FactSnapshot:
    """Return a snapshot whose single line needs two candidate pages."""
    facts = [
        payment_event(f"pe-{index:03d}", payment_id="pay-1", amount_minor=1000 + index)
        for index in range(70)
    ]
    facts += [settlement_line("sl-1", payment_id="pay-1"), payout("po-1")]
    return FactSnapshot.from_index(index_of(*facts))


@pytest.fixture
def no_candidates() -> FactSnapshot:
    """Return a snapshot of settlement lines and nothing else."""
    return FactSnapshot.from_index(
        index_of(settlement_line("sl-1"), settlement_line("sl-2", payment_id="pay-2"))
    )


def report(snapshot: FactSnapshot, behaviour: Behaviour) -> ShadowReport:
    """Evaluate one behaviour against a snapshot."""
    return evaluate(snapshot, FixtureProvider(behaviour))


def abstain_then_fail(request: LinkProposalRequest) -> ProviderResult:
    """Decline the first page and return rubbish on the rest."""
    if request.page_ordinal == 1:
        return {"outcome": "ABSTAIN", "selected_source_record_ids": []}
    return "not a proposal"


class TestOnlyAnActualAbstentionCounts:
    """Each behaviour, and what the rate says about it."""

    def test_abstaining_on_every_page_counts(self, one_page: FactSnapshot) -> None:
        """The provider declined, which is what the rate is named for."""
        assert report(one_page, always_abstains()).fully_abstained_askable_line_rate.value == 1.0

    def test_malformed_output_does_not_count(self, one_page: FactSnapshot) -> None:
        """The reproduction, kept as a test.

        Before this phase this reported 1.000 while the invalid page rate also
        reported 1.000, which cannot both be true of one line.
        """
        result = report(one_page, returns("not a proposal"))

        assert result.fully_abstained_askable_line_rate.value == 0.0
        assert result.invalid_page_rate.value == 1.0

    @pytest.mark.parametrize("kind", list(FailureKind))
    def test_a_provider_failure_does_not_count(
        self, one_page: FactSnapshot, kind: FailureKind
    ) -> None:
        """A provider that did not answer did not decline."""
        assert report(one_page, fails_with(kind)).fully_abstained_askable_line_rate.value == 0.0

    def test_a_partial_abstention_does_not_count(self, two_pages: FactSnapshot) -> None:
        """One declined page and one refused page is not a declined line."""
        result = report(two_pages, abstain_then_fail)

        assert result.fully_abstained_askable_line_rate.value == 0.0
        assert result.abstention_page_rate.value == 0.5

    def test_abstaining_on_both_pages_counts(self, two_pages: FactSnapshot) -> None:
        """So the rule is every page, not merely some page."""
        assert report(two_pages, always_abstains()).fully_abstained_askable_line_rate.value == 1.0

    def test_selecting_anything_does_not_count(self, one_page: FactSnapshot) -> None:
        """A line that produced a link was not declined."""
        result = report(
            one_page, selecting(lambda request: tuple(sorted(truth_for(request, one_page))))
        )

        assert result.fully_abstained_askable_line_rate.value == 0.0

    def test_selecting_everything_does_not_count(self, one_page: FactSnapshot) -> None:
        """Nor was that one."""
        assert report(one_page, selects_everything()).fully_abstained_askable_line_rate.value == 0.0


class TestUnaskableLinesCannotInflateIt:
    """Nobody was asked, so nobody declined."""

    def test_an_all_unaskable_corpus_reports_null(self, no_candidates: FactSnapshot) -> None:
        """The other reproduction.

        Before this phase a corpus the provider was never called on reported a
        full abstention, which described a provider that did nothing at all as
        having behaved carefully.
        """
        result = report(no_candidates, always_abstains())

        assert result.fully_abstained_askable_line_rate.value is None
        assert result.fully_abstained_askable_line_rate.denominator == 0
        assert result.unaskable_line_count == 2
        assert result.page_count == 0

    def test_the_denominator_is_askable_lines_only(self) -> None:
        """One declined line beside one nobody could be asked about.

        Built from line outcomes directly, because the candidate universe is
        every event and payout in the snapshot: either every line has candidates
        or none does, so no snapshot produces this mixture. The arithmetic is
        still worth pinning, since it is what stops an unaskable line diluting
        the rate.
        """
        declined = LineOutcome(
            subject_settlement_line_id="line-1",
            truth=("rec-1",),
            selected=(),
            page_count=1,
            answered_page_count=1,
            abstained_page_count=1,
            unusable_page_count=0,
            expected_action=ExpectedProviderAction.SELECT_EXACTLY,
        )
        unasked = LineOutcome(
            subject_settlement_line_id="line-2",
            truth=(),
            selected=(),
            page_count=0,
            answered_page_count=0,
            abstained_page_count=0,
            unusable_page_count=0,
            expected_action=ExpectedProviderAction.SELECT_EXACTLY,
        )
        snapshot = FactSnapshot.from_index(index_of(settlement_line("sl-1")))

        result = _report(
            snapshot, ProviderIdentity(**FIXTURE.model_dump()), (declined, unasked), (), ()
        )

        assert result.fully_abstained_askable_line_rate.value == 1.0
        assert result.fully_abstained_askable_line_rate.denominator == 1
        assert result.unaskable_line_count == 1
        assert result.line_count == 2

    def test_an_unaskable_line_is_not_fully_abstained(self) -> None:
        """Stated on the outcome itself, not only in the rate."""
        unasked = LineOutcome(
            subject_settlement_line_id="line-2",
            truth=(),
            selected=(),
            page_count=0,
            answered_page_count=0,
            abstained_page_count=0,
            unusable_page_count=0,
            expected_action=ExpectedProviderAction.SELECT_EXACTLY,
        )

        assert not unasked.fully_abstained
        assert not unasked.askable


class TestNoSelectionIsNamedForWhatItCounts:
    """The measure that does count failing, kept and not called an abstention."""

    def test_it_agrees_with_abstention_when_the_provider_declined(
        self, one_page: FactSnapshot
    ) -> None:
        """Declining produces no selection, so both are 1.000."""
        result = report(one_page, always_abstains())

        assert result.no_selection_line_rate.value == 1.0
        assert result.fully_abstained_askable_line_rate.value == 1.0

    def test_it_differs_when_the_provider_failed(self, one_page: FactSnapshot) -> None:
        """The case that shows they are two measures.

        No records were produced and nothing was declined. A single number would
        have to pick one of those to be wrong about.
        """
        result = report(one_page, returns("not a proposal"))

        assert result.no_selection_line_rate.value == 1.0
        assert result.fully_abstained_askable_line_rate.value == 0.0

    def test_it_is_zero_when_records_were_selected(self, one_page: FactSnapshot) -> None:
        """It counts lines that produced nothing, and this one produced links."""
        result = report(
            one_page, selecting(lambda request: tuple(sorted(truth_for(request, one_page))))
        )

        assert result.no_selection_line_rate.value == 0.0

    def test_it_also_excludes_unaskable_lines(self, no_candidates: FactSnapshot) -> None:
        """Same denominator, so neither can be inflated the same way."""
        result = report(no_candidates, always_abstains())

        assert result.no_selection_line_rate.value is None
        assert result.no_selection_line_rate.denominator == 0

    def test_no_field_is_called_an_abstained_line_rate(self) -> None:
        """The name that was wrong is gone, not renamed onto the same meaning."""
        names = set(ShadowReport.model_fields)

        assert "abstained_line_rate" not in names
        assert "fully_abstained_askable_line_rate" in names
        assert "no_selection_line_rate" in names


class TestItAgreesWithTheSafeAbstentionOutcome:
    """The two abstention measures are computed from one property."""

    @pytest.mark.parametrize(
        "behaviour_name", ["abstains", "malformed", "failed", "selects", "partial"]
    )
    def test_a_line_is_safely_abstained_exactly_when_it_is_fully_abstained(
        self, two_pages: FactSnapshot, behaviour_name: str
    ) -> None:
        """So the line rate and the expected-abstention outcome cannot disagree.

        Both read `fully_abstained`. Two independent definitions would drift,
        and a report could then say a line abstained safely and was not fully
        abstained.
        """
        from app.ai.evaluation import LineAbstention

        behaviours: dict[str, Behaviour] = {
            "abstains": always_abstains(),
            "malformed": returns("not a proposal"),
            "failed": fails_with(FailureKind.RAISED),
            "selects": selecting(lambda request: tuple(sorted(truth_for(request, two_pages)))),
            "partial": abstain_then_fail,
        }
        expected = {"line-sl-1": ExpectedProviderAction.ABSTAIN}

        result = evaluate(two_pages, FixtureProvider(behaviours[behaviour_name]), expected)
        outcome = result.line_outcomes[0]

        assert outcome.fully_abstained is (outcome.abstention_outcome is LineAbstention.SAFE)
