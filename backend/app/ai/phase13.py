"""The pre-registered protocol around a hosted shadow evaluation.

This module never constructs a hosted provider and never sends a request.  The
only command that can do either remains :mod:`app.ai.live_shadow`.  Phase 13
uses this module only to freeze the public run conditions, prove that the local
application database did not change, and turn the three local receipts into a
safe-to-publish summary.

The distinction matters.  A database fingerprint is useful evidence for an
operator, but it is not provider input.  The provider receives only the corpus
presentation built by ``live_shadow``; no value calculated here is passed to
it.
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

from app.ai.candidates import build_requests
from app.ai.corpus import ShadowCorpus, build_corpus
from app.ai.evaluation import SHADOW_HARNESS_VERSION, ShadowReport, request_set_fingerprint
from app.ai.hosted import ADAPTER_NAME, HostedProviderConfig, MissingConfiguration
from app.ai.live_shadow import LiveShadowRunReceipt
from app.benchmark.metrics import Rate
from app.reconciliation.snapshot import FactSnapshot

PHASE_13_PROTOCOL_VERSION: Final = "1.0.0"
"""The version of the pre-registered three-run protocol."""

PHASE_13_RUN_COUNT: Final = 3
"""Chosen before any provider call. It is deliberately not a CLI option."""

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[3]
APPLICATION_DATABASE: Final = REPOSITORY_ROOT / "data" / "generated" / "settlement.sqlite"
"""The database used by the documented application commands.

There is intentionally no command-line database option here.  A Phase 13 run
may fingerprint this fixed local file, but cannot be repointed at a merchant
export or make any database content provider input.
"""

PROTECTED_TABLES: Final = (
    "source_facts",
    "import_receipts",
    "reconciliation_runs",
    "reconciliation_decisions",
    "review_events",
    "bank_finality_audits",
    "bank_finality_certificates",
)
"""Every persisted state Phase 13 must prove did not change."""


class Phase13Error(RuntimeError):
    """A safe, input-free refusal of the Phase 13 protocol."""


class DatabaseProofError(Phase13Error):
    """The local database could not be inspected without exposing its content."""


class Phase13Plan(BaseModel):
    """Everything frozen before the first of the three provider calls."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    protocol_version: str
    declared_run_count: int
    commit_sha: str
    corpus_version: str
    corpus_fingerprint: str
    harness_version: str
    corpus_request_set_fingerprint: str
    corpus_page_count: int
    provider_hostname: str
    provider_name: str
    model_id: str
    timeout_seconds: float
    max_response_bytes: int
    max_requests: int
    recorded_at: str


class DatabaseProof(BaseModel):
    """Hashes of the whole database and of every protected table.

    The table hashes are local evidence only.  They make the assertion
    inspectable at the level people care about (facts, receipts, runs,
    decisions, reviews and bank audits) while the complete file-set hash catches
    a change outside those tables too.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    database_present: bool
    database_fingerprint: str
    table_row_counts: dict[str, int | None]
    table_fingerprints: dict[str, str | None]


class Phase13Metrics(BaseModel):
    """The separately reported measures. There is intentionally no accuracy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    strict_link_recall: Rate
    answered_link_recall: Rate
    link_precision: Rate
    exact_set_accuracy: Rate
    false_link_rate: Rate
    safe_abstention: Rate
    unsafe_selection: Rate
    invalid_page_rate: Rate

    @classmethod
    def from_report(cls, report: ShadowReport) -> "Phase13Metrics":
        """Select the measures Phase 13 may publish from one shadow report."""
        return cls(
            strict_link_recall=report.link_recall,
            answered_link_recall=report.answered_link_recall,
            link_precision=report.link_precision,
            exact_set_accuracy=report.exact_set_accuracy,
            false_link_rate=report.false_link_rate,
            safe_abstention=report.safe_abstention_recall,
            unsafe_selection=report.unsafe_selection_rate,
            invalid_page_rate=report.invalid_page_rate,
        )


class Phase13RunRecord(BaseModel):
    """The safe protocol record for one attempted evaluation.

    It deliberately embeds neither the raw hosted receipt nor any model output.
    The raw receipt remains only in ignored ``results/`` beside this record.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_ordinal: int
    outcome: Literal["COMPLETE", "INCOMPLETE"]
    database_before: DatabaseProof
    database_after: DatabaseProof
    database_unchanged: bool
    requests_made: int | None
    typed_failure_counts: dict[str, int]
    metrics: Phase13Metrics | None
    local_failure: Literal["LIVE_COMMAND_DID_NOT_COMPLETE"] | None


class Phase13Aggregate(BaseModel):
    """A pooled aggregate over exactly the three complete planned runs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    completed_run_count: int
    request_count: int
    typed_failure_counts: dict[str, int]
    metrics: Phase13Metrics


