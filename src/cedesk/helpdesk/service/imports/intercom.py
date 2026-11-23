"""Importer for Intercom (read only, REST API, access-token auth).

Endpoints: GET /me, /admins, /teams, /contacts, /contacts/{id}, /conversations,
/conversations/{id}, /tickets/{id}, /articles, /help_center/collections, /macros; and the
read-only searches POST /conversations/search and POST /tickets/search (`SourceHttp.read`).
Docs used (developers.intercom.com/docs/references/rest-api/api.intercom.io/...):
conversations/retrieveconversation, conversations/searchconversations, tickets/searchtickets,
tickets/retrieveticket, macros/listmacros, help-center/listallcollections, contacts/listcontacts,
articles/listarticles; and build-an-integration/learn-more/rest-apis/pagination (cursor
pagination: `pages.next.starting_after`, passed back as `starting_after`).

API versions. Everything is read with `Intercom-Version: 2.14` (`API_VERSION`), except two
calls that need a newer one and are sent by a second client pinned to 2.18
(`NEWER_API_VERSION`, released 2026-11-09):
- `GET /conversations/{id}?page=&per_page=`: without `page` Intercom returns only the 500 most
  recent parts. With `page` (2.18 changelog) the parts come oldest first, `per_page` up to 500,
  and `conversation_parts.pages` says whether there is more; we follow it to the end. 2.18 also
  returns `part_type: "unknown"` for a type it doesn't name (older versions say `comment`, which
  would pass an unknown admin part off as a public reply). A safety limit (`MAX_PART_PAGES`)
  ends the read: the ticket is kept, counted `history_capped` and noted `incomplete`. If the
  answer has no `pages` (the version was not honoured) and 500 parts, it is noted the same way.
- `GET /macros` (documented from 2.17): macros become saved replies, shortcut = slug of the name,
  body = `body_text` (Intercom's own `{{placeholder}}` form kept as it is).

Shape decisions:
- Conversations: the list carries only stubs; each one is read with the paged retrieve above.
  The `tickets` kind then goes on with Intercom Ticket objects (`POST /tickets/search`,
  `updated_at > 0`, then `GET /tickets/{id}` for each): its cursor is `{"after": ...}` for
  conversations and `{"phase": "tickets", "after": ...}` for ticket objects. Ticket objects keep
  their own id as the external id. UNVERIFIED: whether a customer ticket's id is also its
  conversation's (then the second read updates the first instead of copying it). Ticket parts
  have no paging (the doc says 500 at most): a ticket with 500 is noted `incomplete`.
  Back-office and tracker tickets are internal, so all their text is a note.
- Priority: 2.14 says `priority` / `not_priority` (high / medium); 2.16+ say none, low, medium,
  high, urgent. The retrieve call is 2.18, but both forms are mapped (`_priority`).
- Private text: `note` and `note_and_reopen` are notes; so is any other part with text by an
  admin or a team that is not a `comment` or a `close` (an assignment or snooze with a comment,
  away-mode assignment, `unknown`): unsure text stays internal. A customer's part is the
  customer's; a bot's `comment` is a public agent message.
- Articles are paged by number (`page`, `per_page`); their cursor is `{"page": n}`. The category
  is the article's collection (`parent_id`), named from `GET /help_center/collections`.
- `sla_applied.sla_status` `missed` / `hit` -> `sla_breached` True / False (Intercom gives no
  due time). `linked_objects` -> related links. Further contacts on a conversation (group
  conversations) -> `cc`, by their email.

Attachments: `attachments: [{type, name, url, content_type, filesize}]` on the opening `source`
and on each part. Entries without an https `url` are skipped. `fetch` downloads the (signed,
other-host) `url` through `SourceHttp.download`. Intercom's signed links expire (UNVERIFIED:
about 30 minutes, from the community); when one answers nothing, the conversation or ticket is
read once more and the same file (same part, same position) is tried from its fresh link.

Changed since: `POST /conversations/search` for `updated_at > since - 1`, every cursor page to
the end (no reliance on the list's order), then `POST /tickets/search` the same way.

Not verified against a live account: the 2.18 paging; ticket/conversation id overlap; the
collections list's paging (cursor and page number both followed); macro `per_page` limit.
"""

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from functools import partial
from html.parser import HTMLParser
from typing import Any

