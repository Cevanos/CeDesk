"""Sign-in, sign-out, resolving a request's token, and creating a workspace."""

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import auth as rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Role
from cedesk.helpdesk.repo import auth as repo
from cedesk.helpdesk.service import passwords, seeds
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotSignedIn

logger = logging.getLogger("cedesk.auth")

# One message for every sign-in failure: unknown workspace, unknown email,
# wrong password, inactive member or locked account all look the same.
SIGN_IN_FAILED = "Those details did not match. Check the workspace, email and password."
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")

Sessionmaker = async_sessionmaker[AsyncSession]


@dataclass(frozen=True)
class SignedIn:
    token: str
    expires_at: datetime
    caller: Caller


def _now() -> datetime:
    return datetime.now(UTC)


async def sign_in(sm: Sessionmaker, *, workspace: str, email: str, password: str) -> SignedIn:
    slug, email = workspace.strip().lower(), rules.normalise_email(email)
    async with sm() as s, s.begin():
        workspace_id = await repo.workspace_for_slug(s, slug)
    if workspace_id is None:
        passwords.verify(None, password)
        raise NotSignedIn(SIGN_IN_FAILED)

    # The password is checked (slow, Argon2) in a worker thread and before any row is
    # locked, so wrong-password attempts never hold up the member's other writes or the
    # event loop. The row is locked afterwards only to save the result.
    async with workspace_session(sm, workspace_id) as s:
        member = await repo.member_for_login(s, email)
    if member is None or not member["is_active"] or rules.is_locked(member["locked_until"], _now()):
        await asyncio.to_thread(passwords.verify, None, password)
        raise NotSignedIn(SIGN_IN_FAILED)
    read_hash = member["password_hash"]
    ok, new_hash = await asyncio.to_thread(passwords.verify, read_hash, password)

    now = _now()
    async with workspace_session(sm, workspace_id) as s:
        member = await repo.lock_member_for_login(s, member["id"])
        if (
            member is None
            or not member["is_active"]
            or rules.is_locked(member["locked_until"], now)
            or member["password_hash"] != read_hash
        ):
            # Deactivated, locked or given a new password while the password was checked.
            raise NotSignedIn(SIGN_IN_FAILED)
        if not ok:
            # Counted from the locked row, so parallel wrong attempts all count.
            failed, locked_until = rules.after_failure(member["failed_logins"], now)
            await repo.record_login_failure(s, member["id"], failed, locked_until)
            if locked_until is not None:
                logger.warning("member locked after repeated failures: %s", member["id"])
            failed_sign_in = True
        else:
            failed_sign_in = False
            await repo.record_login_success(s, member["id"], new_hash)
            # Each sign-in tidies this member's long-ended sessions.
            await repo.prune_sessions(s, now - rules.ENDED_SESSION_KEPT, member["id"])
            token = rules.new_token(workspace_id)
            expires_at = now + rules.SESSION_LIFETIME
            session_id = await repo.insert_session(
                s,
                workspace_id=workspace_id,
                member_id=member["id"],
                token_hash=rules.token_hash(token),
                expires_at=expires_at,
            )
    # Raised after the commit, so the failure count is saved.
    if failed_sign_in:
        raise NotSignedIn(SIGN_IN_FAILED)
    caller = Caller(workspace_id, member["id"], Role(member["role"]), session_id)
    return SignedIn(token=token, expires_at=expires_at, caller=caller)


async def resolve(sm: Sessionmaker, token: str) -> Caller:
    workspace_id = rules.token_workspace(token)
    if workspace_id is None:
        raise NotSignedIn("Sign in again.")
    async with workspace_session(sm, workspace_id) as s:
        row = await repo.live_session(s, rules.token_hash(token))
    if row is None:
        raise NotSignedIn("Sign in again.")
    return Caller(workspace_id, row["member_id"], Role(row["role"]), row["session_id"])


async def sign_out(sm: Sessionmaker, caller: Caller) -> None:
    async with workspace_session(sm, caller.workspace_id) as s:
        await repo.revoke_session(s, caller.session_id)


async def prune_ended_sessions(sm: Sessionmaker) -> int:
    """Deletes, in every workspace, sessions that ended over `ENDED_SESSION_KEPT` ago.

    For a scheduled job (`cedesk prune-sessions`). Each workspace is its own short
    transaction under RLS. Returns how many sessions were deleted."""
    async with sm() as s, s.begin():
        workspace_ids = await repo.all_workspace_ids(s)
    ended_before = _now() - rules.ENDED_SESSION_KEPT
    deleted = 0
    for workspace_id in workspace_ids:
        async with workspace_session(sm, workspace_id) as s:
            deleted += await repo.prune_sessions(s, ended_before)
    return deleted


async def create_workspace(
    sm: Sessionmaker,
    *,
    name: str,
    slug: str,
    admin_email: str,
    admin_name: str,
    admin_password: str,
    seed: bool = True,
) -> UUID:
    """A new workspace with its first Admin and, unless `seed` is False, the default
    teams and ticket types from the mock. Used by the CLI and tests."""
    slug = slug.strip().lower()
    if not SLUG.match(slug):
        raise Invalid("The workspace address uses 3-40 lowercase letters, digits or dashes.")
    problem = rules.password_problem(admin_password)
    if problem:
        raise Invalid(problem)
    workspace_id = uuid4()
    try:
        async with workspace_session(sm, workspace_id) as s:
            await repo.insert_workspace(s, workspace_id, name)
            await repo.add_directory_entry(s, slug, workspace_id)
            await repo.insert_member(
                s,
                workspace_id=workspace_id,
                email=rules.normalise_email(admin_email),
                name=admin_name,
                role=Role.admin.value,
                password_hash=passwords.hash_password(admin_password),
            )
            if seed:
                await seeds.apply(s, workspace_id)
    except IntegrityError as exc:
        raise Conflict("That workspace address is taken.") from exc
    return workspace_id
