"""Running migrations.

Every database this application creates or opens is brought to head through
these functions, never through `create_all`. That is what makes an existing
database upgradeable: a schema built by `create_all` has no revision stamp, so
the next change would have nothing to migrate from.

The Alembic CLI is deliberately not used. The engine is passed in, so a
migration can never run against a database the caller did not name.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

BACKEND_ROOT = Path(__file__).resolve().parents[2]
"""The directory holding alembic.ini and the migrations package."""


def alembic_config(engine: Engine) -> Config:
    """Return an Alembic configuration bound to one engine."""
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.attributes["connection"] = engine
    return config


def upgrade_to_head(engine: Engine) -> None:
    """Bring a database to the latest revision. Safe to run again."""
    command.upgrade(alembic_config(engine), "head")


def upgrade_to(engine: Engine, revision: str) -> None:
    """Bring a database to one named revision.

    Used by the migration tests, which need to build the older schema, put rows
    in it, and then upgrade, because that is the case a migration exists for.
    """
    command.upgrade(alembic_config(engine), revision)


def current_revision(engine: Engine) -> str | None:
    """Return the revision a database is stamped at, or None if unstamped."""
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def head_revision(engine: Engine) -> str:
    """Return the latest revision the migrations define."""
    script = ScriptDirectory.from_config(alembic_config(engine))
    head = script.get_current_head()
    if head is None:  # pragma: no cover - there is always at least one revision
        message = "no migrations are defined"
        raise RuntimeError(message)
    return head
