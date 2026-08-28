"""Tests for the fixed three-run protocol around hosted shadow evaluation."""

import ast
import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

import app.ai.phase13 as protocol
from app.ai.candidates import build_requests
from app.ai.corpus import build_corpus
from app.ai.evaluation import evaluate, request_set_fingerprint
from app.ai.hosted import ADAPTER_NAME, HostedProviderConfig
from app.ai.live_shadow import LIVE_RECEIPT_VERSION, LiveShadowRunReceipt, rejection_counts
from app.ai.phase13 import (
    APPLICATION_DATABASE,
    PHASE_13_PROTOCOL_VERSION,
    PHASE_13_RUN_COUNT,
    PROTECTED_TABLES,
    DatabaseProof,
    DatabaseProofError,
    Phase13Error,
    Phase13Metrics,
    Phase13Plan,
    _commit_sha,
    _pooled_metrics,
    _sum_typed_failures,
    _table_proof,
    build_parser,
    build_plan,
    corpus_fingerprint,
    database_proof,
    record_completed_run,
    record_local_failure,
    run,
    summarise,
)
from app.ai.provider import FixtureProvider, always_abstains
from app.reconciliation.snapshot import FactSnapshot
from tests.ai.test_hosted import ENVIRONMENT, SECRET


def _plan() -> Phase13Plan:
    """Return a frozen test plan with no dependency on the checked-out commit."""
    return build_plan(
        HostedProviderConfig.from_environment(ENVIRONMENT),
        commit_sha="a" * 40,
        recorded_at="2026-08-29T00:00:00+00:00",
    )


def _proof() -> DatabaseProof:
    """Return a stable proof for record-construction tests."""
    return DatabaseProof(
        database_present=False,
        database_fingerprint="b" * 64,
        table_row_counts=dict.fromkeys(PROTECTED_TABLES),
        table_fingerprints=dict.fromkeys(PROTECTED_TABLES),
    )


def _receipt(*, typed_failure_counts: dict[str, int] | None = None) -> LiveShadowRunReceipt:
    """Return one secret-free receipt shaped like a completed hosted run."""
    corpus = build_corpus()
    snapshot = FactSnapshot.from_index(corpus.index)
    report = evaluate(
        snapshot,
        FixtureProvider(always_abstains()),
        corpus.expected_actions,
        corpus.styling,
    )
    config = HostedProviderConfig.from_environment(ENVIRONMENT)
    return LiveShadowRunReceipt(
        receipt_version=LIVE_RECEIPT_VERSION,
        harness_version=report.harness_version,
        corpus_version=corpus.version,
        provider_name=ADAPTER_NAME,
        model_id=config.model,
        configuration=config.provenance(),
        requests_made=report.page_count,
        report_rejection_counts=rejection_counts(report),
        typed_failure_counts=typed_failure_counts or {},
        report=report,
        ran_at="2026-08-29T00:00:01+00:00",
    )


