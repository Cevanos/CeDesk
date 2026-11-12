"""Two-step sign-in with an authenticator app (md S2).

Setting up: `start_setup` gives a secret (and the otpauth link for the QR code); the first
right code confirms it and returns 10 recovery codes, shown once. Signing in: the
password gives a session that can do nothing but `verify_sign_in` for 10 minutes; a right
code (or an unused recovery code) makes it a normal session. Wrong codes count toward
the same 10-try lockout as wrong passwords. An Admin can reset a member who lost their
phone; the member then sets it up again.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.crypto import CannotOpen, Sealer
from cedesk.db import workspace_session
from cedesk.helpdesk.domain import auth as auth_rules
from cedesk.helpdesk.domain import security as security_rules
from cedesk.helpdesk.domain import two_factor as rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import security as security_repo
from cedesk.helpdesk.repo import two_factor as repo
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Forbidden, Invalid, NotFound
from cedesk.helpdesk.service.members import TOO_MANY_TRIES, require

WRONG_CODE = "That code isn't right. Check the time on your phone, or use a recovery code."
ISSUER = "CeDesk"


@dataclass(frozen=True)
class Setup:
    secret: str
    otpauth_uri: str


@dataclass(frozen=True)
class Status:
    enabled: bool
    required: bool
    recovery_codes_left: int


def _bound(member_id: UUID) -> str:
    return f"2fa:{member_id}"


def _now() -> datetime:
    return datetime.now(UTC)


async def status(sm: Sessionmaker, caller: Caller) -> Status:
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await repo.get(s, caller.member_id)
        required = (await security_repo.current(s)).require_2fa
        left = await repo.codes_left(s, caller.member_id)
    enabled = row is not None and row["confirmed_at"] is not None
    return Status(enabled, required, left if enabled else 0)


async def start_setup(sm: Sessionmaker, sealer: Sealer, caller: Caller) -> Setup:
    secret = rules.new_secret()
    async with workspace_session(sm, caller.workspace_id) as s:
        member = await auth_repo.get_member(s, caller.member_id)
        workspace = await auth_repo.workspace_row(s)
        if member is None or workspace is None:
            raise NotFound("Member not found.")
        sealed = sealer.seal(secret, bound_to=_bound(caller.member_id))
        if not await repo.start(s, caller.workspace_id, caller.member_id, sealed):
            raise Conflict("Two-step sign-in is already on. Turn it off first to change phones.")
    account = f"{member['email']} ({workspace['slug']})"
    return Setup(secret, rules.otpauth_uri(secret, account=account, issuer=ISSUER))


async def _check(
    s: AsyncSession,
    sealer: Sealer,
    member_id: UUID,
    code: str,
    *,
    confirmed: bool,
    recovery: bool,
) -> bool:
    """Checks a code under the member's row lock: a right one is used up (its step, or the
    recovery code), a wrong one is counted as a failed sign-in. Raises when the member is
    locked out or has no 2FA in the expected state."""
    now = _now()
    member = await auth_repo.lock_member_for_login(s, member_id)
    if member is None:
        raise NotFound("Member not found.")
    if auth_rules.is_locked(member["locked_until"], now):
        raise Forbidden(TOO_MANY_TRIES)
    row = await repo.get(s, member_id, lock=True)
    if row is None or (row["confirmed_at"] is not None) is not confirmed:
        raise Conflict(
            "Two-step sign-in is already on." if not confirmed else "Two-step sign-in is off."
        )
    try:
        secret = sealer.open(bytes(row["secret_sealed"]), bound_to=_bound(member_id))
    except CannotOpen as exc:
        # CEDESK_SECRET_KEY changed: only an Admin reset helps.
        raise Conflict("Your two-step sign-in can't be read. Ask an Admin to reset it.") from exc
    step = rules.matching_step(secret, code, now, int(row["last_step"]))
    if step is not None:
        if confirmed:
            await repo.use_step(s, member_id, step)
        else:
            await repo.confirm(s, member_id, step)
        await auth_repo.record_login_success(s, member_id, None)
        return True
    if (
        recovery
        and rules.looks_like_recovery_code(code)
        and await repo.use_recovery_code(s, member_id, rules.recovery_hash(code))
    ):
        await auth_repo.record_login_success(s, member_id, None)
        return True
    failed, locked_until = auth_rules.after_failure(member["failed_logins"], now)
    await auth_repo.record_login_failure(s, member_id, failed, locked_until)
    return False


async def _new_codes(s: AsyncSession, caller: Caller) -> list[str]:
    codes = rules.new_recovery_codes()
    hashes = [rules.recovery_hash(c) for c in codes]
    await repo.replace_codes(s, caller.workspace_id, caller.member_id, hashes)
    return codes


async def confirm_setup(sm: Sessionmaker, sealer: Sealer, caller: Caller, code: str) -> list[str]:
    """The first right code turns 2FA on. Returns the recovery codes (shown once). The
    member's other sessions end: they were signed in without the second step."""
    async with workspace_session(sm, caller.workspace_id) as s:
        ok = await _check(s, sealer, caller.member_id, code, confirmed=False, recovery=False)
        if ok:
            codes = await _new_codes(s, caller)
            await auth_repo.revoke_member_sessions(s, caller.member_id, keep=caller.session_id)
    if not ok:  # raised after the commit, so the failed try is counted
        raise Invalid(WRONG_CODE)
    return codes


