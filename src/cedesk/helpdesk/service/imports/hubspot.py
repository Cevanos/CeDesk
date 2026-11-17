"""The HubSpot importer: contacts, and tickets with their whole history, by pipeline.

HubSpot CRM API, read only, at the fixed host `https://api.hubapi.com`, with a private app's
access token or a service key (both are sent as `Authorization: Bearer`). Lists are paged
with `after` (`paging.next.after`), 100 records a page. Some reads are only offered as a
`POST` of JSON (search, batch read); `SourceHttp.read` allows exactly those paths and
nothing that writes.

Endpoints used:

- check: `GET /account-info/v3/details` (`portalId`, `uiDomain`: the ticket's web address)
- discover: `POST /crm/v3/objects/{contacts,tickets}/search` with `limit: 1` (`total`), and
  one `limit=1` read of each optional source (below) so the dry run says what the key can't
  read
- groups: `GET /crm/v3/pipelines/tickets` (the pipelines, each with its stages)
- contacts: `GET /crm/v3/objects/contacts` (`email`, names, `phone`, `mobilephone`,
  `company`)
- tickets: `GET /crm/v3/objects/tickets`, then for the page:
  `POST /crm/v4/associations/tickets/{contacts,emails,notes,calls,meetings,communications}/
  batch/read` (100 ids a call; each result's own `paging.next.after` is sent back with its
  `id` until it has none) and `POST /crm/v3/objects/{...}/batch/read` (100 ids a call)
- custom ticket properties: `GET /crm/v3/properties/tickets` once; the account's own
  properties (`hubspotDefined` not true, not archived) are read for each page with
  `POST /crm/v3/objects/tickets/batch/read` (a list URL with every name could pass
  HubSpot's URL length) and kept in the fields, an enumeration's value as its label
- assignees and note authors: `GET /crm/v3/owners`, every page, then again with
  `archived=true` (deactivated users) so the tickets of a former agent keep their assignee
- chat and other inbox threads (`conversations.read`): for a ticket whose `source_type` is
  not `EMAIL`, `GET /conversations/v3/conversations/threads?associatedContactId=..&
  threadStatus=OPEN|CLOSED&association=TICKET` for its contact, the threads whose
  `threadAssociations.associatedTicketId` is the ticket, then
  `GET /conversations/v3/conversations/threads/{id}/messages?sort=createdAt`
- files: `GET /files/v3/files/{id}` (name, size, access, url) and
  `GET /files/v3/files/{id}/signed-url`
- changes since a time (M9): `POST /crm/v3/objects/tickets/search`, `hs_lastmodifieddate`
  at or after the time, oldest change first

Scopes (private app or service key): `crm.objects.tickets.read` (or the legacy `tickets`),
`crm.objects.contacts.read`, `crm.objects.owners.read`, `sales-email-read` (without it
HubSpot hides email bodies) and `files.read` (or the legacy `files`). Optional, each only
widening what comes over: `crm.objects.calls.read`, `crm.objects.meetings.read` and
`conversations.read`. When HubSpot refuses one of the optional reads (403), the import goes
on without it and `warnings()` says what was left out.

Decisions:

- Status: HubSpot's stages are the account's own (md 8.3). A stage HubSpot marks closed
  (`metadata.ticketState` `CLOSED`) -> resolved; a stage named like "waiting on contact"
  (HubSpot's default stage for "we asked the customer") -> pending; any other -> open. The
  dry run shows this table (`status_map` words: `closed`, `waiting`, `open`).
- Messages: an email with `hs_email_direction` `INCOMING_EMAIL` -> customer, any other
  email -> agent; a note, a call, a meeting and a logged SMS / WhatsApp / LinkedIn message
  (HubSpot documents no direction for these) -> note, by the record's owner. A thread
  message `INCOMING` -> customer, `OUTGOING` -> agent, an inbox comment -> note.
- The ticket's `content` (its description) is always its first customer message, unless it
  is the text of the first email (a ticket made from an email repeats it).
- Requester: the first contact on the ticket; none -> a placeholder contact.
- HubSpot tickets have no tags; `hs_ticket_category` is kept in the fields as `category`
  and is the ticket type (its label). Properties whose name has `_sla_` are kept in the
  fields as they are (HubSpot doesn't document them: UNVERIFIED).
- A date HubSpot doesn't give is never "now" silently: `rules.dated` takes the nearest
  source date and notes the ticket when there is none.
- A file HubSpot no longer has (404, or archived) is kept as a link line naming it.
  `Attachment.url` is a reference a person can use: the file's own URL when it is public,
  else "<name> in HubSpot (file id N)". The file id at its end is how `fetch` finds it.
- Articles and saved replies are not imported: HubSpot's knowledge base and snippets have
  no public read API (the md: the knowledge base comes from HubSpot's export file). The
  dry run says so (`warnings`).
- Search answers at most 10,000 results for one query, so a long look for changes starts
  a new query from the last change time it reached before that limit.

Not verified against a live account. Unsure of: the `ticketState` metadata on every stage;
`hs_attachment_ids` and `hs_email_to_email` being `;` separated; `hs_email_cc_email` and
`hs_email_bcc_email` (not in HubSpot's table of email properties); the scope that gates
communications; the thread lookup by contact; the web address of a ticket.
"""

