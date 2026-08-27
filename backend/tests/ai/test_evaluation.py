"""Tests for the shadow evaluator.

The paired controls are the important ones. A metric set that a degenerate
provider can score well on is worse than no metrics, because it produces a
number somebody will quote. So the two degenerate strategies are run and each is
required to look bad on at least one axis.
"""

import pytest

from app.ai.candidates import LinkProposalRequest, truth_for
from app.ai.evaluation import SHADOW_HARNESS_VERSION, evaluate
from app.ai.provider import (
    FailureKind,
    FixtureProvider,
    ProviderResult,
    always_abstains,
    fails_with,
    returns,
    selecting,
    selects_everything,
)
from app.ai.validation import RejectionCode
from app.reconciliation.snapshot import FactSnapshot


def perfect(snapshot: FactSnapshot) -> FixtureProvider:
    """Return a provider that selects exactly what the baseline links."""
    return FixtureProvider(selecting(lambda request: tuple(sorted(truth_for(request, snapshot)))))


class TestAPerfectProvider:
    """The ceiling, so the other results have something to sit under."""

    def test_it_scores_perfectly_on_every_axis(self, snapshot: FactSnapshot) -> None:
        """And this is still a linking score, not a reconciliation one."""
        report = evaluate(snapshot, perfect(snapshot))

        assert report.link_precision.value == 1.0
        assert report.link_recall.value == 1.0
        assert report.answered_link_recall.value == 1.0
        assert report.exact_set_accuracy.value == 1.0
        assert report.false_link_rate.value == 0.0
        assert report.abstention_page_rate.value == 0.0
        assert report.invalid_page_rate.value == 0.0

    def test_it_reports_one_outcome_per_line(self, snapshot: FactSnapshot) -> None:
        """In settlement line order, so a number traces to the lines behind it."""
        report = evaluate(snapshot, perfect(snapshot))

        assert report.line_count == 2
        assert [one.subject_settlement_line_id for one in report.line_outcomes] == [
            "line-sl-1",
            "line-sl-2",
        ]

    def test_it_records_the_snapshot_and_the_provider(self, snapshot: FactSnapshot) -> None:
        """A report is only interpretable against both."""
        report = evaluate(snapshot, perfect(snapshot))

        assert report.snapshot_fingerprint == snapshot.digest
        assert report.provider.name == "fixture"
        assert report.harness_version == SHADOW_HARNESS_VERSION


class TestTheBroadGuessingControl:
    """Selecting everything must not look like success."""

    def test_it_achieves_perfect_recall(self, snapshot: FactSnapshot) -> None:
        """Which is exactly why recall alone would be a useless headline."""
        report = evaluate(snapshot, FixtureProvider(selects_everything()))

        assert report.link_recall.value == 1.0

    def test_and_is_caught_by_precision(self, snapshot: FactSnapshot) -> None:
        """The records it linked that the baseline does not."""
        report = evaluate(snapshot, FixtureProvider(selects_everything()))

        assert report.link_precision.value is not None
        assert report.link_precision.value < 1.0

    def test_and_by_exact_set_accuracy(self, snapshot: FactSnapshot) -> None:
        """Right nowhere, despite missing nothing."""
        report = evaluate(snapshot, FixtureProvider(selects_everything()))

        assert report.exact_set_accuracy.value == 0.0

    def test_and_by_the_false_link_rate(self, snapshot: FactSnapshot) -> None:
        """The specific harm, reported in its own right."""
        report = evaluate(snapshot, FixtureProvider(selects_everything()))

        assert report.false_link_rate.numerator > 0
        assert report.false_link_rate.value is not None
        assert report.false_link_rate.value > 0.0

    def test_it_keeps_perfect_recall_under_the_new_definition_too(
        self, snapshot: FactSnapshot
    ) -> None:
        """Changing the denominator did not change this control.

        Selecting everything returns every true link, so recall is 1.0 whether
        it is measured over the corpus or over the answered lines. It is caught
        by precision and exact-set accuracy, as it was before.
        """
        report = evaluate(snapshot, FixtureProvider(selects_everything()))

        assert report.link_recall.value == 1.0
        assert report.answered_link_recall.value == 1.0
        assert report.link_precision.value is not None
        assert report.link_precision.value < 1.0
        assert report.exact_set_accuracy.value == 0.0

    def test_broadening_cannot_raise_exact_set_accuracy(self, snapshot: FactSnapshot) -> None:
        """The paired comparison, stated as one assertion.

        A provider that adds records to a correct answer keeps its recall and
        loses everything else. That is what makes the metric set unable to
        reward guessing broadly.
        """
        narrow = evaluate(snapshot, perfect(snapshot))
        broad = evaluate(snapshot, FixtureProvider(selects_everything()))

        assert broad.link_recall.value == narrow.link_recall.value
        assert broad.exact_set_accuracy.value is not None
        assert narrow.exact_set_accuracy.value is not None
        assert broad.exact_set_accuracy.value < narrow.exact_set_accuracy.value
        assert broad.false_link_rate.numerator > narrow.false_link_rate.numerator


