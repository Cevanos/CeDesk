"""Contact CSV import and export (feature-gap plan 2.5), the rules that need no database.

Import: the file's columns are mapped to contact properties (`email`, `name`, `phone`,
`organization`, `vip`) and contact fields (`attributes`). Without a mapping, the headers
are matched by name (ignoring case, spaces, `-` and `_`). `email` is the key: a row with
no valid address is an error, and a second row with an address already seen in the file
is an error naming the first line. A row that can't be read is reported with its line
number and left out; it never stops the others.

Export: one row per contact, the cells made safe for spreadsheets with the reports' own
rule (`report_csv.cell`: a value that a spreadsheet would run as a formula gets a `'`).
"""

import csv
import io
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from cedesk.helpdesk.domain import report_csv
from cedesk.helpdesk.domain.contact_fields import (
    AttrValue,
    ContactField,
    ContactFieldError,
    from_text,
    to_text,
)
from cedesk.helpdesk.domain.contacts import NAME_MAX, ORGANIZATION_MAX, PHONE_MAX
from cedesk.helpdesk.domain.portal import normalise_email

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
MAX_ERRORS_SHOWN = 200
PROPERTIES = ("email", "name", "phone", "organization", "vip")
_ALIASES = {
    "email": ("email", "emailaddress", "mail", "e-mail"),
    "name": ("name", "fullname", "contactname", "customername"),
    "phone": ("phone", "phonenumber", "mobile", "telephone"),
    "organization": ("organization", "organisation", "company", "companyname"),
    "vip": ("vip",),
}
_TRUE = ("true", "yes", "1", "y")
_FALSE = ("false", "no", "0", "n", "")


class BadFile(ValueError):
    """The file can't be read as a CSV at all (not UTF-8, no header, too big)."""


class BadMapping(ValueError):
    """The mapping names a column the file doesn't have, or a field that doesn't exist."""


@dataclass(frozen=True)
class ColumnMap:
    """Contact property or field key to the file's column header."""

    properties: dict[str, str] = field(default_factory=dict)
    attributes: dict[str, str] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {**self.properties, "attributes": dict(self.attributes)}


@dataclass(frozen=True)
class Row:
    line: int
    email: str
    name: str | None
    phone: str | None
    organization: str | None
    vip: bool | None
    attributes: dict[str, AttrValue]


@dataclass(frozen=True)
class RowError:
    line: int
    reason: str


@dataclass(frozen=True)
class Parsed:
    headers: list[str]
    rows: list[Row]
    errors: list[RowError]
    total: int  # data rows in the file


def _norm(header: str) -> str:
    return re.sub(r"[\s_\-]", "", header.strip().lower())


def read_table(data: bytes) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """The header and the data rows with their line numbers (blank rows left out)."""
    if len(data) > MAX_BYTES:
        raise BadFile(f"The file is over {MAX_BYTES // (1024 * 1024)} MB.")
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BadFile("The file isn't UTF-8 text. Save it as CSV UTF-8 and try again.") from exc
    reader = csv.reader(io.StringIO(content, newline=""))
    try:
        header = next(reader, None)
        if not header or not any(h.strip() for h in header):
            raise BadFile("The file has no header row.")
        headers = [h.strip() for h in header]
        if len(set(headers)) != len(headers):
            raise BadFile("Two columns have the same header.")
        rows: list[tuple[int, list[str]]] = []
        for values in reader:
            if not any(v.strip() for v in values):
                continue
            if len(rows) >= MAX_ROWS:
                raise BadFile(f"The file has more than {MAX_ROWS} rows. Split it.")
            rows.append((reader.line_num, values))
    except csv.Error as exc:
        raise BadFile(f"The file can't be read as CSV: {exc}") from exc
    return headers, rows


def guess_mapping(headers: Sequence[str], fields: Sequence[ContactField]) -> ColumnMap:
    """Columns matched by name: the contact properties, then each field by key or label."""
    by_norm = {_norm(h): h for h in headers}
    props: dict[str, str] = {}
    for prop, names in _ALIASES.items():
        for name in names:
            if _norm(name) in by_norm:
                props[prop] = by_norm[_norm(name)]
                break
    used = set(props.values())
    attrs: dict[str, str] = {}
    for f in fields:
        for name in (f.key, f.label, f"attr.{f.key}"):
            header = by_norm.get(_norm(name))
            if header is not None and header not in used:
                attrs[f.key] = header
                used.add(header)
                break
    return ColumnMap(props, attrs)