import httpx

from cedesk import egress
from cedesk.egress import Guard
from cedesk.helpdesk.domain import imports as rules
from cedesk.helpdesk.domain import markdown
from cedesk.helpdesk.domain.imports import (
    AccountInfo,
    Article,
    Attachment,
    Category,
    Contact,
    Counts,
    Csat,
    Group,
    Link,
    Message,
    Page,
    RecordGone,
    Reply,
    SourceConnector,
    SourceError,
    Ticket,
)
from cedesk.helpdesk.service.imports.http import SourceHttp, bearer, segment, when

API_VERSION = "2.14"
NEWER_API_VERSION = "2.18"  # paged conversation parts, `unknown` part type, GET /macros
PARTS_PER_PAGE = 500  # the most the 2.18 retrieve allows
MAX_PART_PAGES = 200  # 100,000 parts: past this a conversation is noted incomplete
TICKET_PARTS_MAX = 500  # a ticket object carries at most this many parts, without paging
MAX_LIST_PAGES = 50  # collections and macros: small lists read whole
_BLOCKS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre"}
_CHANNELS = {"conversation": "chat", "phone_call": "phone", "phone_switch": "phone", "push": "chat"}
_NOTE_PARTS = {"note", "note_and_reopen"}
_PUBLIC_STAFF_PARTS = {"comment", "close"}  # an admin's text in these reaches the customer
_STAFF = {"admin", "team"}
_PRIORITY = {"priority": "high", "not_priority": "medium"}  # 2.14; 2.16+ use CeDesk's words
_SLA = {"missed": True, "hit": False}
_TICKET_STATES = {
    "submitted": "open",
    "in_progress": "open",
    "waiting_on_customer": "pending",
    "resolved": "resolved",
}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in _BLOCKS:
            self.out.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCKS:
            self.out.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.out.append(data)


def plain_text(html: str | None) -> str:
    """HTML to text: tags dropped, entities decoded, <br>/<p>/<li> kept as line breaks."""
    if not html:
        return ""
    parser = _Text()
    parser.feed(html)
    parser.close()
    lines = [re.sub(r"[ \t\xa0]+", " ", line).strip() for line in "".join(parser.out).split("\n")]
    return "\n".join(line for line in lines if line)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]


