"""SQL for saved ticket views (feature-gap plan 1.2). Run inside a `workspace_session`."""

import json
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

Row = RowMapping

_SELECT = (
    "SELECT v.id, v.name, v.filters, v.shared, v.owner_id, m.name AS owner_name, "
    "v.created_at, v.updated_at FROM hd_saved_view v JOIN member m ON m.id = v.owner_id "
)


async def visible(session: AsyncSession, member_id: UUID) -> Sequence[Row]:
    """The member's own personal views and every shared one: shared first, then by name."""
    result = await session.execute(
        text(
            _SELECT + "WHERE v.shared OR v.owner_id = :me "
            "ORDER BY v.shared DESC, lower(v.name), v.id"
        ),
        {"me": member_id},
    )
    return result.mappings().all()


async def get(session: AsyncSession, view_id: UUID) -> Row | None:
    result = await session.execute(text(_SELECT + "WHERE v.id = :id"), {"id": view_id})
    return result.mappings().first()


async def count_personal(session: AsyncSession, member_id: UUID) -> int:
    result = await session.execute(
        text("SELECT count(*) FROM hd_saved_view WHERE owner_id = :me AND NOT shared"),
        {"me": member_id},
    )
    return int(result.scalar_one())


async def count_shared(session: AsyncSession) -> int:
    result = await session.execute(text("SELECT count(*) FROM hd_saved_view WHERE shared"))
    return int(result.scalar_one())


async def insert(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    owner_id: UUID,
    name: str,
    filters: Mapping[str, Any],
    shared: bool,
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_saved_view (workspace_id, owner_id, name, filters, shared) "
            "VALUES (:ws, :owner, :name, CAST(:filters AS jsonb), :shared) RETURNING id"
        ),
        {
            "ws": workspace_id,
            "owner": owner_id,
            "name": name,
            "filters": json.dumps(dict(filters)),
            "shared": shared,
        },
    )
    return UUID(str(result.scalar_one()))


async def update(
    session: AsyncSession,
    view_id: UUID,
    *,
    name: str,
    filters: Mapping[str, Any],
    shared: bool,
) -> None:
    await session.execute(
        text(
            "UPDATE hd_saved_view SET name = :name, filters = CAST(:filters AS jsonb), "
            "shared = :shared, updated_at = now() WHERE id = :id"
        ),
        {"id": view_id, "name": name, "filters": json.dumps(dict(filters)), "shared": shared},
    )


async def delete(session: AsyncSession, view_id: UUID) -> bool:
    result = await session.execute(
        text("DELETE FROM hd_saved_view WHERE id = :id RETURNING id"), {"id": view_id}
    )
    return result.first() is not None
