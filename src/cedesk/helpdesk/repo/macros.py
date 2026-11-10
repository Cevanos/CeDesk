"""SQL for macros. Always run inside a `workspace_session`."""

import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

Row = RowMapping

_COLUMNS = "SELECT id, name, position, steps, created_at, updated_at FROM hd_macro"
_INSERT_SQL = (
    "INSERT INTO hd_macro (workspace_id, name, steps, created_by, position) "
    "VALUES (:ws, :name, CAST(:steps AS jsonb), :by, "
    "(SELECT coalesce(max(position), -1) + 1 FROM hd_macro))"
)
_INSERT = text(_INSERT_SQL + " RETURNING id")
_INSERT_SKIP = text(_INSERT_SQL + " ON CONFLICT (workspace_id, name) DO NOTHING RETURNING id")


async def list_macros(session: AsyncSession) -> Sequence[Row]:
    result = await session.execute(text(_COLUMNS + " ORDER BY position, created_at, id"))
    return result.mappings().all()


async def get_macro(session: AsyncSession, macro_id: UUID) -> Row | None:
    result = await session.execute(text(_COLUMNS + " WHERE id = :id"), {"id": macro_id})
    return result.mappings().first()


async def count(session: AsyncSession) -> int:
    result = await session.execute(text("SELECT count(*) FROM hd_macro"))
    return int(result.scalar_one())


async def insert_macro(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    name: str,
    steps: list[dict[str, Any]],
    created_by: UUID | None,
    skip_existing: bool = False,
) -> UUID | None:
    """Added last. With `skip_existing`, a macro with that name is left alone (None)."""
    result = await session.execute(
        _INSERT_SKIP if skip_existing else _INSERT,
        {"ws": workspace_id, "name": name, "steps": json.dumps(steps), "by": created_by},
    )
    value = result.scalar_one_or_none()
    return None if value is None else UUID(str(value))


async def update_macro(
    session: AsyncSession, macro_id: UUID, *, name: str, steps: list[dict[str, Any]]
) -> bool:
    result = await session.execute(
        text(
            "UPDATE hd_macro SET name = :name, steps = CAST(:steps AS jsonb), "
            "updated_at = now() WHERE id = :id RETURNING id"
        ),
        {"id": macro_id, "name": name, "steps": json.dumps(steps)},
    )
    return result.first() is not None


async def delete_macro(session: AsyncSession, macro_id: UUID) -> bool:
    result = await session.execute(
        text("DELETE FROM hd_macro WHERE id = :id RETURNING id"), {"id": macro_id}
    )
    return result.first() is not None


async def lock_macros(session: AsyncSession) -> list[UUID]:
    """Every macro id, locked, so two reorders can't interleave."""
    result = await session.execute(text("SELECT id FROM hd_macro ORDER BY id FOR UPDATE"))
    return [UUID(str(r[0])) for r in result.all()]


async def reorder_macros(session: AsyncSession, macro_ids: Sequence[UUID]) -> None:
    await session.execute(
        text(
            "UPDATE hd_macro m SET position = o.pos - 1, updated_at = now() "
            "FROM unnest(CAST(:ids AS uuid[])) WITH ORDINALITY AS o(id, pos) WHERE m.id = o.id"
        ),
        {"ids": list(macro_ids)},
    )
