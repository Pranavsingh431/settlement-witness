"""Tests for the evaluator harness.

Two things matter most here. The oracle has to be independent, or the evaluation
only confirms that the baseline agrees with itself. And every rate has to say
what it means when there was nothing to measure, or an empty run can be read as
a good one.
"""

import json

import pytest

from app.benchmark.evaluator import (
    HARNESS_VERSION,
    EvaluationReport,
    grade,
    render_report,
    run_corpus,
)
from app.benchmark.generator import (
    GENERATOR_VERSION,
    CorpusConfig,
    GeneratedCorpus,
    generate,
)
from app.benchmark.manifest import CorpusManifest
from app.benchmark.metrics import Rate
from app.benchmark.specs import TemplateId
from app.domain.codes import ExceptionCode
from app.domain.decisions import DecisionStatus, ReconciliationDecision
from app.domain.version import DOMAIN_SCHEMA_VERSION
from app.ingestion.schemas import PARSER_VERSION
from app.reconciliation.batch import BASELINE_VERSION
from tests.benchmark.conftest import small_config


class TestRunningTheHarness:
    """The corpus goes through the real ingestion and reconciliation paths."""

    def test_every_document_is_accepted(self, small_corpus: GeneratedCorpus) -> None:
        """A corpus the strict parser refuses cannot be evaluated at all."""
        report = run_corpus(small_corpus)

        assert all(outcome.endswith("=ACCEPTED") for outcome in report.import_outcomes)

    def test_one_decision_per_settlement_line(self, small_corpus: GeneratedCorpus) -> None:
        """Every scenario is graded, none skipped."""
        report = run_corpus(small_corpus)

        assert report.decision_count == report.scenario_count

    def test_the_baseline_matches_the_oracle_on_this_corpus(
        self, small_corpus: GeneratedCorpus
    ) -> None:
        """Every template, reasoned from the contract, agrees with the baseline.

        This is a regression guard, not a claim of general performance. The
        corpus covers exactly the shapes the baseline was built for.
        """
        report = run_corpus(small_corpus)

        assert report.pass_at_1.value == 1.0
        assert report.failures == ()

    def test_no_false_resolutions(self, small_corpus: GeneratedCorpus) -> None:
        """The measure that matters most: nothing resolved that should not have."""
        report = run_corpus(small_corpus)

        assert report.false_resolution_rate.value == 0.0

    def test_the_report_records_every_version(self, small_corpus: GeneratedCorpus) -> None:
        """A number without the rules that produced it is an anecdote."""
        report = run_corpus(small_corpus)

        assert report.harness_version == HARNESS_VERSION
        assert report.generator_version == GENERATOR_VERSION
        assert report.baseline_version == BASELINE_VERSION
        assert report.parser_version == PARSER_VERSION
        assert report.domain_schema_version == DOMAIN_SCHEMA_VERSION

    def test_the_report_records_the_seed(self, small_corpus: GeneratedCorpus) -> None:
        """So the corpus behind a report can be regenerated."""
        report = run_corpus(small_corpus)

        assert report.seed == small_config().seed

    def test_the_report_declares_it_is_synthetic(self, small_corpus: GeneratedCorpus) -> None:
        """No number here is a statement about anyone's real records."""
        assert run_corpus(small_corpus).is_synthetic

    def test_each_run_uses_a_fresh_database(self) -> None:
        """Two runs of the same corpus give the same answer.

        If state leaked between runs, the second would see duplicate facts and
        the import would be a no-op rather than an acceptance.
        """
        corpus = generate(small_config())

        first = run_corpus(corpus)
        second = run_corpus(corpus)

        assert first.import_outcomes == second.import_outcomes
        assert render_report(first) == render_report(second)


class TestReportDeterminism:
    """The same corpus renders the same bytes."""

    def test_two_runs_render_identically(self, small_corpus: GeneratedCorpus) -> None:
        """The property a byte comparison in CI depends on."""
        assert render_report(run_corpus(small_corpus)) == render_report(run_corpus(small_corpus))

    def test_the_rendered_json_has_sorted_keys(self, small_corpus: GeneratedCorpus) -> None:
        """A diff should show a changed value, never a moved key."""
        parsed = json.loads(render_report(run_corpus(small_corpus)))

        assert list(parsed) == sorted(parsed)

    def test_template_breakdown_is_ordered(self, small_corpus: GeneratedCorpus) -> None:
        """Alphabetical by template, so two reports line up."""
        report = run_corpus(small_corpus)

        names = [entry.template.value for entry in report.template_breakdown]
        assert names == sorted(names)

    def test_every_template_appears_in_the_breakdown(self, small_corpus: GeneratedCorpus) -> None:
        """Coverage is visible in the report, not just in the corpus."""
        report = run_corpus(small_corpus)

        assert {entry.template for entry in report.template_breakdown} == set(TemplateId)


