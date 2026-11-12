"""Custom roles (md A3, A4): Admins build roles from five rights and a ticket scope, then
give them to members with `PATCH /members/{id}` `{role: "custom", role_id}`."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, Field, StringConstraints

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain.permissions import Scope
from cedesk.helpdesk.service import roles
from cedesk.helpdesk.service.roles import RoleIn as Role

router = APIRouter(tags=["roles"])


class RoleBody(Body):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
    reply: bool = Field(
        default=True,
        description="Reply, run macros, accept chats, resolve, create tickets, assign to self.",
    )
    assign_any: bool = Field(default=False, description="Assign tickets to anyone.")
    reports: bool = False
    settings: bool = Field(
        default=False,
        description="Workspace settings (teams, types, rules, macros, SLA, email...). Never "
        "members, roles or security: those stay with Admins.",
    )
    approve: bool = Field(default=False, description="Can be asked to approve, and decide.")
    scope: Scope = Field(
        default=Scope.all,
        description="all tickets; team_edit: edit the team's tickets, view the rest; "
        "team_only: the team's tickets only, the rest are hidden.",
    )
    team_id: UUID | None = Field(default=None, description="Required unless scope is all.")

    def to_role(self) -> Role:
        return Role(**self.model_dump())


class RoleOut(BaseModel):
    id: UUID
    name: str
    reply: bool
    assign_any: bool
    reports: bool
    settings: bool
    approve: bool
    scope: Scope
    team_id: UUID | None
    team_name: str | None
    member_count: int
    created_at: datetime
    updated_at: datetime


@router.get("/roles", response_model=list[RoleOut], summary="Custom roles (Admin)")
async def list_roles(caller: CallerDep, sm: SessionmakerDep) -> list[RoleOut]:
    return [RoleOut.model_validate(dict(r)) for r in await roles.list_roles(sm, caller)]


@router.post(
    "/roles",
    response_model=RoleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a custom role (Admin)",
)
async def create_role(body: RoleBody, caller: CallerDep, sm: SessionmakerDep) -> RoleOut:
    return RoleOut.model_validate(dict(await roles.create_role(sm, caller, body.to_role())))


@router.put(
    "/roles/{role_id}",
    response_model=RoleOut,
    summary="Change a custom role (Admin; the whole role is sent; members get it at once)",
)
async def change_role(
    role_id: UUID, body: RoleBody, caller: CallerDep, sm: SessionmakerDep
) -> RoleOut:
    row = await roles.change_role(sm, caller, role_id, body.to_role())
    return RoleOut.model_validate(dict(row))


@router.delete(
    "/roles/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a custom role nobody has (Admin; 409 while members have it)",
)
async def delete_role(role_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> None:
    await roles.delete_role(sm, caller, role_id)
