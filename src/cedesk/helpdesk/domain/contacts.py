"""Contacts (feature-gap plan phase 2), the rules that need no database: the contact
list's filters, sort and cursor (2.1), notes (2.2), and the activity timeline's cursor
(2.7).

The list's filters are checked in one place (`list_filters`), so a segment (2.6) can only
hold what the list accepts, as saved ticket views do with the ticket list.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from cedesk.helpdesk.domain import paging
from cedesk.helpdesk.domain.contact_fields import (
    AttrValue,
    ContactField,
    ContactFieldError,
    clean_value,
    from_text,
)
from cedesk.helpdesk.domain.paging import CursorError

Q_MAX = 200
NOTE_MAX = 5000
MAX_ATTR_FILTERS = 10
NAME_MAX = 120  # hd_contact.name
PHONE_MAX = 50
ORGANIZATION_MAX = 200


class ContactError(ValueError):
    """The message is shown to the member."""


class ContactSort(StrEnum):
    """`name`: A to Z. `recent`: latest activity first (a ticket changed, or the contact
    was made). `tickets`: most tickets first."""

    name = "name"
    recent = "recent"
    tickets = "tickets"


FILTER_KEYS = ("q", "sort", "vip", "has_open", "created_from", "created_to", "attributes")


@dataclass(frozen=True)
class ContactFilters:
    q: str | None = None
    sort: ContactSort = ContactSort.name
    vip: bool | None = None
    has_open: bool | None = None  # has a ticket that isn't resolved
    created_from: date | None = None  # contact made on or after this day (UTC)
    created_to: date | None = None  # ... on or before this day
    attributes: tuple[tuple[str, AttrValue], ...] = ()  # each: the value equals this

    def stored(self) -> dict[str, Any]:
        """As a segment keeps them: only the filters that are set, as JSON values."""
        out: dict[str, Any] = {}
        defaults = ContactFilters()
        for key in FILTER_KEYS:
            value = getattr(self, key)
            if value is None or value == getattr(defaults, key):
                continue
            if key == "attributes":
                out[key] = dict(value)
            elif isinstance(value, date):
                out[key] = value.isoformat()
            elif isinstance(value, StrEnum):
                out[key] = value.value
            else:
                out[key] = value
        return out

    def attribute_json(self) -> dict[str, AttrValue] | None:
        """For `attributes @> ...`: every value must match (None: no attribute filter)."""
        return dict(self.attributes) if self.attributes else None


def _day(key: str, value: object) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            pass
    raise ContactError(f"{key} is a date (YYYY-MM-DD).")


def list_filters(
    fields: Sequence[ContactField],
    *,
    q: str | None = None,
    sort: ContactSort = ContactSort.name,
    vip: bool | None = None,
    has_open: bool | None = None,
    created_from: date | None = None,
    created_to: date | None = None,
    attributes: Mapping[str, object] | None = None,
) -> ContactFilters:
    """Checks what the list route (or a segment) was given; raises ContactError.
    `attributes`: field key to the value it must equal; text values (from a query string)
    are read by the field's kind."""
    q = (q or "").strip() or None
    if q is not None and len(q) > Q_MAX:
        raise ContactError(f"q is at most {Q_MAX} characters.")
    if created_from and created_to and created_from > created_to:
        raise ContactError("created_from is after created_to.")
    by_key = {f.key: f for f in fields}
    attrs: list[tuple[str, AttrValue]] = []
    for key, raw in (attributes or {}).items():
        f = by_key.get(key)
        if f is None:
            raise ContactError(f"No contact field {key}.")
        try:
            value = from_text(f, raw) if isinstance(raw, str) else _typed(f, raw)
        except ContactFieldError as exc:
            raise ContactError(str(exc)) from exc
        if value is None:
            raise ContactError(f"Filter on {key} needs a value.")
        attrs.append((key, value))
    if len(attrs) > MAX_ATTR_FILTERS:
        raise ContactError(f"At most {MAX_ATTR_FILTERS} attribute filters.")
    return ContactFilters(q, sort, vip, has_open, created_from, created_to, tuple(sorted(attrs)))


def _typed(f: ContactField, raw: object) -> AttrValue | None:
    if raw is None:
        return None
    return clean_value(f, raw)


def parse_attribute_params(raw: Sequence[str]) -> dict[str, str]:
    """`?attr=plan:pro&attr=trial:true` as {key: text}. The key is before the first `:`."""
    out: dict[str, str] = {}
    for item in raw:
        key, sep, value = item.partition(":")
        key = key.strip()
        if not sep or not key:
            raise ContactError("attr is key:value, for example attr=plan:pro.")
        if key in out:
            raise ContactError(f"attr {key} is given twice.")
        out[key] = value
    return out


