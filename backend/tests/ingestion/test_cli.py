"""Tests for the import command line entry point."""

from pathlib import Path

import pytest

from app.domain.facts import SourceRecordType, SourceSystem
from app.ingest_cli import build_parser, describe, run
from app.ingestion.service import ImportOutcome, ImportReceipt, RowOutcome, RowResult
from app.storage.database import create_database_engine, database_url_for, session_factory
from app.storage.repository import ImportReceiptRepository, SourceFactRepository
from tests.ingestion.conftest import FIXED_NOW, FIXTURE_DIR


def fixture_path(name: str) -> str:
    """Return the path to one example document."""
    return str(FIXTURE_DIR / name)


def stored_counts(database: Path) -> tuple[int, int]:
    """Return the number of facts and receipts in a database."""
    engine = create_database_engine(database_url_for(database))
    try:
        with session_factory(engine)() as session:
            return (
                SourceFactRepository(session).count(),
                len(ImportReceiptRepository(session).all_receipts()),
            )
    finally:
        engine.dispose()


class TestArgumentParsing:
    """The command declares what it needs and refuses to guess."""

    def test_source_system_and_record_type_are_required(self) -> None:
        """Neither is inferred from the file, because a wrong guess imports cleanly."""
        with pytest.raises(SystemExit):
            build_parser().parse_args([fixture_path("payment_events.csv")])

    def test_only_record_types_with_a_schema_are_offered(self) -> None:
        """BANK_TRANSACTION has no CSV schema, so it is not a choice."""
        with pytest.raises(SystemExit):
            build_parser().parse_args(
                [
                    "x.csv",
                    "--database",
                    "db.sqlite",
                    "--source-system",
                    "PSP_API",
                    "--record-type",
                    "BANK_TRANSACTION",
                ]
            )

    def test_an_unknown_source_system_is_refused(self) -> None:
        """The choices come from the contract."""
        with pytest.raises(SystemExit):
            build_parser().parse_args(
                [
                    "x.csv",
                    "--database",
                    "db.sqlite",
                    "--source-system",
                    "MADE_UP",
                    "--record-type",
                    "PAYMENT_EVENT",
                ]
            )


