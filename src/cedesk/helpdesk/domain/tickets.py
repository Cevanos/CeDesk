"""Ticket rules that need no database: statuses, priorities, custom fields, tags, numbers."""

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from cedesk.helpdesk.domain import paging
from cedesk.helpdesk.domain.paging import CursorError
from cedesk.helpdesk.domain.reports import IMPORT_SOURCES


class Status(StrEnum):
    open = "open"
    pending = "pending"  # customers see "Waiting on you"
    resolved = "resolved"


class Priority(StrEnum):
    urgent = "urgent"
    high = "high"
    medium = "medium"
    low = "low"


class Channel(StrEnum):
    email = "email"
    web = "web"
    portal = "portal"
    api = "api"
    chat = "chat"
    phone = "phone"


class Sentiment(StrEnum):
    """Set by triage (keyword rules, Phase 2) or the Cevanos handover."""

    negative = "negative"
    neutral = "neutral"
    positive = "positive"


LANGUAGE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")  # "hi", "en", "en-IN"
SOURCE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")  # "cevanos", "intercom", "email"


class Sort(StrEnum):
    """Ticket list orders. `priority`: Open and Pending most urgent first, then oldest;
    Resolved after them, newest resolved first. `sla`: Open and Pending by SLA time left,
    soonest due (or most overdue) first; Resolved after them, newest resolved first (T9)."""

    priority = "priority"
    sla = "sla"
    newest = "newest"
    oldest = "oldest"
    updated = "updated"  # most recently changed first


class FieldKind(StrEnum):
    text = "text"
    number = "number"
    select = "select"


class FieldError(ValueError):
    """A ticket type's field list, or a ticket's field values, break the rules."""


MAX_FIELDS = 30
MAX_OPTIONS = 50
TEXT_MAX = 2000
MAX_TAGS = 20
FIELD_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
TAG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
NUMBER_QUERY = re.compile(r"^(?:t-?)?(\d{1,9})$", re.IGNORECASE)

FieldValue = str | int | float


@dataclass(frozen=True)
class FieldDef:
    key: str
    label: str
    kind: FieldKind
    options: tuple[str, ...] = ()
    required: bool = False


def check_definitions(defs: Sequence[FieldDef]) -> None:
    if len(defs) > MAX_FIELDS:
        raise FieldError(f"A ticket type can have at most {MAX_FIELDS} fields.")
    keys = [d.key for d in defs]
    if len(set(keys)) != len(keys):
        raise FieldError("Two fields use the same key.")
    for d in defs:
        if not FIELD_KEY.match(d.key):
            raise FieldError(
                f"Field key '{d.key}' must start with a letter and use a-z, 0-9 or _ (max 40)."
            )
        if not 1 <= len(d.label.strip()) <= 80:
            raise FieldError(f"Field '{d.key}' needs a label of 1-80 characters.")
        if d.kind is FieldKind.select:
            if not 1 <= len(d.options) <= MAX_OPTIONS:
                raise FieldError(f"Select field '{d.key}' needs 1-{MAX_OPTIONS} options.")
            if len(set(d.options)) != len(d.options) or any(not o.strip() for o in d.options):
                raise FieldError(f"Select field '{d.key}' has empty or repeated options.")
        elif d.options:
            raise FieldError(f"Only select fields have options ('{d.key}').")


def clean_values(defs: Sequence[FieldDef], values: Mapping[str, object]) -> dict[str, FieldValue]:
    """Checks every value against its field and returns the values to store."""
    by_key = {d.key: d for d in defs}
    unknown = sorted(set(values) - set(by_key))
    if unknown:
        raise FieldError(f"This ticket type has no field {', '.join(unknown)}.")
    cleaned: dict[str, FieldValue] = {}
    for d in defs:
        value = values.get(d.key)
        if isinstance(value, str):
            value = value.strip()
        if value is None or value == "":
            if d.required:
                raise FieldError(f"{d.label} is required.")
            continue
        cleaned[d.key] = _clean_one(d, value)
    return cleaned


def clean_changes(
    defs: Sequence[FieldDef], changes: Mapping[str, object]
) -> dict[str, FieldValue | None]:
    """Checks only the values being changed; None means "clear this field".

    Values already on the ticket are not re-checked, so a ticket whose type changed
    later (a field became a number, an option was removed) stays editable."""
    by_key = {d.key: d for d in defs}
    unknown = sorted(set(changes) - set(by_key))
    if unknown:
        raise FieldError(f"This ticket type has no field {', '.join(unknown)}.")
    cleaned: dict[str, FieldValue | None] = {}
    for key, value in changes.items():
        d = by_key[key]
        if isinstance(value, str):
            value = value.strip()
        if value is None or value == "":
            if d.required:
                raise FieldError(f"{d.label} is required.")
            cleaned[key] = None
        else:
            cleaned[key] = _clean_one(d, value)
    return cleaned