class TestOneAnsweredLineAndOneNot:
    """The defect this phase exists to fix, as four paired controls.

    A provider that is right about half a corpus and returns nothing usable for
    the other half must not report perfect recall. Before this phase it did:
    recall counted only the lines that produced a selection, so declining to
    answer removed a line from the denominator instead of costing anything.
    """

    @staticmethod
    def _mixed(snapshot: FactSnapshot, second: object) -> FixtureProvider:
        """Return a provider that is perfect on line one and does `second` on line two."""

        def behave(request: LinkProposalRequest) -> ProviderResult:
            if request.subject_settlement_line_id == "line-sl-1":
                return {
                    "outcome": "PROPOSE",
                    "selected_source_record_ids": sorted(truth_for(request, snapshot)),
                }
            return second

        return FixtureProvider(behave)

    @pytest.mark.parametrize(
        ("label", "second"),
        [
            ("abstains", {"outcome": "ABSTAIN", "selected_source_record_ids": []}),
            ("returns malformed output", "not a proposal"),
            (
                "selects out of set",
                {"outcome": "PROPOSE", "selected_source_record_ids": ["PAYMENT_EVENT:never"]},
            ),
        ],
    )
    def test_overall_recall_falls_below_one(
        self, snapshot: FactSnapshot, label: str, second: object
    ) -> None:
        """Half the corpus's true links were never returned, so recall says so."""
        report = self._mixed(snapshot, second)
        result = evaluate(snapshot, report)

        assert result.link_recall.value is not None
        assert result.link_recall.value < 1.0
        assert result.link_recall.value == 0.5

    @pytest.mark.parametrize(
        ("label", "second"),
        [
            ("abstains", {"outcome": "ABSTAIN", "selected_source_record_ids": []}),
            ("returns malformed output", "not a proposal"),
        ],
    )
    def test_the_conditional_measure_may_still_be_one(
        self, snapshot: FactSnapshot, label: str, second: object
    ) -> None:
        """Because it answers a different question, and answers it correctly.

        The provider was perfect on the line it answered. That is worth
        reporting, under a name that says which lines it covers.
        """
        result = evaluate(snapshot, self._mixed(snapshot, second))

        assert result.answered_link_recall.value == 1.0

    def test_the_two_measures_are_reported_side_by_side(self, snapshot: FactSnapshot) -> None:
        """So neither can be read as the other."""
        result = evaluate(
            snapshot,
            self._mixed(snapshot, {"outcome": "ABSTAIN", "selected_source_record_ids": []}),
        )

        assert result.link_recall.value == 0.5
        assert result.answered_link_recall.value == 1.0
        assert result.link_recall.denominator > result.answered_link_recall.denominator

    def test_abstaining_on_a_line_cannot_raise_the_score(self, snapshot: FactSnapshot) -> None:
        """The paired comparison stated directly.

        A provider that answers both lines correctly and one that answers one
        and declines the other are compared. Declining must not score higher on
        any metric.
        """
        both = evaluate(
            snapshot,
            FixtureProvider(selecting(lambda request: tuple(sorted(truth_for(request, snapshot))))),
        )
        half = evaluate(
            snapshot,
            self._mixed(snapshot, {"outcome": "ABSTAIN", "selected_source_record_ids": []}),
        )

        assert half.link_recall.value is not None
        assert both.link_recall.value is not None
        assert half.link_recall.value < both.link_recall.value
        assert half.exact_set_accuracy.value is not None
        assert both.exact_set_accuracy.value is not None
        assert half.exact_set_accuracy.value < both.exact_set_accuracy.value


