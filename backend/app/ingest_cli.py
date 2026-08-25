"""Command line entry point for importing one CSV document.

Usage::

    uv run python -m app.ingest_cli --database ../data/generated/settlement.sqlite \\
        --source-system PSP_API --record-type PAYMENT_EVENT \\
        ../data/fixtures/ingestion/payment_events.csv

The source system and the record type are declared by the caller. Neither is
guessed from the file name or the contents, because a file that is read as the
wrong record type would fail loudly on its headers, whereas a file read as the
wrong source system would import cleanly and be wrong.

Exit status is 0 when the import is accepted or is an exact duplicate, and 1
when it is rejected. A rejected import still writes its receipt.
"""

import argparse
import sys
from pathlib import Path

from app.domain.facts import SourceRecordType, SourceSystem
from app.ingestion.schemas import SUPPORTED_RECORD_TYPES
from app.ingestion.service import ImportOutcome, ImportReceipt, ImportService
from app.storage.database import create_database_engine, create_schema, session_scope

SUCCESS_OUTCOMES = (ImportOutcome.ACCEPTED, ImportOutcome.DUPLICATE_NO_OP)


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for this command."""
    parser = argparse.ArgumentParser(
        prog="ingest",
        description="Import one CSV document into the source fact store.",
    )
    parser.add_argument("path", type=Path, help="CSV file to import")
    parser.add_argument(
        "--database",
        type=Path,
        required=True,
        help="SQLite file to write to. Created with its schema if absent.",
    )
    parser.add_argument(
        "--source-system",
        required=True,
        choices=sorted(member.value for member in SourceSystem),
        help="Which system this document came from",
    )
    parser.add_argument(
        "--record-type",
        required=True,
        choices=sorted(member.value for member in SUPPORTED_RECORD_TYPES),
        help="Which schema to read the document as",
    )
    return parser


def describe(receipt: ImportReceipt) -> str:
    """Return a one screen summary of an import receipt."""
    lines = [
        f"outcome        : {receipt.outcome.value}",
        f"document       : {receipt.document_name}",
        f"document hash  : {receipt.document_hash}",
        f"parser version : {receipt.parser_version}",
        f"rows           : {receipt.row_count}",
        f"accepted       : {receipt.accepted_count}",
        f"duplicates     : {receipt.duplicate_count}",
        f"conflicts      : {receipt.conflict_count}",
        f"rejected       : {receipt.rejected_count}",
        f"receipt id     : {receipt.receipt_id}",
    ]
    if receipt.failure_detail:
        lines.append(f"detail         : {receipt.failure_detail}")
    problems = [
        result
        for result in receipt.row_results
        if result.outcome.value in ("REJECTED", "DUPLICATE_CONFLICT")
    ]
    lines.extend(
        f"  row {result.row_number}: {result.outcome.value} {result.code or ''} "
        f"{result.detail or ''}".rstrip()
        for result in problems
    )
    return "\n".join(lines)


def run(argv: list[str] | None = None) -> int:
    """Import one document and return the process exit status."""
    args = build_parser().parse_args(argv)

    path: Path = args.path
    if not path.is_file():
        print(f"error: no such file: {path}", file=sys.stderr)
        return 1

    engine = create_database_engine(f"sqlite+pysqlite:///{args.database}")
    create_schema(engine)

    content = path.read_bytes()
    with session_scope(engine) as session:
        receipt = ImportService(session).import_document(
            content,
            source_system=SourceSystem(args.source_system),
            record_type=SourceRecordType(args.record_type),
            document_name=path.name,
        )

    print(describe(receipt))
    return 0 if receipt.outcome in SUCCESS_OUTCOMES else 1


def main() -> None:
    """Run the command and exit with its status."""
    raise SystemExit(run())


if __name__ == "__main__":  # pragma: no cover
    main()
