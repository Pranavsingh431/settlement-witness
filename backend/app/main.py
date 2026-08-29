"""FastAPI application factory for the Settlement Witness backend.

Exposes a health endpoint, the CSV import API, the reconciliation run API, the
bank finality audit API and the human review queue.

Uploads are bounded twice. `RequestBodyLimit` counts the bytes of an import
request before anything parses them, which is what stops a client that sends no
`Content-Length` or a false one. `read_bounded` then checks the document itself
against the exact configured limit. The first bounds what the server accepts,
the second decides what it will import.

There is no authentication and no multi-tenancy. The submitted public preview
is therefore a shared synthetic demonstration: visitors can load the four
bundled walkthrough documents, but must never submit merchant data. A real
merchant deployment needs authentication, tenancy and access control; adding a
token-shaped check without those properties would be worse than saying so.

There is no endpoint that changes a stored decision. The review API appends
human workflow events beside a decision and cannot alter one: there is no field
in its command that could carry a status, and the table it writes to refuses
UPDATE and DELETE at the database. A genuine resolution would be a new source
record, imported and reconciled into a new run. It would never be a button.
"""

from typing import Literal

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Engine

from app import __version__
from app.api.bank_finality import router as bank_finality_router
from app.api.body_limit import RequestBodyLimit, post_to
from app.api.demo import router as demo_router
from app.api.imports import IMPORTS_PATH
from app.api.imports import router as imports_router
from app.api.reconciliation import router as reconciliation_router
from app.api.review import router as review_router
from app.config import AppEnv, Settings, get_settings
from app.storage.database import create_database_engine, create_schema

MULTIPART_OVERHEAD_BYTES = 8 * 1024
"""How much a multipart envelope may add on top of the document itself.

A request carries boundaries, part headers and the two declared form fields as
well as the file, so the request budget has to be a little larger than the file
limit or a document of exactly the permitted size could not be sent. The budget
is therefore approximate by design, and it is not the file limit: a document
between the two is refused by the exact byte count in `read_bounded`."""


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
        engine = create_database_engine(resolved.resolved_database_url)
        # A Vercel function has no separate long-lived process in which an
        # operator can run migrations before accepting requests. Managed
        # databases are therefore brought to head on cold start. The migration
        # runner serialises PostgreSQL upgrades, so concurrent function starts
        # do not race to write the Alembic revision.
        if resolved.database_url is not None:
            create_schema(engine)

    application = FastAPI(
        title="Settlement Witness",
        version=__version__,
        description=(
            "Evidence-complete payment reconciliation. Shared synthetic demo "
            "backend: no authentication, no multi-tenancy, no merchant data."
        ),
    )
    application.state.engine = engine
    application.state.settings = resolved

    application.add_middleware(
        RequestBodyLimit,
        max_bytes=resolved.max_upload_bytes + MULTIPART_OVERHEAD_BYTES,
        applies_to=post_to(IMPORTS_PATH),
    )

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
    application.include_router(review_router)
    application.include_router(bank_finality_router)
    application.include_router(demo_router)
    return application


app = create_app()
