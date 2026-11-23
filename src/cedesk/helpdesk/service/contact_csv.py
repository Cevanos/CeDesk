"""Contact CSV import and export (feature-gap plan 2.5); file rules in
domain/contact_csv.py.

Import (`manage_contacts`: Admins; a custom role with the settings right):
- Runs in the request, in one transaction (up to 5,000 rows and 2 MB): all rows are
  written or none. `dry_run` checks everything and writes nothing, so the per-row errors
  can be fixed first.
- Matched by email: the contact's own address, or one merged into it. A new address makes
  a new contact. An existing contact, by `on_existing`: `fill_blanks` (default) only sets
  what it has no value for; `overwrite` sets every mapped value the row has (an empty cell
  never clears one); `skip` leaves it as it is. VIP only ever turns on from a fill.
- Erased customers (md S11) are never changed: their row is reported as an error.
- One audit entry (area `contacts`) with the counts only; no addresses.

Export (`export_contacts`: Admins only, never a custom role or an API key): the contacts
matching the list's filters, never erased ones, at most 50,000 rows. One audit entry with
the filters and the row count; the file itself is never stored.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import contact_csv as rules
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.contacts import ContactFilters, ContactSort
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import contacts as repo
from cedesk.helpdesk.service import audit, sla
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.contacts import check_filters, fields, query_filters
from cedesk.helpdesk.service.errors import Conflict, Invalid, TooLarge
from cedesk.helpdesk.service.members import require

OnExisting = Literal["fill_blanks", "overwrite", "skip"]
MAX_EXPORT = 50_000


@dataclass(frozen=True)
class ImportResult:
    dry_run: bool
    headers: list[str]
    mapping: dict[str, Any]
    rows: int
    created: int
    updated: int
    unchanged: int
    errors: list[rules.RowError]
    error_count: int


def _merged(contact: Mapping[Any, Any], row: rules.Row, on_existing: OnExisting) -> dict[str, Any]:
    """The contact's values after this row."""
    attrs = dict(contact["attributes"] or {})
    out: dict[str, Any] = {
        "name": contact["name"],
        "phone": contact["phone"],
        "organization": contact["organization"],
        "vip": contact["vip"],
    }
    given: dict[str, Any] = {"name": row.name, "phone": row.phone, "organization": row.organization}
    for key, value in given.items():
        if value is None:
            continue
        if on_existing == "overwrite" or not out[key]:
            out[key] = value
    if row.vip is not None and (on_existing == "overwrite" or row.vip):
        out["vip"] = row.vip
    for key, value in row.attributes.items():
        if on_existing == "overwrite" or attrs.get(key) in (None, ""):
            attrs[key] = value
    out["attributes"] = attrs
    return out


async def import_contacts(
    sm: Sessionmaker,
    caller: Caller,
    data: bytes,
    *,
    mapping: Mapping[str, Any] | None,
    on_existing: OnExisting = "fill_blanks",
    dry_run: bool = True,
) -> ImportResult:
    require(caller, Action.manage_contacts)
    try:
        headers, table = rules.read_table(data)
    except rules.BadFile as exc:
        if len(data) > rules.MAX_BYTES:
            raise TooLarge(str(exc)) from exc
        raise Invalid(str(exc)) from exc
    try:
        return await _import(sm, caller, headers, table, mapping, on_existing, dry_run)
    except IntegrityError as exc:
        raise Conflict("A contact was added meanwhile. Run the import again.") from exc


