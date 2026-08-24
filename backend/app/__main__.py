"""Start the backend service using the settings from the environment.

Run it with ``uv run python -m app`` from the backend directory, or with
``make dev-backend`` from the repository root. Reload is switched on only in the
local environment.
"""

import uvicorn

from app.config import get_settings


def main() -> None:
    """Start the API server on the configured host and port."""
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
        reload=settings.app_env == "local",
    )


if __name__ == "__main__":  # pragma: no cover
    main()