class TestTheOracleIsIndependent:
    """The grading has to depend on what the baseline actually produced.

    If the expected outcomes were derived by running the baseline, an evaluation
    would confirm only that the baseline agrees with itself, and any regression
    would move the expectation along with the behaviour. These tests hand the
    grader decisions the baseline never produced and require the score to fall.
    """

    @staticmethod
    def _decisions(corpus: GeneratedCorpus) -> tuple[ReconciliationDecision, ...]:
        """Return the real decisions for a corpus."""
        from app.benchmark.evaluator import EVALUATION_CLOCK, RECORD_TYPE_BY_FILE
        from app.benchmark.generator import SOURCE_SYSTEM
        from app.ingestion.service import ImportService
        from app.reconciliation.batch import reconcile
        from app.storage.database import (
            create_database_engine,
            create_schema,
            session_factory,
            session_scope,
        )
        from app.storage.repository import SourceFactRepository

        engine = create_database_engine("sqlite+pysqlite:///:memory:")
        create_schema(engine)
        with session_scope(engine) as session:
            service = ImportService(session, now=EVALUATION_CLOCK)
            for entry in corpus.manifest.documents:
                service.import_document(
                    corpus.documents[entry.file_name].encode("utf-8"),
                    source_system=SOURCE_SYSTEM,
                    record_type=RECORD_TYPE_BY_FILE[entry.file_name],
                    document_name=entry.file_name,
                )
        with session_factory(engine)() as session:
            decisions = reconcile(SourceFactRepository(session).fact_index()).decisions
        engine.dispose()
        return decisions

    def test_a_perfect_run_scores_one(self, small_corpus: GeneratedCorpus) -> None:
        """The baseline against the unmodified oracle, for comparison."""
        decisions = self._decisions(small_corpus)
        report = grade(small_corpus.manifest, decisions, [])

        assert report.pass_at_1.value == 1.0

    def test_dropping_a_decision_fails_the_evaluation(self, small_corpus: GeneratedCorpus) -> None:
        """A scenario with no decision is a failure, never a skip.

        Silently ignoring an ungraded scenario would flatter every future run.
        """
        decisions = self._decisions(small_corpus)
        report = grade(small_corpus.manifest, decisions[1:], [])

        assert report.pass_at_1.value != 1.0
        assert len(report.failures) == 1
        assert report.failures[0].actual_status is None

    def test_replacing_every_decision_with_resolved_fails(
        self, small_corpus: GeneratedCorpus
    ) -> None:
        """A system that resolves everything must score badly, not well.

        This is the replacement test: swap the baseline for one that always says
        RESOLVED. If the oracle were derived from the baseline, this would still
        pass.
        """
        decisions = self._decisions(small_corpus)
        forged = tuple(
            decision.model_construct(
                **{**decision.__dict__, "status": DecisionStatus.RESOLVED, "exception_codes": ()}
            )
            for decision in decisions
        )
        report = grade(small_corpus.manifest, forged, [])

        assert report.pass_at_1.value != 1.0
        assert report.false_resolution_rate.value == 1.0
        assert report.decision_accuracy.value is not None
        assert report.decision_accuracy.value < 1.0

    def test_perturbing_one_status_is_caught(self, small_corpus: GeneratedCorpus) -> None:
        """One wrong answer among many still fails the run."""
        decisions = list(self._decisions(small_corpus))
        target = next(d for d in decisions if d.status is DecisionStatus.RESOLVED)
        index = decisions.index(target)
        decisions[index] = target.model_construct(
            **{**target.__dict__, "status": DecisionStatus.PENDING}
        )

        report = grade(small_corpus.manifest, tuple(decisions), [])

        assert len(report.failures) == 1
        assert report.failures[0].actual_status is DecisionStatus.PENDING
        assert not report.failures[0].status_correct

    def test_perturbing_one_exception_code_is_caught(self, small_corpus: GeneratedCorpus) -> None:
        """The code set is compared exactly, not by overlap."""
        decisions = list(self._decisions(small_corpus))
        target = next(d for d in decisions if d.exception_codes)
        index = decisions.index(target)
        decisions[index] = target.model_construct(
            **{
                **target.__dict__,
                "exception_codes": (*target.exception_codes, ExceptionCode.MALFORMED_RECORD),
            }
        )

        report = grade(small_corpus.manifest, tuple(decisions), [])

        assert len(report.failures) == 1
        assert not report.failures[0].exception_codes_correct

    def test_perturbing_the_evidence_is_caught(self, small_corpus: GeneratedCorpus) -> None:
        """A right answer citing the wrong records is not a right answer."""
        decisions = list(self._decisions(small_corpus))
        target = decisions[0]
        decisions[0] = target.model_construct(
            **{**target.__dict__, "evidence": target.evidence[:1]}
        )

        report = grade(small_corpus.manifest, tuple(decisions), [])

        assert len(report.failures) == 1
        assert not report.failures[0].evidence_correct
        assert report.evidence_completeness.value != 1.0

    def test_the_failure_records_expected_against_actual(
        self, small_corpus: GeneratedCorpus
    ) -> None:
        """A failure a person cannot diagnose is barely better than a silent one."""
        decisions = list(self._decisions(small_corpus))
        target = next(d for d in decisions if d.status is DecisionStatus.RESOLVED)
        decisions[decisions.index(target)] = target.model_construct(
            **{**target.__dict__, "status": DecisionStatus.EXCEPTION}
        )

        failure = grade(small_corpus.manifest, tuple(decisions), []).failures[0]

        assert failure.expected_status is DecisionStatus.RESOLVED
        assert failure.actual_status is DecisionStatus.EXCEPTION
        assert failure.expected_evidence_record_ids
        assert failure.actual_evidence_record_ids