class TestTheAbstainingControl:
    """Answering nothing must not look like answering well."""

    def test_precision_is_unmeasurable_rather_than_perfect(self, snapshot: FactSnapshot) -> None:
        """Nothing selected means no precision to compute.

        Reported as null, never as 1.0. A provider that answered nothing has not
        earned a perfect precision by having made no mistake.
        """
        report = evaluate(snapshot, FixtureProvider(always_abstains()))

        assert report.link_precision.value is None
        assert report.false_link_rate.value is None
        assert report.answered_link_recall.value is None

    def test_recall_is_zero_because_every_true_link_was_missed(
        self, snapshot: FactSnapshot
    ) -> None:
        """Not null, and certainly not one.

        There are true links in this corpus and the provider returned none of
        them. Reporting that as unmeasurable would let abstaining on everything
        avoid being scored at all.
        """
        report = evaluate(snapshot, FixtureProvider(always_abstains()))

        assert report.link_recall.value == 0.0
        assert report.link_recall.denominator == 4

    def test_the_abstention_rate_says_what_happened(self, snapshot: FactSnapshot) -> None:
        """Which is the metric that stops silence looking like success."""
        report = evaluate(snapshot, FixtureProvider(always_abstains()))

        assert report.abstention_page_rate.value == 1.0
        assert report.exact_set_accuracy.value == 0.0

    def test_an_abstention_is_not_an_invalid_output(self, snapshot: FactSnapshot) -> None:
        """Declining is an answer. The two are counted separately."""
        report = evaluate(snapshot, FixtureProvider(always_abstains()))

        assert report.invalid_page_rate.value == 0.0

    def test_the_conditional_measure_is_null_rather_than_zero(self, snapshot: FactSnapshot) -> None:
        """No line answered, so there is no answered-line ratio to report."""
        report = evaluate(snapshot, FixtureProvider(always_abstains()))

        assert report.answered_link_recall.denominator == 0
        assert report.answered_link_recall.value is None


class TestInvalidOutput:
    """Refusals are counted and never scored as reconciliation outcomes."""

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            ("not a proposal", RejectionCode.MALFORMED),
            (
                {"outcome": "PROPOSE", "selected_source_record_ids": ["nope"]},
                RejectionCode.OUT_OF_CANDIDATE_SET,
            ),
            (
                {"outcome": "PROPOSE", "selected_source_record_ids": ["a"], "provider": {}},
                RejectionCode.MALFORMED,
            ),
        ],
    )
    def test_a_refused_output_is_recorded_as_invalid(
        self, snapshot: FactSnapshot, payload: object, expected: RejectionCode
    ) -> None:
        """With the code that says which rule it broke."""
        report = evaluate(snapshot, FixtureProvider(returns(payload)))

        assert report.invalid_page_rate.numerator >= 1
        assert any(one.rejection is expected for one in report.page_outcomes)

    def test_an_invalid_output_selects_nothing(self, snapshot: FactSnapshot) -> None:
        """A refused proposal contributes no links, right or wrong."""
        report = evaluate(snapshot, FixtureProvider(returns("not a proposal")))

        assert all(one.selected == () for one in report.line_outcomes)
        assert report.link_precision.value is None
        assert report.false_link_rate.value is None

    def test_an_invalid_output_still_misses_its_true_links(self, snapshot: FactSnapshot) -> None:
        """Recall is over the corpus, so failing does not hide the miss.

        Before this phase, recall skipped the lines that produced no selection,
        so a provider could raise its score by returning nothing usable.
        """
        report = evaluate(snapshot, FixtureProvider(returns("not a proposal")))

        assert report.link_recall.value == 0.0
        assert report.link_recall.denominator > 0

    def test_an_invalid_output_is_not_an_abstention(self, snapshot: FactSnapshot) -> None:
        """A provider that failed did not decline; it did not answer."""
        report = evaluate(snapshot, FixtureProvider(returns("not a proposal")))

        assert report.invalid_page_rate.value == 1.0
        assert report.abstention_page_rate.value == 0.0

    def test_the_rate_is_over_every_line_asked(self, snapshot: FactSnapshot) -> None:
        """So it cannot be diluted by counting only the lines that answered."""
        report = evaluate(snapshot, FixtureProvider(returns("not a proposal")))

        assert report.invalid_page_rate.denominator == report.line_count


