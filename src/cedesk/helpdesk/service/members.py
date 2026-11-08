"""Listing, adding and changing members. Permission checks happen here, per caller."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import auth as rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action, Role, can
from cedesk.helpdesk.repo import auth as repo
from cedesk.helpdesk.repo.auth import Row
from cedesk.helpdesk.service import passwords
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Forbidden, Invalid, NotFound


def require(caller: Caller, action: Action) -> None:
    if not can(caller.role, action):
        raise Forbidden("Your role can't do this.")


async def me(sm: Sessionmaker, caller: Caller) -> tuple[Row, Row]:
    async with workspace_session(sm, caller.workspace_id) as s:
        member = await repo.get_member(s, caller.member_id)
        workspace = await repo.workspace_row(s)
    if member is None or workspace is None:
        raise NotFound("Member not found.")
    return member, workspace


async def list_members(
    sm: Sessionmaker, caller: Caller, *, after: UUID | None, limit: int
) -> Sequence[Row]:
    require(caller, Action.list_members)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await repo.list_members(s, after, limit)


async def add_member(
    sm: Sessionmaker, caller: Caller, *, email: str, name: str, role: Role, password: str
) -> Row:
    require(caller, Action.settings)
    problem = rules.password_problem(password)
    if problem:
        raise Invalid(problem)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            return await repo.insert_member(
                s,
                workspace_id=caller.workspace_id,
                email=rules.normalise_email(email),
                name=name,
                role=role.value,
                password_hash=passwords.hash_password(password),
            )
    except IntegrityError as exc:
        raise Conflict("Someone in this workspace already uses that email.") from exc


async def change_member(
    sm: Sessionmaker,
    caller: Caller,
    member_id: UUID,
    *,
    name: str | None,
    role: Role | None,
    is_active: bool | None,
) -> Row:
    require(caller, Action.settings)
    if member_id == caller.member_id and (role is not None or is_active is False):
        raise Forbidden("You can't change your own role or deactivate yourself.")
    async with workspace_session(sm, caller.workspace_id) as s:
        # Every active Admin and the target are locked together, in id order (B5).
        admins, current = await repo.lock_admins_and_member(s, member_id)
        # The role was read when the request started; another change may have
        # removed it since. Checked again under the lock.
        if caller.member_id not in admins:
            raise Forbidden("Your role can't do this.")
        if current is None:
            raise NotFound("Member not found.")
        loses_admin = (
            current["role"] == Role.admin.value
            and current["is_active"]
            and ((role is not None and role != Role.admin) or is_active is False)
        )
        if loses_admin and len(admins) <= 1:
            raise Conflict("A workspace needs at least one active Admin.")
        updated = await repo.update_member(
            s,
            member_id,
            name=name,
            role=role.value if role else None,
            is_active=is_active,
        )
        if is_active is False:
            await repo.revoke_member_sessions(s, member_id)
        return updated
