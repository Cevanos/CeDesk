"""SQL for contact fields (feature-gap plan 2.3). Run inside a `workspace_session`."""

import json
from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

Row = RowMapping

_SELECT = (
    "SELECT id, key, label, kind, options, position, created_at, updated_at FROM hd_contact_field "
)


async def all_fields(session: AsyncSession) -> Sequence[Row]:
    result = await session.execute(text(_SELECT + "ORDER BY position, lower(label), key"))
    return result.mappings().all()


async def get(session: AsyncSession, field_id: UUID) -> Row | None:
    result = await session.execute(text(_SELECT + "WHERE id = :id"), {"id": field_id})
    return result.mappings().first()


async def count(session: AsyncSession) -> int:
    result = await session.execute(text("SELECT count(*) FROM hd_contact_field"))
    return int(result.scalar_one())


async def insert(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    key: str,
    label: str,
    kind: str,
    options: Sequence[str],
    position: int,
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_contact_field (workspace_id, key, label, kind, options, position) "
            "VALUES (:ws, :key, :label, :kind, CAST(:options AS jsonb), :position) RETURNING id"
        ),
        {
            "ws": workspace_id,
            "key": key,
            "label": label,
            "kind": kind,
            "options": json.dumps(list(options)),
            "position": position,
        },
    )
    return UUID(str(result.scalar_one()))


async def update(
    session: AsyncSession, field_id: UUID, *, label: str, options: Sequence[str], position: int
) -> None:
    await session.execute(
        text(
            "UPDATE hd_contact_field SET label = :label, options = CAST(:options AS jsonb), "
            "position = :position, updated_at = now() WHERE id = :id"
        ),
        {
            "id": field_id,
            "label": label,
            "options": json.dumps(list(options)),
            "position": position,
        },
    )


async def delete(session: AsyncSession, field_id: UUID) -> bool:
    result = await session.execute(
        text("DELETE FROM hd_contact_field WHERE id = :id RETURNING id"), {"id": field_id}
    )
    return result.first() is not None


async def drop_values(session: AsyncSession, key: str) -> int:
    """The field's value goes from every contact that has one."""
    result = await session.execute(
        text("UPDATE hd_contact SET attributes = attributes - :k WHERE attributes ? :k"),
        {"k": key},
    )
    return int(cast("CursorResult[Any]", result).rowcount)