def _dict(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _token(payload: Mapping[str, Any]) -> str | None:
    token = _dict(_dict(payload.get("pages")).get("next")).get("starting_after")
    return str(token) if token else None


def _after(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    token = _token(payload)
    return {"after": token} if token else None


def _more(pages: Mapping[str, Any], page: int, got: int, per_page: int) -> bool:
    """Whether a page-numbered list goes on after `page`: its `next` when it says, else
    `total_pages`, else a full page (a page past the end is empty)."""
    if "next" in pages:
        return bool(pages.get("next"))
    total = pages.get("total_pages")
    if isinstance(total, int) and not isinstance(total, bool):
        return page < total
    return got >= per_page


def _since_query(since: datetime | None) -> dict[str, Any]:
    """`updated_at` after `since` (to the second; at or after it, so one second earlier)."""
    value = int(since.timestamp()) - 1 if since is not None else 0
    return {"field": "updated_at", "operator": ">", "value": max(0, value)}


def _id(value: object) -> str | None:
    return None if value in (None, 0, "0", "") else str(value)


type _Key = tuple[str, str, str, int]  # (record kind, record id, part id, position)


class IntercomConnector:
    source = "intercom"
    status_map: Mapping[str, str] = {
        "open": "open",
        "snoozed": "pending",
        "closed": "resolved",
        **_TICKET_STATES,
    }

    def __init__(self, http: SourceHttp, newer: SourceHttp | None = None) -> None:
        self._http = http
        self._newer = newer or http  # NEWER_API_VERSION: paged parts and macros
        self._admins: dict[str, tuple[str | None, str | None]] | None = None
        self._contacts: dict[str, Contact | None] = {}
        self._collections: dict[str, str] | None = None
        self._seen_contacts: set[str] = set()
        self._origin: dict[str, _Key] = {}  # attachment url -> where it came from
        self._fresh: dict[str, str] = {}  # expired attachment url -> its new one
        self._reread: set[tuple[str, str]] = set()
        self._warnings: list[str] = []
        self.failures = rules.RecordFailures()  # records deleted or cut mid-import (P0-2)

    def warnings(self) -> list[str]:
        return list(self._warnings)

    async def check(self) -> AccountInfo:
        me = _dict(await self._http.get("/me"))
        app = _dict(me.get("app"))
        return AccountInfo(name=str(app.get("name") or me.get("email") or "Intercom"))

    async def discover(self) -> Counts:
        async def total(path: str) -> int:
            data = _dict(await self._http.get(path, {"per_page": 1}))
            return _count(data.get("total_count"))

        async def ticket_objects() -> int:
            body = {"query": _since_query(None), "pagination": {"per_page": 1}}
            data = _dict(await self._http.read("/tickets/search", body))
            return _count(data.get("total_count"))

        tickets = await self._optional("tickets", None, ticket_objects)
        macros = await self._optional("macros", None, self._macros)
        return Counts(
            contacts=await total("/contacts"),
            tickets=await total("/conversations") + (tickets or 0),
            articles=await total("/articles"),
            replies=len(macros or []),
            groups=len(await self.groups()),
        )

    async def groups(self) -> Sequence[Group]:
        data = _dict(await self._http.get("/teams"))
        return [
            Group(str(t["id"]), str(t.get("name") or t["id"]))
            for t in _list(data.get("teams"))
            if isinstance(t, Mapping) and t.get("id") is not None
        ]

    async def page(self, kind: str, cursor: Mapping[str, Any] | None) -> Page[Any]:
        if kind == "contacts":
            return await self._contacts_page(cursor)
        if kind == "tickets":
            return await self._tickets_page(cursor, None)
        if kind == "articles":
            return await self._articles_page(cursor)
        if kind == "replies":
            return await self._replies_page(cursor)
        return Page([], None)

    async def _optional[T](
        self, what: str, kind: str | None, call: Callable[[], Awaitable[T]]
    ) -> T | None:
        """`call()` for a part of Intercom the account may not have or may not be allowed to
        read (Ticket objects, macros). A refusal (a permanent error, not a 429 or an outage)
        leaves that part out: a warning for the dry run, and in a run a note against the
        kind, so it is never silent."""
        try:
            return await call()
        except RecordGone:
            raise
        except SourceError as exc:
            if not exc.permanent:
                raise
            why = f"Intercom refused to list its {what} ({exc}); none of them are imported."
            if why not in self._warnings:
                self._warnings.append(why)
            if kind is not None:
                self.failures.incomplete(kind, f"intercom-{what}", why)
        return None

    # Contacts

    async def _contacts_page(self, cursor: Mapping[str, Any] | None) -> Page[Contact]:
        params = {"per_page": rules.PAGE_MAX, "starting_after": (cursor or {}).get("after")}
        data = _dict(await self._http.get("/contacts", params))
        items: list[Contact] = []
        for raw in _list(data.get("data")):
            found = _contact(raw)
            if found is None or found.external_id in self._seen_contacts:
                continue  # a contact listed twice (the list moved under the cursor)
            self._seen_contacts.add(found.external_id)
            items.append(found)
        return Page(items, _after(data))

    # Tickets: conversations, then ticket objects

    async def _admin_directory(self) -> dict[str, tuple[str | None, str | None]]:
        if self._admins is None:
            data = _dict(await self._http.get("/admins"))
            self._admins = {
                str(a["id"]): (a.get("name"), a.get("email"))
                for a in _list(data.get("admins"))
                if isinstance(a, Mapping) and a.get("id") is not None
            }
        return self._admins

    async def _contact(self, contact_id: str) -> Contact | None:
        if contact_id not in self._contacts:
            raw = await self._http.get(f"/contacts/{segment(contact_id)}", missing_ok=True)
            self._contacts[contact_id] = _contact(raw)
        return self._contacts[contact_id]

    async def _tickets_page(
        self, cursor: Mapping[str, Any] | None, since: datetime | None
    ) -> Page[Ticket]:
        self._origin.clear()  # files of the last page were fetched before this one is read
        if (cursor or {}).get("phase") == "tickets":
            return await self._ticket_objects_page(cursor, since)
        after = (cursor or {}).get("after")
        if since is None:
            params = {"per_page": rules.PAGE_MAX, "starting_after": after}
            listing = _dict(await self._http.get("/conversations", params))
        else:
            pagination: dict[str, Any] = {"per_page": rules.PAGE_MAX}
            if after:
                pagination["starting_after"] = after
            body = {"query": _since_query(since), "pagination": pagination}
            listing = _dict(await self._http.read("/conversations/search", body))
        tickets: list[Ticket] = []
        seen: set[str] = set()
        for stub in _list(listing.get("conversations")):
            if not isinstance(stub, Mapping) or stub.get("id") is None:
                continue
            cid = str(stub["id"])
            if cid in seen:
                continue
            seen.add(cid)
            ticket = await self.failures.guard("ticket", cid, partial(self._conversation, cid))
            if ticket is not None:  # None: deleted since it was listed (noted), or isolated
                tickets.append(ticket)
        return Page(tickets, _after(listing) or {"phase": "tickets"})

    async def changed_tickets(
        self, since: datetime, cursor: Mapping[str, Any] | None
    ) -> Page[Ticket]:
        return await self._tickets_page(cursor, since)

    async def _read_conversation(
        self, conversation_id: str
    ) -> tuple[Mapping[str, Any], list[Mapping[str, Any]], bool]:
        """The conversation, all its parts oldest first, and whether they were cut short.
        A 404 is `RecordGone` (deleted since it was listed)."""
        first: Mapping[str, Any] | None = None
        parts: list[Mapping[str, Any]] = []
        ids: set[str] = set()
        page = 1
        while True:
            data = await self._newer.get(
                f"/conversations/{segment(conversation_id)}",
                {"page": page, "per_page": PARTS_PER_PAGE},
                record=True,
            )
            if not isinstance(data, Mapping) or data.get("id") is None:
                raise RecordGone("Intercom sent no conversation for this record.")
            first = first or data
            block = _dict(data.get("conversation_parts"))
            got = [p for p in _list(block.get("conversation_parts")) if isinstance(p, Mapping)]
            for part in got:
                key = str(part.get("id") or "")
                if key and key in ids:
                    continue  # a part seen on the page before
                ids.add(key)
                parts.append(part)
            pages = block.get("pages")
            if not isinstance(pages, Mapping):  # the version ignored `page`: 500 newest at most
                return first, parts, len(got) >= PARTS_PER_PAGE
            if not got or not _more(pages, page, len(got), PARTS_PER_PAGE):
                return first, parts, False
            if page >= MAX_PART_PAGES:
                return first, parts, True
            page += 1

    async def _conversation(self, conversation_id: str) -> Ticket:
        conv, parts, capped = await self._read_conversation(conversation_id)
        ticket = await self._ticket(conv, parts)
        if capped:
            rules.loss("history_capped")
            self.failures.incomplete(
                "ticket",
                ticket.external_id,
                "Intercom gave this conversation's parts only up to a limit; the rest were "
                "not read.",
            )
        return ticket

    async def _ticket(self, conv: Mapping[str, Any], parts: Sequence[Mapping[str, Any]]) -> Ticket:
        admins = await self._admin_directory()
        cid = str(conv["id"])
        src = _dict(conv.get("source"))
        created = rules.dated(
            self.failures, "ticket", cid, when(conv.get("created_at")), when(conv.get("updated_at"))
        )
        messages: list[Message] = []
        first = self._message(src, "comment", created, admins, str(src.get("id") or ""))
        if first:
            self._remember("conversation", cid, "source", first)
            messages.append(first)
        for part in parts:
            at = rules.valid_date(when(part.get("created_at"))) or created
            pid = str(part.get("id") or "")
            msg = self._message(part, str(part.get("part_type") or ""), at, admins, pid)
            if msg:
                self._remember("conversation", cid, pid, msg)
                messages.append(msg)
        messages.sort(key=lambda m: m.created_at)

        listed = [_dict(c) for c in _list(_dict(conv.get("contacts")).get("contacts"))]
        requester = await self._requester(conv, src, listed[0] if listed else {})
        title = plain_text(src.get("subject")) or str(conv.get("title") or "")
        if not title and messages:
            title = messages[0].body.split("\n", 1)[0]
        stats = _dict(conv.get("statistics"))
        assignee = _id(conv.get("admin_assignee_id"))
        sla = _dict(conv.get("sla_applied"))
        return Ticket(
            external_id=cid,
            subject=title,
            status=str(conv.get("state") or "open"),
            created_at=created,
            requester=requester,
            messages=messages,
            priority=_priority(conv.get("priority")),
            channel=_CHANNELS.get(str(src.get("type")), str(src.get("type") or "")) or None,
            group_id=_id(conv.get("team_assignee_id")),
            assignee_email=admins.get(assignee, (None, None))[1] if assignee else None,
            tags=[
                str(t["name"])
                for t in _list(_dict(conv.get("tags")).get("tags"))
                if isinstance(t, Mapping) and t.get("name")
            ],
            fields={k: v for k, v in _dict(conv.get("custom_attributes")).items() if v is not None},
            updated_at=rules.valid_date(when(conv.get("updated_at"))),
            resolved_at=(
                rules.valid_date(when(stats.get("last_close_at")))
                if conv.get("state") == "closed"
                else None
            ),
            csat=_csat(conv.get("conversation_rating")),
            cc=await self._cc(listed[1:], requester),
            links=_links(conv.get("linked_objects")),
            sla_breached=_SLA.get(str(sla.get("sla_status") or "")),
        )

    def _message(
        self,
        raw: Mapping[str, Any],
        part_type: str,
        at: datetime,
        admins: Mapping[str, tuple[str | None, str | None]],
        external_id: str,
        *,
        internal: bool = False,
    ) -> Message | None:
        text = plain_text(raw.get("body"))
        files = _attachments(raw.get("attachments"))
        if not text and not files:  # assignment, open, close and similar events carry no body
            return None
        text = text or "(attachment)"
        author = _dict(raw.get("author"))
        kind_of = str(author.get("type") or "")
        name: str | None = author.get("name") or None
        email: str | None = author.get("email") or None
        if kind_of == "admin":
            known = admins.get(str(author.get("id")), (None, None))
            name, email = name or known[0], email or known[1]
            kind = "agent"
        elif kind_of in ("bot", "team"):
            kind, email = "agent", None
            name = name or ("Fin" if author.get("from_ai_agent") else "Bot")
        else:
            kind = "customer"
        private = part_type in _NOTE_PARTS or (
            kind_of in _STAFF and part_type not in _PUBLIC_STAFF_PARTS
        )
        if internal or private:
            kind = "note"
        return Message(
            kind=kind,
            body=text,
            created_at=at,
            external_id=external_id or None,
            author_name=name,
            author_email=email,
            attachments=files,
        )

    async def _requester(
        self, conv: Mapping[str, Any], src: Mapping[str, Any], ref: Mapping[str, Any]
    ) -> Contact:
        author = _dict(src.get("author"))
        cid = str(ref.get("id") or author.get("id") or "")
        found = await self._contact(cid) if cid and ref.get("id") else None
        if found:
            return found
        email = author.get("email") or None
        name = author.get("name") or email or "Unknown"
        return Contact(cid or str(conv["id"]), email, str(name))

    async def _cc(self, refs: Sequence[Mapping[str, Any]], requester: Contact) -> list[str]:
        """The other contacts on a group conversation, by email (none without one)."""
        out: list[str] = []
        for ref in refs:
            if ref.get("id") is None:
                continue
            found = await self._contact(str(ref["id"]))
            email = found.email if found else None
            if email and email != requester.email and email not in out:
                out.append(email)
        return out

    # Ticket objects

    async def _ticket_objects_page(
        self, cursor: Mapping[str, Any] | None, since: datetime | None
    ) -> Page[Ticket]:
        pagination: dict[str, Any] = {"per_page": rules.PAGE_MAX}
        if (cursor or {}).get("after"):
            pagination["starting_after"] = (cursor or {})["after"]
        body = {"query": _since_query(since), "pagination": pagination}

        async def search() -> Mapping[str, Any]:
            return _dict(await self._http.read("/tickets/search", body))

        listing = await self._optional("tickets", "ticket", search)
        if listing is None:
            return Page([], None)
        items: list[Ticket] = []
        for stub in _list(listing.get("tickets")):
            if not isinstance(stub, Mapping) or stub.get("id") is None:
                continue
            tid = str(stub["id"])
            ticket = await self.failures.guard("ticket", tid, partial(self._ticket_object, tid))
            if ticket is not None:
                items.append(ticket)
        token = _token(listing)
        return Page(items, {"phase": "tickets", "after": token} if token else None)

    async def _read_ticket(self, ticket_id: str) -> Mapping[str, Any]:
        data = await self._http.get(f"/tickets/{segment(ticket_id)}", record=True)
        if not isinstance(data, Mapping) or data.get("id") is None:
            raise RecordGone("Intercom sent no ticket for this record.")
        return data

    async def _ticket_object(self, ticket_id: str) -> Ticket:
        raw = await self._read_ticket(ticket_id)
        admins = await self._admin_directory()
        tid = str(raw["id"])
        attrs = _dict(raw.get("ticket_attributes"))
        kind_of = _dict(raw.get("ticket_type"))
        category = str(raw.get("category") or kind_of.get("category") or "")
        internal = category in ("Back-office", "Tracker")
        created = rules.dated(
            self.failures, "ticket", tid, when(raw.get("created_at")), when(raw.get("updated_at"))
        )
        messages: list[Message] = []
        described = self._message(
            {"body": attrs.get("_default_description_"), "author": {"type": "user"}},
            "comment",
            created,
            admins,
            f"{tid}-description",
            internal=internal,
        )
        if described:
            messages.append(described)
        parts = _list(_dict(raw.get("ticket_parts")).get("ticket_parts"))
        resolved_at: datetime | None = None
        for part in parts:
            if not isinstance(part, Mapping):
                continue
            at = rules.valid_date(when(part.get("created_at"))) or created
            if part.get("ticket_state") == "resolved":
                resolved_at = at
            pid = str(part.get("id") or "")
            msg = self._message(
                part, str(part.get("part_type") or ""), at, admins, pid, internal=internal
            )
            if msg:
                self._remember("ticket", tid, pid, msg)
                messages.append(msg)
        messages.sort(key=lambda m: m.created_at)
        if len(parts) >= TICKET_PARTS_MAX:
            rules.loss("history_capped")
            self.failures.incomplete(
                "ticket",
                tid,
                f"Intercom gives at most {TICKET_PARTS_MAX} parts of a ticket; older ones may "
                "be missing.",
            )

        state = raw.get("ticket_state")
        word = str(_dict(state).get("category") or (state if isinstance(state, str) else ""))
        listed = [_dict(c) for c in _list(_dict(raw.get("contacts")).get("contacts"))]
        requester = await self._requester(raw, {}, listed[0] if listed else {})
        assignee = _id(raw.get("admin_assignee_id"))
        title = plain_text(str(attrs.get("_default_title_") or ""))
        if not title:
            title = str(kind_of.get("name") or f"Ticket {raw.get('ticket_id') or tid}")
        return Ticket(
            external_id=tid,
            subject=title,
            status=word or ("open" if raw.get("open", True) else "resolved"),
            created_at=created,
            requester=requester,
            messages=messages,
            channel=str(raw.get("channel") or "") or None,
            group_id=_id(raw.get("team_assignee_id")),
            assignee_email=admins.get(assignee, (None, None))[1] if assignee else None,
            fields={
                k: v for k, v in attrs.items() if not str(k).startswith("_default_") and _scalar(v)
            },
            updated_at=rules.valid_date(when(raw.get("updated_at"))),
            resolved_at=resolved_at if word == "resolved" else None,
            ticket_type=str(kind_of.get("name") or "") or None,
            cc=await self._cc(listed[1:], requester),
            links=_links(raw.get("linked_objects")),
        )

    # Files

    def _remember(self, kind: str, record: str, part: str, message: Message) -> None:
        for i, att in enumerate(message.attachments):
            self._origin[att.url] = (kind, record, part, i)

    async def fetch(self, attachment: Attachment, limit: int) -> bytes | None:
        url = self._fresh.get(attachment.url, attachment.url)
        data = await self._http.download(url, limit)
        if data is not None or (attachment.size is not None and attachment.size > limit):
            return data
        fresh = await self._fresh_url(attachment.url)
        if fresh is None or fresh == url:
            return None
        return await self._http.download(fresh, limit)

    async def _fresh_url(self, url: str) -> str | None:
        """The link Intercom gives now for the file that `url` was (its signed links expire):
        the conversation or ticket is read once more, at most once per record."""
        origin = self._origin.get(url)
        if origin is None:
            return None
        kind, record, _, _ = origin
        if (kind, record) not in self._reread:
            self._reread.add((kind, record))
            try:
                if kind == "conversation":
                    conv, parts, _ = await self._read_conversation(record)
                    raws: list[tuple[str, Mapping[str, Any]]] = [
                        ("source", _dict(conv.get("source")))
                    ]
                    raws += [(str(p.get("id") or ""), p) for p in parts]
                else:
                    ticket = await self._read_ticket(record)
                    raws = [
                        (str(_dict(p).get("id") or ""), _dict(p))
                        for p in _list(_dict(ticket.get("ticket_parts")).get("ticket_parts"))
                    ]
            except RecordGone:
                return None
            now = {
                (kind, record, pid, i): att.url
                for pid, raw in raws
                for i, att in enumerate(_attachments(raw.get("attachments")))
            }
            for old, key in list(self._origin.items()):
                if key in now:
                    self._fresh[old] = now[key]
        return self._fresh.get(url)

    # Articles

    async def _collection_names(self) -> dict[str, str]:
        """Help center collection id -> name. Names are only labels: if the list can't be
        read, articles keep a category named by its id."""
        if self._collections is not None:
            return self._collections
        names: dict[str, str] = {}
        page, after = 1, None
        try:
            for _ in range(MAX_LIST_PAGES):
                params = {"page": page, "per_page": rules.PAGE_MAX, "starting_after": after}
                data = _dict(
                    await self._http.get("/help_center/collections", params, missing_ok=True)
                )
                rows = _list(data.get("data"))
                for row in rows:
                    if isinstance(row, Mapping) and row.get("id") is not None and row.get("name"):
                        names[str(row["id"])] = str(row["name"])
                after = _token(data)
                pages = _dict(data.get("pages"))
                if not rows or not (after or _more(pages, page, len(rows), rules.PAGE_MAX)):
                    break
                page += 1
        except SourceError as exc:
            if not exc.permanent:
                raise
        self._collections = names
        return names

    async def _articles_page(self, cursor: Mapping[str, Any] | None) -> Page[Article]:
        number = int((cursor or {}).get("page") or 1)
        data = _dict(
            await self._http.get("/articles", {"page": number, "per_page": rules.PAGE_MAX})
        )
        names = await self._collection_names()
        items = [a for raw in _list(data.get("data")) if (a := _article(raw, names)) is not None]
        pages = _dict(data.get("pages"))
        total = pages.get("total_pages")
        more = number < total if isinstance(total, int) else bool(pages.get("next"))
        return Page(items, {"page": number + 1} if more else None)

    # Saved replies (macros)

    async def _macros(self) -> list[Reply]:
        out: list[Reply] = []
        after: str | None = None
        for _ in range(MAX_LIST_PAGES):
            data = _dict(
                await self._newer.get(
                    "/macros", {"per_page": rules.PAGE_MAX, "starting_after": after}
                )
            )
            for raw in _list(data.get("data")):
                reply = _macro(raw)
                if reply is not None:
                    out.append(reply)
            after = _token(data)
            if not after:
                break
        return out

    async def _replies_page(self, cursor: Mapping[str, Any] | None) -> Page[Reply]:
        """Macros are few: read whole, then cut into pages by offset."""
        items = await self._optional("macros", "reply", self._macros) or []
        start = int((cursor or {}).get("offset") or 0)
        end = start + rules.PAGE_MAX
        return Page(items[start:end], {"offset": end} if end < len(items) else None)


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _scalar(value: object) -> bool:
    return value is not None and isinstance(value, (str, int, float, bool))


def _priority(raw: object) -> str | None:
    """2.14's `priority` / `not_priority`, or 2.16+'s none, low, medium, high, urgent."""
    word = str(raw or "").strip().lower()
    if word in ("", "none"):
        return None
    return _PRIORITY.get(word, word)


def _links(raw: object) -> list[Link]:
    out: list[Link] = []
    for item in _list(_dict(raw).get("data")):
        data = _dict(item)
        if data.get("id") is not None:
            out.append(Link("related", str(data["id"])))
    return out


def _size(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _attachments(raw: object) -> list[Attachment]:
    out: list[Attachment] = []
    for item in _list(raw):
        data = _dict(item)
        url = str(data.get("url") or "")
        if not url.lower().startswith("https://"):
            continue
        name = str(data.get("name") or "") or url.rsplit("/", 1)[-1].split("?", 1)[0] or "file"
        mime = data.get("content_type")
        out.append(Attachment(name, url, _size(data.get("filesize")), str(mime) if mime else None))
    return out


def _contact(raw: object) -> Contact | None:
    data = _dict(raw)
    if data.get("id") is None:
        return None
    email = data.get("email") or None
    name = data.get("name") or email or f"Contact {data['id']}"
    phone = data.get("phone")
    return Contact(
        str(data["id"]),
        email,
        str(name),
        phone=str(phone) if phone else None,
        attributes={
            str(k): v for k, v in _dict(data.get("custom_attributes")).items() if _scalar(v)
        },
    )


def _csat(raw: object) -> Csat | None:
    data = _dict(raw)
    score = data.get("rating")
    if not isinstance(score, int) or isinstance(score, bool):
        return None
    return Csat(score, data.get("remark") or None, rules.valid_date(when(data.get("created_at"))))


def _article(raw: object, names: Mapping[str, str]) -> Article | None:
    data = _dict(raw)
    if data.get("id") is None:
        return None
    parent = data.get("parent_id")
    if parent is None:
        parents = _list(data.get("parent_ids"))
        parent = parents[-1] if parents else None
    category = None
    if parent is not None:
        category = Category(str(parent), names.get(str(parent)) or f"Collection {parent}")
    return Article(
        external_id=str(data["id"]),
        title=str(data.get("title") or "(untitled)"),
        body=markdown.from_html(str(data.get("body") or "")),
        category=category,
        published=data.get("state") == "published",
        updated_at=rules.valid_date(when(data.get("updated_at"))),
    )


def _macro(raw: object) -> Reply | None:
    data = _dict(raw)
    if data.get("id") is None:
        return None
    text = str(data.get("body_text") or "").strip() or plain_text(data.get("body"))
    if not text:
        return None
    name = str(data.get("name") or "")
    return Reply(str(data["id"]), slug(name) or f"macro-{data['id']}", rules.body(text))


def make(
    settings: Mapping[str, str],
    credentials: Mapping[str, str],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    guard: Guard | None = None,
) -> SourceConnector:
    """Raises `egress.BadAddress` for an unknown region."""
    host = egress.fixed_host("intercom", (settings.get("region") or "us").strip().lower())

    def client(version: str) -> SourceHttp:
        return SourceHttp(
            host,
            headers={**bearer(credentials["token"]), "Intercom-Version": version},
            guard=guard,
            transport=transport,
            label="Intercom",
        )

    return IntercomConnector(client(API_VERSION), client(NEWER_API_VERSION))
