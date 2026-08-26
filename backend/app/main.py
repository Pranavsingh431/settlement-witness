"""FastAPI application factory for the Settlement Witness backend.

Exposes a health endpoint and the reconciliation run API.

There is no authentication and no multi-tenancy. This is a local and
demonstration backend: it assumes one merchant's data and one trusted operator,
and it must not be exposed to a network where either assumption fails. Adding
authentication is real work that has not been done, and pretending otherwise by
adding a token check without a tenancy model would be worse than saying so.

There is no endpoint that changes a stored decision. Human override is a real
need and is deferred deliberately: the contract rests on conclusions being
immutable and replayable, and a mutable resolve endpoint would end that.
"""

from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Engine

from app import __version__
from app.api.reconciliation import router as reconciliation_router
from app.config import AppEnv, Settings, get_settings
from app.storage.database import create_database_engine, database_url_for


class HealthResponse(BaseModel):
    """Payload returned by the health endpoint."""

    status: Literal["ok"]
    version: str
    environment: AppEnv


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    """Build the FastAPI application.

    Args:
        settings: Settings to bind to this application. When omitted the
            process wide settings are used.
        engine: Database engine the reconciliation endpoints read and write
            through. Held on the application rather than in a module global, so
            a test can build an app against a temporary database and the real
            one is not reachable at all.

    Returns:
        A configured FastAPI application.
    """
    resolved = settings if settings is not None else get_settings()
    if engine is None:
        engine = create_database_engine(database_url_for(resolved.database_path))

    application = FastAPI(
        title="Settlement Witness",
        version=__version__,
        description=(
            "Evidence-complete AI finance controller. Local and demonstration "
            "backend: no authentication, no multi-tenancy."
        ),
    )
    application.state.engine = engine

    @application.get("/health", response_model=HealthResponse, tags=["ops"])
    def health() -> HealthResponse:
        """Report that the service process is running."""
        return HealthResponse(
            status="ok",
            version=__version__,
            environment=resolved.app_env,
        )

    @application.exception_handler(ValueError)
    def handle_value_error(_request: Request, error: ValueError) -> JSONResponse:
        """Turn a domain refusal into a 422 rather than a 500.

        A ValueError here means a contract rule refused something, which is a
        problem with the request or the stored data, not a crash. The message is
        the rule's own, which is written for people and mentions no internals.
        """
        return JSONResponse(
            status_code=422,
            content={"error": "unprocessable", "detail": str(error)},
        )

    application.include_router(reconciliation_router)
    return application


app = create_app()
