"""SQL for routing: teams with their agents' state and load. Run inside a `workspace_session`."""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.domain.routing import DEFAULT_CAPS, ChannelGroup

Row = RowMapping
CAP_COLUMNS = ("cap_email", "cap_chat", "cap_messaging")

# Each cap is the agent's own limit, else the workspace default, else the md default
# (`:d_email` and friends, from `DEFAULT_CAPS`). A member with no hd_agent_state row
# is available on every channel.
_STATE = """
    coalesce(s.status, 'available') AS status,
    coalesce(s.channels, '{email,chat,messaging}') AS channels,
    coalesce(s.cap_email, d.cap_email, CAST(:d_email AS integer)) AS cap_email,
    coalesce(s.cap_chat, d.cap_chat, CAST(:d_chat AS integer)) AS cap_chat,
    coalesce(s.cap_messaging, d.cap_messaging, CAST(:d_messaging AS integer)) AS cap_messaging,
    array_remove(ARRAY[
        CASE WHEN s.cap_email IS NOT NULL THEN 'email' END,
        CASE WHEN s.cap_chat IS NOT NULL THEN 'chat' END,
        CASE WHEN s.cap_messaging IS NOT NULL THEN 'messaging' END], NULL) AS cap_overrides,
    coalesce(s.skills, '{}') AS skills,
    (SELECT coalesce(jsonb_object_agg(o.channel, o.n), '{}')
       FROM (SELECT t.channel, count(*) AS n FROM hd_ticket t
             WHERE t.assignee_id = m.id AND t.status <> 'resolved'
               AND t.id IS DISTINCT FROM CAST(:exclude AS uuid)
             GROUP BY t.channel) o) AS open_by_channel
"""


# Fixed strings joined once at import; every value is a bound parameter.
_JOINS = (
    " LEFT JOIN hd_agent_state s ON s.member_id = m.id"
    " LEFT JOIN hd_routing_defaults d ON d.workspace_id = m.workspace_id"
)
_TEAM_AGENTS = (
    "SELECT m.id, m.name, m.role, m.is_active, "  # noqa: S608
    + _STATE
    + " FROM hd_team_member tm JOIN member m ON m.id = tm.member_id"
    + _JOINS
    + " WHERE tm.team_id = :team ORDER BY tm.position, m.id"
)
_AGENTS = (
    "SELECT m.id, m.name, m.role, m.is_active, "  # noqa: S608
    + _STATE
    + " FROM member m"
    + _JOINS
)
_MD_DEFAULTS = {
    "d_email": DEFAULT_CAPS[ChannelGroup.email],
    "d_chat": DEFAULT_CAPS[ChannelGroup.chat],
    "d_messaging": DEFAULT_CAPS[ChannelGroup.messaging],
}


async def lock_routing(session: AsyncSession) -> None:
    """One routing decision at a time per workspace, until the transaction ends.

    An agent can be in several teams, so locking only the team let two teams fill the
    same agent past their limits at the same moment (B6). Ticket creates already take
    turns on the workspace's number counter, so this adds no new wait there."""
    await session.execute(
        text(
            "SELECT pg_advisory_xact_lock(hashtext('cedesk.routing'), "
            "hashtext(current_setting('cedesk.workspace_id')))"
        )
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
        {"team": team_id, "exclude": exclude_ticket, **_MD_DEFAULTS},
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
        {"exclude": None, **_MD_DEFAULTS},
    )
    return result.mappings().all()


async def agent(session: AsyncSession, member_id: UUID) -> Row | None:
    result = await session.execute(
        text(_AGENTS + " WHERE m.id = :id"),
        {"id": member_id, "exclude": None, **_MD_DEFAULTS},
    )
    return result.mappings().first()


async def save_state(
    session: AsyncSession, workspace_id: UUID, member_id: UUID, values: dict[str, Any]
) -> None:
    """Creates the row if needed, then applies only the keys in `values`.

    A cap set to None goes back to following the workspace default."""
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
            "cap_email = CASE WHEN :set_cap_email "
            "THEN CAST(:cap_email AS integer) ELSE cap_email END, "
            "cap_chat = CASE WHEN :set_cap_chat "
            "THEN CAST(:cap_chat AS integer) ELSE cap_chat END, "
            "cap_messaging = CASE WHEN :set_cap_messaging "
            "THEN CAST(:cap_messaging AS integer) ELSE cap_messaging END, "
            "skills = coalesce(CAST(:skills AS text[]), skills), updated_at = now() "
            "WHERE member_id = :m"
        ),
        {
            "m": member_id,
            "status": values.get("status"),
            "channels": values.get("channels"),
            "skills": values.get("skills"),
            **{c: values.get(c) for c in CAP_COLUMNS},
            **{f"set_{c}": c in values for c in CAP_COLUMNS},
        },
    )


# Workspace defaults (md W5)


async def defaults(session: AsyncSession) -> Row:
    """The workspace's default capacity per channel; the md defaults if never set."""
    result = await session.execute(
        text(
            "SELECT coalesce(max(cap_email), CAST(:d_email AS integer)) AS cap_email, "
            "coalesce(max(cap_chat), CAST(:d_chat AS integer)) AS cap_chat, "
            "coalesce(max(cap_messaging), CAST(:d_messaging AS integer)) AS cap_messaging "
            "FROM hd_routing_defaults"
        ),
        _MD_DEFAULTS,
    )
    return result.mappings().one()


async def save_defaults(session: AsyncSession, workspace_id: UUID, values: dict[str, int]) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_routing_defaults (workspace_id, cap_email, cap_chat, cap_messaging) "
            "VALUES (:ws, coalesce(CAST(:cap_email AS integer), CAST(:d_email AS integer)), "
            "coalesce(CAST(:cap_chat AS integer), CAST(:d_chat AS integer)), "
            "coalesce(CAST(:cap_messaging AS integer), CAST(:d_messaging AS integer))) "
            "ON CONFLICT (workspace_id) DO UPDATE SET "
            "cap_email = coalesce(CAST(:cap_email AS integer), hd_routing_defaults.cap_email), "
            "cap_chat = coalesce(CAST(:cap_chat AS integer), hd_routing_defaults.cap_chat), "
            "cap_messaging = "
            "coalesce(CAST(:cap_messaging AS integer), hd_routing_defaults.cap_messaging), "
            "updated_at = now()"
        ),
        {"ws": workspace_id, **_MD_DEFAULTS, **{c: values.get(c) for c in CAP_COLUMNS}},
    )
