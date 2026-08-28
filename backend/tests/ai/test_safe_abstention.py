"""Tests for what counts as safely abstaining, and what does not.

The defect these exist for: safe abstention was credited to any line where the
provider selected nothing, so returning malformed output on every page scored
1.000 safe abstention while also scoring 1.000 invalid pages. A provider that
never abstained was recorded as having abstained safely, and the metric
flattered exactly the providers that deserve it least.

Declining is an answer. Failing is not answering. The three outcomes below keep
those apart and partition the denominator exactly.
"""

import pytest

from app.ai.candidates import LinkProposalRequest, build_pages, truth_for
from app.ai.evaluation import (
    ExpectedProviderAction,
    LineAbstention,
    ShadowReport,
    evaluate,
)
from app.ai.provider import (
    Behaviour,
    FailureKind,
    FixtureProvider,
    ProviderResult,
    always_abstains,
    fails_with,
    returns,
    selecting,
)
from app.reconciliation.snapshot import FactSnapshot
from tests.reconciliation.conftest import index_of, payment_event, payout, settlement_line

ABSTAIN_EXPECTED = {"line-sl-1": ExpectedProviderAction.ABSTAIN}


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


def report(snapshot: FactSnapshot, behaviour: Behaviour) -> ShadowReport:
    """Evaluate one behaviour against a line where abstaining is safe."""
    return evaluate(snapshot, FixtureProvider(behaviour), ABSTAIN_EXPECTED)


class TestOnlyAnActualAbstentionIsSafe:
    """The correction, one behaviour at a time."""

    def test_abstaining_on_every_page_is_safe(self, one_page: FactSnapshot) -> None:
        """The provider declined, which is what safe abstention means."""
        result = report(one_page, always_abstains())

        assert result.safe_abstention_recall.value == 1.0
        assert result.unsafe_selection_rate.value == 0.0
        assert result.unusable_expected_abstention_rate.value == 0.0

    def test_malformed_output_is_not_safe(self, one_page: FactSnapshot) -> None:
        """The reproduction, kept as a test.

        Before this phase this reported 1.000 safe abstention while also
        reporting 1.000 invalid pages, which cannot both be true of one line.
        """
        result = report(one_page, returns("not a proposal"))

        assert result.safe_abstention_recall.value == 0.0
        assert result.unsafe_selection_rate.value == 0.0
        assert result.unusable_expected_abstention_rate.value == 1.0

    @pytest.mark.parametrize("kind", list(FailureKind))
    def test_a_provider_failure_is_not_safe(
        self, one_page: FactSnapshot, kind: FailureKind
    ) -> None:
        """A provider that did not answer did not decline."""
        result = report(one_page, fails_with(kind))

        assert result.safe_abstention_recall.value == 0.0
        assert result.unusable_expected_abstention_rate.value == 1.0

    def test_selecting_anything_is_unsafe(self, one_page: FactSnapshot) -> None:
        """A link asserted from information that does not identify a record."""
        result = report(
            one_page, selecting(lambda request: tuple(sorted(truth_for(request, one_page))))
        )

        assert result.unsafe_selection_rate.value == 1.0
        assert result.safe_abstention_recall.value == 0.0

    def test_one_abstained_page_and_one_malformed_is_not_safe(
        self, two_pages: FactSnapshot
    ) -> None:
        """Partly declining is not declining.

        The line was not answered, and it was not declined either. Crediting it
        would mean a provider could earn safe abstention by failing on every
        page but one.
        """

        def behave(request: LinkProposalRequest) -> ProviderResult:
            if request.page_ordinal == 1:
                return {"outcome": "ABSTAIN", "selected_source_record_ids": []}
            return "not a proposal"

        result = report(two_pages, behave)

        assert result.safe_abstention_recall.value == 0.0
        assert result.unsafe_selection_rate.value == 0.0
        assert result.unusable_expected_abstention_rate.value == 1.0

    def test_abstaining_on_both_pages_is_safe(self, two_pages: FactSnapshot) -> None:
        """So the rule is about every page, not about there being one."""
        result = report(two_pages, always_abstains())

        assert result.safe_abstention_recall.value == 1.0

    def test_selecting_on_one_page_of_two_is_unsafe(self, two_pages: FactSnapshot) -> None:
        """One asserted link is enough to be an unsafe selection."""
        first = build_pages("line-sl-1", two_pages)[0]
        chosen = sorted(first.candidate_ids)[0]

        def behave(request: LinkProposalRequest) -> ProviderResult:
            if request.page_ordinal == 1:
                return {"outcome": "PROPOSE", "selected_source_record_ids": [chosen]}
            return {"outcome": "ABSTAIN", "selected_source_record_ids": []}

        result = report(two_pages, behave)

        assert result.unsafe_selection_rate.value == 1.0
        assert result.safe_abstention_recall.value == 0.0


