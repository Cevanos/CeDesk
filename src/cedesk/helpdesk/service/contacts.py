"""Contacts (feature-gap plan phase 2): the list and search (2.1), one contact, private
notes (2.2), attribute values (2.3) and the activity timeline (2.7).

- Everyone who reads tickets reads contacts (a Light agent too: read-only, plus notes,
  as on tickets). Erased customers (md S11) are left out of the list.
- Notes: anyone with `private_note` adds one; its author changes or deletes it, and so
  does a role with `manage_contacts` (Admins).
- Attribute values: the right to change ticket fields (`change_fields`), as the
  customer's name and VIP flag. Checked against the workspace's contact fields.
- Audit entries name the contact by id and hold no customer words: a note's text and an
  attribute's value never go into the log (it can't be changed later, md S11).
- The timeline's ticket items follow the caller's team scope (a team-only role sees only
  its team's tickets there), as the customer's ticket list does.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import contact_fields as field_rules
from cedesk.helpdesk.domain import contacts as rules
from cedesk.helpdesk.domain import paging
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.contact_fields import ContactField, ContactFieldKind
from cedesk.helpdesk.domain.contacts import ContactFilters, ContactSort
from cedesk.helpdesk.domain.paging import CursorError
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import contact_fields as field_repo
from cedesk.helpdesk.repo import contacts as repo
from cedesk.helpdesk.repo.contacts import Row
from cedesk.helpdesk.service import audit
from cedesk.helpdesk.service import scope as scope_rules
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Forbidden, Invalid, NotFound
from cedesk.helpdesk.service.members import require

NOT_FOUND = "Customer not found."
NOTE_NOT_FOUND = "Note not found."
LATEST_NOTES = 5


def contact_target(contact_id: UUID) -> str:
    """How the audit log names a customer: by id only (md S11)."""
    return f"Customer {contact_id}"


async def fields(s: AsyncSession) -> list[ContactField]:
    return [
        ContactField(
            key=str(r["key"]),
            label=str(r["label"]),
            kind=ContactFieldKind(r["kind"]),
            options=tuple(r["options"] or ()),
        )
        for r in await field_repo.all_fields(s)
    ]


def _like(q: str) -> str:
    escaped = q.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return f"%{escaped}%"


def query_filters(f: ContactFilters) -> dict[str, Any]:
    """The checked filters as the repo takes them."""
    return {
        "vip": f.vip,
        "has_open": f.has_open,
        "created_from": f.created_from,
        "created_to": f.created_to,
        "attrs": f.attribute_json(),
        "q_like": _like(f.q) if f.q else None,
    }


def check_filters(
    defs: Sequence[ContactField],
    *,
    q: str | None,
    sort: ContactSort,
    vip: bool | None,
    has_open: bool | None,
    created_from: date | None,
    created_to: date | None,
    attr: Sequence[str],
) -> ContactFilters:
    try:
        attributes = rules.parse_attribute_params(attr)
        return rules.list_filters(
            defs,
            q=q,
            sort=sort,
            vip=vip,
            has_open=has_open,
            created_from=created_from,
            created_to=created_to,
            attributes=attributes,
        )
    except rules.ContactError as exc:
        raise Invalid(str(exc)) from exc


@dataclass(frozen=True)
class ContactPage:
    contacts: Sequence[Row]
    next_cursor: str | None


def _cursor_key(sort: ContactSort, row: Row) -> str | int:
    value = row["sort_key"]
    if sort is ContactSort.tickets:
        return int(value)
    if sort is ContactSort.recent:
        return value.isoformat()  # type: ignore[no-any-return]
    return str(value)


async def page(
    s: AsyncSession, f: ContactFilters, *, cursor: str | None, limit: int
) -> ContactPage:
    """One page of the contacts matching `f`, in its sort order."""
    after: tuple[Any, UUID] | None = None
    if cursor:
        try:
            key, row_id = rules.decode_cursor(cursor, f.sort)
        except CursorError as exc:
            raise Invalid(str(exc)) from exc
        after = (datetime.fromisoformat(str(key)) if f.sort is ContactSort.recent else key, row_id)
    rows = await repo.list_contacts(
        s, filters=query_filters(f), sort=f.sort.value, after=after, limit=limit + 1
    )
    found = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = found[-1]
        next_cursor = rules.encode_cursor(f.sort, _cursor_key(f.sort, last), last["id"])
    return ContactPage(found, next_cursor)


async def list_contacts(
    sm: Sessionmaker,
    caller: Caller,
    *,
    q: str | None = None,
    sort: ContactSort = ContactSort.name,
    vip: bool | None = None,
    has_open: bool | None = None,
    created_from: date | None = None,
    created_to: date | None = None,
    attr: Sequence[str] = (),
    cursor: str | None = None,
    limit: int = 50,
) -> ContactPage:
    """Contacts matching every filter given; pass `next_cursor` back as `cursor` with the
    same filters and sort for the next page."""
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        f = check_filters(
            await fields(s),
            q=q,
            sort=sort,
            vip=vip,
            has_open=has_open,
            created_from=created_from,
            created_to=created_to,
            attr=attr,
        )
        return await page(s, f, cursor=cursor, limit=limit)


@dataclass(frozen=True)
class ContactDetail:
    contact: Row
    fields: list[ContactField]
    notes: Sequence[Row]


async def _get(s: AsyncSession, contact_id: UUID, *, lock: bool = False) -> Row:
    row = await repo.get(s, contact_id, lock=lock)
    if row is None:
        raise NotFound(NOT_FOUND)
    return row


async def get_contact(sm: Sessionmaker, caller: Caller, contact_id: UUID) -> ContactDetail:
    """The contact, the workspace's contact fields (for showing its attributes) and its
    newest notes."""
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        contact = await _get(s, contact_id)
        latest = await repo.notes(s, contact_id, after=None, limit=LATEST_NOTES)
        return ContactDetail(contact, await fields(s), latest)


async def set_attributes(
    sm: Sessionmaker, caller: Caller, contact_id: UUID, changes: Mapping[str, object]
) -> ContactDetail:
    """Sets (or with null / "" clears) the values given; the others stay."""
    require(caller, Action.change_fields)
    async with workspace_session(sm, caller.workspace_id) as s:
        contact = await _get(s, contact_id, lock=True)
        if contact["erased_at"] is not None:
            raise Invalid("This customer was erased.")
        defs = await fields(s)
        try:
            cleaned = field_rules.clean_changes(defs, changes)
        except field_rules.ContactFieldError as exc:
            raise Invalid(str(exc)) from exc
        values = dict(contact["attributes"] or {})
        for key, value in cleaned.items():
            if value is None:
                values.pop(key, None)
            else:
                values[key] = value
        changed = sorted(
            k
            for k in set(values) | set(contact["attributes"] or {})
            if values.get(k) != (contact["attributes"] or {}).get(k)
        )
        if changed:
            await repo.set_attributes(s, contact_id, values)
            # Which fields changed, never their values (customer data, md S11).
            await audit.record(
                s,
                caller,
                Area.contacts,
                "attributes_changed",
                contact_target(contact_id),
                target_id=contact_id,
                detail={"fields": changed},
            )
        latest = await repo.notes(s, contact_id, after=None, limit=LATEST_NOTES)
        return ContactDetail(await _get(s, contact_id), defs, latest)


# ---- notes ----------------------------------------------------------------------------


def can_change_note(caller: Caller, note: Row) -> bool:
    """The author (while they still may write notes), or Admins (`manage_contacts`)."""
    if caller.can(Action.manage_contacts):
        return True
    return note["author_id"] == caller.member_id and caller.can(Action.private_note)


def _body(raw: str) -> str:
    try:
        return rules.clean_note(raw)
    except rules.ContactError as exc:
        raise Invalid(str(exc)) from exc


async def list_notes(
    sm: Sessionmaker, caller: Caller, contact_id: UUID, *, cursor: str | None, limit: int
) -> tuple[Sequence[Row], str | None]:
    """Newest first."""
    require(caller, Action.read_ticket)
    after = None
    if cursor:
        try:
            after = paging.decode_time(cursor, "contact_note")
        except CursorError as exc:
            raise Invalid(str(exc)) from exc
    async with workspace_session(sm, caller.workspace_id) as s:
        await _get(s, contact_id)
        rows = await repo.notes(s, contact_id, after=after, limit=limit + 1)
    found, next_cursor = paging.time_page(rows, limit, "contact_note")
    return found, next_cursor


async def add_note(sm: Sessionmaker, caller: Caller, contact_id: UUID, body: str) -> Row:
    require(caller, Action.private_note)
    text = _body(body)
    async with workspace_session(sm, caller.workspace_id) as s:
        contact = await _get(s, contact_id)
        if contact["erased_at"] is not None:
            raise Invalid("This customer was erased.")
        note_id = await repo.insert_note(s, caller.workspace_id, contact_id, caller.member_id, text)
        await audit.record(
            s,
            caller,
            Area.contacts,
            "note_added",
            contact_target(contact_id),
            target_id=contact_id,
            detail={"note_id": str(note_id)},
        )
        row = await repo.note(s, contact_id, note_id)
    assert row is not None
    return row


async def _note_to_change(s: AsyncSession, caller: Caller, contact_id: UUID, note_id: UUID) -> Row:
    note = await repo.note(s, contact_id, note_id)
    if note is None:
        raise NotFound(NOTE_NOT_FOUND)
    if not can_change_note(caller, note):
        raise Forbidden("Only the note's author or an Admin can change it.")
    return note


async def change_note(
    sm: Sessionmaker, caller: Caller, contact_id: UUID, note_id: UUID, body: str
) -> Row:
    require(caller, Action.read_ticket)
    text = _body(body)
    async with workspace_session(sm, caller.workspace_id) as s:
        before = await _note_to_change(s, caller, contact_id, note_id)
        if before["body"] != text:
            await repo.update_note(s, note_id, text)
            await audit.record(
                s,
                caller,
                Area.contacts,
                "note_changed",
                contact_target(contact_id),
                target_id=contact_id,
                detail={"note_id": str(note_id)},
            )
        row = await repo.note(s, contact_id, note_id)
    assert row is not None
    return row


async def delete_note(sm: Sessionmaker, caller: Caller, contact_id: UUID, note_id: UUID) -> None:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        before = await _note_to_change(s, caller, contact_id, note_id)
        await repo.delete_note(s, note_id)
        await audit.record(
            s,
            caller,
            Area.contacts,
            "note_deleted",
            contact_target(contact_id),
            target_id=contact_id,
            detail={"note_id": str(note_id), "author_id": str(before["author_id"])},
        )


# ---- timeline -------------------------------------------------------------------------


async def timeline(
    sm: Sessionmaker,
    caller: Caller,
    contact_id: UUID,
    *,
    kinds: Sequence[str] | None,
    cursor: str | None,
    limit: int,
) -> tuple[Sequence[Row], str | None]:
    """Everything that happened with this customer, newest first."""
    require(caller, Action.read_ticket)
    unknown = sorted(set(kinds or ()) - set(rules.TIMELINE_KINDS))
    if unknown:
        raise Invalid("kind is one of: " + ", ".join(rules.TIMELINE_KINDS) + ".")
    after = None
    if cursor:
        try:
            after = rules.decode_timeline(cursor)
        except CursorError as exc:
            raise Invalid(str(exc)) from exc
    async with workspace_session(sm, caller.workspace_id) as s:
        await _get(s, contact_id)
        rows = await repo.timeline(
            s,
            contact_id,
            visible_team=scope_rules.visible_team(caller),
            kinds=list(kinds) if kinds else None,
            after=after,
            limit=limit + 1,
        )
    found = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = found[-1]
        next_cursor = rules.encode_timeline(last["at"], last["kind"], last["id"])
    return found, next_cursor
