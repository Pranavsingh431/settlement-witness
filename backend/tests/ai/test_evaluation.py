"""Tests for the shadow evaluator.

The paired controls are the important ones. A metric set that a degenerate
provider can score well on is worse than no metrics, because it produces a
number somebody will quote. So the two degenerate strategies are run and each is
required to look bad on at least one axis.
"""

import pytest

from app.ai.candidates import build_request, truth_for
from app.ai.evaluation import SHADOW_HARNESS_VERSION, evaluate
from app.ai.provider import (
    FailureKind,
    FixtureProvider,
    always_abstains,
    fails_with,
    returns,
    selecting,
    selects_everything,
)
from app.ai.validation import RejectionCode
from app.reconciliation.snapshot import FactSnapshot
from tests.ai.conftest import payload_for


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
        assert report.exact_set_accuracy.value == 1.0
        assert report.false_link_rate.value == 0.0
        assert report.abstention_rate.value == 0.0
        assert report.invalid_output_rate.value == 0.0

    def test_it_reports_one_outcome_per_line(self, snapshot: FactSnapshot) -> None:
        """In settlement line order, so a number traces to the lines behind it."""
        report = evaluate(snapshot, perfect(snapshot))

        assert report.line_count == 2
        assert [one.subject_settlement_line_id for one in report.outcomes] == [
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


class TestTheAbstainingControl:
    """Answering nothing must not look like answering well."""

    def test_precision_and_recall_are_unmeasurable_rather_than_perfect(
        self, snapshot: FactSnapshot
    ) -> None:
        """Nothing selected means no rate to compute.

        Reported as null, never as 1.0. A provider that answered nothing has not
        earned a perfect precision by having made no mistake.
        """
        report = evaluate(snapshot, FixtureProvider(always_abstains()))

        assert report.link_precision.value is None
        assert report.link_recall.value is None
        assert report.false_link_rate.value is None

    def test_the_abstention_rate_says_what_happened(self, snapshot: FactSnapshot) -> None:
        """Which is the metric that stops silence looking like success."""
        report = evaluate(snapshot, FixtureProvider(always_abstains()))

        assert report.abstention_rate.value == 1.0
        assert report.exact_set_accuracy.value == 0.0

    def test_an_abstention_is_not_an_invalid_output(self, snapshot: FactSnapshot) -> None:
        """Declining is an answer. The two are counted separately."""
        report = evaluate(snapshot, FixtureProvider(always_abstains()))

        assert report.invalid_output_rate.value == 0.0

    def test_abstaining_does_not_depress_recall(self, snapshot: FactSnapshot) -> None:
        """Recall is over the lines that produced a selection.

        A line nobody answered has no selection to have missed a record from,
        and counting it would make abstaining indistinguishable from guessing
        wrongly. The abstention rate is where declining shows up.
        """
        report = evaluate(snapshot, FixtureProvider(always_abstains()))

        assert report.link_recall.denominator == 0


class TestInvalidOutput:
    """Refusals are counted and never scored as reconciliation outcomes."""

    @pytest.mark.parametrize(
        ("payload_name", "expected"),
        [
            ("malformed", RejectionCode.MALFORMED),
            ("out_of_set", RejectionCode.OUT_OF_CANDIDATE_SET),
            ("stale", RejectionCode.WRONG_SNAPSHOT),
        ],
    )
    def test_a_refused_output_is_recorded_as_invalid(
        self, snapshot: FactSnapshot, payload_name: str, expected: RejectionCode
    ) -> None:
        """With the code that says which rule it broke."""
        request = build_request("line-sl-1", snapshot)
        payloads = {
            "malformed": "not a proposal",
            "out_of_set": payload_for(request, ("PAYMENT_EVENT:never-offered",)),
            "stale": payload_for(request, ("PAYMENT_EVENT:pe-1",), snapshot_fingerprint="f" * 64),
        }

        report = evaluate(snapshot, FixtureProvider(returns(payloads[payload_name])))

        assert report.invalid_output_rate.numerator >= 1
        assert any(one.rejection is expected for one in report.outcomes)

    def test_an_invalid_output_selects_nothing(self, snapshot: FactSnapshot) -> None:
        """A refused proposal contributes no links, right or wrong."""
        report = evaluate(snapshot, FixtureProvider(returns("not a proposal")))

        assert all(one.selected == () for one in report.outcomes)
        assert report.link_precision.value is None
        assert report.false_link_rate.value is None

    def test_an_invalid_output_is_not_an_abstention(self, snapshot: FactSnapshot) -> None:
        """A provider that failed did not decline; it did not answer."""
        report = evaluate(snapshot, FixtureProvider(returns("not a proposal")))

        assert report.invalid_output_rate.value == 1.0
        assert report.abstention_rate.value == 0.0

    def test_the_rate_is_over_every_line_asked(self, snapshot: FactSnapshot) -> None:
        """So it cannot be diluted by counting only the lines that answered."""
        report = evaluate(snapshot, FixtureProvider(returns("not a proposal")))

        assert report.invalid_output_rate.denominator == report.line_count


class TestProviderFailure:
    """A provider that did not answer is explicit, never repaired."""

    @pytest.mark.parametrize("kind", list(FailureKind))
    def test_a_failure_is_recorded_not_retried(
        self, snapshot: FactSnapshot, kind: FailureKind
    ) -> None:
        """Every way of failing, each as a typed outcome."""
        report = evaluate(snapshot, FixtureProvider(fails_with(kind)))

        assert report.invalid_output_rate.value == 1.0
        assert all(one.rejection is RejectionCode.PROVIDER_FAILED for one in report.outcomes)

    def test_a_failure_produces_no_selection(self, snapshot: FactSnapshot) -> None:
        """Nothing is invented to stand in for the answer that did not arrive."""
        report = evaluate(snapshot, FixtureProvider(fails_with(FailureKind.TIMED_OUT)))

        assert all(one.selected == () for one in report.outcomes)
        assert all(not one.answered for one in report.outcomes)


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