def carry_over(
    defs: Sequence[FieldDef], values: Mapping[str, object]
) -> tuple[dict[str, FieldValue], dict[str, str]]:
    """On a type change: the values that still fit the new type's fields, and every
    other key with why it was dropped ("missing": no such field, "invalid": wrong kind
    or option)."""
    by_key = {d.key: d for d in defs}
    kept: dict[str, FieldValue] = {}
    dropped: dict[str, str] = {}
    for key, value in values.items():
        d = by_key.get(key)
        if d is None:
            dropped[key] = "missing"
            continue
        try:
            kept[key] = _clean_one(d, value)
        except FieldError:
            dropped[key] = "invalid"
    return kept, dropped


def first_missing_required(
    defs: Sequence[FieldDef], values: Mapping[str, object]
) -> FieldDef | None:
    return next((d for d in defs if d.required and values.get(d.key) in (None, "")), None)


def _clean_one(d: FieldDef, value: object) -> FieldValue:
    if d.kind is FieldKind.text:
        if not isinstance(value, str) or len(value) > TEXT_MAX:
            raise FieldError(f"{d.label} must be text of at most {TEXT_MAX} characters.")
        return value
    if d.kind is FieldKind.number:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise FieldError(f"{d.label} must be a number.")
        if not math.isfinite(value):
            raise FieldError(f"{d.label} must be a number.")
        return value
    if not isinstance(value, str) or value not in d.options:
        raise FieldError(f"{d.label} must be one of: {', '.join(d.options)}.")
    return value


def clean_tags(tags: Sequence[str]) -> list[str]:
    """Lower-case, spaces become dashes, duplicates dropped, order kept."""
    seen: list[str] = []
    for raw in tags:
        tag = "-".join(raw.strip().lower().split())
        if not TAG.match(tag):
            raise FieldError(f"Tag '{raw}' must use a-z, 0-9, - or _ (max 40).")
        if tag not in seen:
            seen.append(tag)
    if len(seen) > MAX_TAGS:
        raise FieldError(f"A ticket can have at most {MAX_TAGS} tags.")
    return seen


def label(number: int) -> str:
    return f"T-{number}"


def number_in_query(q: str) -> int | None:
    """'T-1005', 't1005' and '1005' all find ticket 1005."""
    match = NUMBER_QUERY.match(q.strip())
    return int(match.group(1)) if match else None


def title(value: str) -> str:
    return value.replace("_", " ").capitalize()


# Cursor pagination (md 2B). The list query turns every sort into four ascending keys
# (see repo.tickets), so "the rows after this one" is a single row comparison. The cursor
# is those keys for the last row returned, plus the sort they belong to.

CursorKey = int | str  # whole numbers as int, exact decimals as text
_DECIMAL = re.compile(r"^-?\d{1,20}(?:\.\d{1,12})?$")

# The kind of each sort key, in order: "int" or "decimal" (an exact epoch). Must agree
# with repo.tickets.SORT_KEYS (a unit test checks).
CURSOR_SHAPE: dict[Sort, tuple[str, ...]] = {
    Sort.priority: ("int", "int", "decimal", "int"),
    Sort.sla: ("int", "decimal", "int"),
    Sort.newest: ("decimal", "int"),
    Sort.oldest: ("decimal", "int"),
    Sort.updated: ("decimal", "int"),
}


def encode_cursor(sort: Sort, keys: Sequence[CursorKey]) -> str:
    return paging.encode([sort.value, *keys])


def decode_cursor(cursor: str, sort: Sort) -> list[CursorKey]:
    """The keys of the row a page ended on, checked against the sort's key shape, so
    nothing malformed reaches the database."""
    value = paging.decode(cursor)
    shape = CURSOR_SHAPE[sort]
    if not value or value[0] != sort.value:
        raise CursorError("This cursor belongs to another sort order.")
    keys = value[1:]
    if len(keys) != len(shape):
        raise CursorError("This cursor is not valid.")
    for key, kind in zip(keys, shape, strict=True):
        ok = (
            type(key) is int and abs(key) < 2**31
            if kind == "int"
            else isinstance(key, str) and bool(_DECIMAL.match(key))
        )
        if not ok:
            raise CursorError("This cursor is not valid.")
    return keys  # type: ignore[return-value]


