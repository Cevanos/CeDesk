"""Custom roles (md A3): an Admin builds a role from five rights and a ticket scope.

A change takes effect on the members' next request (their access is read with the
session), so nobody has to sign in again. A role that members still have, or a team a
role is scoped to, can't be deleted.
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action, Scope
from cedesk.helpdesk.repo import roles as repo
from cedesk.helpdesk.repo.roles import Row
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
from cedesk.helpdesk.service.members import require

NAME_TAKEN = "A role with that name already exists."


@dataclass(frozen=True)
class RoleIn:
    name: str
    reply: bool
    assign_any: bool
    reports: bool
    settings: bool
    approve: bool
    scope: Scope
    team_id: UUID | None


async def _checked(s: AsyncSession, role: RoleIn) -> dict[str, object]:
    name = role.name.strip()
    if not name:
        raise Invalid("Give the role a name.")
    if role.scope is Scope.all and role.team_id is not None:
        raise Invalid("A role that sees all tickets has no team. Leave team_id empty.")
    if role.scope is not Scope.all:
        if role.team_id is None:
            raise Invalid("Pick the team this role works on.")
        if not await repo.team_exists(s, role.team_id):
            raise Invalid("That team doesn't exist.")
    return asdict(role) | {"name": name, "scope": role.scope.value}


async def list_roles(sm: Sessionmaker, caller: Caller) -> Sequence[Row]:
    require(caller, Action.manage_access)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await repo.list_roles(s)


async def create_role(sm: Sessionmaker, caller: Caller, role: RoleIn) -> Row:
    require(caller, Action.manage_access)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            role_id = await repo.insert_role(s, caller.workspace_id, await _checked(s, role))
            row = await repo.get_role(s, role_id)
    except IntegrityError as exc:
        raise Conflict(NAME_TAKEN) from exc
    assert row is not None
    return row


async def change_role(sm: Sessionmaker, caller: Caller, role_id: UUID, role: RoleIn) -> Row:
    """Replaces the role's name, rights and scope (the whole role is sent)."""
    require(caller, Action.manage_access)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            if await repo.get_role(s, role_id, lock=True) is None:
                raise NotFound("Role not found.")
            await repo.update_role(s, role_id, await _checked(s, role))
            row = await repo.get_role(s, role_id)
    except IntegrityError as exc:
        raise Conflict(NAME_TAKEN) from exc
    assert row is not None
    return row


async def delete_role(sm: Sessionmaker, caller: Caller, role_id: UUID) -> None:
    require(caller, Action.manage_access)
    try:
        await _delete(sm, caller, role_id)
    except IntegrityError as exc:  # a member was given the role meanwhile
        raise Conflict("Members have this role. Give them another role first.") from exc


async def _delete(sm: Sessionmaker, caller: Caller, role_id: UUID) -> None:
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await repo.get_role(s, role_id, lock=True)
        if row is None:
            raise NotFound("Role not found.")
        if row["member_count"]:
            n = int(row["member_count"])
            raise Conflict(
                f"{n} member{'s have' if n != 1 else ' has'} this role. Give them another "
                "role first."
            )
        await repo.delete_role(s, role_id)
