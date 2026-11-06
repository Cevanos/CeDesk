"""SQL for routing: teams with their agents' state and load. Run inside a `workspace_session`."""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

Row = RowMapping

# Defaults for a member with no hd_agent_state row; they match the table defaults.
_STATE = """
    coalesce(s.status, 'available') AS status,
    coalesce(s.channels, '{email,chat,messaging}') AS channels,
    coalesce(s.cap_email, 5) AS cap_email,
    coalesce(s.cap_chat, 2) AS cap_chat,
    coalesce(s.cap_messaging, 3) AS cap_messaging,
    coalesce(s.skills, '{}') AS skills,
    (SELECT coalesce(jsonb_object_agg(o.channel, o.n), '{}')
       FROM (SELECT t.channel, count(*) AS n FROM hd_ticket t
             WHERE t.assignee_id = m.id AND t.status <> 'resolved'
               AND t.id IS DISTINCT FROM CAST(:exclude AS uuid)
             GROUP BY t.channel) o) AS open_by_channel
"""


# Fixed strings joined once at import; every value is a bound parameter.
_TEAM_AGENTS = (
    "SELECT m.id, m.name, m.role, m.is_active, "  # noqa: S608
    + _STATE
    + " FROM hd_team_member tm JOIN member m ON m.id = tm.member_id "
    "LEFT JOIN hd_agent_state s ON s.member_id = m.id "
    "WHERE tm.team_id = :team ORDER BY tm.position, m.id"
)
_AGENTS = (
    "SELECT m.id, m.name, m.role, m.is_active, "  # noqa: S608
    + _STATE
    + " FROM member m LEFT JOIN hd_agent_state s ON s.member_id = m.id"
)


async def lock_team(session: AsyncSession, team_id: UUID) -> Row | None:
    """Locks the team, so tickets routed to it at the same moment take turns."""
    result = await session.execute(
        text(
            "SELECT id, name, auto_assign, max_open_per_agent, rr_pointer "
            "FROM hd_team WHERE id = :id FOR UPDATE"
        ),
        {"id": team_id},
    )
    return result.mappings().first()


async def team_agents(
    session: AsyncSession, team_id: UUID, exclude_ticket: UUID | None
) -> Sequence[Row]:
    """The team's members in their listed order, with state and open tickets per channel.

    `exclude_ticket` is left out of the counts (the ticket being routed)."""
    result = await session.execute(
        text(_TEAM_AGENTS),
        {"team": team_id, "exclude": exclude_ticket},
    )
    return result.mappings().all()


async def move_pointer(session: AsyncSession, team_id: UUID, member_id: UUID) -> None:
    await session.execute(
        text("UPDATE hd_team SET rr_pointer = :m, updated_at = now() WHERE id = :id"),
        {"id": team_id, "m": member_id},
    )


async def agents(session: AsyncSession) -> Sequence[Row]:
    """Every member with routing state and load, for the Teams & agents screen."""
    result = await session.execute(
        text(_AGENTS + " ORDER BY m.name, m.id"),
        {"exclude": None},
    )
    return result.mappings().all()


async def agent(session: AsyncSession, member_id: UUID) -> Row | None:
    result = await session.execute(
        text(_AGENTS + " WHERE m.id = :id"),
        {"id": member_id, "exclude": None},
    )
    return result.mappings().first()


async def save_state(
    session: AsyncSession, workspace_id: UUID, member_id: UUID, values: dict[str, Any]
) -> None:
    """Creates the row with defaults if needed, then applies only the given values."""
    await session.execute(
        text(
            "INSERT INTO hd_agent_state (workspace_id, member_id) VALUES (:ws, :m) "
            "ON CONFLICT (workspace_id, member_id) DO NOTHING"
        ),
        {"ws": workspace_id, "m": member_id},
    )
    await session.execute(
        text(
            "UPDATE hd_agent_state SET "
            "status = coalesce(CAST(:status AS text), status), "
            "channels = coalesce(CAST(:channels AS text[]), channels), "
            "cap_email = coalesce(CAST(:cap_email AS integer), cap_email), "
            "cap_chat = coalesce(CAST(:cap_chat AS integer), cap_chat), "
            "cap_messaging = coalesce(CAST(:cap_messaging AS integer), cap_messaging), "
            "skills = coalesce(CAST(:skills AS text[]), skills), updated_at = now() "
            "WHERE member_id = :m"
        ),
        {
            "m": member_id,
            "status": values.get("status"),
            "channels": values.get("channels"),
            "cap_email": values.get("cap_email"),
            "cap_chat": values.get("cap_chat"),
            "cap_messaging": values.get("cap_messaging"),
            "skills": values.get("skills"),
        },
    )