class TestPairedControlBreakdown:
    """A pair is judged together."""

    def test_a_clean_run_has_every_pair_correct(self, small_corpus: GeneratedCorpus) -> None:
        """Nine anomalies, nine controls, all matched."""
        report = run_corpus(small_corpus)
        pairs = report.paired_control_breakdown

        assert pairs.pair_count == 9
        assert pairs.both_correct == 9
        assert pairs.control_failed == 0
        assert pairs.anomaly_failed == 0
        assert pairs.unpaired_anomalies == 0

    def test_a_broken_control_is_attributed_to_the_control(
        self, small_corpus: GeneratedCorpus
    ) -> None:
        """Knowing which half of a pair failed is the point of pairing."""
        from tests.benchmark.test_evaluator import TestTheOracleIsIndependent as Helper

        decisions = list(Helper._decisions(small_corpus))
        manifest: CorpusManifest = small_corpus.manifest
        control_line = next(
            entry.subject_settlement_line_id
            for entry in manifest.scenarios
            if entry.template is TemplateId.RESOLVED_DIRECT
        )
        target = next(d for d in decisions if d.subject_settlement_line_id == control_line)
        decisions[decisions.index(target)] = target.model_construct(
            **{**target.__dict__, "status": DecisionStatus.EXCEPTION}
        )

        pairs = grade(manifest, tuple(decisions), []).paired_control_breakdown

        assert pairs.control_failed == 1
        assert pairs.both_correct == 8


