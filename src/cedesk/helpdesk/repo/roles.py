"""SQL for custom roles (md A3). Always run inside a `workspace_session`."""

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

Row = RowMapping

RIGHTS = ("reply", "assign_any", "reports", "settings", "approve")

_ROLE = (
    "SELECT r.id, r.name, r.name AS role_name, r.reply, r.assign_any, r.reports, r.settings, "
    "r.approve, r.scope, r.team_id, t.name AS team_name, r.created_at, r.updated_at, "
    "(SELECT count(*) FROM member m WHERE m.role_id = r.id) AS member_count "
    "FROM hd_role r LEFT JOIN hd_team t ON t.id = r.team_id "
)


async def list_roles(session: AsyncSession) -> Sequence[Row]:
    result = await session.execute(text(_ROLE + "ORDER BY lower(r.name), r.id"))
    return result.mappings().all()


async def get_role(session: AsyncSession, role_id: UUID, *, lock: bool = False) -> Row | None:
    sql = _ROLE + "WHERE r.id = :id" + (" FOR UPDATE OF r" if lock else "")
    result = await session.execute(text(sql), {"id": role_id})
    return result.mappings().first()


async def insert_role(session: AsyncSession, workspace_id: UUID, values: Mapping[str, Any]) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_role (workspace_id, name, reply, assign_any, reports, settings, "
            "approve, scope, team_id) VALUES (:ws, :name, :reply, :assign_any, :reports, "
            ":settings, :approve, :scope, :team_id) RETURNING id"
        ),
        {"ws": workspace_id, **values},
    )
    return UUID(str(result.scalar_one()))


async def update_role(session: AsyncSession, role_id: UUID, values: Mapping[str, Any]) -> None:
    await session.execute(
        text(
            "UPDATE hd_role SET name = :name, reply = :reply, assign_any = :assign_any, "
            "reports = :reports, settings = :settings, approve = :approve, scope = :scope, "
            "team_id = :team_id, updated_at = now() WHERE id = :id"
        ),
        {"id": role_id, **values},
    )


async def delete_role(session: AsyncSession, role_id: UUID) -> None:
    await session.execute(text("DELETE FROM hd_role WHERE id = :id"), {"id": role_id})


async def team_exists(session: AsyncSession, team_id: UUID) -> bool:
    result = await session.execute(text("SELECT 1 FROM hd_team WHERE id = :id"), {"id": team_id})
    return result.first() is not None


async def roles_of_team(session: AsyncSession, team_id: UUID) -> list[str]:
    result = await session.execute(
        text("SELECT name FROM hd_role WHERE team_id = :id ORDER BY lower(name)"), {"id": team_id}
    )
    return [str(n) for n in result.scalars().all()]