import mimetypes
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
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
    RecordGone,
    SourceConnector,
    SourceError,
    Ticket,
)
from cedesk.helpdesk.service.imports.gorgias import strip_html
from cedesk.helpdesk.service.imports.http import SourceHttp, bearer, segment, when

LIMIT = 100  # documented maximum for lists and batch reads
SEARCH_MAX = 10_000  # HubSpot answers no further than this in one search
FILE_SCHEME = "hubspot-file:"  # the old reference, still understood by `fetch`
FILE_REF = re.compile(r"file id (\d+)\)$")
STATUS_MAP: Mapping[str, str] = {"open": "open", "waiting": "pending", "closed": "resolved"}
TICKET_PROPERTIES = (
    "subject", "content", "hs_pipeline", "hs_pipeline_stage", "hs_ticket_priority",
    "source_type", "createdate", "hs_lastmodifieddate", "closed_date", "hubspot_owner_id",
    "hs_ticket_category",
)  # fmt: skip
EMAIL_PROPERTIES = (
    "hs_email_text", "hs_email_html", "hs_email_direction", "hs_timestamp", "hs_createdate",
    "hs_email_from_email", "hs_email_from_firstname", "hs_email_from_lastname",
    "hs_email_to_email", "hs_email_cc_email", "hs_email_bcc_email", "hs_attachment_ids",
)  # fmt: skip
NOTE_PROPERTIES = (
    "hs_note_body", "hs_timestamp", "hs_createdate", "hs_attachment_ids", "hubspot_owner_id",
)  # fmt: skip
CALL_PROPERTIES = (
    "hs_call_title", "hs_call_body", "hs_call_direction", "hs_call_status",
    "hs_call_duration", "hs_call_recording_url", "hs_timestamp", "hs_createdate",
    "hubspot_owner_id", "hs_attachment_ids",
)  # fmt: skip
MEETING_PROPERTIES = (
    "hs_meeting_title", "hs_meeting_body", "hs_internal_meeting_notes", "hs_meeting_summary",
    "hs_meeting_start_time", "hs_meeting_end_time", "hs_meeting_outcome",
    "hs_meeting_location", "hs_timestamp", "hs_createdate", "hubspot_owner_id",
    "hs_attachment_ids",
)  # fmt: skip
COMMUNICATION_PROPERTIES = (
    "hs_communication_channel_type", "hs_communication_body", "hs_timestamp",
    "hs_createdate", "hubspot_owner_id",
)  # fmt: skip
ACTIVITIES: Mapping[str, tuple[str, ...]] = {
    "calls": CALL_PROPERTIES,
    "meetings": MEETING_PROPERTIES,
    "communications": COMMUNICATION_PROPERTIES,
}
CONTACT_PROPERTIES = ("email", "firstname", "lastname", "phone", "mobilephone", "company")
CHANNEL_NAMES = {"SMS": "SMS", "WHATS_APP": "WhatsApp", "LINKEDIN_MESSAGE": "LinkedIn message"}
UI_DOMAIN = re.compile(r"^app(-[a-z0-9]+)?\.hubspot\.com$")
WAITING = re.compile(r"waiting on (the )?(contact|customer)")
THREADS = "/conversations/v3/conversations/threads"
LEFT_OUT = {
    "calls": "calls logged on tickets (scope crm.objects.calls.read)",
    "meetings": "meetings logged on tickets (scope crm.objects.meetings.read)",
    "communications": "SMS, WhatsApp and LinkedIn messages logged on tickets",
    "conversations": "chat and other inbox threads (scope conversations.read)",
    "properties": "the account's own ticket properties",
}


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