class TestZeroDenominators:
    """A rate over no cases is absent, not zero and not one."""

    def test_a_rate_with_no_cases_is_none(self) -> None:
        """Printing 0.0 would let an empty run look like a perfect failure."""
        rate = Rate.of(0, 0)

        assert rate.value is None
        assert not rate.is_measurable
        assert rate.denominator == 0

    def test_a_rate_with_cases_is_a_fraction(self) -> None:
        """The ordinary path."""
        rate = Rate.of(3, 4)

        assert rate.value == 0.75
        assert rate.is_measurable

    def test_a_rate_keeps_its_counts(self) -> None:
        """A rate without its denominator cannot be checked or combined."""
        rate = Rate.of(3, 4)

        assert (rate.numerator, rate.denominator) == (3, 4)

    def test_a_zero_numerator_is_still_measurable(self) -> None:
        """None of four is a real measurement. None of none is not."""
        rate = Rate.of(0, 4)

        assert rate.value == 0.0
        assert rate.is_measurable

    def test_an_all_control_corpus_has_no_measurable_recall(self) -> None:
        """With no anomalies there is nothing for exception recall to measure.

        Reporting 1.0 would say the system caught every anomaly, when it was
        never shown one.
        """
        config = CorpusConfig(
            corpus_name="controls-only", seed=11, controls_per_anomaly=1, extra_controls=2
        )
        corpus = generate(config)
        controls_only = corpus.manifest.model_copy(
            update={
                "scenarios": tuple(
                    entry
                    for entry in corpus.manifest.scenarios
                    if entry.template is TemplateId.RESOLVED_DIRECT
                )
            }
        )
        decisions = TestTheOracleIsIndependent._decisions(corpus)

        report = grade(controls_only, decisions, [])

        assert report.exception_recall.value is None
        assert report.exact_exception_set_accuracy.value is None
        assert report.false_resolution_rate.value is None
        assert report.decision_accuracy.value == 1.0

    def test_an_empty_manifest_measures_nothing(self) -> None:
        """Every rate absent, rather than a run that looks perfect."""
        empty = CorpusManifest(
            generator_version=GENERATOR_VERSION,
            domain_schema_version=DOMAIN_SCHEMA_VERSION,
            parser_version=PARSER_VERSION,
            seed=0,
            corpus_name="empty",
            scenario_count=0,
            documents=(),
            scenarios=(),
        )

        report = grade(empty, (), [])

        assert report.pass_at_1.value is None
        assert report.decision_accuracy.value is None
        assert report.evidence_completeness.value is None
        assert report.template_breakdown == ()

    def test_the_rendered_report_shows_null_not_zero(self) -> None:
        """What a reader of the JSON actually sees."""
        empty = CorpusManifest(
            generator_version=GENERATOR_VERSION,
            domain_schema_version=DOMAIN_SCHEMA_VERSION,
            parser_version=PARSER_VERSION,
            seed=0,
            corpus_name="empty",
            scenario_count=0,
            documents=(),
            scenarios=(),
        )
        parsed = json.loads(render_report(grade(empty, (), [])))

        assert parsed["pass_at_1"]["value"] is None
        assert parsed["pass_at_1"]["denominator"] == 0


class TestNoPassAtK:
    """The baseline is deterministic and runs once."""

    def test_the_report_has_no_pass_at_k_field(self, small_corpus: GeneratedCorpus) -> None:
        """Reporting one would imply a sampling budget that does not exist."""
        fields = set(EvaluationReport.model_fields)

        assert "pass_at_1" in fields
        assert not any(name.startswith("pass_at_k") for name in fields)

    def test_a_corpus_is_run_once(self, small_corpus: GeneratedCorpus) -> None:
        """Two runs give the same answer, so retries could add nothing."""
        assert render_report(run_corpus(small_corpus)) == render_report(run_corpus(small_corpus))


class TestRefusedCorpus:
    """A corpus the parser will not accept cannot be evaluated."""

    def test_a_corpus_with_a_broken_document_is_refused(self) -> None:
        """Reporting a score over the part that loaded would be worse than stopping."""
        corpus = generate(small_config())
        broken = corpus.model_copy(
            update={
                "documents": {
                    **corpus.documents,
                    "payouts.csv": "provider_event_id,wrong_header\nx,y\n",
                }
            }
        )

        with pytest.raises(RuntimeError, match="refused at import"):
            run_corpus(broken)


class TestAnUnpairedAnomaly:
    """An anomaly naming a control that is not in the corpus.

    A generated corpus always pairs correctly, so this is unreachable through
    the generator. An externally supplied private manifest may not be, and
    counting it is more useful than crashing on it or ignoring it.
    """

    def test_it_is_counted_rather_than_ignored(self, small_corpus: GeneratedCorpus) -> None:
        """Reported in its own field, so a broken manifest is visible."""
        manifest = small_corpus.manifest
        orphaned = manifest.model_copy(
            update={
                "scenarios": tuple(
                    entry.model_copy(update={"paired_control_id": "SW-99999"})
                    if entry.template is not TemplateId.RESOLVED_DIRECT
                    else entry
                    for entry in manifest.scenarios
                )
            }
        )

        report = grade(orphaned, TestTheOracleIsIndependent._decisions(small_corpus), [])

        assert report.paired_control_breakdown.unpaired_anomalies == 9
        assert report.paired_control_breakdown.both_correct == 0

    def test_the_rest_of_the_report_is_unaffected(self, small_corpus: GeneratedCorpus) -> None:
        """A broken pairing does not change whether a decision was right."""
        manifest = small_corpus.manifest
        orphaned = manifest.model_copy(
            update={
                "scenarios": tuple(
                    entry.model_copy(update={"paired_control_id": "SW-99999"})
                    if entry.template is not TemplateId.RESOLVED_DIRECT
                    else entry
                    for entry in manifest.scenarios
                )
            }
        )

        report = grade(orphaned, TestTheOracleIsIndependent._decisions(small_corpus), [])

        assert report.pass_at_1.value == 1.0