# If-Match (md 2B): the client sends back the ticket's `updated_at` (the ETag) and the
# change is refused with 409 if the ticket changed since it was read.


def etag(updated_at: datetime) -> str:
    return f'"{updated_at.isoformat()}"'


def parse_if_match(header: str) -> datetime | None:
    """The version a client expects, or None for `*` (any version). Accepts the ETag with
    or without quotes, weak or not."""
    value = header.strip()
    if value == "*":
        return None
    if value.startswith("W/"):
        value = value[2:]
    value = value.strip().strip('"')
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise FieldError("If-Match must be the ticket's ETag (its updated_at).") from exc
    if parsed.tzinfo is None:
        raise FieldError("If-Match must include the time zone, as the ETag does.")
    return parsed


# The ticket list's filters (`GET /tickets`), checked in one place: the list route and
# saved views (plan 1.2) both use `list_filters`, so a view can only hold what the list
# accepts.

SCOPES = ("everyone", "mine", "unassigned")
Q_MAX = 200
FILTER_KEYS = ("status", "scope", "priority", "team", "q", "sort", "imported", "snoozed")


@dataclass(frozen=True)
class ListFilters:
    status: Status | None = None
    scope: str = "everyone"
    priority: Priority | None = None
    team: UUID | None = None
    q: str | None = None
    sort: Sort = Sort.priority
    imported: str | None = None  # `any` or one source
    snoozed: bool | None = None  # True: only snoozed tickets; False: none of them

    @property
    def sources(self) -> list[str] | None:
        """The `hd_ticket.source` values `imported` stands for (None: no filter)."""
        if self.imported is None:
            return None
        return list(IMPORT_SOURCES) if self.imported == "any" else [self.imported]

    def stored(self) -> dict[str, Any]:
        """As a saved view keeps them: only the filters that are set, as JSON values."""
        out: dict[str, Any] = {}
        defaults = ListFilters()
        for key in FILTER_KEYS:
            value = getattr(self, key)
            if value is None or value == getattr(defaults, key):
                continue
            out[key] = str(value) if isinstance(value, UUID | StrEnum) else value
        return out


def list_filters(
    *,
    status: Status | None = None,
    scope: str = "everyone",
    priority: Priority | None = None,
    team: UUID | None = None,
    q: str | None = None,
    sort: Sort = Sort.priority,
    imported: str | None = None,
    snoozed: bool | None = None,
) -> ListFilters:
    """Checks what the list route was given (raises FieldError)."""
    if scope not in SCOPES:
        raise FieldError("scope is everyone, mine or unassigned.")
    q = (q or "").strip() or None
    if q is not None and len(q) > Q_MAX:
        raise FieldError(f"q is at most {Q_MAX} characters.")
    if imported is not None and imported != "any" and imported not in IMPORT_SOURCES:
        raise FieldError("imported is any, or one of: " + ", ".join(IMPORT_SOURCES) + ".")
    return ListFilters(status, scope, priority, team, q, sort, imported, snoozed)


def _enum[E: StrEnum](kind: type[E], key: str, value: object) -> E:
    if not isinstance(value, str) or value not in {m.value for m in kind}:
        allowed = ", ".join(m.value for m in kind)
        raise FieldError(f"{key} is one of: {allowed}.")
    return kind(value)


def filters_from_json(raw: Mapping[str, object]) -> ListFilters:
    """A saved view's filters: an object with only the list's keys (FILTER_KEYS), each a
    value the list route would take. Unknown keys, wrong types and bad values raise
    FieldError naming the key."""
    unknown = sorted(set(raw) - set(FILTER_KEYS))
    if unknown:
        raise FieldError(
            f"A view can't filter on {', '.join(unknown)}. Use: {', '.join(FILTER_KEYS)}."
        )
    args: dict[str, Any] = {}
    for key, value in raw.items():
        if value is None:
            continue
        if key == "status":
            args[key] = _enum(Status, key, value)
        elif key == "priority":
            args[key] = _enum(Priority, key, value)
        elif key == "sort":
            args[key] = _enum(Sort, key, value)
        elif key == "team":
            try:
                args[key] = UUID(str(value)) if isinstance(value, str) else None
            except ValueError:
                args[key] = None
            if args[key] is None:
                raise FieldError("team is a team id.")
        elif key == "snoozed":
            if not isinstance(value, bool):
                raise FieldError("snoozed is true or false.")
            args[key] = value
        else:  # scope, q, imported: text
            if not isinstance(value, str):
                raise FieldError(f"{key} is text.")
            args[key] = value
    return list_filters(**args)
