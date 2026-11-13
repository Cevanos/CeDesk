"""SQL for saved replies. Always run inside a `workspace_session`."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

Row = RowMapping

_COLUMNS = "SELECT id, shortcut, body, source, created_at, updated_at FROM hd_saved_reply"


_INSERT_SQL = (
    "INSERT INTO hd_saved_reply (workspace_id, shortcut, body, source, created_by) "
    "VALUES (:ws, :shortcut, :body, :source, :by)"
)
_INSERT = text(_INSERT_SQL + " RETURNING id")
_INSERT_SKIP = text(_INSERT_SQL + " ON CONFLICT (workspace_id, shortcut) DO NOTHING RETURNING id")


async def list_replies(session: AsyncSession, prefix: str | None) -> Sequence[Row]:
    """All saved replies by shortcut, or those whose shortcut starts with `prefix`."""
    if prefix:
        result = await session.execute(
            text(_COLUMNS + " WHERE starts_with(shortcut, :prefix) ORDER BY shortcut"),
            {"prefix": prefix},
        )
    else:
        result = await session.execute(text(_COLUMNS + " ORDER BY shortcut"))
    return result.mappings().all()


async def get_reply(session: AsyncSession, reply_id: UUID) -> Row | None:
    result = await session.execute(text(_COLUMNS + " WHERE id = :id"), {"id": reply_id})
    return result.mappings().first()


async def count(session: AsyncSession) -> int:
    result = await session.execute(text("SELECT count(*) FROM hd_saved_reply"))
    return int(result.scalar_one())


async def shortcuts(session: AsyncSession) -> set[str]:
    result = await session.execute(text("SELECT shortcut FROM hd_saved_reply"))
    return {str(r[0]) for r in result.all()}


async def insert_reply(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    shortcut: str,
    body: str,
    source: str,
    created_by: UUID | None,
    skip_existing: bool = False,
) -> UUID | None:
    """The new id. With `skip_existing`, a taken shortcut is left alone and None comes back
    (used to add the defaults to a workspace more than once safely)."""
    result = await session.execute(
        _INSERT_SKIP if skip_existing else _INSERT,
        {
            "ws": workspace_id,
            "shortcut": shortcut,
            "body": body,
            "source": source,
            "by": created_by,
        },
    )
    value = result.scalar_one_or_none()
    return None if value is None else UUID(str(value))


async def update_reply(
    session: AsyncSession, reply_id: UUID, *, shortcut: str | None, body: str | None
) -> bool:
    result = await session.execute(
        text(
            "UPDATE hd_saved_reply SET shortcut = coalesce(:shortcut, shortcut), "
            "body = coalesce(:body, body), updated_at = now() WHERE id = :id RETURNING id"
        ),
        {"id": reply_id, "shortcut": shortcut, "body": body},
    )
    return result.first() is not None


async def delete_reply(session: AsyncSession, reply_id: UUID) -> bool:
    result = await session.execute(
        text("DELETE FROM hd_saved_reply WHERE id = :id RETURNING id"), {"id": reply_id}
    )
    return result.first() is not None


# ---- an app's own reply (Calendly's /book, md W19) ---------------------------------------


async def by_source(session: AsyncSession, source: str, source_id: str) -> Row | None:
    result = await session.execute(
        text(_COLUMNS + " WHERE source = :source AND source_id = :sid"),
        {"source": source, "sid": source_id},
    )
    return result.mappings().first()


async def insert_from(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    shortcut: str,
    body: str,
    source: str,
    source_id: str,
    created_by: UUID | None,
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_saved_reply (workspace_id, shortcut, body, source, source_id, "
            "created_by) VALUES (:ws, :shortcut, :body, :source, :sid, :by) RETURNING id"
        ),
        {
            "ws": workspace_id,
            "shortcut": shortcut,
            "body": body,
            "source": source,
            "sid": source_id,
            "by": created_by,
        },
    )
    return UUID(str(result.scalar_one()))


async def make_manual(session: AsyncSession, reply_id: UUID) -> bool:
    """An app's reply the Admin edited, kept when the app goes: now an ordinary one."""
    result = await session.execute(
        text(
            "UPDATE hd_saved_reply SET source = 'manual', source_id = NULL, updated_at = now() "
            "WHERE id = :id RETURNING id"
        ),
        {"id": reply_id},
    )
    return result.first() is not None