def filters_from_json(fields: Sequence[ContactField], raw: Mapping[str, object]) -> ContactFilters:
    """A segment's filters: only the list's keys (FILTER_KEYS), each a value the list
    route would take. Unknown keys, wrong types and bad values raise ContactError."""
    unknown = sorted(set(raw) - set(FILTER_KEYS))
    if unknown:
        raise ContactError(
            f"A segment can't filter on {', '.join(unknown)}. Use: {', '.join(FILTER_KEYS)}."
        )
    args: dict[str, Any] = {}
    for key, value in raw.items():
        if value is None:
            continue
        if key == "sort":
            if not isinstance(value, str) or value not in {s.value for s in ContactSort}:
                raise ContactError("sort is one of: " + ", ".join(s.value for s in ContactSort))
            args[key] = ContactSort(value)
        elif key in ("vip", "has_open"):
            if not isinstance(value, bool):
                raise ContactError(f"{key} is true or false.")
            args[key] = value
        elif key in ("created_from", "created_to"):
            args[key] = _day(key, value)
        elif key == "attributes":
            if not isinstance(value, Mapping):
                raise ContactError("attributes is an object of field key to value.")
            args[key] = dict(value)
        else:  # q
            if not isinstance(value, str):
                raise ContactError(f"{key} is text.")
            args[key] = value
    return list_filters(fields, **args)


def fields_used(filters: Mapping[str, object]) -> set[str]:
    """The contact field keys a stored filter set names (to refuse deleting one)."""
    attrs = filters.get("attributes")
    return set(attrs) if isinstance(attrs, Mapping) else set()


# Cursor pagination (md 2B). Each sort has one key plus the contact id as a tie-break.

CursorKey = str | int


def encode_cursor(sort: ContactSort, key: CursorKey, contact_id: UUID) -> str:
    return paging.encode(["contacts", sort.value, key, str(contact_id)])


def decode_cursor(cursor: str, sort: ContactSort) -> tuple[CursorKey, UUID]:
    value = paging.decode(cursor)
    try:
        name, kind, key, row_id = value
    except ValueError as exc:
        raise CursorError("This cursor is not valid.") from exc
    if name != "contacts" or kind != sort.value:
        raise CursorError("This cursor belongs to another list or sort order.")
    try:
        parsed_id = UUID(str(row_id))
    except ValueError as exc:
        raise CursorError("This cursor is not valid.") from exc
    if sort is ContactSort.tickets:
        if type(key) is not int or not 0 <= key < 2**31:
            raise CursorError("This cursor is not valid.")
    elif sort is ContactSort.recent:
        try:
            if not isinstance(key, str) or datetime.fromisoformat(key).tzinfo is None:
                raise CursorError("This cursor is not valid.")
        except ValueError as exc:
            raise CursorError("This cursor is not valid.") from exc
    elif not isinstance(key, str) or len(key) > NAME_MAX * 4:
        raise CursorError("This cursor is not valid.")
    return key, parsed_id


# Notes (2.2)


def clean_note(raw: str) -> str:
    body = raw.strip()
    if not 1 <= len(body) <= NOTE_MAX:
        raise ContactError(f"A note is 1-{NOTE_MAX} characters.")
    return body


def clean_name(raw: str) -> str:
    name = " ".join(raw.split())
    if not 1 <= len(name) <= NAME_MAX:
        raise ContactError(f"A name is 1-{NAME_MAX} characters.")
    return name


def clean_optional(raw: str | None, label: str, limit: int) -> str | None:
    value = (raw or "").strip()
    if len(value) > limit:
        raise ContactError(f"{label} is at most {limit} characters.")
    return value or None


# The activity timeline (2.7): newest first; a cursor is the last item's time, kind and id.

TIMELINE_KINDS = (
    "ticket_created",
    "ticket_resolved",
    "message",
    "csat",
    "note",
    "merge",
)
_KIND = re.compile(r"^[a-z_]{1,20}$")


def encode_timeline(at: datetime, kind: str, item_id: UUID) -> str:
    return paging.encode(["timeline", at.isoformat(), kind, str(item_id)])


def decode_timeline(cursor: str) -> tuple[datetime, str, UUID]:
    value = paging.decode(cursor)
    try:
        name, at, kind, item_id = value
        if name != "timeline":
            raise CursorError("This cursor belongs to another list.")
        parsed = datetime.fromisoformat(str(at))
        if parsed.tzinfo is None or not isinstance(kind, str) or not _KIND.match(kind):
            raise CursorError("This cursor is not valid.")
        return parsed, kind, UUID(str(item_id))
    except CursorError:
        raise
    except (ValueError, TypeError) as exc:
        raise CursorError("This cursor is not valid.") from exc


def preview(body: str, limit: int = 200) -> str:
    """The start of a message, on one line, for the timeline."""
    flat = " ".join(body.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"