def _chunks[T](items: Sequence[T]) -> list[list[T]]:
    return [list(items[i : i + LIMIT]) for i in range(0, len(items), LIMIT)]


def _same(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())


def _addresses(raw: object) -> list[str]:
    """`a@x.com;b@y.com` (or comma separated) as a list of addresses."""
    found = re.split(r"[;,\s]+", raw) if isinstance(raw, str) else []
    return list(dict.fromkeys(a for a in found if "@" in a))


def _html_text(raw: object) -> str:
    return strip_html(raw) if isinstance(raw, str) else ""


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
    return Contact(
        str(row["id"]),
        email,
        name or (email or "").split("@")[0] or "Unknown",
        phone=_str(p.get("phone")) or _str(p.get("mobilephone")),
        organization=_str(p.get("company")),
    )


def file_ref(file_id: str, name: str, info: Mapping[str, Any] | None) -> str:
    """What a person can use to find the file (B-34), ending in the id `fetch` reads: the
    file's own URL when HubSpot serves it publicly, else its name in HubSpot."""
    url = _str(info.get("url")) if info else None
    access = str(info.get("access") or "") if info else ""
    if url and url.startswith("https://") and access.upper().startswith("PUBLIC"):
        return f"{url} (HubSpot file id {file_id})"
    return f"{name[:200]} in HubSpot (file id {file_id})"


def file_id_of(url: str) -> str | None:
    if url.startswith(FILE_SCHEME):
        found = url.removeprefix(FILE_SCHEME)
        return found if found.isdigit() else None
    match = FILE_REF.search(url)
    return match.group(1) if match else None


@dataclass(frozen=True)
class Owner:
    email: str | None
    name: str | None


@dataclass(frozen=True)
class CustomProperty:
    name: str
    label: str
    options: Mapping[str, str]  # value -> label (enumerations)


@dataclass
class _Read:
    """What was read for one page of tickets, shared by every ticket on it."""

    contact_ids: dict[str, list[str]]
    email_ids: dict[str, list[str]]
    note_ids: dict[str, list[str]]
    activity_ids: dict[str, dict[str, list[str]]]
    contacts: dict[str, Mapping[str, Any]]
    emails: dict[str, Mapping[str, Any]]
    notes: dict[str, Mapping[str, Any]]
    activities: dict[str, dict[str, Mapping[str, Any]]]
    custom: dict[str, Mapping[str, Any]]
    owners: Mapping[str, Owner]
    threads: dict[str, list[Mapping[str, Any]]] = field(default_factory=dict)


