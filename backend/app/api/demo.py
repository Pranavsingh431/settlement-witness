"""The public, repeatable synthetic batch used to demonstrate Track 04.

This route does not receive, read or write a visitor's data. It generates the
committed 59-scenario corpus, imports it through the real strict parser into a
fresh temporary database, reconciles it through the real baseline and grades
the result against the independent manifest. The temporary database is removed
before the response returns, so the public preview cannot accumulate or expose
merchant records through its demo button.
"""

from time import perf_counter

from fastapi import APIRouter

from app.api.schemas import DemoBatchResult, DemoDocumentSummary, DemoExceptionSummary
from app.benchmark.evaluator import HARNESS_VERSION, run_corpus_with_batch
from app.benchmark.generator import CorpusConfig, generate
from app.benchmark.metrics import Rate
from app.closure.plans import playbook_for
from app.domain.codes import ExceptionCode
from app.domain.decisions import DecisionStatus
from app.domain.facts import SourceRecordType

router = APIRouter(prefix="/v1/demo", tags=["demo"])

TRACK_04_CONFIG = CorpusConfig(
    corpus_name="track-04-public-synthetic-batch",
    seed=20260701,
    controls_per_anomaly=3,
    extra_controls=5,
)
"""The visible, reproducible 59-scenario batch—not an unrecorded sample."""


def _rate(numerator: int, denominator: int) -> Rate:
    """Return one operational rate with a visible numerator and denominator."""
    return Rate.of(numerator, denominator)


def _exception_summary(code: str, count: int) -> DemoExceptionSummary:
    """Attach the same deterministic close playbook the audit workspace uses."""
    _, action = playbook_for(ExceptionCode(code))
    return DemoExceptionSummary(
        code=code,
        finding_count=count,
        owner_lane=action.owner_lane,
        next_action=action.instruction,
        proof_required=action.evidence_required,
        supported_by_current_contract=action.supported_by_current_contract,
    )


def _run_track_batch() -> DemoBatchResult:
    """Evaluate the public corpus and render only honest operational measures."""
    started = perf_counter()
    corpus = generate(TRACK_04_CONFIG)
    report, batch = run_corpus_with_batch(corpus)
    duration_ms = max(1, round((perf_counter() - started) * 1000))

    resolved = batch.status_counts[DecisionStatus.RESOLVED.value]
    exceptions = batch.status_counts[DecisionStatus.EXCEPTION.value]
    insufficient = batch.status_counts[DecisionStatus.INSUFFICIENT_EVIDENCE.value]

    return DemoBatchResult(
        corpus_name=corpus.manifest.corpus_name,
        seed=corpus.manifest.seed,
        scenario_count=corpus.manifest.scenario_count,
        source_record_count=batch.fact_count,
        decision_count=len(batch.decisions),
        source_documents=[
            DemoDocumentSummary(
                document_name=document.file_name,
                source_record_type=SourceRecordType(document.record_type),
                record_count=document.row_count,
            )
            for document in corpus.manifest.documents
        ],
        resolved_count=resolved,
        exception_count=exceptions,
        insufficient_evidence_count=insufficient,
        auto_match_rate=_rate(resolved, len(batch.decisions)),
        exception_breakdown=[
            _exception_summary(code, count) for code, count in batch.exception_counts.items()
        ],
        processing_duration_ms=duration_ms,
        throughput_lines_per_second=round(len(batch.decisions) * 1000 / duration_ms, 2),
        contract_agreement=report.pass_at_1,
        exception_recall=report.exception_recall,
        false_resolution_rate=report.false_resolution_rate,
        generator_version=report.generator_version,
        harness_version=HARNESS_VERSION,
        baseline_version=report.baseline_version,
        limitation=(
            "Generated regression corpus only. These numbers do not measure real-merchant "
            "performance, production accuracy or a production service level."
        ),
    )


@router.get(
    "/batch",
    response_model=DemoBatchResult,
    summary="Run the read-only 59-scenario synthetic reconciliation batch",
)
def run_track_batch() -> DemoBatchResult:
    """Run Track 04's synthetic batch without touching the application database."""
    return _run_track_batch()
