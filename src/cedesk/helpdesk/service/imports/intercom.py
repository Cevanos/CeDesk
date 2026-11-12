"""Importer for Intercom (read only, REST API, access-token auth).

Endpoints (all GET): /me, /admins, /teams, /contacts, /contacts/{id}, /conversations,
/conversations/{id}, /articles. Docs used:
- https://developers.intercom.com/docs/references/rest-api/api.intercom.io/conversations/retrieveconversation
- https://developers.intercom.com/docs/references/rest-api/api.intercom.io/articles/listarticles
- https://developers.intercom.com/docs/build-an-integration/learn-more/rest-apis/pagination
  (cursor pagination: `pages.next.starting_after`, passed back as `starting_after`)

Shape decisions:
- The conversation list carries only the opening `source`; the parts come from
  `GET /conversations/{id}`, one call per ticket (the first 500 parts, which is plenty).
- Articles are paged by number (`page`, `per_page`), not by cursor; their cursor is `{"page": n}`.
- Intercom has no public saved-replies API, so `replies` is always an empty page.

Attachments: `attachments: [{type, name, url, content_type, filesize}]` on the opening `source`
and on each conversation part (same retrieve-conversation doc). Entries without an https `url`
are skipped. `fetch` downloads the (signed, usually other-host) `url` through `SourceHttp.download`.

Changed since: `POST /conversations/search` is a POST and the client is read only, so
`changed_tickets` pages the GET `/conversations` list and stops at the first conversation whose
`updated_at` is before `since`. That relies on the list being newest-update first (believed from
the list-conversations doc, not confirmed); if a list stub has no `updated_at`, the full
conversation's is used.

Not verified against a live account. Unsure: the list order above; the attachment field names
beyond the doc; whether a part with attachments but no text should be kept (it is, with the
body "(attachment)"); the exact `Intercom-Version` to pin (2.14 is used);
whether `parent_id` exists on articles (we read `parent_id`, else the last of `parent_ids`, and
name the category by its id because the collection name needs another endpoint); the
`total_count` on list endpoints (used for `discover`; a missing one counts as 0).
"""

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from html.parser import HTMLParser
from typing import Any

import httpx

from cedesk import egress
from cedesk.egress import Guard
from cedesk.helpdesk.domain import imports as rules
from cedesk.helpdesk.domain.imports import (
    AccountInfo,
    Article,
    Attachment,
    Category,
    Contact,
    Counts,
    Csat,
    Group,
    Message,
    Page,
    SourceConnector,
    Ticket,
)
from cedesk.helpdesk.service.imports.http import SourceHttp, bearer, segment, when

API_VERSION = "2.14"
_BLOCKS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre"}
_CHANNELS = {"conversation": "chat", "phone_call": "phone", "phone_switch": "phone", "push": "chat"}


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


