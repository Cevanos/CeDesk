"""The CeDesk API application."""

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException

from cedesk.db import make_engine, make_sessionmaker
from cedesk.helpdesk.api import auth as auth_api
from cedesk.helpdesk.api import catalog as catalog_api
from cedesk.helpdesk.api import members as members_api
from cedesk.helpdesk.api import messages as messages_api
from cedesk.helpdesk.api import routing as routing_api
from cedesk.helpdesk.api import tickets as tickets_api
from cedesk.helpdesk.service.errors import ServiceError
from cedesk.settings import Settings, get_settings

logger = logging.getLogger("cedesk")
READY_TIMEOUT_SECONDS = 2.0

HTTP_CODES = {
    401: "NotSignedIn",
    403: "Forbidden",
    404: "NotFound",
    405: "MethodNotAllowed",
    409: "Conflict",
    422: "Invalid",
}
# Deadlock, serialization failure, lock not available: losing a race, safe to retry.
RETRYABLE_SQLSTATES = {"40P01", "40001", "55P03"}


def _error(
    status: int,
    code: str,
    message: str,
    *,
    details: list[dict[str, str]] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    error: dict[str, object] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse({"error": error}, status_code=status, headers=headers)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url)
        app.state.engine = engine
        app.state.sessionmaker = make_sessionmaker(engine)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="CeDesk API",
        version="0.1.0",
        description="Help desk backend: tickets, routing, SLA, macros and rules.",
        lifespan=lifespan,
    )
    app.state.settings = settings

    # Every error has one shape (README, "Errors"):
    #   {"error": {"code": "...", "message": "...", "details": [...]}}
    # `details` appears only on validation errors, one entry per field.

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        return _error(exc.status, type(exc).__name__, exc.message, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # The submitted values are left out: they can hold passwords or customer data.
        details = [
            {
                "field": ".".join(str(p) for p in e["loc"][1:]) or str(e["loc"][0]),
                "location": str(e["loc"][0]),
                "message": e["msg"],
            }
            for e in exc.errors()
        ]
        return _error(422, "Invalid", "Some fields are not valid.", details=details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = HTTP_CODES.get(exc.status_code, "Error")
        return _error(exc.status_code, code, str(exc.detail), headers=exc.headers)

    @app.exception_handler(DBAPIError)
    async def database_error(request: Request, exc: DBAPIError) -> JSONResponse:
        sqlstate = getattr(exc.orig, "sqlstate", None)
        if sqlstate in RETRYABLE_SQLSTATES:
            logger.warning("request lost a database race: %s", sqlstate)
            return _error(409, "Conflict", "Someone changed this at the same moment. Try again.")
        logger.error("database error: %s %s", type(exc.orig).__name__, sqlstate)
        return _error(500, "Internal", "Something went wrong.")

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled error: %s", type(exc).__name__)
        return _error(500, "Internal", "Something went wrong.")

    app.include_router(auth_api.router, prefix="/api/v1")
    app.include_router(members_api.router, prefix="/api/v1")
    app.include_router(tickets_api.router, prefix="/api/v1")
    app.include_router(catalog_api.router, prefix="/api/v1")
    app.include_router(messages_api.router, prefix="/api/v1")
    app.include_router(routing_api.router, prefix="/api/v1")

    @app.get("/healthz", tags=["health"], summary="The process is up")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/readyz",
        tags=["health"],
        summary="The database answers",
        responses={503: {"description": "The database did not answer within 2 seconds"}},
    )
    async def readyz() -> JSONResponse:
        try:
            async with asyncio.timeout(READY_TIMEOUT_SECONDS):
                async with app.state.engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
        except Exception as exc:
            # Only the error type is logged: driver messages can carry host details.
            logger.warning("readiness check failed: %s", type(exc).__name__)
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok", "edition": settings.edition.value})

    return app


app = create_app()
