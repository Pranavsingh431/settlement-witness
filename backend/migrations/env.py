"""Alembic environment.

The engine is always supplied by the caller through ``config.attributes``, never
built from a URL in the ini file. That keeps a migration from ever running
against a database the caller did not name, which matters most in tests, where
a stray connection string would quietly migrate the developer's own file.
"""

from alembic import context
from sqlalchemy import Engine

from app.storage.models import Base

target_metadata = Base.metadata


def run_migrations_online() -> None:
    """Run migrations against the engine the caller provided."""
    connectable = context.config.attributes.get("connection", None)
    if connectable is None:
        message = (
            "no engine was supplied; run migrations through "
            "app.storage.migrations.upgrade_to_head rather than the alembic CLI"
        )
        raise RuntimeError(message)

    if isinstance(connectable, Engine):
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    else:
        context.configure(
            connection=connectable,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
