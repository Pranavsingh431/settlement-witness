"""The evaluator harness.

Takes a generated corpus, runs it through the real ingestion and reconciliation
paths, and grades the result against the manifest oracle.

Nothing is simulated. The documents go through the same strict CSV parser, into
the same append-only store, and out through the same deterministic baseline that
a real import would use. If any of those refuse the corpus, the evaluation says
so rather than working around it.
"""

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel, ConfigDict

from app.benchmark.generator import (
    FILE_NAMES,
    SOURCE_SYSTEM,
    TEMPLATE_ORDER,
    GeneratedCorpus,
)
from app.benchmark.manifest import CorpusManifest, ScenarioEntry
from app.benchmark.metrics import Rate
from app.benchmark.specs import TemplateId
from app.domain.codes import ExceptionCode
from app.domain.decisions import DecisionStatus, ReconciliationDecision
from app.domain.facts import SourceRecordType
from app.domain.version import DOMAIN_SCHEMA_VERSION
from app.ingestion.schemas import PARSER_VERSION
from app.ingestion.service import ImportOutcome, ImportService
from app.reconciliation.batch import BASELINE_VERSION, reconcile
from app.storage.database import (
    create_database_engine,
    create_schema,
    database_url_for,
    session_factory,
    session_scope,
)
from app.storage.repository import SourceFactRepository

HARNESS_VERSION = "1.0.0"
"""Version of the grading rules."""

RECORD_TYPE_BY_FILE: Mapping[str, SourceRecordType] = {
    name: record_type for record_type, name in FILE_NAMES.items()
}

EVALUATION_CLOCK = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
"""A fixed observation time for every import in an evaluation.

Facts carry an observed-at, and the reconciliation snapshot derives its as-of
from the latest one. A wall clock would therefore change the report on every run
for a reason that has nothing to do with the corpus.
"""


class ScenarioResult(BaseModel):
    """How one scenario was graded."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: str
    template: TemplateId
    paired_control_id: str | None
    subject_settlement_line_id: str

    expected_status: DecisionStatus
    actual_status: DecisionStatus | None
    """None when the baseline produced no decision for this line at all."""

    expected_exception_codes: tuple[ExceptionCode, ...]
    actual_exception_codes: tuple[ExceptionCode, ...]
    expected_evidence_record_ids: tuple[str, ...]
    actual_evidence_record_ids: tuple[str, ...]

    status_correct: bool
    exception_codes_correct: bool
    evidence_correct: bool
    evidence_fully_verified: bool
    is_false_resolution: bool
    """The decision resolved and the oracle says it must not have."""

    passed: bool
    """Status, codes and evidence all exactly right. This is what pass@1 counts."""


class TemplateBreakdown(BaseModel):
    """Per-template grading."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    template: TemplateId
    scenario_count: int
    decision_accuracy: Rate
    exception_recall: Rate
    evidence_completeness: Rate
    pass_at_1: Rate
    false_resolutions: int


class PairedControlBreakdown(BaseModel):
    """How the matched pairs behaved."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pair_count: int
    both_correct: int
    """Pairs where the control resolved and the anomaly did not, both as expected."""

    control_failed: int
    anomaly_failed: int
    unpaired_anomalies: int
    """Anomalies whose named control is not in the corpus. Always zero for a
    generated corpus, reported because an externally supplied manifest may not
    be."""


class EvaluationReport(BaseModel):
    """The complete, deterministic result of one evaluation run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    harness_version: str = HARNESS_VERSION
    generator_version: str
    baseline_version: str
    parser_version: str
    domain_schema_version: str
    manifest_version: str

    corpus_name: str
    seed: int
    is_synthetic: bool = True
    """These are generated scenarios. No number here is a statement about any
    real merchant's records."""

    scenario_count: int
    decision_count: int
    import_outcomes: tuple[str, ...]

    decision_accuracy: Rate
    exception_recall: Rate
    evidence_completeness: Rate
    evidence_verification_completeness: Rate
    false_resolution_rate: Rate
    pass_at_1: Rate

    template_breakdown: tuple[TemplateBreakdown, ...]
    paired_control_breakdown: PairedControlBreakdown
    failures: tuple[ScenarioResult, ...]
    """Every scenario that did not pass, in scenario order."""


