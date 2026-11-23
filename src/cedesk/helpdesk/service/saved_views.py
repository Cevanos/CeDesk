"""Saved views (feature-gap plan 1.2); rules in domain/saved_views.py.

- Everyone who reads tickets lists their own personal views and every shared one, and
  makes, changes and deletes their own personal views.
- Shared views are made, changed and deleted by roles with `share_views` (Admins and
  Supervisors; a custom role with the settings right). Someone else's personal view is
  never shown or changed: 404, as if it didn't exist.
- The filters are checked with the ticket list's own rules (`tickets.filters_from_json`)
  and stored as the list takes them; a team must be one of this workspace's.
- Every create, change and delete is audited (area `views`).
"""

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import audit as audit_rules
from cedesk.helpdesk.domain import saved_views as rules
from cedesk.helpdesk.domain import tickets as ticket_rules
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import catalog
from cedesk.helpdesk.repo import saved_views as repo
from cedesk.helpdesk.repo.saved_views import Row
from cedesk.helpdesk.service import audit
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Forbidden, Invalid, NotFound
from cedesk.helpdesk.service.members import require

NOT_FOUND = "View not found."
TAKEN = "You already have a view with that name."
TAKEN_SHARED = "A shared view with that name already exists."
CANT_SHARE = "Your role can't share views. Save it as a personal view."


def _name(raw: str) -> str:
    try:
        return rules.clean_name(raw)
    except rules.ViewError as exc:
        raise Invalid(str(exc)) from exc


async def _filters(s: AsyncSession, raw: Mapping[str, Any]) -> dict[str, Any]:
    try:
        checked = ticket_rules.filters_from_json(raw)
    except ticket_rules.FieldError as exc:
        raise Invalid(str(exc)) from exc
    if checked.team is not None and await catalog.get_team(s, checked.team) is None:
        raise Invalid("That team does not exist.")
    return checked.stored()


def _visible(caller: Caller, row: Row | None) -> Row:
    """The view if the caller may see it; someone else's personal view doesn't exist."""
    if row is None or (not row["shared"] and row["owner_id"] != caller.member_id):
        raise NotFound(NOT_FOUND)
    return row


def _may_change(caller: Caller, row: Row) -> None:
    if row["shared"] and not caller.can(Action.share_views):
        raise Forbidden("Your role can't change shared views.")


def can_change(caller: Caller, row: Row) -> bool:
    """For the list: whether to show edit and delete."""
    return caller.can(Action.share_views) if row["shared"] else row["owner_id"] == caller.member_id


async def list_views(sm: Sessionmaker, caller: Caller) -> Sequence[Row]:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await repo.visible(s, caller.member_id)


async def get_view(sm: Sessionmaker, caller: Caller, view_id: UUID) -> Row:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        return _visible(caller, await repo.get(s, view_id))


def _taken(shared: bool) -> Conflict:
    return Conflict(TAKEN_SHARED if shared else TAKEN)


async def create_view(
    sm: Sessionmaker, caller: Caller, *, name: str, filters: Mapping[str, Any], shared: bool
) -> Row:
    require(caller, Action.read_ticket)
    if shared and not caller.can(Action.share_views):
        raise Forbidden(CANT_SHARE)
    clean = _name(name)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            stored = await _filters(s, filters)
            # Soft caps: two saves at the same moment can pass them by one each.
            if shared and await repo.count_shared(s) >= rules.MAX_SHARED:
                raise Invalid(f"A workspace can have at most {rules.MAX_SHARED} shared views.")
            if not shared and await repo.count_personal(s, caller.member_id) >= rules.MAX_PERSONAL:
                raise Invalid(f"You can have at most {rules.MAX_PERSONAL} personal views.")
            view_id = await repo.insert(
                s, caller.workspace_id, owner_id=caller.member_id, name=clean, filters=stored,
                shared=shared,
            )  # fmt: skip
            row = await repo.get(s, view_id)
            assert row is not None
            await audit.record(
                s, caller, Area.views, "created", f"View {clean}", target_id=view_id,
                detail=audit_rules.snapshot(row, ["name", "filters", "shared"]),
            )  # fmt: skip
            return row
    except IntegrityError as exc:
        raise _taken(shared) from exc


async def change_view(
    sm: Sessionmaker,
    caller: Caller,
    view_id: UUID,
    *,
    name: str | None,
    filters: Mapping[str, Any] | None,
    shared: bool | None,
) -> Row:
    require(caller, Action.read_ticket)
    clean = None if name is None else _name(name)
    share = bool(shared)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            before = _visible(caller, await repo.get(s, view_id))
            _may_change(caller, before)
            share = before["shared"] if shared is None else shared
            if share != before["shared"]:
                # Sharing or unsharing needs the right to change shared views; only the
                # owner may share their personal view (it is theirs until then).
                if not caller.can(Action.share_views):
                    raise Forbidden(CANT_SHARE)
            stored = await _filters(s, filters) if filters is not None else before["filters"]
            await repo.update(
                s, view_id, name=clean or before["name"], filters=stored, shared=share
            )
            row = await repo.get(s, view_id)
            assert row is not None
            changed = audit_rules.changes(before, row, fields=("name", "filters", "shared"))
            if changed:
                await audit.record(
                    s, caller, Area.views, "changed", f"View {row['name']}", target_id=view_id,
                    detail={"changes": changed},
                )  # fmt: skip
            return row
    except IntegrityError as exc:
        raise _taken(share) from exc


async def delete_view(sm: Sessionmaker, caller: Caller, view_id: UUID) -> None:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        before = _visible(caller, await repo.get(s, view_id))
        _may_change(caller, before)
        if not await repo.delete(s, view_id):
            raise NotFound(NOT_FOUND)
        await audit.record(
            s, caller, Area.views, "deleted", f"View {before['name']}", target_id=view_id,
            detail=audit_rules.snapshot(before, ["name", "filters", "shared"]),
        )  # fmt: skip
