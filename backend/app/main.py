"""FastAPI application factory for the Settlement Witness backend.

Exposes a health endpoint, the CSV import API and the reconciliation run API.

There is no authentication and no multi-tenancy. This is a local and
demonstration backend: it assumes one merchant's data and one trusted operator,
and it must not be exposed to a network where either assumption fails. Adding
authentication is real work that has not been done, and pretending otherwise by
adding a token check without a tenancy model would be worse than saying so.

There is no endpoint that changes a stored decision. Human override is a real
need and is deferred deliberately: the contract rests on conclusions being
immutable and replayable, and a mutable resolve endpoint would end that.
"""

from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import Engine

from app import __version__
from app.api.imports import router as imports_router
from app.api.reconciliation import router as reconciliation_router
from app.config import AppEnv, Settings, get_settings
from app.storage.database import create_database_engine, database_url_for

MULTIPART_OVERHEAD_BYTES = 8 * 1024
"""How much a multipart envelope may add on top of the document itself.

The early size guard reads `Content-Length`, which covers the boundaries, the
part headers and the two declared form fields as well as the file. Allowing for
that means the guard refuses a request only when the document inside it cannot
fit under the limit, and the exact check on the document is done later while
reading it."""


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
    application.state.settings = resolved

    max_body = resolved.max_upload_bytes + MULTIPART_OVERHEAD_BYTES

    @application.middleware("http")
    async def refuse_oversized_bodies(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Refuse a request whose declared length cannot hold an allowed document.

        Checked here because this is the last point before the server reads the
        body. Past it, an oversized upload is spooled to disk before any
        endpoint sees it, and the endpoint refusing it then has already paid for
        it. A client that sends no `Content-Length`, or lies about it, is caught
        by the exact check while the document is read.
        """
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > max_body:
            return JSONResponse(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                content={
                    "detail": {
                        "error": "request_too_large",
                        "detail": (
                            f"the request body is larger than the {max_body} byte limit; "
                            "it was not read and no receipt was written"
                        ),
                    }
                },
            )
        return await call_next(request)

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

    @application.exception_handler(RequestValidationError)
    def handle_validation_error(_request: Request, error: RequestValidationError) -> JSONResponse:
        """Report a malformed request without echoing what was sent.

        FastAPI's own handler includes the offending input, which on this API
        means it can put part of an uploaded document into an error body. The
        field and the rule it broke are what a caller needs, so only those are
        returned.
        """
        problems = [
            {"field": ".".join(str(part) for part in problem["loc"]), "problem": problem["msg"]}
            for problem in error.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "detail": {
                    "error": "invalid_request",
                    "detail": "; ".join(
                        f"{problem['field']}: {problem['problem']}" for problem in problems
                    ),
                }
            },
        )

    application.include_router(imports_router)
    application.include_router(reconciliation_router)
    return application


app = create_app()
