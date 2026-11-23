"""SQL for segments (feature-gap plan 2.6). Run inside a `workspace_session`."""

import json
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

Row = RowMapping

_SELECT = (
    "SELECT g.id, g.name, g.filters, g.created_by, m.name AS created_by_name, "
    "g.created_at, g.updated_at FROM hd_segment g LEFT JOIN member m ON m.id = g.created_by "
)


async def all_segments(session: AsyncSession) -> Sequence[Row]:
    result = await session.execute(text(_SELECT + "ORDER BY lower(g.name), g.id"))
    return result.mappings().all()


async def get(session: AsyncSession, segment_id: UUID) -> Row | None:
    result = await session.execute(text(_SELECT + "WHERE g.id = :id"), {"id": segment_id})
    return result.mappings().first()


async def count(session: AsyncSession) -> int:
    result = await session.execute(text("SELECT count(*) FROM hd_segment"))
    return int(result.scalar_one())


async def using_field(session: AsyncSession, key: str) -> list[str]:
    """Names of the segments that filter on this contact field."""
    result = await session.execute(
        text("SELECT name FROM hd_segment WHERE filters->'attributes' ? :k ORDER BY name"),
        {"k": key},
    )
    return [str(r[0]) for r in result.all()]


async def insert(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    name: str,
    filters: Mapping[str, Any],
    created_by: UUID,
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_segment (workspace_id, name, filters, created_by) "
            "VALUES (:ws, :name, CAST(:filters AS jsonb), :by) RETURNING id"
        ),
        {"ws": workspace_id, "name": name, "filters": json.dumps(dict(filters)), "by": created_by},
    )
    return UUID(str(result.scalar_one()))


async def update(
    session: AsyncSession, segment_id: UUID, *, name: str, filters: Mapping[str, Any]
) -> None:
    await session.execute(
        text(
            "UPDATE hd_segment SET name = :name, filters = CAST(:filters AS jsonb), "
            "updated_at = now() WHERE id = :id"
        ),
        {"id": segment_id, "name": name, "filters": json.dumps(dict(filters))},
    )


async def delete(session: AsyncSession, segment_id: UUID) -> bool:
    result = await session.execute(
        text("DELETE FROM hd_segment WHERE id = :id RETURNING id"), {"id": segment_id}
    )
    return result.first() is not None
