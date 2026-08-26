"""Wiring the API to a database.

The engine is held on the application rather than in a module global, so a test
can build an app against a temporary database without the production one being
reachable at all.
"""

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.storage.database import session_factory


def get_engine(request: Request) -> Engine:
    """Return the engine this application was built with."""
    engine: object = getattr(request.app.state, "engine", None)
    if not isinstance(engine, Engine):  # pragma: no cover - create_app always sets it
        message = "the application was built without a database engine"
        raise RuntimeError(message)
    return engine


def get_app_settings(request: Request) -> Settings:
    """Return the settings this application was built with.

    Read from the application rather than the process, so a test can build an
    app with a different limit without changing the settings every other test
    sees.
    """
    settings: object = getattr(request.app.state, "settings", None)
    return settings if isinstance(settings, Settings) else get_settings()


def get_upload_limit(request: Request) -> int:
    """Return the largest document an upload endpoint will read, in bytes."""
    return get_app_settings(request).max_upload_bytes


def get_session(request: Request) -> Iterator[Session]:
    """Yield a session for one request, committing on success.

    A commit is needed because creating a run writes. Read-only requests commit
    nothing, so the cost is a no-op for them.
    """
    session = session_factory(get_engine(request))()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