def check_mapping(
    raw: Mapping[str, Any], headers: Sequence[str], fields: Sequence[ContactField]
) -> ColumnMap:
    """`raw`: {"email": "<header>", ..., "attributes": {"<field key>": "<header>"}}."""
    known = set(headers)
    props: dict[str, str] = {}
    attrs: dict[str, str] = {}
    for key, value in raw.items():
        if key == "attributes":
            if not isinstance(value, Mapping):
                raise BadMapping("attributes is an object of field key to column header.")
            keys = {f.key for f in fields}
            for fkey, header in value.items():
                if fkey not in keys:
                    raise BadMapping(f"No contact field {fkey}.")
                if not isinstance(header, str) or header not in known:
                    raise BadMapping(f"The file has no column {header!r}.")
                attrs[str(fkey)] = header
            continue
        if key not in PROPERTIES:
            raise BadMapping(f"Can't map {key}. Use: {', '.join(PROPERTIES)} and attributes.")
        if value is None:
            continue
        if not isinstance(value, str) or value not in known:
            raise BadMapping(f"The file has no column {value!r}.")
        props[key] = value
    if "email" not in props:
        raise BadMapping("Map a column to email: it is how contacts are matched.")
    return ColumnMap(props, attrs)


def _cell(values: list[str], index: dict[str, int], header: str | None) -> str:
    if header is None:
        return ""
    i = index[header]
    return values[i].strip() if i < len(values) else ""


def _short(value: str, label: str, limit: int) -> str | None:
    if len(value) > limit:
        raise ValueError(f"{label} is over {limit} characters.")
    return value or None


def read_rows(
    headers: Sequence[str],
    rows: Sequence[tuple[int, list[str]]],
    mapping: ColumnMap,
    fields: Sequence[ContactField],
) -> Parsed:
    index = {h: i for i, h in enumerate(headers)}
    by_key = {f.key: f for f in fields}
    seen: dict[str, int] = {}
    good: list[Row] = []
    errors: list[RowError] = []
    p = mapping.properties
    for line, values in rows:
        try:
            raw_email = _cell(values, index, p.get("email"))
            email = normalise_email(raw_email)
            if email is None:
                raise ValueError("No valid email address." if raw_email else "No email.")
            if email in seen:
                raise ValueError(f"The same email as line {seen[email]}.")
            name = _short(" ".join(_cell(values, index, p.get("name")).split()), "Name", NAME_MAX)
            phone = _short(_cell(values, index, p.get("phone")), "Phone", PHONE_MAX)
            org = _short(
                _cell(values, index, p.get("organization")), "Organization", ORGANIZATION_MAX
            )
            vip: bool | None = None
            if "vip" in p:
                flag = _cell(values, index, p["vip"]).lower()
                if flag in _TRUE:
                    vip = True
                elif flag in _FALSE:
                    vip = False if flag else None
                else:
                    raise ValueError("VIP is true or false (yes/no, 1/0).")
            attrs: dict[str, AttrValue] = {}
            for key, header in mapping.attributes.items():
                value = from_text(by_key[key], _cell(values, index, header))
                if value is not None:
                    attrs[key] = value
        except (ValueError, ContactFieldError) as exc:
            errors.append(RowError(line, str(exc)))
            continue
        seen[email] = line
        good.append(Row(line, email, name, phone, org, vip, attrs))
    return Parsed(list(headers), good, errors, len(rows))


# ---- export -------------------------------------------------------------------------

EXPORT_COLUMNS = (
    "id",
    "email",
    "name",
    "phone",
    "organization",
    "vip",
    "customer_since",
    "created_at",
    "tickets",
    "open_tickets",
    "other_emails",
)


def _when(value: datetime | None) -> str:
    return "" if value is None else value.isoformat()


def export_header(fields: Sequence[ContactField]) -> list[str]:
    return [*EXPORT_COLUMNS, *(f"attr.{f.key}" for f in fields)]


def export_row(contact: Mapping[Any, Any], fields: Sequence[ContactField]) -> list[str]:
    attrs = contact.get("attributes") or {}
    values: list[report_csv.Cell] = [
        str(contact["id"]),
        contact["email"],
        contact["name"],
        contact.get("phone"),
        contact.get("organization"),
        "true" if contact["vip"] else "false",
        _when(contact.get("customer_since")),
        _when(contact.get("created_at")),
        int(contact.get("ticket_count") or 0),
        int(contact.get("open_ticket_count") or 0),
        ";".join(contact.get("other_emails") or []),
    ]
    values += [to_text(attrs.get(f.key)) for f in fields]
    return [report_csv.cell(v) for v in values]


def render_export(rows: Sequence[Sequence[str]], header: Sequence[str]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow([report_csv.cell(h) for h in header])
    writer.writerows(rows)
    return report_csv.BOM + out.getvalue()


def export_name(now: datetime) -> str:
    return f"contacts-{now.strftime('%Y%m%d-%H%M')}.csv"
