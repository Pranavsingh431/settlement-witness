"""Running migrations.

Every database this application creates or opens is brought to head through
these functions, never through `create_all`. That is what makes an existing
database upgradeable: a schema built by `create_all` has no revision stamp, so
the next change would have nothing to migrate from.

The Alembic CLI is deliberately not used. The engine is passed in, so a
migration can never run against a database the caller did not name.

Databases built before Phase 5 predate that rule and carry no stamp. They are
adopted rather than rebuilt, on the terms set out in `app.storage.legacy`: a
database that is recognisably the Phase 2 schema is stamped at the revision it
already matches and then migrated forward, and one that is not recognisable is
refused untouched.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

from app.storage.legacy import LEGACY_REVISION, AdoptionPlan, plan_adoption

BACKEND_ROOT = Path(__file__).resolve().parents[2]
"""The directory holding alembic.ini and the migrations package."""


def alembic_config(engine: Engine) -> Config:
    """Return an Alembic configuration bound to one engine."""
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.attributes["connection"] = engine
    return config


def adopt_if_legacy(engine: Engine) -> None:
    """Stamp a pre-migration database at the revision its schema already matches.

    Does nothing to a database that is already stamped, and nothing to an empty
    one. A database that is unstamped and not recognisably Phase 2 is refused
    before anything is written, so a refusal leaves no `alembic_version` behind.

    Args:
        engine: The database about to be migrated.

    Raises:
        UnrecognisedSchemaError: When the database is unstamped, not empty, and
            not the Phase 2 schema.
    """
    if current_revision(engine) is not None:
        return
    if plan_adoption(engine) is AdoptionPlan.ADOPT_LEGACY:
        command.stamp(alembic_config(engine), LEGACY_REVISION)


def upgrade_to_head(engine: Engine) -> None:
    """Bring a database to the latest revision. Safe to run again."""
    adopt_if_legacy(engine)
    command.upgrade(alembic_config(engine), "head")


def upgrade_to(engine: Engine, revision: str) -> None:
    """Bring a database to one named revision.

    Used by the migration tests, which need to build the older schema, put rows
    in it, and then upgrade, because that is the case a migration exists for.
    """
    adopt_if_legacy(engine)
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
