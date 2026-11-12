"""The HubSpot importer: contacts, and tickets with their emails and notes, by pipeline.

HubSpot CRM API, read only, a private app's access token (`Authorization: Bearer`) at the
fixed host `https://api.hubapi.com`. Lists are paged with `after` (`paging.next.after`),
100 records a page. Some reads are only offered as a `POST` of JSON (search, batch read);
`SourceHttp.read` allows exactly those paths and nothing that writes.

Endpoints used:

- check: `GET /account-info/v3/details` (`portalId`)
- discover: `POST /crm/v3/objects/{contacts,tickets}/search` with `limit: 1` (`total`)
- groups: `GET /crm/v3/pipelines/tickets` (the pipelines, each with its stages)
- contacts: `GET /crm/v3/objects/contacts` (`email`, `firstname`, `lastname`)
- tickets: `GET /crm/v3/objects/tickets`, then for the page:
  `POST /crm/v4/associations/tickets/{contacts,emails,notes}/batch/read` and
  `POST /crm/v3/objects/{contacts,emails,notes}/batch/read` (100 ids a call)
- assignees: `GET /crm/v3/owners` (owner id -> email)
- files: `GET /files/v3/files/{id}` (name, size) and `GET /files/v3/files/{id}/signed-url`
- changes since a time (M9): `POST /crm/v3/objects/tickets/search`, `hs_lastmodifieddate`
  at or after the time, oldest change first

The private app needs the scopes `tickets`, `crm.objects.contacts.read`,
`crm.objects.owners.read`, `sales-email-read` (without it HubSpot hides email bodies) and
`files`.

Decisions:

- Status: HubSpot's stages are the account's own (md 8.3). A stage HubSpot marks closed
  (`metadata.ticketState` `CLOSED`) -> resolved; a stage named like "waiting on contact"
  (HubSpot's default stage for "we asked the customer") -> pending; any other -> open. The
  dry run shows this table (`status_map` words: `closed`, `waiting`, `open`).
- Messages: an email with `hs_email_direction` `INCOMING_EMAIL` -> customer, any other
  email -> agent; a note -> note. Text: `hs_email_text`, else the HTML stripped. The
  ticket's `content` (its description) is the first customer message when the ticket has
  no email, so a ticket from a form or chat is not empty.
- Requester: the first contact on the ticket; none -> a placeholder contact.
- HubSpot tickets have no tags; `hs_ticket_category` is kept in the fields as `category`.
- Articles and saved replies are not imported: HubSpot's knowledge base and snippets have
  no public read API (the md: the knowledge base comes from HubSpot's export file). The
  dry run says so (`warnings`).
- Search answers at most 10,000 results for one query, so a long look for changes starts
  a new query from the last change time it reached before that limit.

Not verified against a live account. Unsure of: the `ticketState` metadata on every
stage (a stage without it is open unless its name says waiting); association results
over 500 ids on one ticket (only the first page is read); `hs_attachment_ids` being `;`
separated ids; the `signed-url` answer's `url` field.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from cedesk import egress
from cedesk.egress import Guard
from cedesk.helpdesk.domain import imports as rules
from cedesk.helpdesk.domain.imports import (
    AccountInfo,
    Attachment,
    Contact,
    Counts,
    Group,
    Message,
    Page,
    SourceConnector,
    Ticket,
)
from cedesk.helpdesk.service.imports.gorgias import strip_html
from cedesk.helpdesk.service.imports.http import SourceHttp, bearer, segment, when

LIMIT = 100  # documented maximum for lists and batch reads
OWNER_PAGES_MAX = 50
SEARCH_MAX = 10_000  # HubSpot answers no further than this in one search
FILE_SCHEME = "hubspot-file:"
STATUS_MAP: Mapping[str, str] = {"open": "open", "waiting": "pending", "closed": "resolved"}
TICKET_PROPERTIES = (
    "subject", "content", "hs_pipeline", "hs_pipeline_stage", "hs_ticket_priority",
    "source_type", "createdate", "hs_lastmodifieddate", "closed_date", "hubspot_owner_id",
    "hs_ticket_category",
)  # fmt: skip
EMAIL_PROPERTIES = (
    "hs_email_text", "hs_email_html", "hs_email_direction", "hs_timestamp",
    "hs_email_from_email", "hs_email_from_firstname", "hs_email_from_lastname",
    "hs_attachment_ids",
)  # fmt: skip
NOTE_PROPERTIES = ("hs_note_body", "hs_timestamp", "hs_attachment_ids")
CONTACT_PROPERTIES = ("email", "firstname", "lastname")
WAITING = re.compile(r"waiting on (the )?(contact|customer)")


def _props(row: Mapping[str, Any]) -> Mapping[str, Any]:
    found = row.get("properties")
    return found if isinstance(found, dict) else {}


def _str(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _rows(found: object) -> list[Mapping[str, Any]]:
    results = found.get("results") if isinstance(found, dict) else None
    return [r for r in results if isinstance(r, dict)] if isinstance(results, list) else []


def _after(found: object) -> str | None:
    paging = found.get("paging") if isinstance(found, dict) else None
    nxt = paging.get("next") if isinstance(paging, dict) else None
    after = nxt.get("after") if isinstance(nxt, dict) else None
    return str(after) if after not in (None, "") else None


def _chunks(ids: Sequence[str]) -> list[list[str]]:
    return [list(ids[i : i + LIMIT]) for i in range(0, len(ids), LIMIT)]


def stage_word(stage: Mapping[str, Any]) -> str:
    """`closed`, `waiting` or `open` for one pipeline stage (see the module docstring)."""
    meta = stage.get("metadata")
    if isinstance(meta, dict) and str(meta.get("ticketState") or "").upper() == "CLOSED":
        return "closed"
    return "waiting" if WAITING.search(str(stage.get("label") or "").lower()) else "open"


def contact_from(row: Mapping[str, Any]) -> Contact:
    p = _props(row)
    email = _str(p.get("email"))
    name = " ".join(x for x in (_str(p.get("firstname")), _str(p.get("lastname"))) if x)
    return Contact(str(row["id"]), email, name or (email or "").split("@")[0] or "Unknown")


class HubSpotConnector:
    source = "hubspot"
    status_map: Mapping[str, str] = STATUS_MAP

    def __init__(self, http: SourceHttp) -> None:
        self._http = http
        self._stages: dict[str, str] | None = None  # stage id -> closed | waiting | open
        self._pipelines: list[Group] | None = None
        self._owners: dict[str, str] | None = None  # owner id -> email

    def warnings(self) -> list[str]:
        return [
            "HubSpot's knowledge base and snippets can't be read through its API, so "
            "articles and saved replies aren't imported."
        ]

    async def check(self) -> AccountInfo:
        found = await self._http.get("/account-info/v3/details")
        portal = found.get("portalId") if isinstance(found, dict) else None
        return AccountInfo(f"HubSpot account {portal}" if portal else "HubSpot account")

    async def _total(self, obj: str) -> int:
        found = await self._http.read(f"/crm/v3/objects/{obj}/search", {"limit": 1})
        total = found.get("total") if isinstance(found, dict) else None
        return total if isinstance(total, int) and not isinstance(total, bool) else 0

    async def discover(self) -> Counts:
        return Counts(
            contacts=await self._total("contacts"),
            tickets=await self._total("tickets"),
            articles=0,
            replies=0,
            groups=len(await self.groups()),
        )

    async def _load_pipelines(self) -> None:
        found = await self._http.get("/crm/v3/pipelines/tickets")
        pipelines: list[Group] = []
        stages: dict[str, str] = {}
        for row in _rows(found):
            if row.get("id") is None:
                continue
            pipelines.append(Group(str(row["id"]), str(row.get("label") or row["id"])))
            for stage in row.get("stages") or []:
                if isinstance(stage, dict) and stage.get("id") is not None:
                    stages[str(stage["id"])] = stage_word(stage)
        self._pipelines, self._stages = pipelines, stages

    async def groups(self) -> Sequence[Group]:
        if self._pipelines is None:
            await self._load_pipelines()
        return list(self._pipelines or [])

    async def page(self, kind: str, cursor: Mapping[str, Any] | None) -> Page[Any]:
        after = str(cursor["after"]) if cursor and cursor.get("after") else None
        if kind == "contacts":
            found = await self._http.get(
                "/crm/v3/objects/contacts",
                {"limit": LIMIT, "after": after, "properties": ",".join(CONTACT_PROPERTIES)},
            )
            items = [contact_from(r) for r in _rows(found) if r.get("id") is not None]
            nxt = _after(found)
            return Page(items, {"after": nxt} if nxt else None)
        if kind == "tickets":
            found = await self._http.get(
                "/crm/v3/objects/tickets",
                {"limit": LIMIT, "after": after, "properties": ",".join(TICKET_PROPERTIES)},
            )
            nxt = _after(found)
            return Page(await self._tickets(_rows(found)), {"after": nxt} if nxt else None)
        return Page([], None)  # articles and replies: see the module docstring

    async def changed_tickets(
        self, since: datetime, cursor: Mapping[str, Any] | None
    ) -> Page[Ticket]:
        """Oldest change first. Past HubSpot's 10,000 results a new query starts from the
        last change time reached (`from` in the cursor)."""
        start = (
            int(cursor["from"]) if cursor and cursor.get("from") else int(since.timestamp() * 1000)
        )
        after = str(cursor["after"]) if cursor and cursor.get("after") else None
        body: dict[str, Any] = {
            "filterGroups": [{"filters": [
                {"propertyName": "hs_lastmodifieddate", "operator": "GTE", "value": str(start)}
            ]}],
            "sorts": [{"propertyName": "hs_lastmodifieddate", "direction": "ASCENDING"}],
            "properties": list(TICKET_PROPERTIES),
            "limit": LIMIT,
        }  # fmt: skip
        if after:
            body["after"] = after
        found = await self._http.read("/crm/v3/objects/tickets/search", body)
        rows = _rows(found)
        items = await self._tickets(rows)
        nxt = _after(found)
        if nxt is None:
            return Page(items, None)
        if nxt.isdigit() and int(nxt) + LIMIT > SEARCH_MAX and rows:
            last = when(_props(rows[-1]).get("hs_lastmodifieddate"))
            if last is not None:  # a new query from there (a change seen twice is harmless)
                return Page(items, {"from": int(last.timestamp() * 1000)})
        return Page(items, {"from": start, "after": nxt})

    # Tickets

    async def _associations(self, to: str, ticket_ids: Sequence[str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {t: [] for t in ticket_ids}
        for chunk in _chunks(ticket_ids):
            found = await self._http.read(
                f"/crm/v4/associations/tickets/{to}/batch/read",
                {"inputs": [{"id": t} for t in chunk]},
            )
            for row in _rows(found):
                source = row.get("from")
                key = str(source.get("id")) if isinstance(source, dict) else None
                if key not in out:
                    continue
                for target in row.get("to") or []:
                    if isinstance(target, dict) and target.get("toObjectId") is not None:
                        out[key].append(str(target["toObjectId"]))
        return out

    async def _batch(
        self, obj: str, ids: Sequence[str], properties: Sequence[str]
    ) -> dict[str, Mapping[str, Any]]:
        out: dict[str, Mapping[str, Any]] = {}
        for chunk in _chunks(list(dict.fromkeys(ids))):
            found = await self._http.read(
                f"/crm/v3/objects/{obj}/batch/read",
                {"properties": list(properties), "inputs": [{"id": i} for i in chunk]},
            )
            out |= {str(r["id"]): r for r in _rows(found) if r.get("id") is not None}
        return out

    async def _owner_emails(self) -> dict[str, str]:
        if self._owners is None:
            owners: dict[str, str] = {}
            after: str | None = None
            for _ in range(OWNER_PAGES_MAX):
                found = await self._http.get("/crm/v3/owners", {"limit": LIMIT, "after": after})
                for row in _rows(found):
                    email = _str(row.get("email"))
                    if row.get("id") is not None and email:
                        owners[str(row["id"])] = email
                after = _after(found)
                if after is None:
                    break
            self._owners = owners
        return self._owners

    async def _files(self, raw: object) -> list[Attachment]:
        out: list[Attachment] = []
        for file_id in [i.strip() for i in str(raw or "").split(";") if i.strip().isdigit()]:
            info = await self._http.get(f"/files/v3/files/{segment(file_id)}", missing_ok=True)
            if not isinstance(info, dict):
                continue
            name = _str(info.get("name")) or f"attachment-{file_id}"
            extension = _str(info.get("extension"))
            if extension and not name.lower().endswith(f".{extension.lower()}"):
                name = f"{name}.{extension}"
            size = info.get("size")
            mime = _str(info.get("type"))
            out.append(
                Attachment(
                    name=name,
                    url=f"{FILE_SCHEME}{file_id}",
                    size=size if isinstance(size, int) and not isinstance(size, bool) else None,
                    mime=mime if mime and "/" in mime else None,
                )
            )
        return out

    async def fetch(self, attachment: Attachment, limit: int) -> bytes | None:
        """A HubSpot file is read through a short-lived signed link HubSpot hands out; the
        token is never sent to the host that serves it."""
        if not attachment.url.startswith(FILE_SCHEME):
            return None
        file_id = attachment.url.removeprefix(FILE_SCHEME)
        found = await self._http.get(
            f"/files/v3/files/{segment(file_id)}/signed-url", missing_ok=True
        )
        url = found.get("url") if isinstance(found, dict) else None
        return await self._http.download(url, limit) if isinstance(url, str) else None

    async def _email(self, row: Mapping[str, Any]) -> Message | None:
        p = _props(row)
        files = await self._files(p.get("hs_attachment_ids"))
        html = p.get("hs_email_html")
        text = rules.body(
            _str(p.get("hs_email_text")) or (strip_html(html) if isinstance(html, str) else "")
        )
        if not text and not files:
            return None
        incoming = str(p.get("hs_email_direction") or "").upper() == "INCOMING_EMAIL"
        name = " ".join(
            x
            for x in (_str(p.get("hs_email_from_firstname")), _str(p.get("hs_email_from_lastname")))
            if x
        )
        return Message(
            kind="customer" if incoming else "agent",
            body=text or "(attachment only)",
            created_at=when(p.get("hs_timestamp")) or datetime.now(UTC),
            external_id=f"email-{row['id']}",
            author_name=name or None,
            author_email=_str(p.get("hs_email_from_email")),
            attachments=files,
        )

    async def _note(self, row: Mapping[str, Any]) -> Message | None:
        p = _props(row)
        files = await self._files(p.get("hs_attachment_ids"))
        html = p.get("hs_note_body")
        text = rules.body(strip_html(html) if isinstance(html, str) else "")
        if not text and not files:
            return None
        return Message(
            kind="note",
            body=text or "(attachment only)",
            created_at=when(p.get("hs_timestamp")) or datetime.now(UTC),
            external_id=f"note-{row['id']}",
            attachments=files,
        )

    async def _tickets(self, rows: Sequence[Mapping[str, Any]]) -> list[Ticket]:
        rows = [r for r in rows if r.get("id") is not None]
        if not rows:
            return []
        if self._stages is None:
            await self._load_pipelines()
        ids = [str(r["id"]) for r in rows]
        contact_ids = await self._associations("contacts", ids)
        email_ids = await self._associations("emails", ids)
        note_ids = await self._associations("notes", ids)
        contacts = await self._batch(
            "contacts", [c[0] for c in contact_ids.values() if c], CONTACT_PROPERTIES
        )
        emails = await self._batch(
            "emails", [e for es in email_ids.values() for e in es], EMAIL_PROPERTIES
        )
        notes = await self._batch(
            "notes", [n for ns in note_ids.values() for n in ns], NOTE_PROPERTIES
        )
        owners = await self._owner_emails()
        out: list[Ticket] = []
        for row in rows:
            tid = str(row["id"])
            p = _props(row)
            messages: list[Message] = []
            for email_id in email_ids[tid]:
                if email_id in emails and (m := await self._email(emails[email_id])):
                    messages.append(m)
            content = rules.body(_str(p.get("content")))
            created = when(p.get("createdate")) or datetime.now(UTC)
            if content and not messages:
                messages.append(Message("customer", content, created, f"ticket-{tid}"))
            for note_id in note_ids[tid]:
                if note_id in notes and (m := await self._note(notes[note_id])):
                    messages.append(m)
            messages.sort(key=lambda m: m.created_at)
            first = contact_ids[tid][0] if contact_ids[tid] else None
            requester = (
                contact_from(contacts[first])
                if first in contacts
                else Contact(f"ticket-{tid}", None, "Unknown")
            )
            category = _str(p.get("hs_ticket_category"))
            out.append(
                Ticket(
                    external_id=tid,
                    subject=rules.subject(p.get("subject")),
                    status=(self._stages or {}).get(str(p.get("hs_pipeline_stage")), "open"),
                    created_at=created,
                    requester=requester,
                    messages=messages,
                    priority=_str(p.get("hs_ticket_priority")),
                    channel=_str(p.get("source_type")),
                    group_id=_str(p.get("hs_pipeline")),
                    assignee_email=owners.get(str(p.get("hubspot_owner_id"))),
                    fields={"category": category} if category else {},
                    updated_at=when(p.get("hs_lastmodifieddate")),
                    resolved_at=when(p.get("closed_date")),
                )
            )
        return out


def make(
    settings: Mapping[str, str],
    credentials: Mapping[str, str],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    guard: Guard | None = None,
) -> SourceConnector:
    """HubSpot's host is fixed: nothing the Admin types reaches it."""
    http = SourceHttp(
        egress.fixed_host("hubspot", "api"),
        headers=bearer(credentials.get("token", "")),
        guard=guard,
        transport=transport,
        label="HubSpot",
    )
    return HubSpotConnector(http)
