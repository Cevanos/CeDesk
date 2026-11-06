"""Teams and ticket types: everyone reads them, only Admins change them."""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.tickets import FieldDef, FieldError, FieldKind, check_definitions
from cedesk.helpdesk.repo import catalog as repo
from cedesk.helpdesk.repo.catalog import Row
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
from cedesk.helpdesk.service.members import require


def field_defs(raw: Sequence[Any]) -> list[FieldDef]:
    """Stored or submitted field dicts as `FieldDef`s."""
    try:
        return [
            FieldDef(
                key=str(f["key"]),
                label=str(f["label"]),
                kind=FieldKind(f["kind"]),
                options=tuple(str(o) for o in f.get("options") or ()),
                required=bool(f.get("required", False)),
            )
            for f in raw
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise Invalid("Each field needs a key, a label and a kind (text, number, select).") from exc


def _stored(defs: Sequence[FieldDef]) -> list[dict[str, Any]]:
    return [
        {
            "key": d.key,
            "label": d.label.strip(),
            "kind": d.kind.value,
            "options": [o.strip() for o in d.options],
            "required": d.required,
        }
        for d in defs
    ]


def _checked(raw: Sequence[Any]) -> list[dict[str, Any]]:
    defs = field_defs(raw)
    try:
        check_definitions(defs)
    except FieldError as exc:
        raise Invalid(str(exc)) from exc
    return _stored(defs)


# Teams


async def list_teams(sm: Sessionmaker, caller: Caller) -> Sequence[Row]:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await repo.list_teams(s)


async def _check_members(s: AsyncSession, member_ids: Sequence[UUID]) -> None:
    missing = set(member_ids) - await repo.active_members(s, member_ids)
    if missing:
        raise Invalid("Every team member must be an active member of this workspace.")


async def create_team(
    sm: Sessionmaker,
    caller: Caller,
    *,
    name: str,
    auto_assign: bool,
    max_open: int,
    member_ids: Sequence[UUID],
) -> Row:
    require(caller, Action.settings)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            await _check_members(s, member_ids)
            team_id = await repo.insert_team(s, caller.workspace_id, name, auto_assign, max_open)
            await repo.set_team_members(
                s, caller.workspace_id, team_id, list(dict.fromkeys(member_ids))
            )
            row = await repo.get_team(s, team_id)
    except IntegrityError as exc:
        raise Conflict("A team with that name already exists.") from exc
    assert row is not None
    return row


async def change_team(
    sm: Sessionmaker,
    caller: Caller,
    team_id: UUID,
    *,
    name: str | None,
    auto_assign: bool | None,
    max_open: int | None,
    member_ids: Sequence[UUID] | None,
) -> Row:
    require(caller, Action.settings)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            if not await repo.update_team(
                s, team_id, name=name, auto_assign=auto_assign, max_open=max_open
            ):
                raise NotFound("Team not found.")
            if member_ids is not None:
                await _check_members(s, member_ids)
                await repo.set_team_members(
                    s, caller.workspace_id, team_id, list(dict.fromkeys(member_ids))
                )
            row = await repo.get_team(s, team_id)
    except IntegrityError as exc:
        raise Conflict("A team with that name already exists.") from exc
    assert row is not None
    return row


async def delete_team(sm: Sessionmaker, caller: Caller, team_id: UUID) -> None:
    """Tickets of a deleted team keep their other details and lose only the team."""
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        if not await repo.delete_team(s, team_id):
            raise NotFound("Team not found.")


# Ticket types


async def list_types(sm: Sessionmaker, caller: Caller) -> Sequence[Row]:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await repo.list_types(s)


async def create_type(
    sm: Sessionmaker,
    caller: Caller,
    *,
    name: str,
    fields: Sequence[Any],
    required_skill: str | None,
) -> Row:
    require(caller, Action.settings)
    stored = _checked(fields)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            type_id = await repo.insert_type(s, caller.workspace_id, name, stored, required_skill)
            row = await repo.get_type(s, type_id)
    except IntegrityError as exc:
        raise Conflict("A ticket type with that name already exists.") from exc
    assert row is not None
    return row


async def change_type(
    sm: Sessionmaker,
    caller: Caller,
    type_id: UUID,
    *,
    name: str | None,
    fields: Sequence[Any] | None,
    required_skill: str | None,
    clear_skill: bool,
) -> Row:
    """Rename, replace the field list (add, remove, rename or reorder fields), or set the skill.

    Values of a removed field stay on old tickets but are no longer shown or checked.
    """
    require(caller, Action.settings)
    stored = None if fields is None else _checked(fields)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            if not await repo.update_type(
                s,
                type_id,
                name=name,
                fields=stored,
                required_skill=required_skill,
                clear_skill=clear_skill,
            ):
                raise NotFound("Ticket type not found.")
            row = await repo.get_type(s, type_id)
    except IntegrityError as exc:
        raise Conflict("A ticket type with that name already exists.") from exc
    assert row is not None
    return row


async def delete_type(sm: Sessionmaker, caller: Caller, type_id: UUID) -> None:
    """Tickets of a deleted type keep their other details and lose only the type."""
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        if not await repo.delete_type(s, type_id):
            raise NotFound("Ticket type not found.")


async def reorder_types(
    sm: Sessionmaker, caller: Caller, type_ids: Sequence[UUID]
) -> Sequence[Row]:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        current = {r["id"] for r in await repo.list_types(s)}
        if set(type_ids) != current or len(type_ids) != len(current):
            raise Invalid("Send every ticket type id exactly once, in the new order.")
        await repo.reorder_types(s, type_ids)
        return await repo.list_types(s)