async def _import(
    sm: Sessionmaker,
    caller: Caller,
    headers: list[str],
    table: list[tuple[int, list[str]]],
    mapping: Mapping[str, Any] | None,
    on_existing: OnExisting,
    dry_run: bool,
) -> ImportResult:
    async with workspace_session(sm, caller.workspace_id) as s:
        defs = await fields(s)
        try:
            columns = (
                rules.check_mapping(mapping, headers, defs)
                if mapping is not None
                else rules.guess_mapping(headers, defs)
            )
        except rules.BadMapping as exc:
            raise Invalid(str(exc)) from exc
        if "email" not in columns.properties:
            raise Invalid("No column looks like an email. Send a mapping with email in it.")
        parsed = rules.read_rows(headers, table, columns, defs)
        existing = await repo.by_emails(s, [r.email for r in parsed.rows])
        errors = list(parsed.errors)
        created = updated = unchanged = 0
        vip_changed: list[UUID] = []
        seen_ids: dict[UUID, int] = {}
        for row in parsed.rows:
            contact = existing.get(row.email)
            if contact is None:
                created += 1
                if not dry_run:
                    await repo.insert_contact(
                        s,
                        caller.workspace_id,
                        email=row.email,
                        name=row.name or row.email,
                        phone=row.phone,
                        organization=row.organization,
                        vip=bool(row.vip),
                        attributes=row.attributes,
                    )
                continue
            if contact["erased_at"] is not None:
                errors.append(rules.RowError(row.line, "This customer was erased."))
                continue
            if contact["id"] in seen_ids:  # two addresses of one (merged) contact
                errors.append(
                    rules.RowError(
                        row.line, f"The same customer as line {seen_ids[contact['id']]}."
                    )
                )
                continue
            seen_ids[contact["id"]] = row.line
            if on_existing == "skip":
                unchanged += 1
                continue
            after = _merged(contact, row, on_existing)
            before = {k: contact[k] for k in ("name", "phone", "organization", "vip")}
            before["attributes"] = dict(contact["attributes"] or {})
            if after == before:
                unchanged += 1
                continue
            updated += 1
            if not dry_run:
                await repo.update_imported(s, contact["id"], **after)
                if after["vip"] != contact["vip"]:
                    vip_changed.append(contact["id"])
        errors.sort(key=lambda e: e.line)
        if not dry_run:
            for contact_id in vip_changed:  # VIP customers may have another SLA policy
                await sla.refresh(s, contact_id=contact_id)
            await audit.record(
                s,
                caller,
                Area.contacts,
                "imported",
                "Contacts from a CSV file",
                detail={
                    "rows": parsed.total,
                    "created": created,
                    "updated": updated,
                    "unchanged": unchanged,
                    "errors": len(errors),
                    "on_existing": on_existing,
                },
            )
    return ImportResult(
        dry_run=dry_run,
        headers=headers,
        mapping=columns.as_json(),
        rows=parsed.total,
        created=created,
        updated=updated,
        unchanged=unchanged,
        errors=errors[: rules.MAX_ERRORS_SHOWN],
        error_count=len(errors),
    )


def _audited(f: ContactFilters) -> dict[str, Any]:
    """The export's filters for the log; a search's words may be a customer's name or
    address, so only that there was one."""
    stored = f.stored()
    if stored.pop("q", None) is not None:
        stored["search"] = True
    return stored


@dataclass(frozen=True)
class ExportFile:
    name: str
    body: str
    rows: int


async def export_contacts(
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
) -> ExportFile:
    """The contacts `GET /contacts` would list with these filters, all of them."""
    require(caller, Action.export_contacts)
    now = datetime.now(UTC)
    async with workspace_session(sm, caller.workspace_id) as s:
        defs = await fields(s)
        f = check_filters(
            defs,
            q=q,
            sort=sort,
            vip=vip,
            has_open=has_open,
            created_from=created_from,
            created_to=created_to,
            attr=attr,
        )
        found = await repo.count_contacts(s, filters=query_filters(f))
        if found > MAX_EXPORT:
            raise TooLarge(
                f"{found} contacts match. Export at most {MAX_EXPORT} at a time: add filters."
            )
        rows = await repo.list_contacts(
            s, filters=query_filters(f), sort=f.sort.value, after=None, limit=None
        )
        body = rules.render_export(
            [rules.export_row(r, defs) for r in rows], rules.export_header(defs)
        )
        await audit.record(
            s,
            caller,
            Area.contacts,
            "exported",
            "Contacts to a CSV file",
            detail={"rows": len(rows), "filters": _audited(f)},
        )
    return ExportFile(rules.export_name(now), body, len(rows))