def _grade_one(entry: ScenarioEntry, decision: ReconciliationDecision | None) -> ScenarioResult:
    """Grade one scenario against its oracle.

    A missing decision is graded as a failure of everything, rather than being
    skipped. A corpus whose lines produced no decision has not been evaluated,
    and silently dropping it would flatter the result.
    """
    expected_codes = tuple(sorted(entry.expected.exception_codes, key=lambda c: c.value))

    if decision is None:
        return ScenarioResult(
            scenario_id=entry.scenario_id,
            template=entry.template,
            paired_control_id=entry.paired_control_id,
            subject_settlement_line_id=entry.subject_settlement_line_id,
            expected_status=entry.expected.status,
            actual_status=None,
            expected_exception_codes=expected_codes,
            actual_exception_codes=(),
            expected_evidence_record_ids=entry.expected_evidence_record_ids,
            actual_evidence_record_ids=(),
            status_correct=False,
            exception_codes_correct=False,
            evidence_correct=False,
            evidence_fully_verified=False,
            is_false_resolution=False,
            passed=False,
        )

    actual_codes = tuple(sorted(decision.exception_codes, key=lambda c: c.value))
    actual_evidence = tuple(sorted(reference.source_record_id for reference in decision.evidence))

    status_correct = decision.status is entry.expected.status
    codes_correct = actual_codes == expected_codes
    evidence_correct = actual_evidence == entry.expected_evidence_record_ids
    fully_verified = decision.verified_evidence_count == len(decision.evidence)

    return ScenarioResult(
        scenario_id=entry.scenario_id,
        template=entry.template,
        paired_control_id=entry.paired_control_id,
        subject_settlement_line_id=entry.subject_settlement_line_id,
        expected_status=entry.expected.status,
        actual_status=decision.status,
        expected_exception_codes=expected_codes,
        actual_exception_codes=actual_codes,
        expected_evidence_record_ids=entry.expected_evidence_record_ids,
        actual_evidence_record_ids=actual_evidence,
        status_correct=status_correct,
        exception_codes_correct=codes_correct,
        evidence_correct=evidence_correct,
        evidence_fully_verified=fully_verified,
        is_false_resolution=(
            decision.status is DecisionStatus.RESOLVED
            and entry.expected.status is not DecisionStatus.RESOLVED
        ),
        passed=status_correct and codes_correct and evidence_correct,
    )


def _breakdown_by_template(results: Sequence[ScenarioResult]) -> tuple[TemplateBreakdown, ...]:
    """Return per-template grading, covering every template in a fixed order."""
    breakdowns: list[TemplateBreakdown] = []
    for template in TEMPLATE_ORDER:
        subset = [result for result in results if result.template is template]
        if not subset:
            continue
        anomalies = [
            result for result in subset if result.expected_status is not DecisionStatus.RESOLVED
        ]
        breakdowns.append(
            TemplateBreakdown(
                template=template,
                scenario_count=len(subset),
                decision_accuracy=Rate.of(sum(1 for r in subset if r.status_correct), len(subset)),
                exception_recall=Rate.of(
                    sum(1 for r in anomalies if r.exception_codes_correct), len(anomalies)
                ),
                evidence_completeness=Rate.of(
                    sum(1 for r in subset if r.evidence_correct), len(subset)
                ),
                pass_at_1=Rate.of(sum(1 for r in subset if r.passed), len(subset)),
                false_resolutions=sum(1 for r in subset if r.is_false_resolution),
            )
        )
    return tuple(breakdowns)


def _breakdown_pairs(results: Sequence[ScenarioResult]) -> PairedControlBreakdown:
    """Return how the matched pairs behaved.

    A pair is judged together. Both members must be graded correctly for the pair
    to count, because the point of a control is that the same system got the
    unremarkable case right while catching the anomalous one.
    """
    by_id = {result.scenario_id: result for result in results}
    anomalies = [result for result in results if result.paired_control_id is not None]

    both = control_failed = anomaly_failed = unpaired = 0
    for anomaly in anomalies:
        control = by_id.get(anomaly.paired_control_id or "")
        if control is None:
            unpaired += 1
            continue
        if control.passed and anomaly.passed:
            both += 1
        else:
            if not control.passed:
                control_failed += 1
            if not anomaly.passed:
                anomaly_failed += 1

    return PairedControlBreakdown(
        pair_count=len(anomalies),
        both_correct=both,
        control_failed=control_failed,
        anomaly_failed=anomaly_failed,
        unpaired_anomalies=unpaired,
    )