class TestTheThreeOutcomesPartitionTheDenominator:
    """Every expected-abstention line is counted once, under exactly one name."""

    @pytest.mark.parametrize(
        "behaviour_name",
        ["abstains", "malformed", "failed", "selects", "mixed"],
    )
    def test_the_numerators_sum_to_the_denominator(
        self, two_pages: FactSnapshot, behaviour_name: str
    ) -> None:
        """Whatever the provider did.

        A partition rather than three independent rates: a line that fell into
        none of them, or into two, would make the three numbers unreadable.
        """

        def mixed(request: LinkProposalRequest) -> ProviderResult:
            if request.page_ordinal == 1:
                return {"outcome": "ABSTAIN", "selected_source_record_ids": []}
            return "not a proposal"

        behaviours: dict[str, Behaviour] = {
            "abstains": always_abstains(),
            "malformed": returns("not a proposal"),
            "failed": fails_with(FailureKind.TIMED_OUT),
            "selects": selecting(lambda request: tuple(sorted(truth_for(request, two_pages)))),
            "mixed": mixed,
        }
        result = report(two_pages, behaviours[behaviour_name])

        total = (
            result.safe_abstention_recall.numerator
            + result.unsafe_selection_rate.numerator
            + result.unusable_expected_abstention_rate.numerator
        )
        assert total == result.safe_abstention_recall.denominator

    def test_every_outcome_name_is_reachable(self, two_pages: FactSnapshot) -> None:
        """Three names, three behaviours that produce them."""
        seen = {
            report(two_pages, always_abstains()).line_outcomes[0].abstention_outcome,
            report(two_pages, returns("bad")).line_outcomes[0].abstention_outcome,
            report(
                two_pages,
                selecting(lambda request: tuple(sorted(truth_for(request, two_pages)))),
            )
            .line_outcomes[0]
            .abstention_outcome,
        }

        assert seen == set(LineAbstention)

    def test_the_rates_are_null_when_no_line_expects_an_abstention(
        self, one_page: FactSnapshot
    ) -> None:
        """Not measurable rather than zero, as every rate here behaves."""
        result = evaluate(one_page, FixtureProvider(always_abstains()))

        assert result.safe_abstention_recall.value is None
        assert result.unsafe_selection_rate.value is None
        assert result.unusable_expected_abstention_rate.value is None


class TestStrictRecallIsUnchanged:
    """Abstaining is still a miss, safe or not."""

    def test_a_safe_abstention_still_misses_its_links(self, one_page: FactSnapshot) -> None:
        """The tradeoff this pair of metrics exists to keep visible.

        Declining was the right thing to do and the true links were still not
        returned. Both are reported, and neither is adjusted for the other.
        """
        result = report(one_page, always_abstains())

        assert result.safe_abstention_recall.value == 1.0
        assert result.link_recall.value == 0.0
        assert result.link_recall.denominator > 0

    def test_an_unusable_line_also_misses_its_links(self, one_page: FactSnapshot) -> None:
        """Failing costs the same on recall as declining does."""
        result = report(one_page, returns("not a proposal"))

        assert result.link_recall.value == 0.0
