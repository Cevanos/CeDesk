"""The CeDesk API application."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from cedesk.db import make_engine, make_sessionmaker
from cedesk.helpdesk.api import auth as auth_api
from cedesk.helpdesk.api import catalog as catalog_api
from cedesk.helpdesk.api import members as members_api
from cedesk.helpdesk.api import messages as messages_api
from cedesk.helpdesk.api import tickets as tickets_api
from cedesk.helpdesk.service.errors import ServiceError
from cedesk.settings import Settings, get_settings

logger = logging.getLogger("cedesk")
READY_TIMEOUT_SECONDS = 2.0


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

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        body = {"error": {"code": type(exc).__name__, "message": exc.message}}
        return JSONResponse(body, status_code=exc.status, headers=headers)

    app.include_router(auth_api.router, prefix="/api/v1")
    app.include_router(members_api.router, prefix="/api/v1")
    app.include_router(tickets_api.router, prefix="/api/v1")
    app.include_router(catalog_api.router, prefix="/api/v1")
    app.include_router(messages_api.router, prefix="/api/v1")

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
