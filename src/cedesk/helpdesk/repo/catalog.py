"""SQL for teams and ticket types. Always run inside a `workspace_session`."""

import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

Row = RowMapping

_TEAM = """
    SELECT t.id, t.name, t.auto_assign, t.max_open_per_agent,
           coalesce(array_agg(m.member_id ORDER BY m.position, m.member_id)
                    FILTER (WHERE m.member_id IS NOT NULL), '{}') AS member_ids
    FROM hd_team t LEFT JOIN hd_team_member m ON m.team_id = t.id
"""


async def list_teams(session: AsyncSession) -> Sequence[Row]:
    result = await session.execute(text(_TEAM + " GROUP BY t.id ORDER BY t.name"))
    return result.mappings().all()


async def get_team(session: AsyncSession, team_id: UUID) -> Row | None:
    result = await session.execute(text(_TEAM + " WHERE t.id = :id GROUP BY t.id"), {"id": team_id})
    return result.mappings().first()


async def insert_team(
    session: AsyncSession, workspace_id: UUID, name: str, auto_assign: bool, max_open: int
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_team (workspace_id, name, auto_assign, max_open_per_agent) "
            "VALUES (:ws, :name, :auto, :max) RETURNING id"
        ),
        {"ws": workspace_id, "name": name, "auto": auto_assign, "max": max_open},
    )
    return UUID(str(result.scalar_one()))


async def update_team(
    session: AsyncSession,
    team_id: UUID,
    *,
    name: str | None,
    auto_assign: bool | None,
    max_open: int | None,
) -> bool:
    result = await session.execute(
        text(
            "UPDATE hd_team SET name = coalesce(:name, name), "
            "auto_assign = coalesce(:auto, auto_assign), "
            "max_open_per_agent = coalesce(:max, max_open_per_agent), updated_at = now() "
            "WHERE id = :id RETURNING id"
        ),
        {"id": team_id, "name": name, "auto": auto_assign, "max": max_open},
    )
    return result.first() is not None


async def set_team_members(
    session: AsyncSession, workspace_id: UUID, team_id: UUID, member_ids: Sequence[UUID]
) -> None:
    await session.execute(text("DELETE FROM hd_team_member WHERE team_id = :t"), {"t": team_id})
    if member_ids:
        await session.execute(
            text(
                "INSERT INTO hd_team_member (workspace_id, team_id, member_id, position) "
                "SELECT :ws, :t, o.id, o.pos FROM unnest(CAST(:ids AS uuid[])) "
                "WITH ORDINALITY AS o(id, pos)"
            ),
            {"ws": workspace_id, "t": team_id, "ids": list(member_ids)},
        )


async def delete_team(session: AsyncSession, team_id: UUID) -> bool:
    result = await session.execute(
        text("DELETE FROM hd_team WHERE id = :id RETURNING id"), {"id": team_id}
    )
    return result.first() is not None


async def active_members(session: AsyncSession, member_ids: Sequence[UUID]) -> set[UUID]:
    """The ids, of those given, that are active members of this workspace."""
    result = await session.execute(
        text("SELECT id FROM member WHERE id = ANY(CAST(:ids AS uuid[])) AND is_active"),
        {"ids": list(member_ids)},
    )
    return {UUID(str(r[0])) for r in result.all()}


_TYPE = "SELECT id, name, fields, position, required_skill FROM hd_ticket_type"


async def list_types(session: AsyncSession) -> Sequence[Row]:
    result = await session.execute(text(_TYPE + " ORDER BY position, name"))
    return result.mappings().all()


async def get_type(session: AsyncSession, type_id: UUID) -> Row | None:
    result = await session.execute(text(_TYPE + " WHERE id = :id"), {"id": type_id})
    return result.mappings().first()


async def insert_type(
    session: AsyncSession,
    workspace_id: UUID,
    name: str,
    fields: list[dict[str, Any]],
    required_skill: str | None,
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_ticket_type (workspace_id, name, fields, required_skill, position) "
            "VALUES (:ws, :name, CAST(:fields AS jsonb), :skill, "
            "(SELECT coalesce(max(position), -1) + 1 FROM hd_ticket_type)) RETURNING id"
        ),
        {"ws": workspace_id, "name": name, "fields": json.dumps(fields), "skill": required_skill},
    )
    return UUID(str(result.scalar_one()))


async def update_type(
    session: AsyncSession,
    type_id: UUID,
    *,
    name: str | None,
    fields: list[dict[str, Any]] | None,
    required_skill: str | None,
    clear_skill: bool,
) -> bool:
    result = await session.execute(
        text(
            "UPDATE hd_ticket_type SET name = coalesce(:name, name), "
            "fields = coalesce(CAST(:fields AS jsonb), fields), "
            "required_skill = CASE WHEN :clear THEN NULL "
            "ELSE coalesce(:skill, required_skill) END, updated_at = now() "
            "WHERE id = :id RETURNING id"
        ),
        {
            "id": type_id,
            "name": name,
            "fields": None if fields is None else json.dumps(fields),
            "skill": required_skill,
            "clear": clear_skill,
        },
    )
    return result.first() is not None


async def delete_type(session: AsyncSession, type_id: UUID) -> bool:
    result = await session.execute(
        text("DELETE FROM hd_ticket_type WHERE id = :id RETURNING id"), {"id": type_id}
    )
    return result.first() is not None


async def reorder_types(session: AsyncSession, type_ids: Sequence[UUID]) -> None:
    await session.execute(
        text(
            "UPDATE hd_ticket_type t SET position = o.pos - 1, updated_at = now() "
            "FROM unnest(CAST(:ids AS uuid[])) WITH ORDINALITY AS o(id, pos) WHERE t.id = o.id"
        ),
        {"ids": list(type_ids)},
    )
