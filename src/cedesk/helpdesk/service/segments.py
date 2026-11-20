"""Segments (feature-gap plan 2.6): saved contact-list filters shared by the workspace,
usable later as a recipient list (`contacts`).

- Everyone who reads tickets lists segments and their contacts.
- `manage_segments` (Admins and Supervisors; a custom role with the settings right)
  makes, changes and deletes them. Audited (area `segments`); the filters are logged
  except a search's words (they may be a customer's name or address).
- The filters are `GET /contacts`' own, checked with the same rules
  (`contacts.filters_from_json`); who is in a segment is worked out when it is read, so
  an erased customer is never in one.
"""

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import audit as audit_rules
from cedesk.helpdesk.domain import contacts as contact_rules
from cedesk.helpdesk.domain import saved_views as name_rules
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.contacts import ContactFilters
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import contacts as contact_repo
from cedesk.helpdesk.repo import segments as repo
from cedesk.helpdesk.repo.segments import Row
from cedesk.helpdesk.service import audit
from cedesk.helpdesk.service import contacts as contact_service
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
from cedesk.helpdesk.service.members import require

NOT_FOUND = "Segment not found."
TAKEN = "A segment with that name already exists."
MAX_SEGMENTS = 200  # per workspace


def _name(raw: str) -> str:
    try:
        return name_rules.clean_name(raw)
    except name_rules.ViewError as exc:
        raise Invalid(str(exc).replace("view", "segment")) from exc


async def _filters(s: AsyncSession, raw: Mapping[str, Any]) -> ContactFilters:
    try:
        return contact_rules.filters_from_json(await contact_service.fields(s), raw)
    except contact_rules.ContactError as exc:
        raise Invalid(str(exc)) from exc


def _logged(filters: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(filters)
    if out.pop("q", None) is not None:
        out["search"] = True
    return out


async def _get(s: AsyncSession, segment_id: UUID) -> Row:
    row = await repo.get(s, segment_id)
    if row is None:
        raise NotFound(NOT_FOUND)
    return row


async def list_segments(sm: Sessionmaker, caller: Caller) -> Sequence[Row]:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await repo.all_segments(s)


async def get_segment(sm: Sessionmaker, caller: Caller, segment_id: UUID) -> tuple[Row, int]:
    """The segment and how many contacts are in it now."""
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await _get(s, segment_id)
        f = await _filters(s, row["filters"])
        count = await contact_repo.count_contacts(s, filters=contact_service.query_filters(f))
        return row, count


async def create_segment(
    sm: Sessionmaker, caller: Caller, *, name: str, filters: Mapping[str, Any]
) -> Row:
    require(caller, Action.manage_segments)
    clean = _name(name)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            stored = (await _filters(s, filters)).stored()
            if await repo.count(s) >= MAX_SEGMENTS:
                raise Invalid(f"A workspace can have at most {MAX_SEGMENTS} segments.")
            segment_id = await repo.insert(
                s, caller.workspace_id, name=clean, filters=stored, created_by=caller.member_id
            )
            await audit.record(
                s,
                caller,
                Area.segments,
                "created",
                f"Segment {clean}",
                target_id=segment_id,
                detail={"name": clean, "filters": _logged(stored)},
            )
            return await _get(s, segment_id)
    except IntegrityError as exc:
        raise Conflict(TAKEN) from exc


async def change_segment(
    sm: Sessionmaker,
    caller: Caller,
    segment_id: UUID,
    *,
    name: str | None,
    filters: Mapping[str, Any] | None,
) -> Row:
    require(caller, Action.manage_segments)
    clean = None if name is None else _name(name)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            before = await _get(s, segment_id)
            stored = (
                (await _filters(s, filters)).stored() if filters is not None else before["filters"]
            )
            await repo.update(s, segment_id, name=clean or before["name"], filters=stored)
            row = await _get(s, segment_id)
            changed = audit_rules.changes(
                {"name": before["name"], "filters": _logged(before["filters"])},
                {"name": row["name"], "filters": _logged(row["filters"])},
            )
            if changed:
                await audit.record(
                    s,
                    caller,
                    Area.segments,
                    "changed",
                    f"Segment {row['name']}",
                    target_id=segment_id,
                    detail={"changes": changed},
                )
            return row
    except IntegrityError as exc:
        raise Conflict(TAKEN) from exc


async def delete_segment(sm: Sessionmaker, caller: Caller, segment_id: UUID) -> None:
    require(caller, Action.manage_segments)
    async with workspace_session(sm, caller.workspace_id) as s:
        before = await _get(s, segment_id)
        await repo.delete(s, segment_id)
        await audit.record(
            s,
            caller,
            Area.segments,
            "deleted",
            f"Segment {before['name']}",
            target_id=segment_id,
            detail={"name": before["name"], "filters": _logged(before["filters"])},
        )


async def segment_contacts(
    sm: Sessionmaker, caller: Caller, segment_id: UUID, *, cursor: str | None, limit: int
) -> tuple[Row, contact_service.ContactPage]:
    """The contacts in the segment now, in its sort order, a page at a time."""
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        row = await _get(s, segment_id)
        f = await _filters(s, row["filters"])
        return row, await contact_service.page(s, f, cursor=cursor, limit=limit)