def grade(
    manifest: CorpusManifest,
    decisions: Sequence[ReconciliationDecision],
    import_outcomes: Sequence[str],
) -> EvaluationReport:
    """Grade a set of decisions against a manifest.

    Separated from running the corpus so a test can hand it decisions the
    baseline never produced, and prove the grading actually depends on them.
    """
    by_line = {decision.subject_settlement_line_id: decision for decision in decisions}
    results = tuple(
        _grade_one(entry, by_line.get(entry.subject_settlement_line_id))
        for entry in manifest.scenarios
    )

    anomalies = [r for r in results if r.expected_status is not DecisionStatus.RESOLVED]

    return EvaluationReport(
        generator_version=manifest.generator_version,
        baseline_version=BASELINE_VERSION,
        parser_version=PARSER_VERSION,
        domain_schema_version=DOMAIN_SCHEMA_VERSION,
        manifest_version=manifest.manifest_version,
        corpus_name=manifest.corpus_name,
        seed=manifest.seed,
        scenario_count=len(results),
        decision_count=len(decisions),
        import_outcomes=tuple(import_outcomes),
        decision_accuracy=Rate.of(sum(1 for r in results if r.status_correct), len(results)),
        exception_recall=Rate.of(
            sum(1 for r in anomalies if r.exception_codes_correct), len(anomalies)
        ),
        evidence_completeness=Rate.of(sum(1 for r in results if r.evidence_correct), len(results)),
        evidence_verification_completeness=Rate.of(
            sum(1 for r in results if r.evidence_fully_verified), len(results)
        ),
        false_resolution_rate=Rate.of(
            sum(1 for r in anomalies if r.is_false_resolution), len(anomalies)
        ),
        pass_at_1=Rate.of(sum(1 for r in results if r.passed), len(results)),
        template_breakdown=_breakdown_by_template(results),
        paired_control_breakdown=_breakdown_pairs(results),
        failures=tuple(result for result in results if not result.passed),
    )


def run_corpus(corpus: GeneratedCorpus) -> EvaluationReport:
    """Import and reconcile a corpus in a fresh database, then grade it.

    A new temporary database per run, so no evaluation can be influenced by an
    earlier one. The database is discarded afterwards: an evaluation is a
    measurement, not a place to accumulate state.
    """
    with TemporaryDirectory(prefix="settlement-witness-eval-") as directory:
        database = Path(directory) / "evaluation.sqlite"
        engine = create_database_engine(database_url_for(database))
        try:
            create_schema(engine)
            outcomes = _import_all(engine, corpus)
            with session_factory(engine)() as session:
                index = SourceFactRepository(session).fact_index()
                decisions = reconcile(index).decisions
        finally:
            engine.dispose()

    return grade(corpus.manifest, decisions, outcomes)


def _import_all(engine: object, corpus: GeneratedCorpus) -> list[str]:
    """Import every document through the real ingestion path.

    Raises:
        RuntimeError: If any document is refused. A corpus the strict parser will
            not accept cannot be evaluated, and reporting a score over the part
            that loaded would be worse than stopping.
    """
    outcomes: list[str] = []
    with session_scope(engine) as session:  # type: ignore[arg-type]
        service = ImportService(session, now=EVALUATION_CLOCK)
        for entry in corpus.manifest.documents:
            receipt = service.import_document(
                corpus.documents[entry.file_name].encode("utf-8"),
                source_system=SOURCE_SYSTEM,
                record_type=RECORD_TYPE_BY_FILE[entry.file_name],
                document_name=entry.file_name,
            )
            outcomes.append(f"{entry.file_name}={receipt.outcome.value}")
            if receipt.outcome is not ImportOutcome.ACCEPTED:
                message = (
                    f"the generated corpus was refused at import: {entry.file_name} "
                    f"gave {receipt.outcome.value} ({receipt.failure_detail})"
                )
                raise RuntimeError(message)
    return outcomes


def render_report(report: EvaluationReport) -> str:
    """Return the report as deterministic JSON."""
    return (
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False)
        + "\n"
    )
