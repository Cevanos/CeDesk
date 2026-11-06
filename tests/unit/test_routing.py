"""Round-robin auto-assignment rules (md 6.2, W7), without a database."""

from dataclasses import replace
from uuid import UUID, uuid4

from cedesk.helpdesk.domain.routing import (
    Agent,
    AgentStatus,
    ChannelGroup,
    Team,
    channel_group,
    pick_agent,
)

ALL = frozenset(ChannelGroup)
CAPS = {ChannelGroup.email: 5, ChannelGroup.chat: 2, ChannelGroup.messaging: 3}


def agent(name: str, **changes: object) -> Agent:
    base = Agent(
        member_id=uuid4(),
        name=name,
        is_active=True,
        is_light=False,
        status=AgentStatus.available,
        channels=ALL,
        caps=CAPS,
        skills=frozenset(),
        open_by_group={},
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def team(pointer: UUID | None = None, **changes: object) -> Team:
    return replace(Team("Billing", True, 5, pointer), **changes)  # type: ignore[arg-type]


def test_round_robin_starts_after_the_last_pick() -> None:
    a, b, c = agent("A"), agent("B"), agent("C")
    agents = [a, b, c]
    picks = []
    pointer = None
    for _ in range(4):
        chosen = pick_agent(team(pointer), agents, channel="email", required_skill=None).agent
        assert chosen is not None
        picks.append(chosen.name)
        pointer = chosen.member_id
    assert picks == ["A", "B", "C", "A"]


def test_the_md_example_line() -> None:
    adarsh = agent("Adarsh", status=AgentStatus.on_break)
    neha = agent("Neha", skills=frozenset({"refunds"}))
    pick = pick_agent(team(), [adarsh, neha], channel="chat", required_skill="Refunds")
    assert pick.agent == neha
    assert pick.line == (
        "Auto-assigned to Neha (Billing · skill Refunds · Website chat 1 of 2)"
        " · skipped: Adarsh is On a break"
    )


def test_every_skip_has_a_reason() -> None:
    agents = [
        agent("Off", status=AgentStatus.offline),
        agent("Transfers", status=AgentStatus.transfers_only),
        agent("NoEmail", channels=frozenset({ChannelGroup.chat})),
        agent("Full", open_by_group={ChannelGroup.email: 5}),
        agent(
            "Busy",
            open_by_group={ChannelGroup.email: 2, ChannelGroup.chat: 2, ChannelGroup.messaging: 1},
        ),
        agent("Light", is_light=True),
        agent("Gone", is_active=False),
    ]
    pick = pick_agent(team(), agents, channel="email", required_skill=None)
    assert pick.agent is None
    assert pick.skipped == (
        "Off is Offline",
        "Transfers is Transfers only",
        "NoEmail has Email off",
        "Full is full on Email (5)",
        "Busy has 5 open",
        "Light is a light agent",
        "Gone is deactivated",
    )
    assert pick.line.startswith("Left unassigned in Billing: nobody is free · skipped: Off is")


def test_skilled_agents_go_first_then_anyone() -> None:
    a, b = agent("A"), agent("B", skills=frozenset({"refunds"}))
    assert pick_agent(team(), [a, b], channel="email", required_skill="refunds").agent == b
    pick = pick_agent(team(), [a], channel="email", required_skill="Refunds")
    assert pick.agent == a
    assert pick.skipped == ("nobody free has skill Refunds",)
    assert "skill" not in pick.line.split(" · skipped")[0]


def test_team_switch_and_empty_team() -> None:
    off = pick_agent(team(auto_assign=False), [agent("A")], channel="email", required_skill=None)
    assert (off.agent, off.line) == (None, "Left unassigned: auto-assign is off for Billing")
    empty = pick_agent(team(), [], channel="email", required_skill=None)
    assert empty.line == "Left unassigned: Billing has no members"


def test_channel_groups() -> None:
    assert channel_group("chat") is ChannelGroup.chat
    for channel in ("email", "portal", "web", "api", "phone"):
        assert channel_group(channel) is ChannelGroup.email
