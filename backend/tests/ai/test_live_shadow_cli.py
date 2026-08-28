"""Tests for the command that runs the corpus against a hosted model.

The command is the only way a hosted model is reached, so most of these are
about what it refuses. It never contacts the network here: every test either
stops before a client is built or serves the requests in process.
"""

import json
from pathlib import Path

import httpx2
import pytest

from app.ai.evaluation import SHADOW_HARNESS_VERSION
from app.ai.hosted import HostedProviderConfig
from app.ai.live_shadow import (
    LiveShadowRunReceipt,
    build_parser,
    describe,
    failure_counts,
    run,
)
from tests.ai.test_hosted import ENVIRONMENT, SECRET, completion, serving


class TestTheOptInIsMandatory:
    """A run that was not asked for must not reach a credential."""

    def test_it_refuses_without_allow_network(self, capsys: pytest.CaptureFixture[str]) -> None:
        """The gate, and it stops before the environment is read."""
        status = run([], environment=ENVIRONMENT)

        assert status == 2
        assert "--allow-network" in capsys.readouterr().err

    def test_it_reads_no_configuration_without_the_flag(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A complete, valid environment is still not touched.

        The check is before the read, so a command started by accident cannot
        even look at a key, let alone send one.
        """
        status = run([], environment=ENVIRONMENT)
        captured = capsys.readouterr()

        assert status == 2
        assert SECRET not in captured.out
        assert SECRET not in captured.err
        assert "environment variables" not in captured.err

    def test_the_flag_is_not_on_by_default(self) -> None:
        """Asserted on the parser, so a default cannot drift to True."""
        assert build_parser().parse_args([]).allow_network is False


class TestItCannotBePointedAtRealData:
    """There is no argument that changes what is evaluated."""

    def test_the_parser_offers_only_two_options(self) -> None:
        """No database, no file, no snapshot, no corpus selector."""
        actions = {action.dest for action in build_parser()._actions}

        assert actions == {"help", "allow_network", "output"}

    @pytest.mark.parametrize(
        "argument",
        ["--database", "--snapshot", "--input", "--facts", "--corpus", "--documents"],
    )
    def test_a_data_argument_is_refused(self, argument: str) -> None:
        """Not silently ignored: the command exits rather than run on a default."""
        with pytest.raises(SystemExit):
            build_parser().parse_args(["--allow-network", argument, "anything"])

    @staticmethod
    def _imports_of(module: object) -> set[str]:
        """Return every module name a module imports, from its syntax tree.

        Read from the imports rather than from the source text, because the
        source text says "database" several times explaining that it has no
        database access. What matters is what it can reach.
        """
        import ast

        source = Path(module.__file__).read_text(encoding="utf-8")  # type: ignore[attr-defined]
        names: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        return names

    @pytest.mark.parametrize("module_name", ["app.ai.live_shadow", "app.ai.hosted"])
    def test_it_imports_nothing_that_could_read_the_database(self, module_name: str) -> None:
        """The command and the adapter both.

        Neither can read a fact, a receipt, a run or a decision, because neither
        imports anything that can. That is a stronger statement than a promise
        not to: there is no handle to misuse.
        """
        import importlib

        imported = self._imports_of(importlib.import_module(module_name))

        for forbidden in ("sqlalchemy", "app.storage", "app.api", "app.ingestion"):
            assert not any(name.startswith(forbidden) for name in imported), imported

    def test_the_command_evaluates_the_generated_corpus_only(self) -> None:
        """It builds the corpus itself, from a function that takes no argument."""
        imported = self._imports_of(__import__("app.ai.live_shadow", fromlist=["run"]))

        assert "app.ai.corpus" in imported
        assert not any(name.startswith("app.storage") for name in imported)


class TestMissingConfigurationStopsTheRun:
    """With the flag given and the environment wrong."""

    @pytest.mark.parametrize("missing", sorted(ENVIRONMENT))
    def test_it_names_the_variable_and_not_its_value(
        self, capsys: pytest.CaptureFixture[str], missing: str
    ) -> None:
        """A message about a bad key must not contain the key."""
        environment = {key: value for key, value in ENVIRONMENT.items() if key != missing}

        status = run(["--allow-network"], environment=environment)

        captured = capsys.readouterr()
        assert status == 2
        assert missing in captured.err
        assert SECRET not in captured.err

    def test_a_plain_http_endpoint_stops_the_run(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Before any client exists."""
        environment = {
            **ENVIRONMENT,
            "SETTLEMENT_WITNESS_AI_BASE_URL": "http://remote.example.test/v1",
        }

        status = run(["--allow-network"], environment=environment)

        assert status == 2
        assert "https" in capsys.readouterr().err


class TestTheReceipt:
    """What a completed run records, and what it does not."""

    @pytest.fixture
    def receipt(self) -> LiveShadowRunReceipt:
        """Return a receipt from a run served in process."""
        from app.ai.corpus import build_corpus
        from app.ai.evaluation import evaluate
        from app.reconciliation.snapshot import FactSnapshot

        corpus = build_corpus()
        snapshot = FactSnapshot.from_index(corpus.index)
        config = HostedProviderConfig.from_environment(ENVIRONMENT)
        handler = lambda _request: completion(  # noqa: E731
            '{"outcome": "ABSTAIN", "selected_source_record_ids": []}'
        )

        with serving(handler, config) as provider:
            report = evaluate(snapshot, provider, corpus.expected_actions, corpus.styling)
            return LiveShadowRunReceipt(
                harness_version=SHADOW_HARNESS_VERSION,
                corpus_version=corpus.version,
                provider_name=provider.identity.name,
                model_id=config.model,
                configuration=config.provenance(),
                requests_made=provider.requests_made,
                failure_counts=failure_counts(report),
                report=report,
                ran_at="2026-08-27T00:00:00+00:00",
            )

    def test_it_declares_only_what_it_should(self) -> None:
        """Asserted over the schema, so a field cannot appear unnoticed."""
        assert set(LiveShadowRunReceipt.model_fields) == {
            "harness_version",
            "corpus_version",
            "provider_name",
            "model_id",
            "configuration",
            "requests_made",
            "failure_counts",
            "report",
            "ran_at",
        }

    def test_it_records_the_versions_and_the_model(self, receipt: LiveShadowRunReceipt) -> None:
        """A number is only interpretable against all three."""
        assert receipt.harness_version == SHADOW_HARNESS_VERSION
        assert receipt.corpus_version == "1.0.0"
        assert receipt.model_id == "some-model-1"
        assert receipt.provider_name == "openai-compatible"

    def test_it_records_the_request_count(self, receipt: LiveShadowRunReceipt) -> None:
        """One per page of the corpus."""
        assert receipt.requests_made == receipt.report.page_count == 24

    def test_it_records_the_non_secret_configuration(self, receipt: LiveShadowRunReceipt) -> None:
        """So a reader knows the settings the numbers were produced under."""
        assert receipt.configuration["model"] == "some-model-1"
        assert receipt.configuration["temperature"] == 0.0
        assert receipt.configuration["timeout_seconds"] == 20.0

    def test_it_carries_no_prompt_response_header_or_key(
        self, receipt: LiveShadowRunReceipt
    ) -> None:
        """The artifact most likely to be pasted somewhere."""
        rendered = receipt.model_dump_json()

        assert SECRET not in rendered
        assert "authorization" not in rendered.lower()
        assert "Bearer" not in rendered
        assert "messages" not in rendered
        assert "You are given one page" not in rendered

    def test_it_carries_the_request_set_fingerprint(self, receipt: LiveShadowRunReceipt) -> None:
        """So two runs over different renderings are not read as comparable."""
        assert len(receipt.report.request_set_fingerprint) == 64

    def test_the_summary_says_what_the_run_is_not(self, receipt: LiveShadowRunReceipt) -> None:
        """Printed with every run, so the caveat travels with the number."""
        summary = describe(receipt)

        assert "not reconciliation accuracy" in summary
        assert "not production performance" in summary
        assert SECRET not in summary

    def test_the_summary_reports_every_rate_with_its_counts(
        self, receipt: LiveShadowRunReceipt
    ) -> None:
        """A rate without its denominator cannot be checked."""
        summary = describe(receipt)

        for label in ("link recall", "exact set", "safe abstention", "invalid pages"):
            assert label in summary
        assert "/" in summary

    def test_an_unmeasurable_rate_is_named_not_shown_as_zero(
        self, receipt: LiveShadowRunReceipt
    ) -> None:
        """The provider abstained everywhere, so precision has no denominator."""
        assert "not measurable" in describe(receipt)


class TestFailureCounting:
    """What the receipt says when pages did not answer."""

    def test_it_counts_each_rejection_by_name(self) -> None:
        """So a reader can tell a timeout from a malformed answer."""
        from app.ai.corpus import build_corpus
        from app.ai.evaluation import evaluate
        from app.reconciliation.snapshot import FactSnapshot

        corpus = build_corpus()
        snapshot = FactSnapshot.from_index(corpus.index)
        config = HostedProviderConfig.from_environment(ENVIRONMENT)
        handler = lambda _request: httpx2.Response(429, json={})  # noqa: E731

        with serving(handler, config) as provider:
            report = evaluate(snapshot, provider, corpus.expected_actions, corpus.styling)

        assert failure_counts(report) == {"PROVIDER_FAILED": 24}

    def test_it_is_empty_when_every_page_answered(self) -> None:
        """Rather than reporting zeroes for kinds that did not occur."""
        from app.ai.corpus import build_corpus
        from app.ai.evaluation import evaluate
        from app.reconciliation.snapshot import FactSnapshot

        corpus = build_corpus()
        snapshot = FactSnapshot.from_index(corpus.index)
        config = HostedProviderConfig.from_environment(ENVIRONMENT)
        handler = lambda _request: completion(  # noqa: E731
            '{"outcome": "ABSTAIN", "selected_source_record_ids": []}'
        )

        with serving(handler, config) as provider:
            report = evaluate(snapshot, provider, corpus.expected_actions, corpus.styling)

        assert failure_counts(report) == {}


class TestTheArtifact:
    """Written only when asked for, and never containing a secret."""

    def _receipt(self) -> LiveShadowRunReceipt:
        from app.ai.evaluation import ShadowReport, evaluate
        from app.ai.provider import FixtureProvider, always_abstains
        from app.reconciliation.snapshot import FactSnapshot
        from tests.reconciliation.conftest import index_of, settlement_line

        snapshot = FactSnapshot.from_index(index_of(settlement_line("sl-1")))
        report: ShadowReport = evaluate(snapshot, FixtureProvider(always_abstains()))
        return LiveShadowRunReceipt(
            harness_version=SHADOW_HARNESS_VERSION,
            corpus_version="1.0.0",
            provider_name="openai-compatible",
            model_id="some-model-1",
            configuration=HostedProviderConfig.from_environment(ENVIRONMENT).provenance(),
            requests_made=0,
            failure_counts={},
            report=report,
            ran_at="2026-08-27T00:00:00+00:00",
        )

    def test_a_written_artifact_holds_no_secret(self, tmp_path: Path) -> None:
        """What lands on disk is what gets shared."""
        target = tmp_path / "nested" / "live.json"
        receipt = self._receipt()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(receipt.model_dump_json(indent=2), encoding="utf-8")

        written = target.read_text(encoding="utf-8")

        assert SECRET not in written
        assert json.loads(written)["model_id"] == "some-model-1"

    def test_results_are_ignored_by_git(self) -> None:
        """A run receipt is a local artifact, not repository content."""
        ignore = Path(__file__).resolve().parents[3] / ".gitignore"

        assert "results/" in ignore.read_text(encoding="utf-8")


class TestACompleteRun:
    """The whole command, served in process so nothing reaches the network."""

    @staticmethod
    def _transport(content: str) -> httpx2.MockTransport:
        """Return a transport whose model always answers with one string."""
        return httpx2.MockTransport(lambda _request: completion(content))

    def test_it_runs_the_corpus_and_reports(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Exit zero, and a summary on standard output."""
        status = run(
            ["--allow-network"],
            environment=ENVIRONMENT,
            transport=self._transport('{"outcome": "ABSTAIN", "selected_source_record_ids": []}'),
        )

        captured = capsys.readouterr()
        assert status == 0
        assert "requests made    : 24" in captured.out
        assert "not reconciliation accuracy" in captured.out
        assert SECRET not in captured.out

    def test_a_poor_score_is_still_a_successful_run(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Exit zero when every page failed.

        The command reports what happened. A model that answered badly is a
        result, and exiting non-zero would make a bad score look like a broken
        command.
        """
        status = run(
            ["--allow-network"],
            environment=ENVIRONMENT,
            transport=httpx2.MockTransport(lambda _request: httpx2.Response(429, json={})),
        )

        captured = capsys.readouterr()
        assert status == 0
        assert "PROVIDER_FAILED" in captured.out
        assert SECRET not in captured.out

    def test_it_writes_no_artifact_without_output(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Nothing lands on disk unless it was asked for."""
        run(
            ["--allow-network"],
            environment=ENVIRONMENT,
            transport=self._transport('{"outcome": "ABSTAIN", "selected_source_record_ids": []}'),
        )
        capsys.readouterr()

        assert list(tmp_path.iterdir()) == []

    def test_it_writes_the_receipt_when_asked(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Creating the directory, so a first run does not fail on a path."""
        target = tmp_path / "results" / "live.json"

        status = run(
            ["--allow-network", "--output", str(target)],
            environment=ENVIRONMENT,
            transport=self._transport('{"outcome": "ABSTAIN", "selected_source_record_ids": []}'),
        )

        captured = capsys.readouterr()
        assert status == 0
        assert target.is_file()
        assert f"receipt written to {target}" in captured.out

        written = json.loads(target.read_text(encoding="utf-8"))
        assert written["model_id"] == "some-model-1"
        assert written["requests_made"] == 24
        assert SECRET not in target.read_text(encoding="utf-8")

    def test_the_written_receipt_carries_the_report(self, tmp_path: Path) -> None:
        """The metrics, in full, with their denominators."""
        target = tmp_path / "live.json"
        run(
            ["--allow-network", "--output", str(target)],
            environment=ENVIRONMENT,
            transport=self._transport('{"outcome": "ABSTAIN", "selected_source_record_ids": []}'),
        )

        report = json.loads(target.read_text(encoding="utf-8"))["report"]

        assert report["line_count"] == 6
        assert report["page_count"] == 24
        assert report["link_recall"]["denominator"] == 161
        assert report["harness_version"] == SHADOW_HARNESS_VERSION

    def test_a_run_leaves_no_module_state_behind(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Two runs produce the same request count, not an accumulating one."""
        transport = self._transport('{"outcome": "ABSTAIN", "selected_source_record_ids": []}')
        run(["--allow-network"], environment=ENVIRONMENT, transport=transport)
        first = capsys.readouterr().out
        run(["--allow-network"], environment=ENVIRONMENT, transport=transport)
        second = capsys.readouterr().out

        assert "requests made    : 24" in first
        assert "requests made    : 24" in second


class TestTheModuleEntryPoint:
    """`python -m app.ai.live_shadow` turns the status into an exit code."""

    def test_it_exits_with_the_status(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Run with no arguments, so it stops at the opt-in gate.

        Which is the behaviour worth pinning here: the entry point a person
        reaches by typing the module name refuses to call anything.
        """
        import sys

        import app.ai.live_shadow as command

        monkeypatch.setattr(sys, "argv", ["live-shadow"])

        with pytest.raises(SystemExit) as caught:
            command.main()

        assert caught.value.code == 2
        assert "--allow-network" in capsys.readouterr().err
