"""The CeDesk API application."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from cedesk.db import make_engine, make_sessionmaker
from cedesk.settings import Settings, get_settings


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

    @app.get("/healthz", tags=["health"], summary="The process is up")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["health"], summary="The database answers")
    async def readyz() -> dict[str, str]:
        async with app.state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok", "edition": settings.edition.value}

    return app


app = create_app()