def _dict(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _after(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    token = _dict(_dict(payload.get("pages")).get("next")).get("starting_after")
    return {"after": str(token)} if token else None


class IntercomConnector:
    source = "intercom"
    status_map: Mapping[str, str] = {"open": "open", "snoozed": "pending", "closed": "resolved"}

    def __init__(self, http: SourceHttp) -> None:
        self._http = http
        self._admins: dict[str, tuple[str | None, str | None]] | None = None
        self._contacts: dict[str, Contact | None] = {}

    async def check(self) -> AccountInfo:
        me = _dict(await self._http.get("/me"))
        app = _dict(me.get("app"))
        return AccountInfo(name=str(app.get("name") or me.get("email") or "Intercom"))

    async def discover(self) -> Counts:
        async def total(path: str) -> int:
            data = _dict(await self._http.get(path, {"per_page": 1}))
            count = data.get("total_count")
            return count if isinstance(count, int) else 0

        return Counts(
            contacts=await total("/contacts"),
            tickets=await total("/conversations"),
            articles=await total("/articles"),
            replies=0,
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
            return await self._tickets_page(cursor)
        if kind == "articles":
            return await self._articles_page(cursor)
        return Page([], None)  # replies: no public API

    # Contacts

    async def _contacts_page(self, cursor: Mapping[str, Any] | None) -> Page[Contact]:
        params = {"per_page": rules.PAGE_MAX, "starting_after": (cursor or {}).get("after")}
        data = _dict(await self._http.get("/contacts", params))
        items = [c for raw in _list(data.get("data")) if (c := _contact(raw)) is not None]
        return Page(items, _after(data))

    # Tickets

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
        self, cursor: Mapping[str, Any] | None, since: datetime | None = None
    ) -> Page[Ticket]:
        """With `since`, stops (cursor None) at the first conversation last updated before it;
        the list is newest-update first, so everything after it is older too."""
        params = {"per_page": rules.PAGE_MAX, "starting_after": (cursor or {}).get("after")}
        listing = _dict(await self._http.get("/conversations", params))
        tickets: list[Ticket] = []
        for stub in _list(listing.get("conversations")):
            if not isinstance(stub, Mapping) or stub.get("id") is None:
                continue
            if since is not None and _older(stub, since):
                return Page(tickets, None)
            full = await self._http.get(f"/conversations/{segment(stub['id'])}", missing_ok=True)
            if not isinstance(full, Mapping):
                continue
            if since is not None and _older(full, since):
                return Page(tickets, None)
            tickets.append(await self._ticket(full))
        return Page(tickets, _after(listing))

    async def changed_tickets(
        self, since: datetime, cursor: Mapping[str, Any] | None
    ) -> Page[Ticket]:
        return await self._tickets_page(cursor, since)

    async def fetch(self, attachment: Attachment, limit: int) -> bytes | None:
        return await self._http.download(attachment.url, limit)

    async def _ticket(self, conv: Mapping[str, Any]) -> Ticket:
        admins = await self._admin_directory()
        src = _dict(conv.get("source"))
        created = when(conv.get("created_at")) or when(0)
        assert created is not None
        messages: list[Message] = []
        first = await self._message(src, "comment", created, admins, str(src.get("id") or ""))
        if first:
            messages.append(first)
        parts = _list(_dict(conv.get("conversation_parts")).get("conversation_parts"))
        for part in parts:
            if not isinstance(part, Mapping):
                continue
            at = when(part.get("created_at")) or created
            msg = await self._message(part, str(part.get("part_type")), at, admins, part.get("id"))
            if msg:
                messages.append(msg)
        messages.sort(key=lambda m: m.created_at)

        requester = await self._requester(conv, src)
        title = plain_text(src.get("subject")) or str(conv.get("title") or "")
        if not title and messages:
            title = messages[0].body.split("\n", 1)[0]
        stats = _dict(conv.get("statistics"))
        raw_priority = str(conv.get("priority") or "")
        assignee = conv.get("admin_assignee_id")
        team = conv.get("team_assignee_id")
        return Ticket(
            external_id=str(conv["id"]),
            subject=title,
            status=str(conv.get("state") or "open"),
            created_at=created,
            requester=requester,
            messages=messages,
            priority=None if raw_priority in ("", "none") else raw_priority,
            channel=_CHANNELS.get(str(src.get("type")), str(src.get("type") or "")) or None,
            group_id=str(team) if team not in (None, 0, "0", "") else None,
            assignee_email=(admins.get(str(assignee), (None, None))[1] if assignee else None),
            tags=[
                str(t["name"])
                for t in _list(_dict(conv.get("tags")).get("tags"))
                if isinstance(t, Mapping) and t.get("name")
            ],
            fields={k: v for k, v in _dict(conv.get("custom_attributes")).items() if v is not None},
            updated_at=when(conv.get("updated_at")),
            resolved_at=when(stats.get("last_close_at")) if conv.get("state") == "closed" else None,
            csat=_csat(conv.get("conversation_rating")),
        )

    async def _message(
        self,
        raw: Mapping[str, Any],
        part_type: str,
        at: Any,
        admins: Mapping[str, tuple[str | None, str | None]],
        external_id: object,
    ) -> Message | None:
        text = plain_text(raw.get("body"))
        files = _attachments(raw.get("attachments"))
        if not text and not files:  # assignment, open, close and similar events carry no body
            return None
        text = text or "(attachment)"
        author = _dict(raw.get("author"))
        kind_of = str(author.get("type") or "")
        name: str | None = author.get("name")
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
        if part_type == "note":
            kind = "note"
        return Message(
            kind=kind,
            body=text,
            created_at=at,
            external_id=str(external_id) if external_id else None,
            author_name=name or None,
            author_email=email,
            attachments=files,
        )

    async def _requester(self, conv: Mapping[str, Any], src: Mapping[str, Any]) -> Contact:
        listed = _list(_dict(conv.get("contacts")).get("contacts"))
        ref = _dict(listed[0]) if listed else {}
        author = _dict(src.get("author"))
        cid = str(ref.get("id") or author.get("id") or "")
        found = await self._contact(cid) if cid and ref.get("id") else None
        if found:
            return found
        email = author.get("email") or None
        name = author.get("name") or email or "Unknown"
        return Contact(cid or str(conv["id"]), email, str(name))

    # Articles

    async def _articles_page(self, cursor: Mapping[str, Any] | None) -> Page[Article]:
        number = int((cursor or {}).get("page") or 1)
        data = _dict(
            await self._http.get("/articles", {"page": number, "per_page": rules.PAGE_MAX})
        )
        items = [a for raw in _list(data.get("data")) if (a := _article(raw)) is not None]
        pages = _dict(data.get("pages"))
        total = pages.get("total_pages")
        more = number < total if isinstance(total, int) else bool(pages.get("next"))
        return Page(items, {"page": number + 1} if more else None)


def _older(conv: Mapping[str, Any], since: datetime) -> bool:
    updated = when(conv.get("updated_at"))
    return updated is not None and updated < since


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
    return Contact(str(data["id"]), email, str(name))


def _csat(raw: object) -> Csat | None:
    data = _dict(raw)
    score = data.get("rating")
    if not isinstance(score, int) or isinstance(score, bool):
        return None
    return Csat(score, data.get("remark") or None, when(data.get("created_at")))


def _article(raw: object) -> Article | None:
    data = _dict(raw)
    if data.get("id") is None:
        return None
    parent = data.get("parent_id")
    if parent is None:
        parents = _list(data.get("parent_ids"))
        parent = parents[-1] if parents else None
    category = Category(str(parent), f"Collection {parent}") if parent is not None else None
    return Article(
        external_id=str(data["id"]),
        title=str(data.get("title") or "(untitled)"),
        body=str(data.get("body") or ""),
        category=category,
        published=data.get("state") == "published",
    )


def make(
    settings: Mapping[str, str],
    credentials: Mapping[str, str],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    guard: Guard | None = None,
) -> SourceConnector:
    """Raises `egress.BadAddress` for an unknown region."""
    host = egress.fixed_host("intercom", (settings.get("region") or "us").strip().lower())
    http = SourceHttp(
        host,
        headers={**bearer(credentials["token"]), "Intercom-Version": API_VERSION},
        guard=guard,
        transport=transport,
        label="Intercom",
    )
    return IntercomConnector(http)
