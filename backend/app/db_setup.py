"""Create the database schema.

Run it with ``make db-setup``. It is safe to run again: tables that already
exist are left alone, and no data is touched.
"""

import argparse
from pathlib import Path

from app.storage.database import create_database_engine, create_schema, database_url_for
from app.storage.models import Base


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for this command."""
    parser = argparse.ArgumentParser(
        prog="db-setup", description="Create the SQLite schema if it is not there yet."
    )
    parser.add_argument("--database", type=Path, required=True, help="SQLite file to create")
    return parser


def run(argv: list[str] | None = None) -> int:
    """Create the schema and report the tables that exist afterwards."""
    args = build_parser().parse_args(argv)
    database: Path = args.database
    database.parent.mkdir(parents=True, exist_ok=True)

    engine = create_database_engine(database_url_for(database))
    try:
        create_schema(engine)
    finally:
        engine.dispose()

    print(f"database : {database}")
    for name in sorted(Base.metadata.tables):
        print(f"table    : {name}")
    return 0


def main() -> None:
    """Run the command and exit with its status."""
    raise SystemExit(run())


if __name__ == "__main__":  # pragma: no cover
    main()
