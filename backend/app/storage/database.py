"""Engine and session management.

Setup is `create_all` against the declarative metadata rather than a migration
tool. There is one released schema and no deployed database to migrate, so a
migration framework would be ceremony without a subject. The moment a schema
change has to preserve existing rows, this needs revisiting, and ADR-004 records
that.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry

from app.storage.models import Base

IN_MEMORY_URL = "sqlite+pysqlite:///:memory:"
"""A database that exists only for the life of one engine. Used by tests."""


def database_url_for(path: Path) -> str:
    """Return the SQLAlchemy URL for a SQLite file at ``path``."""
    return f"sqlite+pysqlite:///{path}"


def create_database_engine(url: str, *, echo: bool = False) -> Engine:
    """Return an engine with the settings this application relies on.

    Two adjustments, both of which SQLite needs and neither of which is optional
    here.

    Foreign keys are switched on, because SQLite leaves them off by default and a
    constraint declared in the schema would otherwise be decoration.

    The driver's implicit transaction handling is switched off and BEGIN is
    emitted explicitly. This is the workaround the SQLAlchemy documentation
    gives for pysqlite, whose legacy behaviour silently commits before a
    SAVEPOINT. Without it ``begin_nested`` does not roll back, which would make
    the import service's all-or-nothing guarantee untrue while appearing to
    work. There is a test for exactly that.
    """
    engine = create_engine(url, echo=echo, future=True)

    @event.listens_for(engine, "connect")
    def _configure_connection(
        dbapi_connection: DBAPIConnection, _record: ConnectionPoolEntry
    ) -> None:
        # Stop pysqlite issuing its own BEGIN, which is what breaks SAVEPOINT.
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _emit_begin(connection: Connection) -> None:
        connection.exec_driver_sql("BEGIN")

    return engine


#: Tables that may only ever be inserted into.
APPEND_ONLY_TABLES: tuple[str, ...] = ("source_facts", "import_receipts")


def _immutability_triggers() -> tuple[str, ...]:
    """Return the DDL that makes the append-only tables reject changes.

    The repositories have no update or delete method, which stops the
    application from rewriting history by mistake. It does nothing about a
    migration script, a maintenance session, or anything else holding a
    connection. Append-only is a property of the data, so it is enforced where
    the data lives.

    ``IF NOT EXISTS`` makes this safe to run again, which is what lets ordinary
    setup create the protections rather than needing a separate step.
    """
    statements: list[str] = []
    for table in APPEND_ONLY_TABLES:
        for operation in ("UPDATE", "DELETE"):
            statements.append(
                f"CREATE TRIGGER IF NOT EXISTS trg_{table}_no_{operation.lower()} "
                f"BEFORE {operation} ON {table} "
                "BEGIN "
                f"SELECT RAISE(ABORT, '{table} is append-only: {operation} is not permitted'); "
                "END;"
            )
    return tuple(statements)


def create_schema(engine: Engine) -> None:
    """Create every table and protection that is not already there.

    Safe to run again. Existing tables are left alone and no data is touched.
    """
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        for statement in _immutability_triggers():
            connection.exec_driver_sql(statement)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a factory for sessions bound to ``engine``."""
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Yield a session, committing on success and rolling back on failure."""
    session = session_factory(engine)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
