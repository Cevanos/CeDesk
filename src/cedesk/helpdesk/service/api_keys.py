"""API keys (md PL1): Admins make, list and revoke them; every request with one comes
through `resolve`. Rules are in domain/api_keys.py.

Making and revoking a key are audited (area `api_keys`, actions `created` and `revoked`;
the entry has the name and level, never the key). Keys themselves can't reach these
routes (domain/api_keys.ROUTES), so a key never makes or revokes another.
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import api_keys as rules
from cedesk.helpdesk.domain import security as security_rules
from cedesk.helpdesk.domain.api_keys import KeyAccess
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action, Role
from cedesk.helpdesk.repo import api_keys as repo
from cedesk.helpdesk.service import audit
from cedesk.helpdesk.service.auth import Sessionmaker, member_access
from cedesk.helpdesk.service.errors import Forbidden, Invalid, IpNotAllowed, NotFound, NotSignedIn
from cedesk.helpdesk.service.members import require

NOT_VALID = "This API key isn't valid, or it was revoked."
NOT_FOUND = "API key not found."


@dataclass(frozen=True)
class KeyView:
    id: UUID
    name: str
    access: KeyAccess
    hint: str  # cdk_…abcd
    created_at: datetime
    created_by: UUID
    created_by_name: str
    last_used_at: datetime | None
    revoked_at: datetime | None
    revoked_by_name: str | None


@dataclass(frozen=True)
class NewKey:
    view: KeyView
    key: str  # shown once


def _view(row: Any) -> KeyView:
    return KeyView(
        id=row["id"],
        name=str(row["name"]),
        access=KeyAccess(row["access"]),
        hint=rules.hint(str(row["last4"])),
        created_at=row["created_at"],
        created_by=row["created_by"],
        created_by_name=str(row["created_by_name"]),
        last_used_at=row["last_used_at"],
        revoked_at=row["revoked_at"],
        revoked_by_name=row["revoked_by_name"],
    )


def _person(caller: Caller) -> None:
    """Keys are managed by a person signed in, never by another key."""
    if caller.api_key_id is not None:
        raise Forbidden("An API key can't manage API keys.")


async def create_key(sm: Sessionmaker, caller: Caller, *, name: str, access: KeyAccess) -> NewKey:
    require(caller, Action.manage_access)
    _person(caller)
    name = name.strip()
    if not 1 <= len(name) <= rules.NAME_MAX:
        raise Invalid(f"Give the key a name of 1 to {rules.NAME_MAX} characters.")
    if not rules.can_make(access, caller.access.actions):
        raise Forbidden("Your role can't make a key with this access.")
    key = rules.new_key(caller.workspace_id)
    async with workspace_session(sm, caller.workspace_id) as s:
        key_id = await repo.insert(
            s, caller.workspace_id, name=name, access=access.value,
            key_hash=rules.key_hash(key), last4=rules.last4(key), created_by=caller.member_id,
        )  # fmt: skip
        await audit.record(
            s, caller, Area.api_keys, "created", name, target_id=key_id,
            detail={"access": access.value, "hint": rules.hint(rules.last4(key))},
        )  # fmt: skip
        row = await repo.get(s, key_id)
    assert row is not None  # just made, in this transaction
    return NewKey(_view(row), key)


async def all_keys(sm: Sessionmaker, caller: Caller) -> list[KeyView]:
    require(caller, Action.manage_access)
    _person(caller)
    async with workspace_session(sm, caller.workspace_id) as s:
        return [_view(r) for r in await repo.all_keys(s)]


async def revoke_key(sm: Sessionmaker, caller: Caller, key_id: UUID) -> None:
    """Revoking twice changes nothing; the first one is what the list and log show."""
    require(caller, Action.manage_access)
    _person(caller)
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await repo.get(s, key_id)
        if row is None:
            raise NotFound(NOT_FOUND)
        if await repo.revoke(s, key_id, caller.member_id):
            await audit.record(
                s, caller, Area.api_keys, "revoked", str(row["name"]), target_id=key_id,
                detail={"access": str(row["access"]), "hint": rules.hint(str(row["last4"]))},
            )  # fmt: skip


async def resolve(sm: Sessionmaker, key: str, ip: str | None) -> Caller:
    """The caller behind a key: the member who made it, with the key's level on top of
    their rights. The workspace's IP list (S4) applies as for a person. 401 when the key
    is unknown, revoked, or its member was deactivated."""
    workspace_id = rules.key_workspace(key)
    if workspace_id is None:
        raise NotSignedIn(NOT_VALID)
    async with workspace_session(sm, workspace_id) as s:
        row = await repo.live_key(s, rules.key_hash(key))
        if row is None:
            raise NotSignedIn(NOT_VALID)
        networks = security_rules.parse_networks(row["ip_allowlist"] or ())
        if ip is not None and not security_rules.ip_allowed(ip, networks):
            raise IpNotAllowed(security_rules.ip_refused(ip))
        used = row["last_used_at"]
        if used is None or datetime.now(UTC) - used >= security_rules.TOUCH_EVERY:
            await repo.touch(s, row["key_id"])
    level = KeyAccess(row["access"])
    access = member_access(row)
    return Caller(
        workspace_id,
        row["member_id"],
        Role(row["role"]),
        row["key_id"],  # no session: the key stands in for one
        replace(access, actions=rules.capped(level, access.actions)),
        api_key_id=row["key_id"],
        api_key_name=str(row["key_name"]),
        api_key_access=level.value,
    )
