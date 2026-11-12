"""The workspace's sandbox row (md S10)."""

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

_COLUMNS = (
    "SELECT s.config, s.created_by, s.created_at, s.updated_by, s.updated_at, "
    "m.name AS updated_by_name FROM hd_sandbox s LEFT JOIN member m ON m.id = s.updated_by"
)


async def get(session: AsyncSession, *, lock: bool = False) -> RowMapping | None:
    sql = _COLUMNS + (" FOR UPDATE OF s" if lock else "")
    return (await session.execute(text(sql))).mappings().first()


async def insert(
    session: AsyncSession, workspace_id: UUID, config: dict[str, Any], member_id: UUID
) -> bool:
    """False when the workspace already has a sandbox."""
    result = await session.execute(
        text(
            "INSERT INTO hd_sandbox (workspace_id, config, created_by, updated_by) "
            "VALUES (:ws, CAST(:config AS jsonb), :by, :by) "
            "ON CONFLICT (workspace_id) DO NOTHING RETURNING workspace_id"
        ),
        {"ws": workspace_id, "config": json.dumps(config), "by": member_id},
    )
    return result.first() is not None


async def save(session: AsyncSession, config: dict[str, Any], member_id: UUID) -> None:
    await session.execute(
        text(
            "UPDATE hd_sandbox SET config = CAST(:config AS jsonb), updated_by = :by, "
            "updated_at = now()"
        ),
        {"config": json.dumps(config), "by": member_id},
    )


async def delete(session: AsyncSession) -> bool:
    result = await session.execute(text("DELETE FROM hd_sandbox RETURNING workspace_id"))
    return result.first() is not None
