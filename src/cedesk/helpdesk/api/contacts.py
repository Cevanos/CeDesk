"""Contacts (feature-gap plan phase 2): the list and search, one contact, notes,
attribute values, the activity timeline, merge, and CSV import and export.

`GET /contacts/{id}/tickets` and `PATCH /contacts/{id}/profile` (name, VIP) stay in
tickets.py. Static paths (`/contacts/export`, `/contacts/import`) are declared before
`/contacts/{contact_id}`."""

import json
from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import BaseModel, Field, StringConstraints

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain import contact_csv as csv_rules
from cedesk.helpdesk.domain import contacts as rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.contacts import ContactSort
from cedesk.helpdesk.service import contact_csv, contact_merge, contacts
from cedesk.helpdesk.service.errors import Invalid, TooLarge

router = APIRouter(tags=["contacts"])

C = "/contacts"
Cursor = Annotated[
    str | None, Query(max_length=400, description="`next_cursor` from the previous page.")
]
Limit = Annotated[int, Query(ge=1, le=200)]
Q = Annotated[
    str | None,
    Query(
        max_length=rules.Q_MAX,
        description="Part of a name, email (also merged-in ones), phone or organization, any case.",
    ),
]
Attr = Annotated[
    list[str],
    Query(
        max_length=rules.MAX_ATTR_FILTERS,
        description="A contact field's value, as `key:value` (`attr=plan:pro&attr=trial:true`"
        "); the contact's value must equal it. Dates as YYYY-MM-DD, checkboxes true/false.",
    ),
]
SortQ = Annotated[
    ContactSort,
    Query(
        description="`name` (A-Z), `recent` (latest ticket activity first), `tickets` "
        "(most tickets first)."
    ),
]
VipQ = Annotated[bool | None, Query(description="Only VIP customers (true) or none (false).")]
OpenQ = Annotated[
    bool | None,
    Query(
        description="Only customers with a ticket that isn't resolved (true), or with none (false)."
    ),
]
FromQ = Annotated[date | None, Query(description="Contact made on or after this day (UTC).")]
ToQ = Annotated[date | None, Query(description="Contact made on or before this day (UTC).")]


class ContactRowOut(BaseModel):
    id: UUID
    name: str
    email: str
    phone: str | None
    organization: str | None
    vip: bool
    customer_since: datetime
    created_at: datetime
    ticket_count: int = Field(description="Live tickets (not archived).")
    open_ticket_count: int = Field(description="Tickets that aren't resolved.")
    last_activity_at: datetime = Field(
        description="When one of their tickets last changed, or the contact was made."
    )
    other_emails: list[str] = Field(description="Addresses merged in from duplicates.")
    attributes: dict[str, Any] = Field(description="Contact field values, by field key.")


class ContactListOut(BaseModel):
    contacts: list[ContactRowOut]
    next_cursor: str | None


class ContactNoteOut(BaseModel):
    id: UUID
    author_id: UUID | None = Field(description="null: the author was removed.")
    author_name: str
    body: str
    created_at: datetime
    updated_at: datetime
    can_edit: bool = Field(description="Whether you may change or delete it.")


class ContactFieldBrief(BaseModel):
    key: str
    label: str
    kind: str
    options: list[str]


class ContactDetailOut(ContactRowOut):
    erased_at: datetime | None = Field(description="Set when the customer was erased (S11).")
    csat_average: float | None = Field(description="Average rating 1-5; null: none yet.")
    csat_count: int
    note_count: int
    notes: list[ContactNoteOut] = Field(description="The newest 5 notes; all: GET .../notes.")
    fields: list[ContactFieldBrief] = Field(
        description="The workspace's contact fields, in order: show `attributes` with these."
    )


class ContactNotesOut(BaseModel):
    notes: list[ContactNoteOut] = Field(description="Newest first.")
    next_cursor: str | None


class ContactNoteIn(Body):
    body: Annotated[str, StringConstraints(min_length=1, max_length=rules.NOTE_MAX)]


