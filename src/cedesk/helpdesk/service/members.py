"""Listing, adding and changing members. Permission checks happen here, per caller."""

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
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

logger = logging.getLogger("cedesk.auth")

WRONG_CURRENT = "Your current password is not right."
TOO_MANY_TRIES = "Too many wrong passwords. Try again in 15 minutes."


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


async def change_own_password(
    sm: Sessionmaker, caller: Caller, *, current_password: str, new_password: str
) -> None:
    """The signed-in member changes their password. Their other sessions end; this one stays.

    A wrong current password counts as a failed sign-in, so guessing it from a stolen
    session hits the same lockout as the sign-in page."""
    problem = rules.password_problem(new_password)
    if problem:
        raise Invalid(problem)
    if new_password == current_password:
        raise Invalid("Choose a password different from your current one.")
    async with workspace_session(sm, caller.workspace_id) as s:
        member = await repo.lock_member_for_login(s, caller.member_id)
    if member is None:
        raise NotFound("Member not found.")
    if rules.is_locked(member["locked_until"], datetime.now(UTC)):
        raise Forbidden(TOO_MANY_TRIES)
    read_hash = member["password_hash"]
    # Slow (Argon2), so checked in a worker thread with no row locked.
    ok, _ = await asyncio.to_thread(passwords.verify, read_hash, current_password)
    new_hash = await asyncio.to_thread(passwords.hash_password, new_password) if ok else None

    now = datetime.now(UTC)
    async with workspace_session(sm, caller.workspace_id) as s:
        member = await repo.lock_member_for_login(s, caller.member_id)
        if member is None:
            raise NotFound("Member not found.")
        if member["password_hash"] != read_hash:
            # Changed or reset by someone else while this one was checked.
            raise Conflict("Your password was just changed. Sign in again.")
        if rules.is_locked(member["locked_until"], now):
            raise Forbidden(TOO_MANY_TRIES)
        if new_hash is None:
            failed, locked_until = rules.after_failure(member["failed_logins"], now)
            await repo.record_login_failure(s, caller.member_id, failed, locked_until)
        else:
            await repo.set_password(s, caller.member_id, new_hash)
            await repo.revoke_member_sessions(s, caller.member_id, keep=caller.session_id)
    # Raised after the commit, so the failure count is saved.
    if new_hash is None:
        raise Invalid(WRONG_CURRENT)
    logger.info("member changed their password: %s", caller.member_id)


async def reset_password(
    sm: Sessionmaker, caller: Caller, member_id: UUID, *, new_password: str
) -> None:
    """An Admin gives a member a new password: it also unlocks them and ends every
    session they have, so whoever had the old password is signed out."""
    require(caller, Action.settings)
    if member_id == caller.member_id:
        raise Forbidden("Change your own password from your profile, with your current one.")
    problem = rules.password_problem(new_password)
    if problem:
        raise Invalid(problem)
    new_hash = await asyncio.to_thread(passwords.hash_password, new_password)
    async with workspace_session(sm, caller.workspace_id) as s:
        # Same id-ordered lock as other member changes (B5); confirms the caller is
        # still an active Admin.
        admins, target = await repo.lock_admins_and_member(s, member_id)
        if caller.member_id not in admins:
            raise Forbidden("Your role can't do this.")
        if target is None:
            raise NotFound("Member not found.")
        await repo.set_password(s, member_id, new_hash)
        await repo.revoke_member_sessions(s, member_id)
    logger.info("admin %s reset the password of %s", caller.member_id, member_id)
