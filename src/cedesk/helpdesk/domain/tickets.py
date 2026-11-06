"""Ticket rules that need no database: statuses, priorities, custom fields, tags, numbers."""

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum


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