class HubSpotConnector:
    source = "hubspot"
    status_map: Mapping[str, str] = STATUS_MAP

    def __init__(self, http: SourceHttp) -> None:
        self._http = http
        self._stages: dict[str, str] | None = None  # stage id -> closed | waiting | open
        self._pipelines: list[Group] | None = None
        self._owners: dict[str, Owner] | None = None
        self._custom: list[CustomProperty] | None = None
        self._category_labels: dict[str, str] = {}
        self._sla: list[str] = []
        self._account: tuple[str, str] | None = None  # (portal id, ui domain)
        self._account_read = False
        self._blocked: set[str] = set()  # optional reads HubSpot refused (see LEFT_OUT)
        self.failures = rules.RecordFailures()  # tickets deleted mid-import (P0-2)

    def warnings(self) -> list[str]:
        out = [
            "HubSpot's knowledge base and snippets can't be read through its API, so "
            "articles and saved replies aren't imported."
        ]
        for what in LEFT_OUT:
            if what in self._blocked:
                out.append(
                    f"HubSpot refused this key access to {LEFT_OUT[what]}, so they aren't "
                    "imported. Add the scope to the private app or service key to bring them."
                )
        return out

    async def _optional[T](self, what: str, call: Callable[[], Awaitable[T]]) -> T | None:
        """`call()`, or None when HubSpot refuses it for good (a scope the key lacks): that
        part is left out, and `warnings()` says so. A temporary error or a 429 is raised."""
        if what in self._blocked:
            return None
        try:
            return await call()
        except SourceError as exc:
            if not exc.permanent or isinstance(exc, RecordGone):
                raise
            self._blocked.add(what)
            return None

    async def check(self) -> AccountInfo:
        found = await self._http.get("/account-info/v3/details")
        self._remember_account(found)
        portal = found.get("portalId") if isinstance(found, dict) else None
        return AccountInfo(f"HubSpot account {portal}" if portal else "HubSpot account")

    def _remember_account(self, found: object) -> None:
        self._account_read = True
        portal = found.get("portalId") if isinstance(found, dict) else None
        domain = str(found.get("uiDomain") or "") if isinstance(found, dict) else ""
        if portal is not None and not isinstance(portal, bool) and str(portal).isdigit():
            self._account = (str(portal), domain if UI_DOMAIN.match(domain) else "app.hubspot.com")

    async def _ticket_url(self, ticket_id: str) -> str | None:
        if not self._account_read:
            found = await self._optional(
                "account", lambda: self._http.get("/account-info/v3/details")
            )
            self._remember_account(found)
        if self._account is None:
            return None
        portal, domain = self._account
        return f"https://{domain}/contacts/{portal}/ticket/{segment(ticket_id)}"

    async def _total(self, obj: str) -> int:
        found = await self._http.read(f"/crm/v3/objects/{obj}/search", {"limit": 1})
        total = found.get("total") if isinstance(found, dict) else None
        return total if isinstance(total, int) and not isinstance(total, bool) else 0

    async def discover(self) -> Counts:
        counts = Counts(
            contacts=await self._total("contacts"),
            tickets=await self._total("tickets"),
            articles=0,
            replies=0,
            groups=len(await self.groups()),
        )
        for obj in ACTIVITIES:  # so the dry run can say what this key can't read
            await self._optional(
                obj, partial(self._http.get, f"/crm/v3/objects/{obj}", {"limit": 1})
            )
        await self._optional("conversations", partial(self._http.get, THREADS, {"limit": 1}))
        await self._custom_properties()
        return counts

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

    # What every page needs

    async def _associations(self, to: str, ticket_ids: Sequence[str]) -> dict[str, list[str]]:
        """Every `to` record linked to each ticket. HubSpot pages the links of each input on
        its own: a result with `paging.next.after` is asked again with that `after`."""
        out: dict[str, list[str]] = {t: [] for t in ticket_ids}
        pending: list[dict[str, str]] = [{"id": t} for t in ticket_ids]
        asked: set[tuple[str, str]] = set()
        while pending:
            again: list[dict[str, str]] = []
            for chunk in _chunks(pending):
                found = await self._http.read(
                    f"/crm/v4/associations/tickets/{to}/batch/read", {"inputs": chunk}
                )
                for row in _rows(found):
                    source = row.get("from")
                    key = str(source.get("id")) if isinstance(source, dict) else None
                    if key is None or key not in out:
                        continue
                    for target in row.get("to") or []:
                        if isinstance(target, dict) and target.get("toObjectId") is not None:
                            linked = str(target["toObjectId"])
                            if linked not in out[key]:
                                out[key].append(linked)
                    after = _after(row)
                    if after is not None and (key, after) not in asked:
                        asked.add((key, after))
                        again.append({"id": key, "after": after})
            pending = again
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

    async def _owner_pages(self, owners: dict[str, Owner], archived: bool) -> None:
        after: str | None = None
        seen: set[str] = set()
        while True:
            params: dict[str, Any] = {"limit": LIMIT, "after": after}
            if archived:
                params["archived"] = "true"
            found = await self._http.get("/crm/v3/owners", params)
            for row in _rows(found):
                if row.get("id") is None or str(row["id"]) in owners:
                    continue
                name = " ".join(
                    x for x in (_str(row.get("firstName")), _str(row.get("lastName"))) if x
                )
                owners[str(row["id"])] = Owner(_str(row.get("email")), name or None)
            after = _after(found)
            if after is None or after in seen:
                return
            seen.add(after)

    async def _owner_map(self) -> dict[str, Owner]:
        """Every owner, active first, then archived (deactivated users, C6)."""
        if self._owners is None:
            owners: dict[str, Owner] = {}
            await self._owner_pages(owners, archived=False)
            await self._owner_pages(owners, archived=True)
            self._owners = owners
        return self._owners

    async def _custom_properties(self) -> list[CustomProperty]:
        """The account's own ticket properties (not HubSpot's), read once."""
        if self._custom is None:
            found = await self._optional(
                "properties", lambda: self._http.get("/crm/v3/properties/tickets")
            )
            custom: list[CustomProperty] = []
            for row in _rows(found):
                name = _str(row.get("name"))
                if not name or row.get("archived") is True:
                    continue
                options = {
                    str(o.get("value")): str(o.get("label") or o.get("value"))
                    for o in row.get("options") or []
                    if isinstance(o, dict) and o.get("value") is not None
                }
                if name == "hs_ticket_category":
                    self._category_labels = options
                if row.get("hubspotDefined") is True:
                    if "_sla_" in name and name not in TICKET_PROPERTIES:
                        self._sla.append(name)
                    continue
                if name not in TICKET_PROPERTIES:
                    custom.append(CustomProperty(name, _str(row.get("label")) or name, options))
            self._custom = custom
        return self._custom

    # Files

    async def _files(self, raw: object) -> list[Attachment]:
        ids = [i.strip() for i in str(raw or "").split(";") if i.strip().isdigit()]
        return [await self._file(file_id) for file_id in ids]

    async def _file(self, file_id: str, name: str | None = None) -> Attachment:
        """A file on a message. One HubSpot no longer has (404 or archived) is still handed
        over, so the framework keeps a link line naming it and counts it (C20)."""
        info = await self._http.get(f"/files/v3/files/{segment(file_id)}", missing_ok=True)
        if not isinstance(info, dict) or info.get("archived") is True:
            fallback = name or f"attachment-{file_id}"
            return Attachment(name=fallback, url=file_ref(file_id, fallback, None))
        found = _str(info.get("name")) or name or f"attachment-{file_id}"
        extension = _str(info.get("extension"))
        if extension and not found.lower().endswith(f".{extension.lower().lstrip('.')}"):
            found = f"{found}.{extension.lstrip('.')}"
        size = info.get("size")
        mime = _str(info.get("type"))  # HubSpot's own kind (DOCUMENT, IMG), seldom a type
        if not mime or "/" not in mime:
            mime = mimetypes.guess_type(found)[0]
        return Attachment(
            name=found,
            url=file_ref(file_id, found, info),
            size=size if isinstance(size, int) and not isinstance(size, bool) else None,
            mime=mime if mime and "/" in mime else None,
        )

    async def fetch(self, attachment: Attachment, limit: int) -> bytes | None:
        """A HubSpot file is read through a short-lived signed link HubSpot hands out; the
        token is never sent to the host that serves it. None when HubSpot no longer has it."""
        file_id = file_id_of(attachment.url)
        if file_id is None:
            return None
        found = await self._http.get(
            f"/files/v3/files/{segment(file_id)}/signed-url", missing_ok=True
        )
        url = found.get("url") if isinstance(found, dict) else None
        return await self._http.download(url, limit) if isinstance(url, str) else None

    # Messages

    def _date(self, tid: str, *candidates: object, what: str = "message date") -> datetime:
        dates = [c if isinstance(c, datetime) else when(c) for c in candidates]
        return rules.dated(self.failures, "ticket", tid, *dates, what=what)

    async def _email(self, tid: str, row: Mapping[str, Any], fallback: object) -> Message | None:
        p = _props(row)
        files = await self._files(p.get("hs_attachment_ids"))
        text = rules.body(_str(p.get("hs_email_text")) or _html_text(p.get("hs_email_html")))
        if not text and not files:
            rules.loss("message_without_text")
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
            created_at=self._date(tid, p.get("hs_timestamp"), p.get("hs_createdate"), fallback),
            external_id=f"email-{row['id']}",
            author_name=name or None,
            author_email=_str(p.get("hs_email_from_email")),
            attachments=files,
            to=_addresses(p.get("hs_email_to_email")),
            cc=_addresses(p.get("hs_email_cc_email")),
            bcc=_addresses(p.get("hs_email_bcc_email")),
        )

    async def _logged(
        self, tid: str, prefix: str, row: Mapping[str, Any], text: str, fallback: object, r: _Read
    ) -> Message | None:
        """A note, call, meeting or logged message: a note by the record's owner."""
        p = _props(row)
        files = await self._files(p.get("hs_attachment_ids"))
        text = rules.body(text)
        if not text and not files:
            rules.loss("message_without_text")
            return None
        owner = r.owners.get(str(p.get("hubspot_owner_id")))
        return Message(
            kind="note",
            body=text or "(attachment only)",
            created_at=self._date(tid, p.get("hs_timestamp"), p.get("hs_createdate"), fallback),
            external_id=f"{prefix}-{row['id']}",
            author_name=owner.name if owner else None,
            author_email=owner.email if owner else None,
            attachments=files,
        )

    async def _thread_messages(self, tid: str, thread_id: str, known: list[str]) -> list[Message]:
        out: list[Message] = []
        after: str | None = None
        seen: set[str] = set()
        path = f"{THREADS}/{segment(thread_id)}/messages"
        while True:
            try:
                found = await self._optional(
                    "conversations",
                    partial(
                        self._http.get,
                        path,
                        {"limit": LIMIT, "after": after, "sort": "createdAt"},
                        record=True,
                    ),
                )
            except RecordGone:
                self.failures.incomplete(
                    "ticket", tid, "HubSpot no longer has one of its inbox threads (404)."
                )
                return out
            for row in _rows(found):
                message = await self._thread_message(tid, thread_id, row, known)
                if message is not None:
                    out.append(message)
            after = _after(found)
            if after is None or after in seen:
                return out
            seen.add(after)

    async def _thread_message(
        self, tid: str, thread_id: str, row: Mapping[str, Any], known: list[str]
    ) -> Message | None:
        kind = str(row.get("type") or "").upper()
        if kind not in ("MESSAGE", "COMMENT") or row.get("id") is None:
            return None  # welcome, assignment and status lines are HubSpot's own
        text = rules.body(_str(row.get("text")) or _html_text(row.get("richText")))
        files = [
            await self._file(str(a["fileId"]), _str(a.get("name")))
            for a in row.get("attachments") or []
            if isinstance(a, dict)
            and str(a.get("type")) == "FILE"
            and str(a.get("fileId")).isdigit()
        ]
        if not text and not files:
            return None
        if text and any(_same(text, k) for k in known):
            return None  # already here as an email or the description
        senders = [s for s in row.get("senders") or [] if isinstance(s, dict)]
        sender = senders[0] if senders else {}
        ident = sender.get("deliveryIdentifier")
        address = _str(ident.get("value")) if isinstance(ident, dict) else None
        if kind == "COMMENT":
            role = "note"
        else:
            role = "customer" if str(row.get("direction")).upper() == "INCOMING" else "agent"
        return Message(
            kind=role,
            body=text or "(attachment only)",
            created_at=self._date(tid, row.get("createdAt")),
            external_id=f"thread-{thread_id}-{row['id']}",
            author_name=_str(sender.get("name")),
            author_email=address if address and "@" in address else None,
            attachments=files,
        )

    async def _threads_of(self, contact_id: str, r: _Read) -> list[Mapping[str, Any]]:
        """The contact's inbox threads (open and closed), each with its ticket, once a page."""
        if contact_id in r.threads:
            return r.threads[contact_id]
        out: list[Mapping[str, Any]] = []
        for status in ("OPEN", "CLOSED"):
            after: str | None = None
            seen: set[str] = set()
            while True:
                params = {
                    "associatedContactId": contact_id, "threadStatus": status,
                    "association": "TICKET", "limit": LIMIT, "after": after,
                }  # fmt: skip
                found = await self._optional(  # a contact deleted since: no threads (404)
                    "conversations", partial(self._http.get, THREADS, params, missing_ok=True)
                )
                if found is None:
                    r.threads[contact_id] = []
                    return []
                out.extend(_rows(found))
                after = _after(found)
                if after is None or after in seen:
                    break
                seen.add(after)
        r.threads[contact_id] = out
        return out

    async def _inbox(
        self, tid: str, contact_id: str | None, known: list[str], r: _Read
    ) -> list[Message]:
        if contact_id is None or not contact_id.isdigit() or "conversations" in self._blocked:
            return []
        out: list[Message] = []
        for thread in await self._threads_of(contact_id, r):
            links = thread.get("threadAssociations")
            linked = links.get("associatedTicketId") if isinstance(links, dict) else None
            if str(linked) == tid and thread.get("id") is not None:
                out.extend(await self._thread_messages(tid, str(thread["id"]), known))
        return out

    # Tickets

    async def _read_page(self, rows: Sequence[Mapping[str, Any]]) -> _Read:
        ids = [str(r["id"]) for r in rows]
        custom = await self._custom_properties()
        extra = [p.name for p in custom] + self._sla
        contact_ids = await self._associations("contacts", ids)
        email_ids = await self._associations("emails", ids)
        note_ids = await self._associations("notes", ids)
        activity_ids: dict[str, dict[str, list[str]]] = {}
        activities: dict[str, dict[str, Mapping[str, Any]]] = {}
        for obj, properties in ACTIVITIES.items():
            linked = await self._optional(obj, partial(self._associations, obj, ids))
            wanted = [a for found in (linked or {}).values() for a in found]
            records = await self._optional(obj, partial(self._batch, obj, wanted, properties))
            activity_ids[obj] = linked or {}
            activities[obj] = records or {}
        return _Read(
            contact_ids=contact_ids,
            email_ids=email_ids,
            note_ids=note_ids,
            activity_ids=activity_ids,
            contacts=await self._batch(
                "contacts", [c[0] for c in contact_ids.values() if c], CONTACT_PROPERTIES
            ),
            emails=await self._batch(
                "emails", [e for es in email_ids.values() for e in es], EMAIL_PROPERTIES
            ),
            notes=await self._batch(
                "notes", [n for ns in note_ids.values() for n in ns], NOTE_PROPERTIES
            ),
            activities=activities,
            custom=await self._batch("tickets", ids, extra) if extra else {},
            owners=await self._owner_map(),
        )

    async def _tickets(self, rows: Sequence[Mapping[str, Any]]) -> list[Ticket]:
        rows = [r for r in rows if r.get("id") is not None]
        if not rows:
            return []
        if self._stages is None:
            await self._load_pipelines()
        r = await self._read_page(rows)
        out: list[Ticket] = []
        for row in rows:
            ticket = await self.failures.guard(
                "ticket", str(row["id"]), partial(self._ticket, row, r)
            )
            if ticket is not None:
                out.append(ticket)
        return out

    def _fields(self, tid: str, p: Mapping[str, Any], r: _Read) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        category = _str(p.get("hs_ticket_category"))
        if category:
            fields["category"] = category
        extra = _props(r.custom.get(tid) or {})
        for prop in self._custom or []:
            value = extra.get(prop.name)
            if value is None or value == "":
                continue
            if prop.options:
                value = ", ".join(prop.options.get(v, v) for v in str(value).split(";") if v)
            fields.setdefault(prop.label, value)
        for name in self._sla:
            if extra.get(name) not in (None, ""):
                fields.setdefault(name, extra[name])
        return fields

    async def _ticket(self, row: Mapping[str, Any], r: _Read) -> Ticket:
        tid = str(row["id"])
        p = _props(row)
        own_created = p.get("createdate")
        fallback = own_created or p.get("hs_lastmodifieddate")
        messages: list[Message] = []
        for email_id in r.email_ids.get(tid, []):
            if email_id in r.emails and (m := await self._email(tid, r.emails[email_id], fallback)):
                messages.append(m)
        messages.sort(key=lambda m: m.created_at)
        for note_id in r.note_ids.get(tid, []):
            if note_id in r.notes:
                note = r.notes[note_id]
                text = _html_text(_props(note).get("hs_note_body"))
                if m := await self._logged(tid, "note", note, text, fallback, r):
                    messages.append(m)
        for obj, prefix, describe in (
            ("calls", "call", _call_text),
            ("meetings", "meeting", _meeting_text),
            ("communications", "communication", _communication_text),
        ):
            records = r.activities.get(obj, {})
            for aid in r.activity_ids.get(obj, {}).get(tid, []):
                if aid in records:
                    found = records[aid]
                    m = await self._logged(tid, prefix, found, describe(_props(found)), fallback, r)
                    if m is not None:
                        messages.append(m)
        first_contact = r.contact_ids.get(tid, [None])[0] if r.contact_ids.get(tid) else None
        content = rules.body(_str(p.get("content")))
        emails = [m for m in messages if (m.external_id or "").startswith("email-")]
        known = [m.body for m in emails]
        if str(p.get("source_type") or "").upper() != "EMAIL":
            messages.extend(await self._inbox(tid, first_contact, [*known, content], r))
        created = self._date(
            tid,
            own_created,
            min((m.created_at for m in messages), default=None),
            p.get("hs_lastmodifieddate"),
            what="creation date",
        )
        messages.sort(key=lambda m: m.created_at)
        if content and not (emails and _same(content, emails[0].body)):
            # The description is the first thing the customer said (C7).
            start = min([created, *(m.created_at for m in messages)])
            messages.insert(0, Message("customer", content, start, f"ticket-{tid}"))
        requester = (
            contact_from(r.contacts[first_contact])
            if first_contact in r.contacts
            else Contact(f"ticket-{tid}", None, "Unknown")
        )
        category = _str(p.get("hs_ticket_category"))
        owner = r.owners.get(str(p.get("hubspot_owner_id")))
        return Ticket(
            external_id=tid,
            subject=rules.subject(p.get("subject")),
            status=(self._stages or {}).get(str(p.get("hs_pipeline_stage")), "open"),
            created_at=created,
            requester=requester,
            messages=messages,
            priority=_str(p.get("hs_ticket_priority")),
            channel=_str(p.get("source_type")),
            group_id=_str(p.get("hs_pipeline")),
            assignee_email=owner.email if owner else None,
            fields=self._fields(tid, p, r),
            updated_at=rules.valid_date(when(p.get("hs_lastmodifieddate"))),
            resolved_at=rules.valid_date(when(p.get("closed_date"))),
            ticket_type=self._category_labels.get(category, category) if category else None,
            source_url=await self._ticket_url(tid),
        )


