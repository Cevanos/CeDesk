"""Ticket rules that need no database: statuses, priorities, custom fields, tags, numbers."""

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from cedesk.helpdesk.domain import paging
from cedesk.helpdesk.domain.paging import CursorError


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


class Sort(StrEnum):
    """Ticket list orders. `priority`: Open and Pending most urgent first, then oldest;
    Resolved after them, newest resolved first (T9; SLA time left joins when SLA lands)."""

    priority = "priority"
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

CursorKeys = tuple[int, int, str, int]
_DECIMAL = re.compile(r"^-?\d{1,20}(?:\.\d{1,12})?$")


def encode_cursor(sort: Sort, keys: CursorKeys) -> str:
    return paging.encode([sort.value, *keys])


def decode_cursor(cursor: str, sort: Sort) -> CursorKeys:
    value = paging.decode(cursor)
    if len(value) != 5:
        raise CursorError("This cursor is not valid.")
    name, k1, k2, k3, k4 = value
    if name != sort.value:
        raise CursorError("This cursor belongs to another sort order.")
    if type(k1) is not int or type(k2) is not int or type(k4) is not int:
        raise CursorError("This cursor is not valid.")
    if not isinstance(k3, str) or not _DECIMAL.match(k3):
        raise CursorError("This cursor is not valid.")
    return k1, k2, k3, k4


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