class Phase13Summary(BaseModel):
    """A secret-free local summary suitable for manual publication."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan: Phase13Plan
    runs: tuple[Phase13RunRecord, ...]
    aggregate: Phase13Aggregate | None
    aggregate_withheld_reason: str | None


def corpus_fingerprint(corpus: ShadowCorpus) -> str:
    """Return a canonical digest of the generated corpus and private manifest."""
    rendered = json.dumps(
        corpus.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _commit_sha(repository_root: Path) -> str:
    """Return the checked-out commit without letting git output reach a receipt."""
    git = shutil.which("git")
    if git is None:
        raise Phase13Error("git is not available to freeze the checked-out commit")
    try:
        completed = subprocess.run(  # noqa: S603 - absolute executable from shutil.which; fixed argv.
            [git, "rev-parse", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise Phase13Error("could not freeze the checked-out commit") from error
    commit = completed.stdout.strip()
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise Phase13Error("git did not return a full commit SHA")
    return commit


def build_plan(
    config: HostedProviderConfig,
    *,
    corpus: ShadowCorpus | None = None,
    commit_sha: str | None = None,
    recorded_at: str | None = None,
    repository_root: Path = REPOSITORY_ROOT,
) -> Phase13Plan:
    """Freeze the exact corpus and non-secret host configuration before a run."""
    selected_corpus = corpus if corpus is not None else build_corpus()
    snapshot = FactSnapshot.from_index(selected_corpus.index)
    requests = build_requests(snapshot, selected_corpus.styling)
    if config.max_requests < len(requests):
        raise Phase13Error(
            "SETTLEMENT_WITNESS_AI_MAX_REQUESTS is below the frozen corpus page count"
        )
    hostname = urlparse(config.base_url).hostname
    if hostname is None:  # Config validation already rejects this; keep the proof local and total.
        raise Phase13Error("the configured provider endpoint has no hostname")
    return Phase13Plan(
        protocol_version=PHASE_13_PROTOCOL_VERSION,
        declared_run_count=PHASE_13_RUN_COUNT,
        commit_sha=commit_sha if commit_sha is not None else _commit_sha(repository_root),
        corpus_version=selected_corpus.version,
        corpus_fingerprint=corpus_fingerprint(selected_corpus),
        harness_version=SHADOW_HARNESS_VERSION,
        corpus_request_set_fingerprint=request_set_fingerprint(requests),
        corpus_page_count=len(requests),
        provider_hostname=hostname,
        provider_name=ADAPTER_NAME,
        model_id=config.model,
        timeout_seconds=config.timeout_seconds,
        max_response_bytes=config.max_response_bytes,
        max_requests=config.max_requests,
        recorded_at=recorded_at if recorded_at is not None else datetime.now(UTC).isoformat(),
    )


def _hash_file_set(database: Path) -> str:
    """Hash the database and SQLite sidecars without opening any for writing."""
    digest = hashlib.sha256()
    for suffix in ("", "-wal", "-shm"):
        path = Path(f"{database}{suffix}")
        digest.update(suffix.encode("utf-8"))
        digest.update(b"\x00")
        if not path.is_file():
            digest.update(b"ABSENT")
            continue
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(64 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _table_proof(connection: sqlite3.Connection, table: str) -> tuple[int | None, str | None]:
    """Return a deterministic local row count and digest for one known table."""
    if table not in PROTECTED_TABLES:
        raise Phase13Error("the requested table is outside the Phase 13 proof")
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    if exists is None:
        return None, None
    rows = connection.execute(
        f'SELECT * FROM "{table}" ORDER BY rowid'  # noqa: S608 - checked fixed table vocabulary.
    ).fetchall()
    rendered = json.dumps(rows, default=str, separators=(",", ":"), ensure_ascii=True)
    return len(rows), hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def database_proof(database: Path = APPLICATION_DATABASE) -> DatabaseProof:
    """Read the fixed local database only to prove it did not change.

    It never creates a database, writes a transaction, imports application
    storage code, or exposes a row.  The resulting hashes stay in ignored local
    protocol artifacts and are never part of a hosted request.
    """
    if not database.is_file():
        return DatabaseProof(
            database_present=False,
            database_fingerprint=_hash_file_set(database),
            table_row_counts=dict.fromkeys(PROTECTED_TABLES),
            table_fingerprints=dict.fromkeys(PROTECTED_TABLES),
        )
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            proofs = {table: _table_proof(connection, table) for table in PROTECTED_TABLES}
        finally:
            connection.close()
    except sqlite3.DatabaseError as error:
        raise DatabaseProofError(
            "could not read the application database for a Phase 13 proof"
        ) from error
    return DatabaseProof(
        database_present=True,
        database_fingerprint=_hash_file_set(database),
        table_row_counts={table: proofs[table][0] for table in PROTECTED_TABLES},
        table_fingerprints={table: proofs[table][1] for table in PROTECTED_TABLES},
    )


def _same_plan(plan: Phase13Plan, receipt: LiveShadowRunReceipt) -> bool:
    """Return whether a receipt says it ran the frozen corpus under frozen settings."""
    configuration = receipt.configuration
    hostname = urlparse(str(configuration.get("base_url", ""))).hostname
    return (
        receipt.harness_version == plan.harness_version
        and receipt.corpus_version == plan.corpus_version
        and receipt.provider_name == plan.provider_name
        and receipt.model_id == plan.model_id
        and receipt.report.request_set_fingerprint == plan.corpus_request_set_fingerprint
        and receipt.report.page_count == plan.corpus_page_count
        and receipt.requests_made <= plan.max_requests
        and hostname == plan.provider_hostname
        and configuration.get("timeout_seconds") == plan.timeout_seconds
        and configuration.get("max_response_bytes") == plan.max_response_bytes
        and configuration.get("max_requests") == plan.max_requests
    )


def record_completed_run(
    plan: Phase13Plan,
    *,
    run_ordinal: int,
    receipt: LiveShadowRunReceipt,
    database_before: DatabaseProof,
    database_after: DatabaseProof,
) -> Phase13RunRecord:
    """Turn one raw receipt into a safe protocol record without copying it."""
    if not 1 <= run_ordinal <= plan.declared_run_count:
        raise Phase13Error("the run ordinal is outside the frozen protocol")
    if not _same_plan(plan, receipt):
        raise Phase13Error("the live receipt does not match the frozen Phase 13 plan")
    complete = receipt.requests_made == plan.corpus_page_count and not receipt.typed_failure_counts
    return Phase13RunRecord(
        run_ordinal=run_ordinal,
        outcome="COMPLETE" if complete else "INCOMPLETE",
        database_before=database_before,
        database_after=database_after,
        database_unchanged=database_before == database_after,
        requests_made=receipt.requests_made,
        typed_failure_counts=receipt.typed_failure_counts,
        metrics=Phase13Metrics.from_report(receipt.report),
        local_failure=None,
    )


def record_local_failure(
    plan: Phase13Plan,
    *,
    run_ordinal: int,
    database_before: DatabaseProof,
    database_after: DatabaseProof,
) -> Phase13RunRecord:
    """Record an interrupted command without preserving its prose or traceback."""
    if not 1 <= run_ordinal <= plan.declared_run_count:
        raise Phase13Error("the run ordinal is outside the frozen protocol")
    return Phase13RunRecord(
        run_ordinal=run_ordinal,
        outcome="INCOMPLETE",
        database_before=database_before,
        database_after=database_after,
        database_unchanged=database_before == database_after,
        requests_made=None,
        typed_failure_counts={},
        metrics=None,
        local_failure="LIVE_COMMAND_DID_NOT_COMPLETE",
    )


def _pooled_rate(rates: Sequence[Rate]) -> Rate:
    """Pool numerator and denominator rather than averaging already-rounded rates."""
    return Rate.of(sum(rate.numerator for rate in rates), sum(rate.denominator for rate in rates))


def _pooled_metrics(runs: Sequence[Phase13RunRecord]) -> Phase13Metrics:
    """Pool each like-for-like metric over complete protocol runs."""
    metrics = [run.metrics for run in runs]
    if any(metric is None for metric in metrics):
        raise Phase13Error("an incomplete run has no metrics to aggregate")
    present = [metric for metric in metrics if metric is not None]
    return Phase13Metrics(
        strict_link_recall=_pooled_rate([metric.strict_link_recall for metric in present]),
        answered_link_recall=_pooled_rate([metric.answered_link_recall for metric in present]),
        link_precision=_pooled_rate([metric.link_precision for metric in present]),
        exact_set_accuracy=_pooled_rate([metric.exact_set_accuracy for metric in present]),
        false_link_rate=_pooled_rate([metric.false_link_rate for metric in present]),
        safe_abstention=_pooled_rate([metric.safe_abstention for metric in present]),
        unsafe_selection=_pooled_rate([metric.unsafe_selection for metric in present]),
        invalid_page_rate=_pooled_rate([metric.invalid_page_rate for metric in present]),
    )


def _sum_typed_failures(runs: Sequence[Phase13RunRecord]) -> dict[str, int]:
    """Return typed failure totals without inventing a provider-error message."""
    counts: dict[str, int] = {}
    for run in runs:
        for kind, amount in run.typed_failure_counts.items():
            counts[kind] = counts.get(kind, 0) + amount
    return dict(sorted(counts.items()))


def summarise(plan: Phase13Plan, runs: Sequence[Phase13RunRecord]) -> Phase13Summary:
    """Build the publication-safe summary, withholding a partial aggregate."""
    ordered = tuple(sorted(runs, key=lambda run: run.run_ordinal))
    if len(ordered) != plan.declared_run_count:
        raise Phase13Error("the summary does not contain every frozen Phase 13 run")
    if tuple(run.run_ordinal for run in ordered) != tuple(range(1, plan.declared_run_count + 1)):
        raise Phase13Error("the summary run ordinals do not match the frozen protocol")
    all_complete = all(run.outcome == "COMPLETE" for run in ordered)
    if not all_complete:
        return Phase13Summary(
            plan=plan,
            runs=ordered,
            aggregate=None,
            aggregate_withheld_reason="one or more planned runs were incomplete",
        )
    return Phase13Summary(
        plan=plan,
        runs=ordered,
        aggregate=Phase13Aggregate(
            completed_run_count=len(ordered),
            request_count=sum(run.requests_made or 0 for run in ordered),
            typed_failure_counts=_sum_typed_failures(ordered),
            metrics=_pooled_metrics(ordered),
        ),
        aggregate_withheld_reason=None,
    )


def _write(model: BaseModel, output: Path) -> None:
    """Write one local JSON artifact; callers choose only ignored results paths."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(model.model_dump_json(indent=2), encoding="utf-8")


