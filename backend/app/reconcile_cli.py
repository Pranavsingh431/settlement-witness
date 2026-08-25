"""Command line entry point for the reconciliation baseline.

Reads the complete accepted fact index from a database and prints the batch as
JSON.

Usage::

    uv run python -m app.reconcile_cli --database ../data/generated/settlement.sqlite

The output is deterministic: the same database produces byte identical JSON
every time, so two runs can be diffed and a change in the result always means a
change in the facts or in the rules.
"""

import argparse
import json
import sys
from pathlib import Path

from app.reconciliation.batch import ReconciliationBatch, reconcile
from app.storage.database import create_database_engine, database_url_for, session_factory
from app.storage.repository import SourceFactRepository


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for this command."""
    parser = argparse.ArgumentParser(
        prog="reconcile",
        description="Run the deterministic reconciliation baseline and print JSON.",
    )
    parser.add_argument(
        "--database", type=Path, required=True, help="SQLite file to read facts from"
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print the counts without the individual decisions",
    )
    return parser


def render(batch: ReconciliationBatch, *, summary_only: bool = False) -> str:
    """Return the batch as deterministic JSON.

    Keys are sorted and indentation is fixed, so the same batch always renders
    to the same bytes.
    """
    payload = batch.model_dump(mode="json")
    if summary_only:
        payload.pop("decisions", None)
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)


def run(argv: list[str] | None = None) -> int:
    """Reconcile one database and print the result."""
    args = build_parser().parse_args(argv)

    database: Path = args.database
    if not database.is_file():
        print(f"error: no such database: {database}", file=sys.stderr)
        return 1

    engine = create_database_engine(database_url_for(database))
    try:
        with session_factory(engine)() as session:
            index = SourceFactRepository(session).fact_index()
    finally:
        engine.dispose()

    if not index:
        print("error: the database holds no accepted facts to reconcile", file=sys.stderr)
        return 1

    print(render(reconcile(index), summary_only=args.summary_only))
    return 0


def main() -> None:
    """Run the command and exit with its status."""
    raise SystemExit(run())


if __name__ == "__main__":  # pragma: no cover
    main()