class TestExceptionRecallIsRecall:
    """Recall counts codes found. Exact-set accuracy counts sets matched.

    Until harness 2.0.0 the metric named recall counted an anomaly only when its
    entire code set matched, which is exact-set accuracy wearing a recall label.
    A case expecting two codes where one was raised scored zero, when half the
    findings had in fact been made. The two questions are different and a single
    number hid one of them.
    """

    TWO_CODE_TEMPLATE = TemplateId.OUT_OF_ORDER_RETURN
    """Expects OUT_OF_ORDER_EVENT and PARTIAL_REFUND, so it can be half right."""

    @staticmethod
    def _regrade(
        corpus: GeneratedCorpus, template: TemplateId, codes: tuple[ExceptionCode, ...]
    ) -> EvaluationReport:
        """Replace one scenario's exception codes and grade the run again."""
        decisions = list(TestTheOracleIsIndependent._decisions(corpus))
        line = next(
            entry.subject_settlement_line_id
            for entry in corpus.manifest.scenarios
            if entry.template is template
        )
        target = next(d for d in decisions if d.subject_settlement_line_id == line)
        decisions[decisions.index(target)] = target.model_construct(
            **{**target.__dict__, "exception_codes": codes}
        )
        return grade(corpus.manifest, tuple(decisions), [])

    def test_one_of_two_expected_codes_is_half_recalled(
        self, small_corpus: GeneratedCorpus
    ) -> None:
        """The case the old metric got wrong.

        Expected two codes, raised one. Recall is one half; exact-set accuracy
        is zero, because the set did not match.
        """
        report = self._regrade(
            small_corpus, self.TWO_CODE_TEMPLATE, (ExceptionCode.PARTIAL_REFUND,)
        )
        breakdown = next(
            entry for entry in report.template_breakdown if entry.template is self.TWO_CODE_TEMPLATE
        )

        assert breakdown.exception_recall.value == 0.5
        assert (breakdown.exception_recall.numerator, breakdown.exception_recall.denominator) == (
            1,
            2,
        )
        assert breakdown.exact_exception_set_accuracy.value == 0.0
        assert breakdown.exact_exception_set_accuracy.denominator == 1

    def test_an_extra_unexpected_code_leaves_recall_perfect(
        self, small_corpus: GeneratedCorpus
    ) -> None:
        """Every expected code was found, so recall is one.

        Exact-set accuracy is zero, because an extra code means the set does not
        match. Recall is deliberately blind to over-reporting; that is what the
        second metric is for.
        """
        expected = next(
            entry.expected.exception_codes
            for entry in small_corpus.manifest.scenarios
            if entry.template is self.TWO_CODE_TEMPLATE
        )
        report = self._regrade(
            small_corpus,
            self.TWO_CODE_TEMPLATE,
            (*expected, ExceptionCode.MALFORMED_RECORD),
        )
        breakdown = next(
            entry for entry in report.template_breakdown if entry.template is self.TWO_CODE_TEMPLATE
        )

        assert breakdown.exception_recall.value == 1.0
        assert breakdown.exact_exception_set_accuracy.value == 0.0

    def test_the_two_metrics_differ_on_the_same_run(self, small_corpus: GeneratedCorpus) -> None:
        """Which is the point of reporting both."""
        report = self._regrade(
            small_corpus, self.TWO_CODE_TEMPLATE, (ExceptionCode.PARTIAL_REFUND,)
        )

        assert report.exception_recall.value != report.exact_exception_set_accuracy.value

    def test_recall_is_counted_over_code_occurrences(self, small_corpus: GeneratedCorpus) -> None:
        """Summed across scenarios, not averaged per scenario.

        A case expecting two codes weighs twice as much as one expecting a
        single code. Averaging per scenario would let a system that always finds
        the easy single code look better than one finding most of a harder pair.
        """
        report = self._regrade(
            small_corpus, self.TWO_CODE_TEMPLATE, (ExceptionCode.PARTIAL_REFUND,)
        )

        expected_total = sum(
            len(entry.expected.exception_codes) for entry in small_corpus.manifest.scenarios
        )
        assert report.exception_recall.denominator == expected_total
        assert report.exception_recall.numerator == expected_total - 1

    def test_a_repeated_code_cannot_be_counted_twice(self, small_corpus: GeneratedCorpus) -> None:
        """Codes are compared as sets within one scenario.

        Raising the same code twice does not manufacture recall it did not earn.
        """
        report = self._regrade(
            small_corpus,
            self.TWO_CODE_TEMPLATE,
            (ExceptionCode.PARTIAL_REFUND, ExceptionCode.PARTIAL_REFUND),
        )
        breakdown = next(
            entry for entry in report.template_breakdown if entry.template is self.TWO_CODE_TEMPLATE
        )

        assert breakdown.exception_recall.numerator == 1
        assert breakdown.exception_recall.denominator == 2

    def test_missing_every_code_scores_zero_recall(self, small_corpus: GeneratedCorpus) -> None:
        """The floor, so the metric is not merely always near one."""
        report = self._regrade(small_corpus, self.TWO_CODE_TEMPLATE, ())
        breakdown = next(
            entry for entry in report.template_breakdown if entry.template is self.TWO_CODE_TEMPLATE
        )

        assert breakdown.exception_recall.value == 0.0

    def test_pass_at_1_is_unchanged_by_the_split(self, small_corpus: GeneratedCorpus) -> None:
        """It remains the strict composite: status, exact code set, exact evidence.

        A half-recalled case still fails pass@1, because the set did not match.
        """
        report = self._regrade(
            small_corpus, self.TWO_CODE_TEMPLATE, (ExceptionCode.PARTIAL_REFUND,)
        )

        assert report.pass_at_1.value != 1.0
        assert len(report.failures) == 1
        assert not report.failures[0].exception_codes_correct

    def test_a_scenario_carries_the_counts_its_rate_was_built_from(
        self, small_corpus: GeneratedCorpus
    ) -> None:
        """A rate is only checkable against its own detail."""
        report = self._regrade(
            small_corpus, self.TWO_CODE_TEMPLATE, (ExceptionCode.PARTIAL_REFUND,)
        )
        failure = report.failures[0]

        assert failure.expected_exception_code_count == 2
        assert failure.matched_exception_code_count == 1

    def test_a_clean_run_scores_one_on_both(self, small_corpus: GeneratedCorpus) -> None:
        """The corrected metric did not weaken the unmodified case."""
        report = run_corpus(small_corpus)

        assert report.exception_recall.value == 1.0
        assert report.exact_exception_set_accuracy.value == 1.0

    def test_both_metrics_appear_in_the_rendered_report(
        self, small_corpus: GeneratedCorpus
    ) -> None:
        """Separately, at the top level and per template."""
        parsed = json.loads(render_report(run_corpus(small_corpus)))

        assert "exception_recall" in parsed
        assert "exact_exception_set_accuracy" in parsed
        for entry in parsed["template_breakdown"]:
            assert "exception_recall" in entry
            assert "exact_exception_set_accuracy" in entry

    def test_the_harness_version_records_the_change(self) -> None:
        """A report from 1.0.0 and one from 2.0.0 are not comparable."""
        assert HARNESS_VERSION == "2.0.0"