class TestTheFrozenPlan:
    """Conditions are recorded before an invocation, not reconstructed after."""

    def test_it_freezes_the_exact_three_run_protocol(self) -> None:
        """The run count is a constant rather than an operator-controlled option."""
        plan = _plan()

        assert plan.protocol_version == PHASE_13_PROTOCOL_VERSION == "1.0.0"
        assert plan.declared_run_count == PHASE_13_RUN_COUNT == 3
        assert plan.commit_sha == "a" * 40
        assert plan.corpus_version == "1.0.0"
        assert plan.harness_version == "5.0.0"
        assert plan.corpus_page_count == 24

    def test_it_records_only_the_provider_hostname_and_safe_settings(self) -> None:
        """The endpoint path and key have no route into a publishable plan."""
        plan = _plan()
        rendered = plan.model_dump_json()

        assert plan.provider_hostname == "api.example.test"
        assert plan.provider_name == ADAPTER_NAME
        assert plan.model_id == "some-model-1"
        assert plan.timeout_seconds == 20.0
        assert plan.max_response_bytes == 20_000
        assert plan.max_requests == 50
        assert "base_url" not in rendered
        assert SECRET not in rendered
        assert "v1" not in rendered

    def test_its_corpus_hash_is_deterministic_and_covers_the_same_questions(self) -> None:
        """The corpus and the rendered request set are independent, stable facts."""
        first = build_corpus()
        second = build_corpus()
        plan = _plan()

        assert corpus_fingerprint(first) == corpus_fingerprint(second) == plan.corpus_fingerprint
        assert (
            plan.corpus_request_set_fingerprint
            == plan.corpus_request_set_fingerprint
            == request_set_fingerprint(
                build_requests(FactSnapshot.from_index(first.index), first.styling)
            )
        )

    def test_it_refuses_a_request_budget_that_cannot_finish_the_frozen_corpus(self) -> None:
        """A predeclared three-run protocol cannot knowingly budget away pages."""
        environment = {**ENVIRONMENT, "SETTLEMENT_WITNESS_AI_MAX_REQUESTS": "23"}

        with pytest.raises(Phase13Error, match="MAX_REQUESTS"):
            build_plan(
                HostedProviderConfig.from_environment(environment),
                commit_sha="a" * 40,
            )

    def test_it_keeps_the_hostname_check_total_even_if_validation_were_bypassed(self) -> None:
        """A hand-built config cannot manufacture a publishable endpoint host."""
        malformed = HostedProviderConfig.model_construct(
            base_url="https:///v1",
            api_key=SecretStr(SECRET),
            model="some-model-1",
            timeout_seconds=20.0,
            max_response_bytes=20_000,
            max_requests=50,
        )

        with pytest.raises(Phase13Error, match="no hostname"):
            build_plan(malformed, commit_sha="a" * 40)

    @pytest.mark.parametrize("case", ["no-git", "failed-git", "bad-sha"])
    def test_it_refuses_an_unfreezable_or_invalid_commit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, case: str
    ) -> None:
        """A run never starts with an invented or shortened source revision."""
        if case == "no-git":
            monkeypatch.setattr("app.ai.phase13.shutil.which", lambda _name: None)
        elif case == "failed-git":
            monkeypatch.setattr("app.ai.phase13.shutil.which", lambda _name: "/usr/bin/git")

            def fails(*_args: object, **_kwargs: object) -> object:
                raise OSError("not recorded")

            monkeypatch.setattr("app.ai.phase13.subprocess.run", fails)
        else:
            monkeypatch.setattr("app.ai.phase13.shutil.which", lambda _name: "/usr/bin/git")

            class Completed:
                """The small part of a subprocess result this code reads."""

                stdout = "not-a-commit\n"

            monkeypatch.setattr(
                "app.ai.phase13.subprocess.run", lambda *_args, **_kwargs: Completed()
            )

        with pytest.raises(Phase13Error):
            _commit_sha(tmp_path)

    def test_its_helper_has_no_provider_call_path(self) -> None:
        """Only live_shadow constructs the adapter that can reach the host."""
        import app.ai.phase13 as protocol

        source = Path(protocol.__file__).read_text(encoding="utf-8")
        imported = {
            node.module
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }

        assert "app.ai.hosted" in imported
        assert "app.ai.hosted" in imported
        assert "HostedLinkProposalProvider" not in imported


class TestTheDatabaseProof:
    """The proof is read-only, includes sidecars, and never exposes rows."""

    def test_an_absent_application_database_is_a_stable_proof(self, tmp_path: Path) -> None:
        """The protocol does not create one merely to make a hash possible."""
        database = tmp_path / "missing.sqlite"

        proof = database_proof(database)

        assert proof.database_present is False
        assert proof.table_row_counts == dict.fromkeys(PROTECTED_TABLES)
        assert proof.table_fingerprints == dict.fromkeys(PROTECTED_TABLES)
        assert database.exists() is False

    def test_it_hashes_the_whole_database_and_each_protected_table(self, tmp_path: Path) -> None:
        """A change to a protected row changes both the local table and file proof."""
        database = tmp_path / "application.sqlite"
        connection = sqlite3.connect(database)
        connection.execute("CREATE TABLE source_facts (value TEXT)")
        connection.execute("CREATE TABLE review_events (value TEXT)")
        connection.execute("INSERT INTO source_facts VALUES ('not provider input')")
        connection.commit()
        connection.close()

        before = database_proof(database)
        connection = sqlite3.connect(database)
        connection.execute("INSERT INTO review_events VALUES ('a local event')")
        connection.commit()
        connection.close()
        after = database_proof(database)

        assert before.database_present is True
        assert before.table_row_counts["source_facts"] == 1
        assert before.table_row_counts["review_events"] == 0
        assert before.table_fingerprints["source_facts"] is not None
        assert before.table_fingerprints["import_receipts"] is None
        assert before.database_fingerprint != after.database_fingerprint
        assert before.table_fingerprints["source_facts"] == after.table_fingerprints["source_facts"]
        assert (
            before.table_fingerprints["review_events"] != after.table_fingerprints["review_events"]
        )
        assert "not provider input" not in after.model_dump_json()

    def test_a_non_sqlite_file_refuses_without_echoing_its_content(self, tmp_path: Path) -> None:
        """A proof failure has no database text to accidentally log or publish."""
        database = tmp_path / "not-a-database.sqlite"
        database.write_text(SECRET, encoding="utf-8")

        with pytest.raises(DatabaseProofError) as caught:
            database_proof(database)

        assert SECRET not in str(caught.value)

    def test_sidecars_contribute_to_the_whole_database_fingerprint(self, tmp_path: Path) -> None:
        """The proof does not miss SQLite state currently living in a WAL file."""
        database = tmp_path / "application.sqlite"
        connection = sqlite3.connect(database)
        connection.execute("CREATE TABLE source_facts (value TEXT)")
        connection.commit()
        connection.close()
        before = database_proof(database)
        Path(f"{database}-wal").write_bytes(b"local sidecar")
        after = database_proof(database)

        assert before.database_fingerprint != after.database_fingerprint

    def test_the_table_helper_refuses_any_name_outside_the_fixed_vocabulary(
        self, tmp_path: Path
    ) -> None:
        """A string interpolation cannot become a generic database reader."""
        connection = sqlite3.connect(tmp_path / "application.sqlite")

        with pytest.raises(Phase13Error, match="outside"):
            _table_proof(connection, "sqlite_master")

        connection.close()

    def test_the_application_database_path_is_fixed(self) -> None:
        """The protocol has no database argument that could become provider input."""
        assert APPLICATION_DATABASE.name == "settlement.sqlite"
        assert APPLICATION_DATABASE.parent.name == "generated"


