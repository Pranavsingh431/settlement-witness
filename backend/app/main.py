"""FastAPI application factory for the Settlement Witness backend.

This phase exposes a health endpoint only. It exists so that the toolchain,
the container image and the continuous integration pipeline are exercised end
to end before any reconciliation logic is written.
"""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from app import __version__
from app.config import AppEnv, Settings, get_settings


class HealthResponse(BaseModel):
    """Payload returned by the health endpoint."""

    status: Literal["ok"]
    version: str
    environment: AppEnv


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application.

    Args:
        settings: Settings to bind to this application. When omitted the
            process wide settings are used.

    Returns:
        A configured FastAPI application.
    """
    resolved = settings if settings is not None else get_settings()

    application = FastAPI(
        title="Settlement Witness",
        version=__version__,
        description="Evidence-complete AI finance controller.",
    )

    @application.get("/health", response_model=HealthResponse, tags=["ops"])
    def health() -> HealthResponse:
        """Report that the service process is running."""
        return HealthResponse(
            status="ok",
            version=__version__,
            environment=resolved.app_env,
        )

    return application


app = create_app()
