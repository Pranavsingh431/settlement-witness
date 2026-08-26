"""Start the backend service using the settings from the environment.

The database is migrated to head before the server binds. A service that
started against an out of date schema would fail on its first real request
rather than on start, which is the harder failure to diagnose.

Run it with ``uv run python -m app`` from the backend directory, or with
``make dev-backend`` from the repository root. Reload is switched on only in the
local environment.
"""

import uvicorn

from app.config import get_settings
from app.storage.database import (
    create_database_engine,
    create_schema,
    database_url_for,
)


def main() -> None:
    """Migrate the database, then start the API server."""
    settings = get_settings()

    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_database_engine(database_url_for(settings.database_path))
    try:
        create_schema(engine)
    finally:
        engine.dispose()

    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
        reload=settings.app_env == "local",
    )


if __name__ == "__main__":  # pragma: no cover
    main()