class TestRunRecordsAndAggregate:
    """Runs are kept separate, and any incomplete run withholds the aggregate."""

    def test_a_complete_run_has_every_required_metric_and_no_generic_accuracy(self) -> None:
        """The report's meaningful axes are preserved by name."""
        record = record_completed_run(
            _plan(),
            run_ordinal=1,
            receipt=_receipt(),
            database_before=_proof(),
            database_after=_proof(),
        )

        assert record.outcome == "COMPLETE"
        assert record.database_unchanged is True
        assert record.requests_made == 24
        assert record.typed_failure_counts == {}
        assert record.metrics is not None
        assert set(Phase13Metrics.model_fields) == {
            "strict_link_recall",
            "answered_link_recall",
            "link_precision",
            "exact_set_accuracy",
            "false_link_rate",
            "safe_abstention",
            "unsafe_selection",
            "invalid_page_rate",
        }
        assert '"accuracy"' not in record.model_dump_json().lower()

    def test_typed_provider_failures_make_a_run_incomplete_without_hiding_its_metrics(self) -> None:
        """A partial result is shown separately but is not eligible for success pooling."""
        record = record_completed_run(
            _plan(),
            run_ordinal=1,
            receipt=_receipt(typed_failure_counts={"CONNECTION_FAILED": 24}),
            database_before=_proof(),
            database_after=_proof(),
        )

        assert record.outcome == "INCOMPLETE"
        assert record.metrics is not None
        assert record.typed_failure_counts == {"CONNECTION_FAILED": 24}

    def test_it_refuses_a_wrong_ordinal_or_a_receipt_that_drifted_from_the_plan(self) -> None:
        """Neither a fourth attempt nor a differently configured receipt can enter the record."""
        plan = _plan()
        with pytest.raises(Phase13Error, match="ordinal"):
            record_completed_run(
                plan,
                run_ordinal=4,
                receipt=_receipt(),
                database_before=_proof(),
                database_after=_proof(),
            )
        with pytest.raises(Phase13Error, match="does not match"):
            record_completed_run(
                plan,
                run_ordinal=1,
                receipt=_receipt().model_copy(update={"model_id": "different-model"}),
                database_before=_proof(),
                database_after=_proof(),
            )
        with pytest.raises(Phase13Error, match="ordinal"):
            record_local_failure(
                plan, run_ordinal=4, database_before=_proof(), database_after=_proof()
            )

    def test_a_database_change_is_visible_even_when_a_hosted_run_completed(self) -> None:
        """A matching score cannot hide an application-state mutation."""
        changed = _proof().model_copy(update={"database_fingerprint": "c" * 64})
        record = record_completed_run(
            _plan(),
            run_ordinal=1,
            receipt=_receipt(),
            database_before=_proof(),
            database_after=changed,
        )

        assert record.outcome == "COMPLETE"
        assert record.database_unchanged is False

    def test_an_unexpected_local_stop_is_recorded_without_its_prose(self) -> None:
        """A broken command is visible without preserving an exception string."""
        record = record_local_failure(
            _plan(), run_ordinal=1, database_before=_proof(), database_after=_proof()
        )

        assert record.outcome == "INCOMPLETE"
        assert record.metrics is None
        assert record.local_failure == "LIVE_COMMAND_DID_NOT_COMPLETE"

    def test_three_complete_runs_pool_counts_not_rounded_rate_values(self) -> None:
        """The predeclared aggregate contains all and only the three completed attempts."""
        plan = _plan()
        records = [
            record_completed_run(
                plan,
                run_ordinal=ordinal,
                receipt=_receipt(),
                database_before=_proof(),
                database_after=_proof(),
            )
            for ordinal in range(1, 4)
        ]

        summary = summarise(plan, records)

        assert summary.aggregate_withheld_reason is None
        assert summary.aggregate is not None
        assert summary.aggregate.completed_run_count == 3
        assert summary.aggregate.request_count == 72
        assert summary.aggregate.metrics.strict_link_recall.numerator == 0
        assert summary.aggregate.metrics.strict_link_recall.denominator == 483

    def test_a_low_level_pool_refuses_a_missing_metric(self) -> None:
        """The aggregate cannot accidentally include an interrupted local command."""
        with pytest.raises(Phase13Error, match="no metrics"):
            _pooled_metrics(
                [
                    record_local_failure(
                        _plan(),
                        run_ordinal=1,
                        database_before=_proof(),
                        database_after=_proof(),
                    )
                ]
            )

    def test_typed_failure_totals_are_counts_not_provider_error_text(self) -> None:
        """The aggregate carries only enum names and integers."""
        records = [
            record_completed_run(
                _plan(),
                run_ordinal=1,
                receipt=_receipt(typed_failure_counts={"TIMED_OUT": 1}),
                database_before=_proof(),
                database_after=_proof(),
            ),
            record_completed_run(
                _plan(),
                run_ordinal=2,
                receipt=_receipt(typed_failure_counts={"TIMED_OUT": 2, "CONNECTION_FAILED": 1}),
                database_before=_proof(),
                database_after=_proof(),
            ),
        ]

        assert _sum_typed_failures(records) == {"CONNECTION_FAILED": 1, "TIMED_OUT": 3}

    def test_an_incomplete_run_withholds_instead_of_averaging_a_success_claim(self) -> None:
        """The aggregate is absent even if two other runs completed."""
        plan = _plan()
        complete = record_completed_run(
            plan,
            run_ordinal=1,
            receipt=_receipt(),
            database_before=_proof(),
            database_after=_proof(),
        )
        incomplete = record_completed_run(
            plan,
            run_ordinal=2,
            receipt=_receipt(typed_failure_counts={"TIMED_OUT": 1}),
            database_before=_proof(),
            database_after=_proof(),
        )
        third = record_completed_run(
            plan,
            run_ordinal=3,
            receipt=_receipt(),
            database_before=_proof(),
            database_after=_proof(),
        )

        summary = summarise(plan, [complete, incomplete, third])

        assert summary.aggregate is None
        assert summary.aggregate_withheld_reason == "one or more planned runs were incomplete"

    @pytest.mark.parametrize("run_ordinals", [(1, 2), (1, 1, 3), (1, 2, 4)])
    def test_the_summary_refuses_missing_duplicate_or_out_of_protocol_runs(
        self, run_ordinals: tuple[int, ...]
    ) -> None:
        """A hand-picked subset cannot become an aggregate after the fact."""
        plan = _plan()
        records = [
            record_completed_run(
                plan,
                run_ordinal=ordinal,
                receipt=_receipt(),
                database_before=_proof(),
                database_after=_proof(),
            )
            for ordinal in run_ordinals
            if ordinal <= 3
        ]

        with pytest.raises(Phase13Error):
            summarise(plan, records)