class TestProviderFailure:
    """A provider that did not answer is explicit, never repaired."""

    @pytest.mark.parametrize("kind", list(FailureKind))
    def test_a_failure_is_recorded_not_retried(
        self, snapshot: FactSnapshot, kind: FailureKind
    ) -> None:
        """Every way of failing, each as a typed outcome."""
        report = evaluate(snapshot, FixtureProvider(fails_with(kind)))

        assert report.invalid_page_rate.value == 1.0
        assert all(one.rejection is RejectionCode.PROVIDER_FAILED for one in report.page_outcomes)

    def test_a_failure_produces_no_selection(self, snapshot: FactSnapshot) -> None:
        """Nothing is invented to stand in for the answer that did not arrive."""
        report = evaluate(snapshot, FixtureProvider(fails_with(FailureKind.TIMED_OUT)))

        assert all(one.selected == () for one in report.page_outcomes)
        assert all(not one.answered for one in report.page_outcomes)


class TestTheReportIsReproducible:
    """Same snapshot, same provider, byte-identical report."""

    def test_two_evaluations_produce_identical_json(self, snapshot: FactSnapshot) -> None:
        """Which is what lets one be compared against another at all."""
        first = evaluate(snapshot, perfect(snapshot)).model_dump_json()
        second = evaluate(snapshot, perfect(snapshot)).model_dump_json()

        assert first == second

    def test_the_report_carries_no_wall_clock_value(self, snapshot: FactSnapshot) -> None:
        """A timestamp would make every report differ for no reason."""
        rendered = evaluate(snapshot, perfect(snapshot)).model_dump_json()

        assert "created_at" not in rendered
        assert "timestamp" not in rendered

    def test_a_different_snapshot_produces_a_different_fingerprint(
        self, snapshot: FactSnapshot
    ) -> None:
        """So two reports cannot be compared as though they saw the same facts."""
        from tests.reconciliation.conftest import index_of, payment_event, payout, settlement_line

        other = FactSnapshot.from_index(
            index_of(payment_event("pe-9"), settlement_line("sl-9"), payout("po-9"))
        )

        assert (
            evaluate(snapshot, perfect(snapshot)).snapshot_fingerprint
            != evaluate(other, perfect(other)).snapshot_fingerprint
        )


class TestTheReportNamesNothingItIsNot:
    """The metrics measure linking. They are not a reconciliation result."""

    def test_no_field_is_called_accuracy_without_saying_of_what(
        self, snapshot: FactSnapshot
    ) -> None:
        """`exact_set_accuracy` says which set. Nothing says accuracy alone."""
        from app.ai.evaluation import ShadowReport

        names = set(ShadowReport.model_fields)

        assert "accuracy" not in names
        assert "reconciliation_accuracy" not in names
        assert "exact_set_accuracy" in names

    def test_the_report_carries_no_status_or_exception_counts(self, snapshot: FactSnapshot) -> None:
        """Those belong to a run. A shadow report is not one."""
        rendered = evaluate(snapshot, perfect(snapshot)).model_dump_json()

        for word in ("RESOLVED", "EXCEPTION", "INSUFFICIENT_EVIDENCE", "status_counts"):
            assert word not in rendered

    def test_pass_at_k_is_not_reported(self, snapshot: FactSnapshot) -> None:
        """One deterministic call per line, so pass@k would be pass@1 repeated."""
        from app.ai.evaluation import ShadowReport

        assert not any("pass" in name for name in ShadowReport.model_fields)
