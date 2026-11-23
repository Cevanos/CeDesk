"""Agent status (Available, On a break, Transfers only, Offline) and routing settings."""

from collections.abc import Mapping
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field, StringConstraints

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain.permissions import Role
from cedesk.helpdesk.domain.routing import CAP_MAX, AgentStatus, ChannelGroup
from cedesk.helpdesk.service import routing

router = APIRouter(tags=["teams & agents"])

Skill = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
Cap = Annotated[int, Field(ge=0, le=CAP_MAX)]
_RESET = "null: follow the workspace default again."
CAPS = ("cap_email", "cap_chat", "cap_messaging")


class AgentOut(BaseModel):
    id: UUID
    name: str
    role: Role
    is_active: bool
    status: AgentStatus | None = Field(description="None for light agents: they have no status.")
    channels: list[ChannelGroup]
    cap_email: int
    cap_chat: int
    cap_messaging: int
    cap_overrides: list[ChannelGroup] = Field(
        description="Channels where this agent has their own limit; the rest follow "
        "the workspace default."
    )
    skills: list[str]
    open_tickets: int
    open_by_channel: dict[str, int]
    assignable: bool = Field(
        description="Can be given tickets: active, and the role replies to customers (not "
        "a light agent, nor a custom role without the reply right). An Assignee picker "
        "lists only these; anyone else is refused with a 422 that says why."
    )
    works_on_team_id: UUID | None = Field(
        description="A custom role limited to one team: only that team's tickets can be "
        "given to them (a ticket of another team, or of no team, is a 422). null: any team."
    )


class StatusIn(Body):
    status: AgentStatus


class RoutingPatch(Body):
    status: AgentStatus | None = None
    channels: list[ChannelGroup] | None = Field(default=None, max_length=3)
    cap_email: Cap | None = Field(default=None, description=_RESET)
    cap_chat: Cap | None = Field(default=None, description=_RESET)
    cap_messaging: Cap | None = Field(default=None, description=_RESET)
    skills: list[Skill] | None = Field(default=None, max_length=30)


class DefaultsOut(BaseModel):
    cap_email: int
    cap_chat: int
    cap_messaging: int


class DefaultsPatch(Body):
    cap_email: Cap | None = None
    cap_chat: Cap | None = None
    cap_messaging: Cap | None = None


def _out(row: Mapping[Any, Any]) -> AgentOut:
    light = not row["takes_tickets"]
    open_by_channel = {str(k): int(v) for k, v in row["open_by_channel"].items()}
    return AgentOut.model_validate(
        {
            **dict(row),
            "status": None if light else row["status"],
            "open_tickets": sum(open_by_channel.values()),
            "open_by_channel": open_by_channel,
            "assignable": bool(row["is_active"]) and not light,
        }
    )


@router.get("/agents", response_model=list[AgentOut], summary="Everyone's status, load and skills")
async def list_agents(caller: CallerDep, sm: SessionmakerDep) -> list[AgentOut]:
    return [_out(r) for r in await routing.list_agents(sm, caller)]


@router.put("/me/status", response_model=AgentOut, summary="Set your own status")
async def set_my_status(body: StatusIn, caller: CallerDep, sm: SessionmakerDep) -> AgentOut:
    return _out(await routing.set_my_status(sm, caller, body.status))


@router.patch(
    "/agents/{member_id}/routing",
    response_model=AgentOut,
    summary="Status, channels, capacity per channel and skills (Admin)",
)
async def set_routing(
    member_id: UUID, body: RoutingPatch, caller: CallerDep, sm: SessionmakerDep
) -> AgentOut:
    # null on a cap is a reset to the workspace default; on anything else it changes nothing.
    sent = body.model_dump(exclude_unset=True)
    values = {k: v for k, v in sent.items() if v is not None or k in CAPS}
    return _out(await routing.set_routing(sm, caller, member_id, values))


@router.get(
    "/routing-defaults",
    response_model=DefaultsOut,
    summary="The workspace's default capacity per channel",
)
async def get_defaults(caller: CallerDep, sm: SessionmakerDep) -> DefaultsOut:
    return DefaultsOut.model_validate(dict(await routing.get_defaults(sm, caller)))


@router.patch(
    "/routing-defaults",
    response_model=DefaultsOut,
    summary="Change the default capacity per channel (Admin)",
)
async def set_defaults(body: DefaultsPatch, caller: CallerDep, sm: SessionmakerDep) -> DefaultsOut:
    values = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    return DefaultsOut.model_validate(dict(await routing.set_defaults(sm, caller, values)))