async def new_recovery_codes(
    sm: Sessionmaker, sealer: Sealer, caller: Caller, code: str
) -> list[str]:
    """A fresh set of 10 (the old ones stop working); needs a code from the app."""
    async with workspace_session(sm, caller.workspace_id) as s:
        ok = await _check(s, sealer, caller.member_id, code, confirmed=True, recovery=False)
        codes = await _new_codes(s, caller) if ok else []
    if not ok:
        raise Invalid(WRONG_CODE)
    return codes


async def turn_off(sm: Sessionmaker, sealer: Sealer, caller: Caller, code: str) -> None:
    async with workspace_session(sm, caller.workspace_id) as s:
        if (await security_repo.current(s)).require_2fa:
            raise Forbidden("Your workspace requires two-step sign-in, so it can't be turned off.")
        ok = await _check(s, sealer, caller.member_id, code, confirmed=True, recovery=True)
        if ok:
            await repo.remove(s, caller.member_id)
    if not ok:
        raise Invalid(WRONG_CODE)


async def verify_sign_in(sm: Sessionmaker, sealer: Sealer, caller: Caller, code: str) -> datetime:
    """The second step of signing in. Returns when the session now ends."""
    if not caller.second_factor_pending:
        raise Conflict("This session is already fully signed in.")
    now = _now()
    async with workspace_session(sm, caller.workspace_id) as s:
        ok = await _check(s, sealer, caller.member_id, code, confirmed=True, recovery=True)
        if ok:
            length = (await security_repo.current(s)).session_length
            expires_at = security_rules.session_ends(length, now, now)
            await repo.finish_sign_in(s, caller.session_id, expires_at)
    if not ok:
        raise Invalid(WRONG_CODE)
    return expires_at


async def admin_reset(sm: Sessionmaker, caller: Caller, member_id: UUID) -> None:
    """For a member who lost their phone and recovery codes: 2FA goes off and they are
    signed out everywhere. If the workspace requires 2FA, they set it up at next sign-in."""
    require(caller, Action.manage_access)
    if member_id == caller.member_id:
        raise Forbidden("Turn off your own two-step sign-in from your profile.")
    async with workspace_session(sm, caller.workspace_id) as s:
        admins, target = await auth_repo.lock_admins_and_member(s, member_id)
        if caller.member_id not in admins:
            raise Forbidden("Your role can't do this.")
        if target is None:
            raise NotFound("Member not found.")
        if not await repo.remove(s, member_id):
            raise Conflict("This member doesn't use two-step sign-in.")
        await auth_repo.revoke_member_sessions(s, member_id)
        await security_repo.queue_alert(
            s,
            caller.workspace_id,
            f"Two-step sign-in reset for {target['name']} <{target['email']}>.",
            caller.member_id,
        )
