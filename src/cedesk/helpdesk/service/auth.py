"""Sign-in, sign-out, resolving a request's token, and creating a workspace."""

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import auth as rules
from cedesk.helpdesk.domain import security as security_rules
from cedesk.helpdesk.domain import signup as signup_rules
from cedesk.helpdesk.domain import two_factor as two_factor_rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Access, Role, access_of
from cedesk.helpdesk.repo import auth as repo
from cedesk.helpdesk.repo import security as security_repo
from cedesk.helpdesk.repo import sso as sso_repo
from cedesk.helpdesk.service import passwords, seeds
from cedesk.helpdesk.service.errors import (
    Conflict,
    Invalid,
    IpNotAllowed,
    NotSignedIn,
    SsoRequired,
)

logger = logging.getLogger("cedesk.auth")

# One message for every sign-in failure: unknown workspace, unknown email,
# wrong password, inactive member or locked account all look the same.
SIGN_IN_FAILED = "Those details did not match. Check the workspace, email and password."

Sessionmaker = async_sessionmaker[AsyncSession]


@dataclass(frozen=True)
class SignedIn:
    token: str
    expires_at: datetime
    caller: Caller
    password_expired: bool = False
    two_factor_required: bool = False


def _now() -> datetime:
    return datetime.now(UTC)


def member_access(row: Mapping[Any, Any]) -> Access:
    """From a row with `role` and `repo.auth.ROLE_COLUMNS`."""
    role = Role(row["role"])
    return access_of(role, row if role is Role.custom else None)


async def sign_in(
    sm: Sessionmaker, *, workspace: str, email: str, password: str, ip: str | None = None
) -> SignedIn:
    """`ip`: the caller's address, checked against the workspace's IP list (S4)."""
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
        security = await security_repo.current(s)
        sso_enforced = await sso_repo.enforced(s)
        member = await repo.member_for_login(s, email)
    if not security_rules.ip_allowed(ip, security.ip_allowlist):
        raise IpNotAllowed(security_rules.ip_refused(ip))
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
        elif sso_enforced and member["role"] != Role.admin.value:
            # The password was right, but this workspace signs in through its identity
            # provider (S1). Admins keep their password, so SSO trouble can be fixed.
            raise SsoRequired("Your workspace signs in with SSO. Use Sign in with SSO.")
        else:
            failed_sign_in = False
            await repo.record_login_success(s, member["id"], new_hash)
            # Each sign-in tidies this member's long-ended sessions.
            await repo.prune_sessions(s, now - rules.ENDED_SESSION_KEPT, member["id"])
            token = rules.new_token(workspace_id)
            # With 2FA on, this session only waits for the code (S2), and not for long.
            pending = bool(member["two_factor_on"])
            expires_at = (
                now + two_factor_rules.PENDING_LIFETIME
                if pending
                else security_rules.session_ends(security.session_length, now, now)
            )
            session_id = await repo.insert_session(
                s,
                workspace_id=workspace_id,
                member_id=member["id"],
                token_hash=rules.token_hash(token),
                expires_at=expires_at,
                second_factor_pending=pending,
            )
    # Raised after the commit, so the failure count is saved.
    if failed_sign_in:
        raise NotSignedIn(SIGN_IN_FAILED)
    expired = security_rules.password_expired(
        member["password_changed_at"], security.password_expiry_days, now
    )
    role = Role(member["role"])
    caller = Caller(
        workspace_id,
        member["id"],
        role,
        session_id,
        member_access(member),
        expired,
        second_factor_pending=pending,
        two_factor_setup_required=security.require_2fa and not pending,
    )
    return SignedIn(
        token=token,
        expires_at=expires_at,
        caller=caller,
        password_expired=expired,
        two_factor_required=pending,
    )


async def resolve(sm: Sessionmaker, token: str, ip: str | None = None) -> Caller:
    """The caller behind a token, on every signed-in request: also where the IP list (S4)
    and the idle sign-out (S3) are enforced. `ip` None skips the IP check (internal use)."""
    workspace_id = rules.token_workspace(token)
    if workspace_id is None:
        raise NotSignedIn("Sign in again.")
    now = _now()
    async with workspace_session(sm, workspace_id) as s:
        row = await repo.live_session(s, rules.token_hash(token))
        if row is None:
            raise NotSignedIn("Sign in again.")
        networks = security_rules.parse_networks(row["ip_allowlist"] or ())
        if ip is not None and not security_rules.ip_allowed(ip, networks):
            raise IpNotAllowed(security_rules.ip_refused(ip))
        if now - row["last_used_at"] >= security_rules.TOUCH_EVERY:
            length = security_rules.SessionLength(row["session_length"] or "12h")
            expires_at = row["expires_at"]
            # A session waiting for the 2FA code keeps its short end time.
            if length is security_rules.SessionLength.idle_1h and not row["second_factor_pending"]:
                expires_at = security_rules.session_ends(length, row["signed_in_at"], now)
            await security_repo.touch_session(s, row["session_id"], expires_at)
    expired = not row["via_sso"] and security_rules.password_expired(
        row["password_changed_at"], row["password_expiry_days"], now
    )
    return Caller(
        workspace_id,
        row["member_id"],
        Role(row["role"]),
        row["session_id"],
        member_access(row),
        expired,
        second_factor_pending=bool(row["second_factor_pending"]),
        # Signed in through the identity provider: its own MFA applies, not ours.
        two_factor_setup_required=(
            bool(row["require_2fa"]) and not row["two_factor_on"] and not row["via_sso"]
        ),
    )


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
    slug = signup_rules.clean_slug(slug)
    # The same address rules as sign-up: no reserved names (the agent app's own pages, or
    # names that pass for CeDesk) and no double dashes. Only older workspaces keep theirs.
    slug_problem = signup_rules.slug_problem(slug)
    if slug_problem:
        raise Invalid(slug_problem)
    problem = rules.password_problem(admin_password)
    if problem:
        raise Invalid(problem)
    workspace_id = uuid4()
    try:
        async with workspace_session(sm, workspace_id) as s:
            await insert_workspace(
                s,
                workspace_id,
                name=name,
                slug=slug,
                admin_email=admin_email,
                admin_name=admin_name,
                password_hash=passwords.hash_password(admin_password),
                seed=seed,
            )
    except IntegrityError as exc:
        raise Conflict("That workspace address is taken.") from exc
    return workspace_id


async def insert_workspace(
    s: AsyncSession,
    workspace_id: UUID,
    *,
    name: str,
    slug: str,
    admin_email: str,
    admin_name: str,
    password_hash: str,
    seed: bool = True,
) -> None:
    """The rows of a new workspace, in the caller's `workspace_session(workspace_id)`: the
    workspace, its address, its first Admin (password already hashed) and the defaults.
    A taken address raises IntegrityError, which rolls the whole transaction back."""
    await repo.insert_workspace(s, workspace_id, name)
    await repo.add_directory_entry(s, slug, workspace_id)
    await repo.insert_member(
        s,
        workspace_id=workspace_id,
        email=rules.normalise_email(admin_email),
        name=admin_name,
        role=Role.admin.value,
        password_hash=password_hash,
    )
    if seed:
        await seeds.apply(s, workspace_id)