class TestExceptionMetricsWithNoAnomalies:
    """Both metrics are absent when nothing anomalous was shown."""

    def test_an_empty_manifest_leaves_both_null(self) -> None:
        """Neither zero nor one, following the existing policy."""
        empty = CorpusManifest(
            generator_version=GENERATOR_VERSION,
            domain_schema_version=DOMAIN_SCHEMA_VERSION,
            parser_version=PARSER_VERSION,
            seed=0,
            corpus_name="empty",
            scenario_count=0,
            documents=(),
            scenarios=(),
        )

        report = grade(empty, (), [])

        assert report.exception_recall.value is None
        assert report.exact_exception_set_accuracy.value is None

    def test_the_rendered_report_shows_null_for_both(self) -> None:
        """What a reader of the JSON actually sees."""
        empty = CorpusManifest(
            generator_version=GENERATOR_VERSION,
            domain_schema_version=DOMAIN_SCHEMA_VERSION,
            parser_version=PARSER_VERSION,
            seed=0,
            corpus_name="empty",
            scenario_count=0,
            documents=(),
            scenarios=(),
        )
        parsed = json.loads(render_report(grade(empty, (), [])))

        assert parsed["exception_recall"]["value"] is None
        assert parsed["exact_exception_set_accuracy"]["value"] is None
        assert parsed["exception_recall"]["denominator"] == 0
