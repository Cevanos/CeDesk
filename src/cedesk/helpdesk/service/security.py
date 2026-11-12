"""Workspace security settings, Test my IP and the security score (md S3-S6, S8). Admins
only (`manage_access`): a custom role with the settings right never gets here.

Saving applies at once: a shorter sign-out time ends longer sessions early, a new IP list
is checked on the next request, a higher password minimum applies to the next password
set (existing ones keep working until they expire).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import security as rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.security import SessionLength
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import security as repo
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Invalid, NotFound
from cedesk.helpdesk.service.members import require


@dataclass(frozen=True)
class SettingsIn:
    password_min: int
    password_expiry_days: int | None
    session_length: SessionLength
    ip_allowlist: list[str]
    attachments_need_sign_in: bool
    admin_alerts: bool
    require_2fa: bool = False


@dataclass(frozen=True)
class IpCheck:
    ip: str | None
    allowed: bool


async def get_settings(sm: Sessionmaker, caller: Caller) -> RowMapping | None:
    """None: the workspace never saved them, so the defaults apply."""
    require(caller, Action.manage_access)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await repo.settings_row(s)


def _lists(networks: tuple[rules.IpNetwork, ...]) -> str:
    return ", ".join(str(n) for n in networks) or "empty (every address allowed)"


def _merged(old: rules.Security, changes: Mapping[str, Any]) -> SettingsIn:
    """The saved settings with only the sent fields changed: a field left out of the body
    keeps its value (so a client that forgets `require_2fa` can't turn 2FA off)."""
    kept = {
        "password_min": old.password_min,
        "password_expiry_days": old.password_expiry_days,
        "session_length": old.session_length,
        "ip_allowlist": [str(n) for n in old.ip_allowlist],
        "attachments_need_sign_in": old.attachments_need_sign_in,
        "admin_alerts": old.admin_alerts,
        "require_2fa": old.require_2fa,
    }
    return SettingsIn(**(kept | {k: v for k, v in changes.items() if k in kept}))


async def save_settings(
    sm: Sessionmaker, caller: Caller, changes: Mapping[str, Any], *, ip: str | None
) -> RowMapping | None:
    """`changes`: only the fields to change. `ip`: the caller's address. A list that
    leaves it out is refused, so an Admin can't lock themselves (and maybe everyone) out
    by mistake."""
    require(caller, Action.manage_access)
    async with workspace_session(sm, caller.workspace_id) as s:
        old = rules.from_row(await repo.lock_settings(s, caller.workspace_id))
        new = _merged(old, changes)
        return await _save(s, caller, old, new, ip)


async def _save(
    s: AsyncSession, caller: Caller, old: rules.Security, new: SettingsIn, ip: str | None
) -> RowMapping | None:
    low, high = rules.PASSWORD_MIN_RANGE
    if not low <= new.password_min <= high:
        raise Invalid(f"Minimum password length is {low} to {high}.")
    if new.password_expiry_days not in (None, *rules.EXPIRY_DAYS):
        raise Invalid("Password expiry is never (null), 90 or 180 days.")
    try:
        networks = rules.parse_networks(new.ip_allowlist)
    except rules.BadNetwork as exc:
        raise Invalid(str(exc)) from exc
    if not rules.ip_allowed(ip, networks):
        raise Invalid(
            f"Your own address ({ip or 'unknown'}) isn't in the list, so saving it would "
            "sign you out. Add it first (use Test my IP)."
        )
    if (
        new.require_2fa
        and not old.require_2fa
        and not await repo.has_two_factor(s, caller.member_id)
    ):
        raise Invalid(
            "Turn on two-step sign-in for yourself first (Profile > Two-step sign-in), "
            "then require it for everyone."
        )
    if new.require_2fa != old.require_2fa:
        await repo.queue_alert(
            s,
            caller.workspace_id,
            "Two-step sign-in is now "
            + ("required for every member." if new.require_2fa else "optional."),
            caller.member_id,
        )
    # Queued before saving, so the alert setting that applies is the one before this
    # change: turning alerts off is itself reported.
    if old.ip_allowlist != networks:
        await repo.queue_alert(
            s,
            caller.workspace_id,
            f"IP allowlist changed from {_lists(old.ip_allowlist)} to {_lists(networks)}.",
            caller.member_id,
        )
    if old.admin_alerts and not new.admin_alerts:
        await repo.queue_alert(
            s, caller.workspace_id, "Admin security alerts were turned off.", caller.member_id
        )
    await repo.save(
        s,
        {
            "password_min": new.password_min,
            "password_expiry_days": new.password_expiry_days,
            "session_length": new.session_length.value,
            "ip_allowlist": [str(n) for n in networks],
            "attachments_need_sign_in": new.attachments_need_sign_in,
            "admin_alerts": new.admin_alerts,
            "require_2fa": new.require_2fa,
        },
        caller.member_id,
    )
    if new.session_length is not old.session_length:
        await repo.shorten_sessions(s, repo.SHORTEN[new.session_length.value])
    return await repo.settings_row(s)


async def test_ip(
    sm: Sessionmaker, caller: Caller, *, ip: str | None, allowlist: list[str] | None
) -> IpCheck:
    """Test my IP: would this address get in with `allowlist` (the saved list when None)?"""
    require(caller, Action.manage_access)
    if allowlist is None:
        async with workspace_session(sm, caller.workspace_id) as s:
            networks = (await repo.current(s)).ip_allowlist
    else:
        try:
            networks = rules.parse_networks(allowlist)
        except rules.BadNetwork as exc:
            raise Invalid(str(exc)) from exc
    return IpCheck(ip, rules.ip_allowed(ip, networks))


async def security_score(sm: Sessionmaker, caller: Caller) -> rules.Score:
    require(caller, Action.manage_access)
    async with workspace_session(sm, caller.workspace_id) as s:
        settings = await repo.current(s)
        facts = await repo.score_facts(s)
    return rules.score(
        settings,
        sso=bool(facts["sso"]),
        redaction=bool(facts["redaction"]),
        portal_domains=bool(facts["portal_domains"]),
    )


async def clear_ip_allowlist(sm: Sessionmaker, slug: str) -> bool:
    """For the server's operator when the list locks every Admin out (e.g. the office
    address changed): `uv run cedesk clear-ip-allowlist --workspace <slug>`. The Admins are
    emailed. False when the list was already empty."""
    async with sm() as s, s.begin():
        workspace_id = await auth_repo.workspace_for_slug(s, slug.strip().lower())
    if workspace_id is None:
        raise NotFound(f"No workspace with the address {slug!r}.")
    async with workspace_session(sm, workspace_id) as s:
        cleared = await repo.clear_ip_allowlist(s)
        if cleared:
            await repo.queue_alert(
                s,
                workspace_id,
                "IP allowlist cleared from the server (every address can sign in again).",
                None,
            )
    return cleared
