"""Agent status (Available, On a break, Transfers only, Offline) and routing settings."""

from collections.abc import Mapping
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field, StringConstraints

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain.permissions import Role
from cedesk.helpdesk.domain.routing import AgentStatus, ChannelGroup
from cedesk.helpdesk.service import routing

router = APIRouter(tags=["teams & agents"])

Skill = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]


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
    skills: list[str]
    open_tickets: int
    open_by_channel: dict[str, int]


class StatusIn(Body):
    status: AgentStatus


class RoutingPatch(Body):
    status: AgentStatus | None = None
    channels: list[ChannelGroup] | None = Field(default=None, max_length=3)
    cap_email: int | None = Field(default=None, ge=0, le=100)
    cap_chat: int | None = Field(default=None, ge=0, le=100)
    cap_messaging: int | None = Field(default=None, ge=0, le=100)
    skills: list[Skill] | None = Field(default=None, max_length=30)


def _out(row: Mapping[Any, Any]) -> AgentOut:
    light = row["role"] == Role.light_agent.value
    open_by_channel = {str(k): int(v) for k, v in row["open_by_channel"].items()}
    return AgentOut.model_validate(
        {
            **dict(row),
            "status": None if light else row["status"],
            "open_tickets": sum(open_by_channel.values()),
            "open_by_channel": open_by_channel,
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
    values = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    return _out(await routing.set_routing(sm, caller, member_id, values))