class TestTheLocalHelperCli:
    """The script-facing helper refuses unsafe configuration before artifacts."""

    def test_its_parser_has_no_provider_or_database_argument(self) -> None:
        """It is a local protocol helper, not another way to call a host."""
        parsed = build_parser().parse_args(["database-proof", "--output", "proof.json"])

        assert parsed.command == "database-proof"
        assert set(vars(parsed)) == {"command", "output"}

    def test_the_launcher_declares_three_and_only_three_hosted_attempts(self) -> None:
        """The shell entry point cannot quietly turn a poor run into a fourth attempt."""
        script = Path(__file__).resolve().parents[3] / "scripts" / "run-phase-13.sh"
        source = script.read_text(encoding="utf-8")

        assert "for phase13_run in 1 2 3; do" in source
        assert source.count("uv run python -m app.ai.live_shadow --allow-network") == 1
        assert "record-local-failure" in source
        assert '--run-ordinal "$phase13_run"' in source

    def test_preflight_stops_cleanly_when_configuration_is_absent(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """No plan or score is created when a key-setting prerequisite is absent."""
        target = tmp_path / "plan.json"

        status = run(["preflight", "--output", str(target)], environment={})

        captured = capsys.readouterr()
        assert status == 2
        assert target.exists() is False
        assert "SETTLEMENT_WITNESS_AI_API_KEY" in captured.err
        assert SECRET not in captured.err

    def test_preflight_writes_a_secret_free_plan(self, tmp_path: Path) -> None:
        """The local artifact has the hostname and no credential or endpoint path."""
        target = tmp_path / "plan.json"

        status = run(["preflight", "--output", str(target)], environment=ENVIRONMENT)
        written = target.read_text(encoding="utf-8")

        assert status == 0
        assert json.loads(written)["declared_run_count"] == 3
        assert SECRET not in written
        assert "base_url" not in written

    def test_it_reads_and_writes_the_record_and_summary_artifacts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The shell protocol gets typed artifacts without another provider caller."""
        plan_path = tmp_path / "plan.json"
        before_path = tmp_path / "before.json"
        after_path = tmp_path / "after.json"
        receipt_path = tmp_path / "receipt.json"
        record_path = tmp_path / "record.json"
        failure_path = tmp_path / "failure.json"
        summary_path = tmp_path / "summary.json"
        plan = _plan()
        proof = _proof()
        plan_path.write_text(plan.model_dump_json(), encoding="utf-8")
        before_path.write_text(proof.model_dump_json(), encoding="utf-8")
        after_path.write_text(proof.model_dump_json(), encoding="utf-8")
        receipt_path.write_text(_receipt().model_dump_json(), encoding="utf-8")

        assert (
            run(
                [
                    "record",
                    "--plan",
                    str(plan_path),
                    "--run-ordinal",
                    "1",
                    "--before",
                    str(before_path),
                    "--after",
                    str(after_path),
                    "--receipt",
                    str(receipt_path),
                    "--output",
                    str(record_path),
                ]
            )
            == 0
        )
        assert (
            run(
                [
                    "record-local-failure",
                    "--plan",
                    str(plan_path),
                    "--run-ordinal",
                    "2",
                    "--before",
                    str(before_path),
                    "--after",
                    str(after_path),
                    "--output",
                    str(failure_path),
                ]
            )
            == 0
        )
        third_path = tmp_path / "third.json"
        third = record_completed_run(
            plan,
            run_ordinal=3,
            receipt=_receipt(),
            database_before=proof,
            database_after=proof,
        )
        third_path.write_text(third.model_dump_json(), encoding="utf-8")

        assert (
            run(
                [
                    "summary",
                    "--plan",
                    str(plan_path),
                    "--run",
                    str(record_path),
                    "--run",
                    str(failure_path),
                    "--run",
                    str(third_path),
                    "--output",
                    str(summary_path),
                ]
            )
            == 0
        )
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert summary["aggregate"] is None
        assert SECRET not in summary_path.read_text(encoding="utf-8")

        monkeypatch.setattr(protocol, "database_proof", lambda: proof)
        db_output = tmp_path / "database.json"
        assert run(["database-proof", "--output", str(db_output)]) == 0
        assert db_output.read_text(encoding="utf-8") == proof.model_dump_json(indent=2)

    def test_a_bad_local_artifact_stops_without_echoing_the_receipt_text(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Validation errors are not a path for a raw hosted response into stderr."""
        plan_path = tmp_path / "plan.json"
        before_path = tmp_path / "before.json"
        after_path = tmp_path / "after.json"
        receipt_path = tmp_path / "receipt.json"
        plan_path.write_text(_plan().model_dump_json(), encoding="utf-8")
        before_path.write_text(_proof().model_dump_json(), encoding="utf-8")
        after_path.write_text(_proof().model_dump_json(), encoding="utf-8")
        receipt_path.write_text("not a receipt", encoding="utf-8")

        with pytest.raises(ValidationError):
            run(
                [
                    "record",
                    "--plan",
                    str(plan_path),
                    "--run-ordinal",
                    "1",
                    "--before",
                    str(before_path),
                    "--after",
                    str(after_path),
                    "--receipt",
                    str(receipt_path),
                    "--output",
                    str(tmp_path / "record.json"),
                ]
            )
        assert SECRET not in capsys.readouterr().err

    def test_the_module_entry_point_exits_with_the_helper_status(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The public module entry point has no separate implementation path."""
        monkeypatch.setattr(protocol, "run", lambda: 2)

        with pytest.raises(SystemExit) as caught:
            protocol.main()

        assert caught.value.code == 2
