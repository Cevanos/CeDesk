"""Round-robin auto-assignment (md 6.2). Pure: the caller loads the team and its agents.

An agent can take the ticket when they are an active, non-light member, Available,
have the ticket's channel group on, are under their capacity for that group, and
are under the team's limit of open tickets. Agents with the required skill go first.
The pointer remembers who got the last ticket, so the next search starts after them.
Every skipped agent gets a reason, written on the ticket.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class AgentStatus(StrEnum):
    available = "available"
    on_break = "on_break"
    transfers_only = "transfers_only"  # takes transfers, not new tickets
    offline = "offline"


class ChannelGroup(StrEnum):
    email = "email"
    chat = "chat"
    messaging = "messaging"


STATUS_LABEL = {
    AgentStatus.available: "Available",
    AgentStatus.on_break: "On a break",
    AgentStatus.transfers_only: "Transfers only",
    AgentStatus.offline: "Offline",
}
GROUP_LABEL = {
    ChannelGroup.email: "Email",
    ChannelGroup.chat: "Website chat",
    ChannelGroup.messaging: "Messaging",
}
# md W5: a workspace's default capacity per channel until its Admin changes it.
DEFAULT_CAPS = {ChannelGroup.email: 5, ChannelGroup.chat: 2, ChannelGroup.messaging: 3}
CAP_MAX = 100
TEAM_MAX_OPEN = 50  # md W1: a team's maximum open tickets per agent is 1-50

# Email, portal, web form, API and phone tickets are worked like email; the chat widget is chat.
_GROUP_OF_CHANNEL = {"chat": ChannelGroup.chat, "whatsapp": ChannelGroup.messaging}


def channel_group(channel: str) -> ChannelGroup:
    return _GROUP_OF_CHANNEL.get(channel, ChannelGroup.email)


@dataclass(frozen=True)
class Agent:
    member_id: UUID
    name: str
    is_active: bool
    is_light: bool
    status: AgentStatus
    channels: frozenset[ChannelGroup]
    caps: Mapping[ChannelGroup, int]
    skills: frozenset[str]
    open_by_group: Mapping[ChannelGroup, int]
    # A custom role scoped to another team (A3): never routed this team's tickets.
    outside_scope: bool = False

    @property
    def open_total(self) -> int:
        return sum(self.open_by_group.values())


@dataclass(frozen=True)
class Team:
    name: str
    auto_assign: bool
    max_open_per_agent: int
    pointer: UUID | None


@dataclass(frozen=True)
class Pick:
    agent: Agent | None
    line: str
    skipped: tuple[str, ...]


def _why_not(agent: Agent, team: Team, group: ChannelGroup) -> str | None:
    if not agent.is_active:
        return f"{agent.name} is deactivated"
    if agent.is_light:
        return f"{agent.name} is a light agent"
    if agent.outside_scope:
        return f"{agent.name}'s role works on another team's tickets"
    if agent.status is not AgentStatus.available:
        return f"{agent.name} is {STATUS_LABEL[agent.status]}"
    if group not in agent.channels:
        return f"{agent.name} has {GROUP_LABEL[group]} off"
    cap = agent.caps.get(group, 0)
    if agent.open_by_group.get(group, 0) >= cap:
        return f"{agent.name} is full on {GROUP_LABEL[group]} ({cap})"
    if agent.open_total >= team.max_open_per_agent:
        return f"{agent.name} has {agent.open_total} open"
    return None


def _after_pointer(agents: Sequence[Agent], pointer: UUID | None) -> list[Agent]:
    ids = [a.member_id for a in agents]
    if pointer not in ids:
        return list(agents)
    start = ids.index(pointer) + 1
    return list(agents[start:]) + list(agents[:start])


def pick_agent(
    team: Team,
    agents: Sequence[Agent],
    *,
    channel: str,
    required_skill: str | None,
    language_skill: str | None = None,
) -> Pick:
    """`agents` in the team's fixed order (the order the Admin listed them).

    `required_skill`: the ticket type's; `language_skill`: "hindi" for a Hindi ticket
    (md W6). Free agents with both go first, then those with the type's skill, then
    those with the language, then anyone free."""
    if not team.auto_assign:
        return Pick(None, f"Left unassigned: auto-assign is off for {team.name}", ())
    if not agents:
        return Pick(None, f"Left unassigned: {team.name} has no members", ())

    group = channel_group(channel)
    free: list[Agent] = []
    skipped: list[str] = []
    for agent in _after_pointer(agents, team.pointer):
        reason = _why_not(agent, team, group)
        if reason:
            skipped.append(reason)
        else:
            free.append(agent)

    wanted = [
        (k, shown)
        for k, shown in (
            ((required_skill or "").strip().lower(), required_skill),
            ((language_skill or "").strip().lower(), language_skill),
        )
        if k
    ]
    keys = [k for k, _ in wanted]
    for key, shown in wanted:
        if free and not any(key in a.skills for a in free):
            skipped.append(f"nobody free has skill {shown}")
    pools = [[a for a in free if all(k in a.skills for k in keys)]] if keys else []
    pools += [[a for a in free if k in a.skills] for k in keys]
    pool = next((p for p in pools if p), free)
    chosen = pool[0] if pool else None

    tail = f" · skipped: {'; '.join(skipped)}" if skipped else ""
    if chosen is None:
        return Pick(None, f"Left unassigned in {team.name}: nobody is free{tail}", tuple(skipped))
    parts = [team.name]
    parts += [f"skill {shown}" for key, shown in wanted if key in chosen.skills]
    load = chosen.open_by_group.get(group, 0) + 1
    parts.append(f"{GROUP_LABEL[group]} {load} of {chosen.caps.get(group, 0)}")
    line = f"Auto-assigned to {chosen.name} ({' · '.join(parts)}){tail}"
    return Pick(chosen, line, tuple(skipped))