def _read[ModelT: BaseModel](model: type[ModelT], source: Path) -> ModelT:
    """Load a typed local JSON artifact."""
    return model.model_validate_json(source.read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    """Return the local-only helper parser; it has no provider or data input."""
    parser = argparse.ArgumentParser(
        prog="phase-13",
        description="Prepare and summarise the fixed hosted shadow-evaluation protocol.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--output", type=Path, required=True)
    proof = commands.add_parser("database-proof")
    proof.add_argument("--output", type=Path, required=True)
    record = commands.add_parser("record")
    record.add_argument("--plan", type=Path, required=True)
    record.add_argument("--run-ordinal", type=int, required=True)
    record.add_argument("--before", type=Path, required=True)
    record.add_argument("--after", type=Path, required=True)
    record.add_argument("--receipt", type=Path, required=True)
    record.add_argument("--output", type=Path, required=True)
    incomplete = commands.add_parser("record-local-failure")
    incomplete.add_argument("--plan", type=Path, required=True)
    incomplete.add_argument("--run-ordinal", type=int, required=True)
    incomplete.add_argument("--before", type=Path, required=True)
    incomplete.add_argument("--after", type=Path, required=True)
    incomplete.add_argument("--output", type=Path, required=True)
    summary = commands.add_parser("summary")
    summary.add_argument("--plan", type=Path, required=True)
    summary.add_argument("--run", type=Path, action="append", required=True)
    summary.add_argument("--output", type=Path, required=True)
    return parser


def run(argv: Sequence[str] | None = None, environment: Mapping[str, str] | None = None) -> int:
    """Run a local-only protocol helper command.

    This command never imports or constructs ``HostedLinkProposalProvider``.
    Its role ends before and resumes after ``live_shadow`` is run by the
    protocol script.
    """
    args = build_parser().parse_args(argv)
    try:
        if args.command == "preflight":
            config = HostedProviderConfig.from_environment(
                environment if environment is not None else os.environ
            )
            _write(build_plan(config), args.output)
        elif args.command == "database-proof":
            _write(database_proof(), args.output)
        elif args.command == "record":
            plan = _read(Phase13Plan, args.plan)
            before = _read(DatabaseProof, args.before)
            after = _read(DatabaseProof, args.after)
            receipt = _read(LiveShadowRunReceipt, args.receipt)
            _write(
                record_completed_run(
                    plan,
                    run_ordinal=args.run_ordinal,
                    receipt=receipt,
                    database_before=before,
                    database_after=after,
                ),
                args.output,
            )
        elif args.command == "record-local-failure":
            plan = _read(Phase13Plan, args.plan)
            before = _read(DatabaseProof, args.before)
            after = _read(DatabaseProof, args.after)
            _write(
                record_local_failure(
                    plan,
                    run_ordinal=args.run_ordinal,
                    database_before=before,
                    database_after=after,
                ),
                args.output,
            )
        elif args.command == "summary":
            plan = _read(Phase13Plan, args.plan)
            runs = [_read(Phase13RunRecord, one) for one in args.run]
            _write(summarise(plan, runs), args.output)
        else:  # pragma: no cover - argparse holds the command vocabulary.
            raise Phase13Error("the requested Phase 13 helper command is unknown")
    except (DatabaseProofError, MissingConfiguration, Phase13Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"Phase 13 {args.command} artifact written to {args.output}")
    return 0


def main() -> None:
    """Run the local protocol helper and exit with its status."""
    raise SystemExit(run())


if __name__ == "__main__":  # pragma: no cover
    main()