def _duration(raw: object) -> str | None:
    try:
        seconds = int(float(str(raw))) // 1000
    except ValueError:
        return None
    return f"{seconds // 60}:{seconds % 60:02d}" if seconds > 0 else None


def _call_text(p: Mapping[str, Any]) -> str:
    direction = {"INBOUND": "incoming", "OUTBOUND": "outgoing"}.get(
        str(p.get("hs_call_direction") or "").upper()
    )
    status = _str(p.get("hs_call_status"))
    details = [x for x in (direction, status.lower() if status else None) if x]
    if length := _duration(p.get("hs_call_duration")):
        details.append(length)
    title = _str(p.get("hs_call_title"))
    head = (
        "Call" + (f": {title}" if title else "") + (f" ({', '.join(details)})" if details else "")
    )
    parts = [head, _html_text(p.get("hs_call_body"))]
    recording = _str(p.get("hs_call_recording_url"))
    if recording and recording.startswith("https://"):
        parts.append(f"Recording: {recording}")
    return "\n\n".join(x for x in parts if x)


def _meeting_text(p: Mapping[str, Any]) -> str:
    title = _str(p.get("hs_meeting_title"))
    start, end = when(p.get("hs_meeting_start_time")), when(p.get("hs_meeting_end_time"))
    span = " to ".join(f"{d:%Y-%m-%d %H:%M} UTC" for d in (start, end) if d)
    outcome = _str(p.get("hs_meeting_outcome"))
    details = [x for x in (span, outcome.lower() if outcome else None) if x]
    head = (
        "Meeting"
        + (f": {title}" if title else "")
        + (f" ({', '.join(details)})" if details else "")
    )
    parts = [head]
    if location := _str(p.get("hs_meeting_location")):
        parts.append(f"Location: {location}")
    parts.append(_html_text(p.get("hs_meeting_body")))
    if notes := _html_text(p.get("hs_internal_meeting_notes")):
        parts.append(f"Internal notes:\n{notes}")
    if summary := _html_text(p.get("hs_meeting_summary")):
        parts.append(f"Summary:\n{summary}")
    return "\n\n".join(x for x in parts if x)


def _communication_text(p: Mapping[str, Any]) -> str:
    raw = str(p.get("hs_communication_channel_type") or "").strip().upper()
    channel = CHANNEL_NAMES.get(raw, "Message")
    body = _html_text(p.get("hs_communication_body"))
    return f"{channel} logged in HubSpot\n\n{body}" if body else ""


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