class ContactAttributesIn(Body):
    attributes: dict[str, Any] = Field(
        max_length=50,
        description='Field key to its new value; null or "" clears it. Keys not sent stay.',
    )


class ContactTimelineItemOut(BaseModel):
    kind: str = Field(description=", ".join(rules.TIMELINE_KINDS))
    at: datetime
    id: UUID = Field(description="The ticket, message, rating, note or merge.")
    ticket_id: UUID | None
    ticket_number: int | None
    ticket_subject: str | None
    author: str | None = Field(description="message: who wrote it; note, merge: the agent.")
    text: str | None = Field(
        description="message, note: its start (up to 1000 characters); csat: the comment; "
        "merge: the duplicate's name."
    )
    score: int | None = Field(description="csat: 1-5.")
    detail: str | None = Field(
        description="ticket_created: the channel; message: customer, agent or ai; merge: "
        "the duplicate's address."
    )


class ContactTimelineOut(BaseModel):
    items: list[ContactTimelineItemOut] = Field(description="Newest first.")
    next_cursor: str | None


class ContactMergeIn(Body):
    duplicate_id: UUID = Field(description="The customer merged in, then deleted.")
    confirm: bool = Field(
        default=False, description="Must be true: a merge can't be undone (422 otherwise)."
    )


class ContactMergePreviewIn(Body):
    duplicate_id: UUID


class ContactBriefOut(BaseModel):
    id: UUID
    name: str
    email: str


class MergeConflictOut(BaseModel):
    field: str = Field(description="name, phone, organization or attributes.<key>")
    kept: Any = Field(description="The survivor's value, kept.")
    dropped: Any = Field(description="The duplicate's value, dropped.")


class MergePreviewOut(BaseModel):
    survivor: ContactBriefOut
    duplicate: ContactBriefOut
    moves: dict[str, int] = Field(
        description="tickets, archived_tickets, ratings, notes, other_emails (with its own "
        "address), portal_sessions (ended, not moved), privacy_requests (blocks the merge)."
    )
    taken: list[str] = Field(description="Values the survivor takes from the duplicate.")
    conflicts: list[MergeConflictOut]
    result: dict[str, Any] = Field(
        description="The survivor after the merge: phone, organization, vip, customer_since, "
        "attributes."
    )
    can_merge: bool
    blocked: str | None = Field(description="Why it can't be merged, when it can't.")
    warnings: list[str]


class ContactMergeOut(BaseModel):
    contact: ContactRowOut
    moved: dict[str, int]


class ContactImportErrorOut(BaseModel):
    line: int
    reason: str


class ContactImportOut(BaseModel):
    dry_run: bool
    headers: list[str] = Field(description="The file's columns.")
    mapping: dict[str, Any] = Field(
        description="What was used: property to column, and attributes: field key to column."
    )
    rows: int
    created: int
    updated: int
    unchanged: int
    error_count: int
    errors: list[ContactImportErrorOut] = Field(description="By line; the first 200.")


def _note(caller: Caller, row: Any) -> ContactNoteOut:
    return ContactNoteOut.model_validate(
        dict(row) | {"can_edit": contacts.can_change_note(caller, row)}
    )


def _detail(caller: Caller, d: contacts.ContactDetail) -> ContactDetailOut:
    return ContactDetailOut.model_validate(
        dict(d.contact)
        | {
            "notes": [_note(caller, n) for n in d.notes],
            "fields": [
                ContactFieldBrief(
                    key=f.key, label=f.label, kind=f.kind.value, options=list(f.options)
                )
                for f in d.fields
            ],
        }
    )


