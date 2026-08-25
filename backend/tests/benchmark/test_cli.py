"""Tests for the generator and evaluator command line."""

import json
from pathlib import Path

import pytest

from app.benchmark_cli import build_parser, load_config, run
from tests.benchmark.conftest import PUBLIC_CONFIG_PATH


@pytest.fixture
def small_config_file(tmp_path: Path) -> Path:
    """Write a one-pair-per-template configuration and return its path."""
    path = tmp_path / "corpus.json"
    path.write_text(
        json.dumps(
            {
                "corpus_name": "cli-test",
                "seed": 2468,
                "controls_per_anomaly": 1,
                "extra_controls": 0,
            }
        ),
        encoding="utf-8",
    )
    return path


class TestArgumentParsing:
    """The command needs a configuration, because the seed lives in it."""

    def test_a_subcommand_is_required(self) -> None:
        """There is no default action."""
        with pytest.raises(SystemExit):
            build_parser().parse_args([])

    def test_generate_requires_a_config_and_an_output(self) -> None:
        """A corpus with no recorded seed cannot be reproduced."""
        with pytest.raises(SystemExit):
            build_parser().parse_args(["generate"])

    def test_evaluate_requires_a_config(self) -> None:
        """Same reason."""
        with pytest.raises(SystemExit):
            build_parser().parse_args(["evaluate"])


class TestGenerate:
    """Writing a corpus to disk."""

    def test_it_writes_documents_and_a_manifest(
        self, small_config_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Three documents and the oracle."""
        output = tmp_path / "corpus"
        status = run(["generate", "--config", str(small_config_file), "--output", str(output)])

        assert status == 0
        assert {path.name for path in output.iterdir()} == {
            "payment_events.csv",
            "settlement_lines.csv",
            "payouts.csv",
            "manifest.json",
        }
        assert "seed       : 2468" in capsys.readouterr().out

    def test_two_generations_are_byte_identical(
        self, small_config_file: Path, tmp_path: Path
    ) -> None:
        """The same seed always produces the same corpus."""
        first, second = tmp_path / "a", tmp_path / "b"
        run(["generate", "--config", str(small_config_file), "--output", str(first)])
        run(["generate", "--config", str(small_config_file), "--output", str(second)])

        for path in sorted(first.iterdir()):
            assert path.read_bytes() == (second / path.name).read_bytes()

    def test_it_reports_the_corpus_is_synthetic(
        self, small_config_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Said out loud, not only recorded in the manifest."""
        run(
            [
                "generate",
                "--config",
                str(small_config_file),
                "--output",
                str(tmp_path / "c"),
            ]
        )

        assert "synthetic  : True" in capsys.readouterr().out


class TestEvaluate:
    """Running the baseline against a corpus."""

    def test_a_clean_run_succeeds(
        self, small_config_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Exit zero when everything passed."""
        status = run(["evaluate", "--config", str(small_config_file)])

        payload = json.loads(capsys.readouterr().out)
        assert status == 0
        assert payload["pass_at_1"]["value"] == 1.0

    def test_two_evaluations_print_identical_bytes(
        self, small_config_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The property a byte comparison in CI depends on."""
        run(["evaluate", "--config", str(small_config_file)])
        first = capsys.readouterr().out
        run(["evaluate", "--config", str(small_config_file)])
        second = capsys.readouterr().out

        assert first == second

    def test_the_report_can_be_written_to_a_file(
        self, small_config_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Written and printed, so a run is usable either way."""
        report = tmp_path / "nested" / "report.json"
        run(["evaluate", "--config", str(small_config_file), "--report", str(report)])

        printed = capsys.readouterr().out
        assert report.is_file()
        assert report.read_text(encoding="utf-8") == printed

    def test_summary_only_omits_the_failures(
        self, small_config_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """For a quick look at the metrics."""
        run(["evaluate", "--config", str(small_config_file), "--summary-only"])

        payload = json.loads(capsys.readouterr().out)
        assert "failures" not in payload
        assert "decision_accuracy" in payload

    def test_the_report_carries_the_seed_and_versions(
        self, small_config_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A number without the rules that produced it is an anecdote."""
        run(["evaluate", "--config", str(small_config_file)])

        payload = json.loads(capsys.readouterr().out)
        assert payload["seed"] == 2468
        assert payload["is_synthetic"] is True
        for key in (
            "harness_version",
            "generator_version",
            "baseline_version",
            "parser_version",
            "domain_schema_version",
        ):
            assert payload[key]


class TestMissingConfiguration:
    """Nothing runs without a seed."""

    def test_a_missing_config_is_reported(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """For both subcommands, before anything is generated."""
        status = run(
            [
                "generate",
                "--config",
                str(tmp_path / "nope.json"),
                "--output",
                str(tmp_path / "out"),
            ]
        )

        assert status == 1
        assert "no such configuration" in capsys.readouterr().err
        assert not (tmp_path / "out").exists()

    def test_a_missing_config_stops_an_evaluation(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Same check, same message."""
        status = run(["evaluate", "--config", str(tmp_path / "nope.json")])

        assert status == 1
        assert "no such configuration" in capsys.readouterr().err

    def test_main_exits_with_the_run_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The module entry point turns the status into an exit code."""
        import app.benchmark_cli as cli

        monkeypatch.setattr(cli, "run", lambda argv=None: 1)

        with pytest.raises(SystemExit) as caught:
            cli.main()
        assert caught.value.code == 1


class TestThePublicConfiguration:
    """The committed configuration behind `make benchmark-evaluate`."""

    def test_it_is_committed_and_parses(self) -> None:
        """A public corpus nobody can regenerate is not a public corpus."""
        config = load_config(PUBLIC_CONFIG_PATH)

        assert config.corpus_name == "public-demonstration"
        assert config.seed == 20260701

    def test_it_produces_at_least_fifty_scenarios(self) -> None:
        """The floor this phase requires, checked against the config itself."""
        assert load_config(PUBLIC_CONFIG_PATH).scenario_count >= 50

    def test_evaluating_it_succeeds(self, capsys: pytest.CaptureFixture[str]) -> None:
        """The exact run `make benchmark-evaluate` performs."""
        status = run(["evaluate", "--config", str(PUBLIC_CONFIG_PATH), "--summary-only"])

        payload = json.loads(capsys.readouterr().out)
        assert status == 0
        assert payload["scenario_count"] >= 50
        assert payload["false_resolution_rate"]["value"] == 0.0
