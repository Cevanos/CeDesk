"""Auto-assignment for a ticket, and each agent's status, channels, capacity and skills."""

import re
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.routing import (
    Agent,
    AgentStatus,
    ChannelGroup,
    Pick,
    Team,
    channel_group,
    pick_agent,
)
from cedesk.helpdesk.domain.triage import SKILL_FOR_LANGUAGE
from cedesk.helpdesk.repo import catalog
from cedesk.helpdesk.repo import routing as repo
from cedesk.helpdesk.repo.routing import Row
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Forbidden, Invalid, NotFound
from cedesk.helpdesk.service.members import require

SKILL = re.compile(r"^[a-z0-9][a-z0-9 _-]{0,39}$")
MAX_SKILLS = 30


def _open_by_group(open_by_channel: Mapping[str, int]) -> dict[ChannelGroup, int]:
    counts: dict[ChannelGroup, int] = {}
    for channel, n in open_by_channel.items():
        group = channel_group(channel)
        counts[group] = counts.get(group, 0) + int(n)
    return counts


def _agent(row: Mapping[Any, Any]) -> Agent:
    return Agent(
        member_id=row["id"],
        name=str(row["name"]),
        is_active=bool(row["is_active"]),
        is_light=not row["takes_tickets"],
        outside_scope=not row.get("fits_team", True),
        status=AgentStatus(row["status"]),
        channels=frozenset(ChannelGroup(c) for c in row["channels"]),
        caps={
            ChannelGroup.email: int(row["cap_email"]),
            ChannelGroup.chat: int(row["cap_chat"]),
            ChannelGroup.messaging: int(row["cap_messaging"]),
        },
        skills=frozenset(row["skills"]),
        open_by_group=_open_by_group(row["open_by_channel"]),
    )


async def route(
    s: AsyncSession,
    *,
    ticket_id: UUID,
    team_id: UUID,
    channel: str,
    type_id: UUID | None,
) -> Pick:
    """Picks an agent in `team_id` for the ticket and moves the team's pointer to them.

    Runs inside the caller's transaction. The workspace's routing lock, then the team
    row, stay held until it commits, so load is read and assigned with no one in between."""
    await repo.lock_routing(s)
    team_row = await repo.lock_team(s, team_id)
    if team_row is None:
        raise Invalid("That team does not exist.")
    ticket_type = await catalog.get_type(s, type_id) if type_id else None
    pick = pick_agent(
        Team(
            name=str(team_row["name"]),
            auto_assign=bool(team_row["auto_assign"]),
            max_open_per_agent=int(team_row["max_open_per_agent"]),
            pointer=team_row["rr_pointer"],
        ),
        [_agent(r) for r in await repo.team_agents(s, team_id, ticket_id)],
        channel=channel,
        required_skill=ticket_type["required_skill"] if ticket_type else None,
        language_skill=SKILL_FOR_LANGUAGE.get(str(await repo.ticket_language(s, ticket_id))),
    )
    if pick.agent is not None:
        await repo.move_pointer(s, team_id, pick.agent.member_id)
    return pick


# Agent routing state


def _skills(raw: Sequence[str]) -> list[str]:
    skills = list(dict.fromkeys(" ".join(s.strip().lower().split()) for s in raw))
    if len(skills) > MAX_SKILLS or not all(SKILL.match(s) for s in skills):
        raise Invalid(f"Up to {MAX_SKILLS} skills of 1-40 letters, digits, spaces, - or _.")
    return skills


async def list_agents(sm: Sessionmaker, caller: Caller) -> Sequence[Row]:
    require(caller, Action.list_members)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await repo.agents(s)


async def set_my_status(sm: Sessionmaker, caller: Caller, status: AgentStatus) -> Row:
    """Available / On a break / Transfers only / Offline. Light agents have no status."""
    if not caller.access.takes_tickets:
        raise Forbidden("Your role takes no tickets, so it has no status.")
    async with workspace_session(sm, caller.workspace_id) as s:
        await repo.save_state(s, caller.workspace_id, caller.member_id, {"status": status.value})
        row = await repo.agent(s, caller.member_id)
    assert row is not None
    return row


async def set_routing(
    sm: Sessionmaker, caller: Caller, member_id: UUID, values: Mapping[str, Any]
) -> Row:
    """Admin: any of status, channels, cap_email, cap_chat, cap_messaging, skills.

    A cap of None means the agent follows the workspace default again."""
    require(caller, Action.settings)
    changes = dict(values)
    if "status" in changes:
        changes["status"] = AgentStatus(changes["status"]).value
    if "channels" in changes:
        changes["channels"] = sorted({ChannelGroup(c).value for c in changes["channels"]})
    if "skills" in changes:
        changes["skills"] = _skills(changes["skills"])
    async with workspace_session(sm, caller.workspace_id) as s:
        target = await repo.agent(s, member_id)
        if target is None:
            raise NotFound("Member not found.")
        if not target["takes_tickets"] and "status" in changes:
            raise Invalid("This member's role takes no tickets, so it has no status.")
        await repo.save_state(s, caller.workspace_id, member_id, changes)
        row = await repo.agent(s, member_id)
    assert row is not None
    return row


# Workspace defaults (md W5)


async def get_defaults(sm: Sessionmaker, caller: Caller) -> Row:
    require(caller, Action.list_members)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await repo.defaults(s)


async def set_defaults(sm: Sessionmaker, caller: Caller, values: Mapping[str, int]) -> Row:
    """Admin: the default capacity per channel for every agent without their own limit."""
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        await repo.save_defaults(s, caller.workspace_id, dict(values))
        return await repo.defaults(s)