class TestRunningTheCommand:
    """End to end, against a database the command creates itself."""

    def test_a_valid_import_succeeds_and_creates_the_database(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A clean run needs no separate setup step."""
        database = tmp_path / "new.sqlite"
        status = run(
            [
                fixture_path("payment_events.csv"),
                "--database",
                str(database),
                "--source-system",
                "PSP_API",
                "--record-type",
                "PAYMENT_EVENT",
            ]
        )

        assert status == 0
        assert "ACCEPTED" in capsys.readouterr().out
        assert stored_counts(database) == (5, 1)

    def test_a_second_identical_run_is_a_no_op_and_still_succeeds(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Re-running an import must be safe, and must still be auditable."""
        database = tmp_path / "twice.sqlite"
        argv = [
            fixture_path("payment_events.csv"),
            "--database",
            str(database),
            "--source-system",
            "PSP_API",
            "--record-type",
            "PAYMENT_EVENT",
        ]
        run(argv)
        capsys.readouterr()

        status = run(argv)

        assert status == 0
        assert "DUPLICATE_NO_OP" in capsys.readouterr().out
        assert stored_counts(database) == (5, 2)

    def test_a_conflicting_import_fails_and_writes_no_facts(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Exit status 1, five facts unchanged, two receipts."""
        database = tmp_path / "conflict.sqlite"
        base = [
            "--database",
            str(database),
            "--source-system",
            "PSP_API",
            "--record-type",
            "PAYMENT_EVENT",
        ]
        run([fixture_path("payment_events.csv"), *base])
        capsys.readouterr()

        status = run([fixture_path("conflicting_payment_events.csv"), *base])

        assert status == 1
        assert "REJECTED_CONFLICT" in capsys.readouterr().out
        assert stored_counts(database) == (5, 2)

    def test_an_invalid_import_fails_and_reports_each_bad_row(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A person should be able to fix the file from the output alone."""
        database = tmp_path / "invalid.sqlite"
        status = run(
            [
                fixture_path("invalid_mixed_rows.csv"),
                "--database",
                str(database),
                "--source-system",
                "PSP_API",
                "--record-type",
                "PAYMENT_EVENT",
            ]
        )

        printed = capsys.readouterr().out
        assert status == 1
        assert "row 3" in printed
        assert "INVALID_ENUM" in printed
        assert "row 4" in printed
        assert "MISSING_VALUE" in printed
        assert stored_counts(database) == (0, 1)

    def test_a_missing_file_is_reported_without_touching_the_database(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Nothing is created for a file that does not exist."""
        database = tmp_path / "untouched.sqlite"
        status = run(
            [
                str(tmp_path / "nope.csv"),
                "--database",
                str(database),
                "--source-system",
                "PSP_API",
                "--record-type",
                "PAYMENT_EVENT",
            ]
        )

        assert status == 1
        assert "no such file" in capsys.readouterr().err
        assert not database.exists()

    def test_all_three_documented_documents_import(self, tmp_path: Path) -> None:
        """The same command loads every example the contract documents."""
        database = tmp_path / "all.sqlite"
        for name, record_type in [
            ("payment_events.csv", "PAYMENT_EVENT"),
            ("settlement_lines.csv", "SETTLEMENT_LINE"),
            ("payouts.csv", "PAYOUT"),
        ]:
            status = run(
                [
                    fixture_path(name),
                    "--database",
                    str(database),
                    "--source-system",
                    "PSP_API",
                    "--record-type",
                    record_type,
                ]
            )
            assert status == 0

        assert stored_counts(database) == (10, 3)


class TestSummary:
    """What the command prints."""

    def test_the_summary_names_the_document_hash_and_parser_version(self) -> None:
        """Enough to trace a fact back to the bytes and the rules behind it."""
        receipt = ImportReceipt(
            receipt_id="r-1",
            document_hash="a" * 64,
            document_name="x.csv",
            source_system=SourceSystem.PSP_API,
            source_record_type=SourceRecordType.PAYMENT_EVENT,
            parser_version="1.0.0",
            received_at=FIXED_NOW,
            outcome=ImportOutcome.ACCEPTED,
            row_count=1,
            accepted_count=1,
            duplicate_count=0,
            conflict_count=0,
            rejected_count=0,
            row_results=(RowResult(row_number=2, outcome=RowOutcome.ACCEPTED),),
        )

        summary = describe(receipt)
        assert "a" * 64 in summary
        assert "1.0.0" in summary
        assert "ACCEPTED" in summary

    def test_only_problem_rows_are_listed(self) -> None:
        """A clean import of a large file should not print a line per row."""
        receipt = ImportReceipt(
            receipt_id="r-2",
            document_hash="b" * 64,
            document_name="x.csv",
            source_system=SourceSystem.PSP_API,
            source_record_type=SourceRecordType.PAYMENT_EVENT,
            parser_version="1.0.0",
            received_at=FIXED_NOW,
            outcome=ImportOutcome.REJECTED_INVALID,
            row_count=2,
            accepted_count=0,
            duplicate_count=0,
            conflict_count=0,
            rejected_count=1,
            row_results=(
                RowResult(row_number=2, outcome=RowOutcome.NOT_APPLIED),
                RowResult(row_number=3, outcome=RowOutcome.REJECTED, code="MISSING_VALUE"),
            ),
            failure_detail="1 row(s) could not be read",
        )

        summary = describe(receipt)
        assert "row 3" in summary
        assert "row 2" not in summary
        assert "1 row(s) could not be read" in summary


def test_main_exits_with_the_run_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The module entry point turns the status into an exit code."""
    import app.ingest_cli as cli

    monkeypatch.setattr(
        cli,
        "run",
        lambda argv=None: 1,
    )

    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 1
