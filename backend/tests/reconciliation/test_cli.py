"""Tests for the reconciliation command line entry point."""

import json
from pathlib import Path

import pytest

from app.domain.facts import SourceRecordType, SourceSystem
from app.ingestion.service import ImportService
from app.reconcile_cli import build_parser, run
from app.storage.database import (
    create_database_engine,
    create_schema,
    database_url_for,
    session_scope,
)
from tests.ingestion.conftest import FIXED_NOW, read_fixture


@pytest.fixture
def loaded_database(tmp_path: Path) -> Path:
    """Return a database with the three documented example documents imported."""
    database = tmp_path / "reconcile.sqlite"
    engine = create_database_engine(database_url_for(database))
    create_schema(engine)
    with session_scope(engine) as session:
        service = ImportService(session, now=FIXED_NOW)
        for fixture, record_type in [
            ("payment_events.csv", SourceRecordType.PAYMENT_EVENT),
            ("settlement_lines.csv", SourceRecordType.SETTLEMENT_LINE),
            ("payouts.csv", SourceRecordType.PAYOUT),
        ]:
            service.import_document(
                read_fixture(fixture),
                source_system=SourceSystem.PSP_API,
                record_type=record_type,
                document_name=fixture,
            )
    engine.dispose()
    return database


class TestArgumentParsing:
    """The command needs a database and nothing else."""

    def test_the_database_is_required(self) -> None:
        """There is no default, because guessing which one would be worse."""
        with pytest.raises(SystemExit):
            build_parser().parse_args([])


class TestRunningTheCommand:
    """End to end over the demo fixtures."""

    def test_the_demo_fixtures_reconcile(
        self, loaded_database: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """One line resolves, two do not. That is the honest result."""
        status = run(["--database", str(loaded_database)])

        payload = json.loads(capsys.readouterr().out)
        assert status == 0
        assert payload["settlement_line_count"] == 3
        assert payload["status_counts"]["RESOLVED"] == 1
        assert payload["status_counts"]["EXCEPTION"] == 2

    def test_the_demo_exceptions_are_named(
        self, loaded_database: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A partial refund and a payment returned in full."""
        run(["--database", str(loaded_database)])

        payload = json.loads(capsys.readouterr().out)
        assert payload["exception_counts"] == {
            "UNSUPPORTED_STATE": 1,
            "PARTIAL_REFUND": 1,
        }

    def test_two_runs_print_identical_bytes(
        self, loaded_database: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The property that makes the output diffable."""
        run(["--database", str(loaded_database)])
        first = capsys.readouterr().out
        run(["--database", str(loaded_database)])
        second = capsys.readouterr().out

        assert first == second

    def test_summary_only_omits_the_decisions(
        self, loaded_database: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """For a quick look without the detail."""
        run(["--database", str(loaded_database), "--summary-only"])

        payload = json.loads(capsys.readouterr().out)
        assert "decisions" not in payload
        assert payload["status_counts"]["RESOLVED"] == 1

    def test_every_decision_is_present_by_default(
        self, loaded_database: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """One per settlement line, with its evidence."""
        run(["--database", str(loaded_database)])

        payload = json.loads(capsys.readouterr().out)
        assert len(payload["decisions"]) == 3
        assert all(decision["evidence"] for decision in payload["decisions"])

    def test_a_missing_database_is_reported(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Nothing is created for a database that does not exist."""
        status = run(["--database", str(tmp_path / "nope.sqlite")])

        assert status == 1
        assert "no such database" in capsys.readouterr().err

    def test_an_empty_database_is_reported(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An empty run would look like a clean result, so it is refused."""
        database = tmp_path / "empty.sqlite"
        engine = create_database_engine(database_url_for(database))
        create_schema(engine)
        engine.dispose()

        status = run(["--database", str(database)])

        assert status == 1
        assert "no accepted facts" in capsys.readouterr().err

    def test_main_exits_with_the_run_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The module entry point turns the status into an exit code."""
        import app.reconcile_cli as cli

        monkeypatch.setattr(cli, "run", lambda argv=None: 1)

        with pytest.raises(SystemExit) as caught:
            cli.main()
        assert caught.value.code == 1


class TestTheDemoResultIsHonest:
    """What the fixtures actually demonstrate."""

    def test_the_resolved_line_is_the_one_with_no_refund(
        self, loaded_database: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """line-0002 settles pay-0002, which was captured and never returned."""
        run(["--database", str(loaded_database)])

        payload = json.loads(capsys.readouterr().out)
        resolved = [d for d in payload["decisions"] if d["status"] == "RESOLVED"]
        assert [d["subject_settlement_line_id"] for d in resolved] == ["line-0002"]

    def test_the_partially_refunded_line_does_not_resolve(
        self, loaded_database: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """line-0001 settles pay-0001, which was refunded in part."""
        run(["--database", str(loaded_database)])

        payload = json.loads(capsys.readouterr().out)
        line = next(
            d for d in payload["decisions"] if d["subject_settlement_line_id"] == "line-0001"
        )
        assert line["status"] == "EXCEPTION"
        assert "PARTIAL_REFUND" in line["exception_codes"]

    def test_the_charged_back_line_does_not_resolve(
        self, loaded_database: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """line-0003 settles pay-0003, which was charged back in full."""
        run(["--database", str(loaded_database)])

        payload = json.loads(capsys.readouterr().out)
        line = next(
            d for d in payload["decisions"] if d["subject_settlement_line_id"] == "line-0003"
        )
        assert line["status"] == "EXCEPTION"
        assert "UNSUPPORTED_STATE" in line["exception_codes"]
