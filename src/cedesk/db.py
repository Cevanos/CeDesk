"""Database engine and the tenancy session.

Every query on tenant data runs inside `workspace_session`, which sets
`cedesk.workspace_id` for the transaction; the RLS policies on every tenant
table read that setting, so a missing or wrong workspace sees no rows.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

WORKSPACE_SETTING = "cedesk.workspace_id"


def make_engine(url: str) -> AsyncEngine:
    # hide_parameters: a statement's values (device tokens, emails...) never reach a log or
    # an error message; only the SQL does.
    return create_async_engine(url, pool_pre_ping=True, hide_parameters=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def workspace_session(
    sessionmaker: async_sessionmaker[AsyncSession], workspace_id: UUID
) -> AsyncIterator[AsyncSession]:
    """One transaction scoped to one workspace. Commits on success, rolls back on error."""
    async with sessionmaker() as session, session.begin():
        # `is_local = true`: the setting ends with the transaction and never leaks
        # to the next user of the pooled connection.
        await session.execute(
            text("SELECT set_config(:name, :value, true)"),
            {"name": WORKSPACE_SETTING, "value": str(workspace_id)},
        )
        yield session


@asynccontextmanager
async def notifications(engine: AsyncEngine, channel: str) -> AsyncIterator[asyncio.Event]:
    """An event set whenever someone runs `pg_notify(channel, ...)` (Postgres LISTEN), on
    one connection held for as long as this is open. If that connection drops, the event
    simply stops firing: whoever waits on it must also wake on a timer."""
    event = asyncio.Event()

    def wake(*_: object) -> None:
        event.set()

    async with engine.connect() as conn:
        raw = await conn.get_raw_connection()
        listener: Any = raw.driver_connection  # asyncpg.Connection
        await listener.add_listener(channel, wake)
        try:
            yield event
        finally:
            try:
                await listener.remove_listener(channel, wake)
            except Exception:  # noqa: S110  (the connection is gone already)
                pass
