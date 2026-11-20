"""SQL for saved reports (feature-gap plan 3.2). Run inside a `workspace_session`."""

import json
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

Row = RowMapping

_SELECT = (
    "SELECT r.id, r.name, r.definition, r.shared, r.owner_id, m.name AS owner_name, "
    "r.created_at, r.updated_at FROM hd_saved_report r JOIN member m ON m.id = r.owner_id "
)


async def visible(session: AsyncSession, member_id: UUID) -> Sequence[Row]:
    """The member's own personal reports and every shared one: shared first, then by name."""
    result = await session.execute(
        text(
            _SELECT + "WHERE r.shared OR r.owner_id = :me "
            "ORDER BY r.shared DESC, lower(r.name), r.id"
        ),
        {"me": member_id},
    )
    return result.mappings().all()


async def get(session: AsyncSession, report_id: UUID) -> Row | None:
    result = await session.execute(text(_SELECT + "WHERE r.id = :id"), {"id": report_id})
    return result.mappings().first()


async def count_personal(session: AsyncSession, member_id: UUID) -> int:
    result = await session.execute(
        text("SELECT count(*) FROM hd_saved_report WHERE owner_id = :me AND NOT shared"),
        {"me": member_id},
    )
    return int(result.scalar_one())


async def count_shared(session: AsyncSession) -> int:
    result = await session.execute(text("SELECT count(*) FROM hd_saved_report WHERE shared"))
    return int(result.scalar_one())


async def insert(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    owner_id: UUID,
    name: str,
    definition: Mapping[str, Any],
    shared: bool,
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_saved_report (workspace_id, owner_id, name, definition, shared) "
            "VALUES (:ws, :owner, :name, CAST(:definition AS jsonb), :shared) RETURNING id"
        ),
        {
            "ws": workspace_id,
            "owner": owner_id,
            "name": name,
            "definition": json.dumps(dict(definition)),
            "shared": shared,
        },
    )
    return UUID(str(result.scalar_one()))


async def update(
    session: AsyncSession,
    report_id: UUID,
    *,
    name: str,
    definition: Mapping[str, Any],
    shared: bool,
) -> None:
    await session.execute(
        text(
            "UPDATE hd_saved_report SET name = :name, definition = CAST(:definition AS jsonb), "
            "shared = :shared, updated_at = now() WHERE id = :id"
        ),
        {
            "id": report_id,
            "name": name,
            "definition": json.dumps(dict(definition)),
            "shared": shared,
        },
    )


async def delete(session: AsyncSession, report_id: UUID) -> bool:
    result = await session.execute(
        text("DELETE FROM hd_saved_report WHERE id = :id RETURNING id"), {"id": report_id}
    )
    return result.first() is not None