@router.get(
    C,
    response_model=ContactListOut,
    summary="Find customers: search, filter and sort, a page at a time",
    description="Erased customers are left out. Send `next_cursor` back as `cursor` with the "
    "same filters and sort for the next page.",
    responses={422: {"description": "A bad filter or cursor, or an unknown field in attr"}},
)
async def list_contacts(
    caller: CallerDep,
    sm: SessionmakerDep,
    q: Q = None,
    sort: SortQ = ContactSort.name,
    vip: VipQ = None,
    has_open: OpenQ = None,
    created_from: FromQ = None,
    created_to: ToQ = None,
    attr: Attr = [],  # noqa: B006 (FastAPI copies it)
    cursor: Cursor = None,
    limit: Limit = 50,
) -> ContactListOut:
    found = await contacts.list_contacts(
        sm,
        caller,
        q=q,
        sort=sort,
        vip=vip,
        has_open=has_open,
        created_from=created_from,
        created_to=created_to,
        attr=attr,
        cursor=cursor,
        limit=limit,
    )
    return ContactListOut(
        contacts=[ContactRowOut.model_validate(dict(r)) for r in found.contacts],
        next_cursor=found.next_cursor,
    )


@router.get(
    C + "/export",
    summary="Download customers as a CSV file (Admin; audited)",
    description="The same filters as `GET /contacts` (erased customers never). One row per "
    "customer: id, email, name, phone, organization, vip, customer_since, created_at, "
    "tickets, open_tickets, other_emails (`;` between) and `attr.<key>` per contact field. "
    "UTF-8 with a byte order mark; a value a spreadsheet would run as a formula starts "
    f"with `'`. At most {contact_csv.MAX_EXPORT} rows (413: add filters). Not for API keys.",
    response_class=Response,
    responses={
        200: {"content": {"text/csv": {"schema": {"type": "string"}}}},
        403: {"description": "Not an Admin"},
        413: {"description": "Too many contacts match"},
    },
)
async def export_contacts(
    caller: CallerDep,
    sm: SessionmakerDep,
    q: Q = None,
    sort: SortQ = ContactSort.name,
    vip: VipQ = None,
    has_open: OpenQ = None,
    created_from: FromQ = None,
    created_to: ToQ = None,
    attr: Attr = [],  # noqa: B006 (FastAPI copies it)
) -> Response:
    file = await contact_csv.export_contacts(
        sm,
        caller,
        q=q,
        sort=sort,
        vip=vip,
        has_open=has_open,
        created_from=created_from,
        created_to=created_to,
        attr=attr,
    )
    return Response(
        content=file.body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{file.name}"'},
    )


async def _read_file(request: Request) -> bytes:
    limit = csv_rules.MAX_BYTES
    declared = request.headers.get("content-length", "")
    too_big = f"The file is larger than {limit // (1024 * 1024)} MB."
    if declared.isdigit() and int(declared) > limit:
        raise TooLarge(too_big)
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > limit:
            raise TooLarge(too_big)
    return bytes(data)


