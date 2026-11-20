"""Custom attributes on contacts (feature-gap plan 2.3), the rules that need no database.

A workspace defines its contact fields (key, label, kind, options); each contact holds
its values in `hd_contact.attributes` (an object keyed by field key). The checks for
text, number and select values are the ticket fields' own (`tickets._clean_one`), so a
contact field behaves like a ticket field of the same kind; contacts add `date`
(`YYYY-MM-DD`) and `checkbox` (true or false).

A field's key and kind never change once made (values already stored keep their
meaning); its label, options and position may.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from cedesk.helpdesk.domain import tickets as ticket_rules
from cedesk.helpdesk.domain.tickets import FIELD_KEY, MAX_OPTIONS, TEXT_MAX, FieldDef, FieldKind

MAX_FIELDS = 50  # per workspace
LABEL_MAX = 80
OPTION_MAX = 100
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

AttrValue = str | int | float | bool


class ContactFieldKind(StrEnum):
    text = "text"
    number = "number"
    date = "date"
    select = "select"  # a dropdown: one of `options`
    checkbox = "checkbox"


class ContactFieldError(ValueError):
    """The message is shown to the member."""


@dataclass(frozen=True)
class ContactField:
    key: str
    label: str
    kind: ContactFieldKind
    options: tuple[str, ...] = ()


def check_key(key: str) -> str:
    if not FIELD_KEY.match(key):
        raise ContactFieldError(
            "A field key starts with a letter and uses a-z, 0-9 or _ (at most 40)."
        )
    return key


def clean_label(raw: str) -> str:
    label = " ".join(raw.split())
    if not 1 <= len(label) <= LABEL_MAX:
        raise ContactFieldError(f"A field's label is 1-{LABEL_MAX} characters.")
    return label


def clean_options(kind: ContactFieldKind, raw: Sequence[str]) -> tuple[str, ...]:
    options = tuple(o.strip() for o in raw)
    if kind is not ContactFieldKind.select:
        if options:
            raise ContactFieldError("Only select fields have options.")
        return ()
    if not 1 <= len(options) <= MAX_OPTIONS:
        raise ContactFieldError(f"A select field needs 1-{MAX_OPTIONS} options.")
    if any(not o or len(o) > OPTION_MAX for o in options) or len(set(options)) != len(options):
        raise ContactFieldError(
            f"A select field's options are 1-{OPTION_MAX} characters each, none repeated."
        )
    return options


def _as_ticket_field(f: ContactField) -> FieldDef:
    return FieldDef(key=f.key, label=f.label, kind=FieldKind(f.kind.value), options=f.options)


def clean_value(f: ContactField, value: object) -> AttrValue:
    """One value checked against its field (raises ContactFieldError)."""
    if f.kind is ContactFieldKind.checkbox:
        if not isinstance(value, bool):
            raise ContactFieldError(f"{f.label} must be true or false.")
        return value
    if f.kind is ContactFieldKind.date:
        if not isinstance(value, str) or not _DATE.match(value.strip()):
            raise ContactFieldError(f"{f.label} must be a date (YYYY-MM-DD).")
        try:
            return date.fromisoformat(value.strip()).isoformat()
        except ValueError as exc:
            raise ContactFieldError(f"{f.label} must be a date (YYYY-MM-DD).") from exc
    if isinstance(value, str):
        value = value.strip()
    try:
        return ticket_rules._clean_one(_as_ticket_field(f), value)
    except ticket_rules.FieldError as exc:
        raise ContactFieldError(str(exc)) from exc


def clean_changes(
    fields: Sequence[ContactField], changes: Mapping[str, object]
) -> dict[str, AttrValue | None]:
    """The values being set (None or "" clears one). Unknown keys are refused; values
    already stored are not re-checked (a field's options may have changed since)."""
    by_key = {f.key: f for f in fields}
    unknown = sorted(set(changes) - set(by_key))
    if unknown:
        raise ContactFieldError(f"No contact field {', '.join(unknown)}.")
    out: dict[str, AttrValue | None] = {}
    for key, value in changes.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            out[key] = None
        else:
            out[key] = clean_value(by_key[key], value)
    return out


def from_text(f: ContactField, raw: str) -> AttrValue | None:
    """A value as typed in a CSV cell or a query string: numbers and true/false are read
    from text. Empty: None."""
    value = raw.strip()
    if not value:
        return None
    if f.kind is ContactFieldKind.number:
        try:
            number = float(value)
        except ValueError as exc:
            raise ContactFieldError(f"{f.label} must be a number.") from exc
        return clean_value(f, int(number) if number.is_integer() and "." not in value else number)
    if f.kind is ContactFieldKind.checkbox:
        lowered = value.lower()
        if lowered in ("true", "yes", "1", "y"):
            return True
        if lowered in ("false", "no", "0", "n"):
            return False
        raise ContactFieldError(f"{f.label} must be true or false.")
    if f.kind is ContactFieldKind.text and len(value) > TEXT_MAX:
        raise ContactFieldError(f"{f.label} must be text of at most {TEXT_MAX} characters.")
    return clean_value(f, value)


def to_text(value: object) -> str:
    """A stored value as a CSV cell."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def merge_values(
    survivor: Mapping[str, object], duplicate: Mapping[str, object]
) -> tuple[dict[str, object], list[str], list[str]]:
    """Contact merge (plan 2.4): the survivor's value wins; the duplicate's fills a key
    the survivor has no value for. Returns the merged values, the keys taken from the
    duplicate, and the keys where both had different values (the duplicate's is dropped)."""
    merged = dict(survivor)
    taken: list[str] = []
    conflicts: list[str] = []
    for key, value in duplicate.items():
        mine = survivor.get(key)
        if mine is None or mine == "":
            if value is not None and value != "":
                merged[key] = value
                taken.append(key)
        elif value is not None and value != "" and value != mine:
            conflicts.append(key)
    return merged, sorted(taken), sorted(conflicts)
