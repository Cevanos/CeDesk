"""Which default saved replies, rules and macros a workspace already got (by shortcut or
name). Always run inside a `workspace_session`."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def given(session: AsyncSession) -> set[tuple[str, str]]:
    result = await session.execute(text("SELECT kind, key FROM hd_default_given"))
    return {(str(r[0]), str(r[1])) for r in result.all()}


async def mark_given(session: AsyncSession, workspace_id: UUID, kind: str, key: str) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_default_given (workspace_id, kind, key) VALUES (:ws, :kind, :key) "
            "ON CONFLICT DO NOTHING"
        ),
        {"ws": workspace_id, "kind": kind, "key": key},
    )