def _mapping(raw: str | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise Invalid("mapping is a JSON object.") from exc
    if not isinstance(value, dict):
        raise Invalid("mapping is a JSON object.")
    return value


@router.post(
    C + "/import",
    response_model=ContactImportOut,
    summary="Import customers from a CSV file: dry run, then run (Admin; audited)",
    description=(
        "The request body is the file itself (UTF-8 CSV with a header row), not a form. At "
        f"most {csv_rules.MAX_BYTES // (1024 * 1024)} MB (413) and {csv_rules.MAX_ROWS} rows. "
        "`mapping` (JSON) says which column holds what: `email` (required), `name`, `phone`, "
        "`organization`, `vip` (true/false, yes/no, 1/0) and `attributes` (field key to "
        "column). Without it, columns are matched by name; the answer shows the mapping used. "
        "Matched by email (also merged-in addresses): a new address makes a customer; an "
        "existing one is changed by `on_existing`. A row that can't be read is listed by line "
        "and left out. `dry_run=true` (the default) writes nothing: send the same file with "
        "`dry_run=false` to import. The whole file is written in one go or not at all."
    ),
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
    responses={
        409: {"description": "A contact was added meanwhile: run it again"},
        413: {"description": "The file is too big"},
        422: {"description": "Not a CSV, no email column, or a bad mapping"},
    },
)
async def import_contacts(
    request: Request,
    caller: CallerDep,
    sm: SessionmakerDep,
    dry_run: Annotated[bool, Query(description="true: check only, write nothing.")] = True,
    on_existing: Annotated[
        Literal["fill_blanks", "overwrite", "skip"],
        Query(
            description="An existing customer: `fill_blanks` sets only what it has no "
            "value for; `overwrite` sets every value the row has (an empty cell never "
            "clears one); `skip` leaves it."
        ),
    ] = "fill_blanks",
    mapping: Annotated[
        str | None,
        Query(
            max_length=4000,
            description='JSON, e.g. {"email": "E-mail", "name": "Full '
            'name", "attributes": {"plan": "Plan"}}.',
        ),
    ] = None,
) -> ContactImportOut:
    data = await _read_file(request)
    result = await contact_csv.import_contacts(
        sm, caller, data, mapping=_mapping(mapping), on_existing=on_existing, dry_run=dry_run
    )
    return ContactImportOut(
        dry_run=result.dry_run,
        headers=result.headers,
        mapping=result.mapping,
        rows=result.rows,
        created=result.created,
        updated=result.updated,
        unchanged=result.unchanged,
        error_count=result.error_count,
        errors=[ContactImportErrorOut(line=e.line, reason=e.reason) for e in result.errors],
    )


@router.get(
    C + "/{contact_id}",
    response_model=ContactDetailOut,
    summary="One customer: profile, attributes, counts and newest notes",
    description="For the customer page and a ticket's Customer tab (the ticket's "
    "`contact.id`). An erased customer is still found (with `erased_at`).",
)
async def get_contact(contact_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> ContactDetailOut:
    return _detail(caller, await contacts.get_contact(sm, caller, contact_id))


@router.put(
    C + "/{contact_id}/attributes",
    response_model=ContactDetailOut,
    summary="Set a customer's custom attribute values (audited)",
    description="Each value is checked by its field's kind: text (up to 2000), number, date "
    "(YYYY-MM-DD), select (one of its options), checkbox (true/false). Needs the right to "
    "change ticket fields. The audit entry names the fields, never the values.",
    responses={422: {"description": "No such field, a wrong value, or an erased customer"}},
)
async def set_attributes(
    contact_id: UUID, body: ContactAttributesIn, caller: CallerDep, sm: SessionmakerDep
) -> ContactDetailOut:
    return _detail(caller, await contacts.set_attributes(sm, caller, contact_id, body.attributes))


@router.get(
    C + "/{contact_id}/notes",
    response_model=ContactNotesOut,
    summary="A customer's private notes, newest first",
)
async def list_notes(
    contact_id: UUID,
    caller: CallerDep,
    sm: SessionmakerDep,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> ContactNotesOut:
    rows, next_cursor = await contacts.list_notes(
        sm, caller, contact_id, cursor=cursor, limit=limit
    )
    return ContactNotesOut(notes=[_note(caller, r) for r in rows], next_cursor=next_cursor)


@router.post(
    C + "/{contact_id}/notes",
    response_model=ContactNoteOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a private note about a customer (audited; customers never see it)",
    description="Anyone who may add notes on tickets (a Light agent too). The audit entry "
    "holds the note's id, not its words.",
)
async def add_note(
    contact_id: UUID, body: ContactNoteIn, caller: CallerDep, sm: SessionmakerDep
) -> ContactNoteOut:
    return _note(caller, await contacts.add_note(sm, caller, contact_id, body.body))


@router.patch(
    C + "/{contact_id}/notes/{note_id}",
    response_model=ContactNoteOut,
    summary="Change a note (its author, or an Admin; audited)",
    responses={403: {"description": "Someone else's note"}},
)
async def change_note(
    contact_id: UUID, note_id: UUID, body: ContactNoteIn, caller: CallerDep, sm: SessionmakerDep
) -> ContactNoteOut:
    return _note(caller, await contacts.change_note(sm, caller, contact_id, note_id, body.body))


@router.delete(
    C + "/{contact_id}/notes/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a note (its author, or an Admin; audited)",
    responses={403: {"description": "Someone else's note"}},
)
async def delete_note(
    contact_id: UUID, note_id: UUID, caller: CallerDep, sm: SessionmakerDep
) -> None:
    await contacts.delete_note(sm, caller, contact_id, note_id)


@router.get(
    C + "/{contact_id}/timeline",
    response_model=ContactTimelineOut,
    summary="Everything that happened with a customer, newest first",
    description="Tickets created and resolved (archived ones too), public messages "
    "(customer, agent, AI; never notes or system lines), ratings, notes and merges. A "
    "team-only role sees only its team's tickets here. `kind` (repeat it) narrows it.",
)
async def timeline(
    contact_id: UUID,
    caller: CallerDep,
    sm: SessionmakerDep,
    kind: Annotated[list[str], Query(max_length=6)] = [],  # noqa: B006
    cursor: Cursor = None,
    limit: Limit = 50,
) -> ContactTimelineOut:
    rows, next_cursor = await contacts.timeline(
        sm, caller, contact_id, kinds=kind, cursor=cursor, limit=limit
    )
    items = [
        ContactTimelineItemOut(
            kind=r["kind"],
            at=r["at"],
            id=r["id"],
            ticket_id=r["ticket_id"],
            ticket_number=r["number"],
            ticket_subject=r["subject"],
            author=r["author"],
            text=r["body"],
            score=r["score"],
            detail=r["extra"],
        )
        for r in rows
    ]
    return ContactTimelineOut(items=items, next_cursor=next_cursor)


def _brief(row: Any) -> ContactBriefOut:
    return ContactBriefOut(id=row["id"], name=row["name"], email=row["email"])


@router.post(
    C + "/{contact_id}/merge-preview",
    response_model=MergePreviewOut,
    summary="What merging a duplicate into this customer would do (changes nothing)",
    description="`contact_id` is the survivor. Needs the right to merge customers.",
    responses={404: {"description": "Either customer doesn't exist"}},
)
async def merge_preview(
    contact_id: UUID, body: ContactMergePreviewIn, caller: CallerDep, sm: SessionmakerDep
) -> MergePreviewOut:
    p = await contact_merge.preview(sm, caller, contact_id, body.duplicate_id)
    plan = p.plan
    return MergePreviewOut(
        survivor=_brief(p.survivor),
        duplicate=_brief(p.duplicate),
        moves=p.moves,
        taken=plan.taken,
        conflicts=[
            MergeConflictOut(field=c.field, kept=c.kept, dropped=c.dropped) for c in plan.conflicts
        ],
        result={
            "phone": plan.phone,
            "organization": plan.organization,
            "vip": plan.vip,
            "customer_since": plan.customer_since.isoformat(),
            "attributes": plan.attributes,
        },
        can_merge=p.blocked is None,
        blocked=p.blocked,
        warnings=p.warnings,
    )


@router.post(
    C + "/{contact_id}/merge",
    response_model=ContactMergeOut,
    summary="Merge a duplicate into this customer (can't be undone; audited)",
    description="`contact_id` is the survivor; it keeps its name and email. The duplicate's "
    "tickets, ratings, notes and addresses move to it, it takes values it has none of, and "
    "the duplicate is deleted. Show `merge-preview` first; send `confirm: true`.",
    responses={
        404: {"description": "Either customer doesn't exist"},
        409: {"description": "An erased customer, or a duplicate with privacy requests"},
        422: {"description": "confirm isn't true, or the same customer twice"},
    },
)
async def merge(
    contact_id: UUID, body: ContactMergeIn, caller: CallerDep, sm: SessionmakerDep
) -> ContactMergeOut:
    row, moved = await contact_merge.merge(
        sm, caller, contact_id, body.duplicate_id, confirm=body.confirm
    )
    return ContactMergeOut(contact=ContactRowOut.model_validate(dict(row)), moved=moved)
