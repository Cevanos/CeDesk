"""The Freshdesk importer (md section 8): tickets with conversations, contacts, solution
articles and canned responses, read through the Freshdesk API v2 (read only, `GET` only).

Account: the `domain` setting (`acme` -> `acme.freshdesk.com`, built by `egress.account_host`)
and the `api_key` credential (HTTP Basic: the key as user name, `X` as password).

Sources used (developers.freshdesk.com/api):
- tickets list and its `include`, `updated_since`, `order_by`, pagination (page size at most
  100, at most 300 pages = 30,000 tickets per query): /api/#tickets
- archive tickets (view, conversations; closed tickets inactive 120 days): /api/#archive_tickets
- filter tickets (`/search/tickets`, `total`, archived excluded): /api/#filter_tickets
- conversations, satisfaction ratings, agents, contacts, groups, solutions, canned responses and
  rate limits (`429` with `Retry-After`): all on the same page.

Endpoints: GET /api/v2/agents/me, /agents, /groups, /contacts, /contacts/{id}, /tickets,
/tickets/{id}/conversations, /tickets/{id}/satisfaction_ratings, /tickets/archived/{id},
/tickets/archived/{id}/conversations, /search/tickets, /solutions/categories,
/solutions/categories/{id}/folders, /solutions/folders/{id}/articles,
/canned_response_folders, /canned_response_folders/{id}/responses.

Tickets
- The list is asked with `updated_since` far in the past (without it Freshdesk returns only
  the last 30 days), oldest update first, 100 per page, `include=requester,stats,description`
  (name and email of the requester, `resolved_at`/`closed_at`, and the first message, which
  Freshdesk keeps on the ticket and not among its conversations).
- The list stops after 300 pages. When the cursor reaches page 300 and there are more, the
  window moves: the next query starts at the `updated_at` of the last ticket read. Tickets
  with exactly that time come back again; the cursor remembers their ids (`edge_ids`) and
  the new window drops them, so no ticket is written twice. If a whole window shares one
  `updated_at` (30,000 tickets in one second) the window steps one second on and the ties
  beyond the cap are lost.
- Status codes arrive as numbers; `status_map` knows their string forms (2 open, 3 pending,
  4 and 5 resolved). A custom status code is open by the framework's default.
- Conversations: `private` is a note; `incoming` is the customer; anything else is an agent
  whose email comes from `/agents` by `user_id` (cached for the run).
- CSAT: `ratings` holds a number per survey question; the first one counts. Freshdesk's
  scale as documented by its examples is 103 extremely happy, 102 very happy, 101 happy, 100
  neutral, -101 unhappy, -102 very unhappy, -103 extremely unhappy; it is folded onto 1-5 as
  103 and 102 -> 5, 101 -> 4, 100 -> 3, -101 -> 2, -102 and -103 -> 1. Only resolved and
  closed tickets are asked. If the key may not read ratings the import goes on without them.

Archived tickets (a limitation)
Freshdesk archives a closed ticket 120 days after its last activity. The list and the filter
API no longer return it, and the docs show no way to list archived tickets: only view,
delete and conversations by id. So after the live list ends, the importer walks the ticket
ids `1..max id seen` and asks `GET /tickets/archived/{id}` for each; a 404 (live, deleted or
never existed) is skipped. This costs one request per id in that range, so on a big account
it is slow and uses much of the rate limit. If the very first probe is refused (a plan
without the archive API) the walk is dropped. An archive made newer than every live ticket
(an id above the highest live one) is not reached. `warnings()` says all of this.

Articles: solution categories -> folders -> articles; the `Category` of an article is its
folder (the finest level). `status` 1 is a draft, 2 published. Replies: canned response
folders -> responses; the shortcut is the slug of the title.

`discover` counts exactly where it can (contacts, articles, replies by finding the last
page) and estimates tickets from the filter API's `total`, which excludes archived tickets.

Attachments: a ticket's `attachments` (first message) and each conversation's `attachments`
are lists of `{name, content_type, size, attachment_url}` (/api/#ticket_attributes and
/api/#conversations). Entries without an https `attachment_url` are skipped. `fetch` downloads
the (signed, short lived) `attachment_url` through `SourceHttp.download`.

Changes since a time (`changed_tickets`, for the sync until go-live): the same `/tickets` list
with `updated_since` set to that time, oldest update first, same parsing. Archived tickets are
NOT looked at (no archive walk), so a ticket archived since is not reported.

Not verified against a live account. Unsure of: whether the ticket list (not only the single
ticket view) carries `attachments`; whether `attachment_url` needs the API key (it is a signed
storage link in the docs' examples); the time format `updated_since` accepts beyond
`YYYY-MM-DDTHH:MM:SSZ`; the satisfaction scale beyond the values in
Freshdesk's examples; whether `per_page` is honoured on conversations, solutions and canned
responses (the code carries on while a page has 30 or more rows, so it works with either 30
or 100); field names of solution articles (`description`, `description_text`, `status`) and
canned responses (`title`, `content`, `content_html`); whether the archive view answers 404
or 403 for a live ticket.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr
from functools import partial
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
    Reply,
    SourceConnector,
    SourceError,
    Ticket,
)
from cedesk.helpdesk.domain.portal import normalise_email
from cedesk.helpdesk.service.imports.http import SourceHttp, basic, segment, when

PER_PAGE = 100
FULL_PAGE = 30  # a page with at least this many rows may have a next one (Freshdesk's default size)
LIST_PAGE_CAP = 300  # the ticket list serves at most this many pages per query
EARLIEST = "2000-01-01T00:00:00Z"
STATUS_MAP = {"2": "open", "3": "pending", "4": "resolved", "5": "resolved"}
_PRIORITY = {1: "low", 2: "medium", 3: "high", 4: "urgent"}
_SOURCE = {
    1: "email", 2: "portal", 3: "phone", 5: "twitter", 6: "facebook", 7: "chat",
    9: "web", 10: "email",
}  # fmt: skip
_CSAT = {103: 5, 102: 5, 101: 4, 100: 3, -101: 2, -102: 1, -103: 1}
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_BLOCKS = frozenset(
    {"p", "div", "br", "li", "tr", "ul", "ol", "table", "blockquote", "h1", "h2", "h3", "h4", "h5"}
)


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def _newline(self) -> None:
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "br":
            self.parts.append("\n")
        elif tag in _BLOCKS:
            self._newline()
            if tag == "li":
                self.parts.append("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCKS and tag != "br":
            self._newline()

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw: str | None) -> str:
    """Plain text of an HTML fragment: blocks become lines, tags and scripts vanish."""
    if not raw:
        return ""
    parser = _Text()
    parser.feed(raw)
    parser.close()
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]


def _rows(data: Any) -> list[Any]:
    return data if isinstance(data, list) else []


def _stamp(value: object) -> str:
    return str(value or "")


class FreshdeskConnector:
    source = "freshdesk"
    status_map: Mapping[str, str] = STATUS_MAP

    def __init__(self, http: SourceHttp, host: str, *, include_archived: bool = False) -> None:
        self._http = http
        self._host = host
        self._archived = include_archived
        self._agents: dict[str, tuple[str | None, str | None]] | None = None
        self._contacts: dict[str, Contact] = {}
        self._csat_ok = True
        self.failures = rules.RecordFailures()  # tickets deleted or failing mid-import (P0-2)

    # Helpers

    async def _all(
        self, path: str, params: Mapping[str, Any] | None = None, *, record: bool = False
    ) -> list[Any]:
        """Every row of a paginated list (carries on while a page has 30 rows or more).
        `record`: the list belongs to one ticket, so a 404 means that ticket is gone."""
        out: list[Any] = []
        for page in range(1, LIST_PAGE_CAP + 1):
            rows = _rows(
                await self._http.get(
                    path, {**(params or {}), "per_page": PER_PAGE, "page": page}, record=record
                )
            )
            out.extend(rows)
            if len(rows) < FULL_PAGE:
                break
        return out

    async def _agent_book(self) -> dict[str, tuple[str | None, str | None]]:
        if self._agents is None:
            book: dict[str, tuple[str | None, str | None]] = {}
            for agent in await self._all("/api/v2/agents"):
                contact = agent.get("contact") or {}
                email = normalise_email(str(contact.get("email") or ""))
                book[str(agent.get("id"))] = (email, contact.get("name"))
            self._agents = book
        return self._agents

    async def _agent_email(self, agent_id: object) -> str | None:
        if agent_id is None:
            return None
        return (await self._agent_book()).get(str(agent_id), (None, None))[0]

    async def _count(self, path: str) -> int:
        """Rows of a list by finding its last page (about log2 of the pages in requests)."""

        async def size(page: int) -> int:
            data = await self._http.get(path, {"per_page": PER_PAGE, "page": page})
            return len(_rows(data))

        first = await size(1)
        if first < PER_PAGE:
            return first
        low, high = 1, 2
        high_n = await size(high)
        while high_n == PER_PAGE:
            low, high = high, high * 2
            high_n = await size(high)
        while high - low > 1:
            mid = (low + high) // 2
            n = await size(mid)
            if n == PER_PAGE:
                low = mid
            else:
                high, high_n = mid, n
        return (high - 1) * PER_PAGE + high_n

    # The connector interface

    async def check(self) -> AccountInfo:
        await self._http.get("/api/v2/agents/me")
        return AccountInfo(self._host)

    async def groups(self) -> Sequence[Group]:
        rows = await self._all("/api/v2/groups")
        return [Group(str(g["id"]), str(g.get("name") or g["id"])) for g in rows if "id" in g]

    async def discover(self) -> Counts:
        found = await self._http.get(
            "/api/v2/search/tickets", {"query": "\"created_at:>'2000-01-01'\""}
        )
        total = found.get("total") if isinstance(found, dict) else 0
        articles = 0
        for folder in await self._article_folders():
            articles += await self._count(
                f"/api/v2/solutions/folders/{segment(folder[0])}/articles"
            )
        replies = 0
        for folder in await self._reply_folders():
            replies += await self._count(
                f"/api/v2/canned_response_folders/{segment(folder[0])}/responses"
            )
        return Counts(
            contacts=await self._count("/api/v2/contacts"),
            tickets=int(total or 0),  # estimate: the filter API leaves archived tickets out
            articles=articles,
            replies=replies,
            groups=len(await self.groups()),
        )

    def warnings(self) -> list[str]:
        if self._archived:
            archive = (
                "Archived tickets are included: Freshdesk offers no way to list them, so they are "
                "fetched by trying every ticket number up to the highest one found, one request "
                "each. That is slow on a large account and uses its Freshdesk API quota. The "
                "ticket count shown leaves archived tickets out."
            )
        else:
            archive = (
                "Freshdesk archives closed tickets after 120 days and offers no way to list them, "
                "so they are NOT imported. To include them, connect with the setting "
                "include_archived = true: every ticket number is then tried, one request each, "
                "which is slow on a large account and uses its Freshdesk API quota."
            )
        return [
            archive,
            "Satisfaction ratings are folded onto 1 to 5 (happy 4, extremely or very happy 5, "
            "neutral 3, unhappy 2, very or extremely unhappy 1). Custom ticket statuses come "
            "in as open.",
        ]

    async def page(self, kind: str, cursor: Mapping[str, Any] | None) -> Page[Any]:
        if kind == "contacts":
            return await self._contacts_page(cursor)
        if kind == "tickets":
            if cursor and cursor.get("phase") == "archive":
                return await self._archived_page(cursor)
            return await self._listed_page(cursor)
        if kind == "articles":
            return await self._nested(
                cursor,
                self._article_folders,
                "/api/v2/solutions/folders/{}/articles",
                self._article,
            )
        if kind == "replies":
            return await self._nested(
                cursor,
                self._reply_folders,
                "/api/v2/canned_response_folders/{}/responses",
                self._reply,
            )
        return Page([], None)

    # Contacts

    async def _contacts_page(self, cursor: Mapping[str, Any] | None) -> Page[Any]:
        number = int(cursor["page"]) if cursor else 1
        data = await self._http.get("/api/v2/contacts", {"per_page": PER_PAGE, "page": number})
        rows = _rows(data)
        items = [_contact(c) for c in rows if isinstance(c, dict) and "id" in c]
        return Page(items, {"page": number + 1} if len(rows) >= FULL_PAGE else None)

    async def _contact_by_id(self, contact_id: object) -> Contact:
        key = str(contact_id)
        if key not in self._contacts:
            raw = await self._http.get(f"/api/v2/contacts/{segment(key)}", missing_ok=True)
            if isinstance(raw, dict) and "id" in raw:
                self._contacts[key] = _contact(raw)
            else:
                email, name = (await self._agent_book()).get(key, (None, None))
                self._contacts[key] = Contact(key, email, name or "Unknown requester")
        return self._contacts[key]

    # Tickets

    async def _listed_page(self, cursor: Mapping[str, Any] | None) -> Page[Any]:
        c: dict[str, Any] = (
            dict(cursor)
            if cursor
            else {
                "phase": "list",
                "since": EARLIEST,
                "page": 1,
                "edge_at": "",
                "edge_ids": [],
                "skip": [],
                "max_id": 0,
            }
        )
        data = await self._http.get(
            "/api/v2/tickets",
            {
                "updated_since": c["since"],
                "order_by": "updated_at",
                "order_type": "asc",
                "per_page": PER_PAGE,
                "page": c["page"],
                "include": "requester,stats,description",
            },
        )
        rows = [r for r in _rows(data) if isinstance(r, dict) and "id" in r]
        skip = set(c["skip"])
        edge_at: str = c["edge_at"]
        edge_ids: list[int] = list(c["edge_ids"])
        max_id = int(c["max_id"])
        items: list[Ticket] = []
        for raw in rows:
            ticket_id = int(raw["id"])
            max_id = max(max_id, ticket_id)
            stamp = _stamp(raw.get("updated_at"))
            if stamp == edge_at:
                if ticket_id not in edge_ids:
                    edge_ids.append(ticket_id)
            else:
                edge_at, edge_ids = stamp, [ticket_id]
            if ticket_id not in skip:
                await self._read(items, raw, archived=False)
        if len(_rows(data)) < FULL_PAGE:
            if max_id < 1 or not self._archived:
                return Page(items, None)
            return Page(items, {"phase": "archive", "next_id": 1, "max_id": max_id})
        nxt: dict[str, Any] = {**c, "edge_at": edge_at, "edge_ids": edge_ids, "max_id": max_id}
        if c["page"] < LIST_PAGE_CAP:
            return Page(items, {**nxt, "page": c["page"] + 1})
        # The cap: the next window starts where this one stopped. Tickets with that very time
        # are read again and dropped by id; a window that cannot move steps one second on.
        start = when(edge_at) or when(c["since"])
        stuck = start is None or start == when(c["since"])
        if start is None or stuck:
            start = (start or _EPOCH) + timedelta(seconds=1)
        since = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        return Page(
            items,
            {**nxt, "since": since, "page": 1, "skip": [] if stuck else edge_ids},
        )

    async def changed_tickets(
        self, since: datetime, cursor: Mapping[str, Any] | None
    ) -> Page[Ticket]:
        """Tickets updated at or after `since` (the live list only, never the archive)."""
        c: dict[str, Any] = (
            dict(cursor)
            if cursor
            else {"since": since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "page": 1}
        )
        data = await self._http.get(
            "/api/v2/tickets",
            {
                "updated_since": c["since"],
                "order_by": "updated_at",
                "order_type": "asc",
                "per_page": PER_PAGE,
                "page": c["page"],
                "include": "requester,stats,description",
            },
        )
        rows = [r for r in _rows(data) if isinstance(r, dict) and "id" in r]
        items: list[Ticket] = []
        for raw in rows:
            await self._read(items, raw, archived=False)
        if len(_rows(data)) < FULL_PAGE:
            return Page(items, None)
        if c["page"] < LIST_PAGE_CAP:
            return Page(items, {**c, "page": c["page"] + 1})
        # At the cap the window moves to the last update seen (a tie may repeat a ticket).
        last = _stamp(rows[-1].get("updated_at")) if rows else ""
        return Page(items, {"since": last or c["since"], "page": 1})

    async def fetch(self, attachment: Attachment, limit: int) -> bytes | None:
        return await self._http.download(attachment.url, limit)

    async def _archived_page(self, cursor: Mapping[str, Any]) -> Page[Any]:
        first, last = int(cursor["next_id"]), int(cursor["max_id"])
        end = min(first + rules.PAGE_MAX - 1, last)
        items: list[Ticket] = []
        for ticket_id in range(first, end + 1):
            try:
                raw = await self._http.get(
                    f"/api/v2/tickets/archived/{segment(ticket_id)}", missing_ok=True
                )
            except SourceError as exc:
                if exc.permanent and ticket_id == 1:
                    return Page([], None)  # no archive API on this plan
                raise
            if isinstance(raw, dict) and "id" in raw:
                await self._read(items, raw, archived=True)
        more = {"phase": "archive", "next_id": end + 1, "max_id": last}
        return Page(items, more if end < last else None)

    async def _read(self, items: list[Ticket], raw: Mapping[str, Any], *, archived: bool) -> None:
        """Adds the ticket to `items`, or notes it in `failures` when it was deleted after it
        was listed (or keeps failing while the run isolates records)."""
        ticket = await self.failures.guard(
            "ticket", str(raw["id"]), partial(self._ticket, raw, archived=archived)
        )
        if ticket is not None:
            items.append(ticket)

    async def _ticket(self, raw: Mapping[str, Any], *, archived: bool) -> Ticket:
        ticket_id = str(raw["id"])
        created = when(raw.get("created_at")) or _EPOCH
        embedded = raw.get("requester")
        if isinstance(embedded, dict) and embedded.get("id") is not None:
            requester = _contact(embedded)
        elif raw.get("requester_id") is not None:
            requester = await self._contact_by_id(raw["requester_id"])
        else:
            requester = Contact(f"ticket-{ticket_id}", None, "Unknown requester")
        messages: list[Message] = []
        first = str(raw.get("description_text") or "") or html_to_text(raw.get("description"))
        first_files = _attachments(raw)
        if first.strip() or first_files:
            messages.append(
                Message(
                    "customer",
                    first,
                    created,
                    author_name=requester.name,
                    author_email=requester.email,
                    attachments=first_files,
                )
            )
        base = "/api/v2/tickets/archived" if archived else "/api/v2/tickets"
        for conv in await self._all(f"{base}/{segment(ticket_id)}/conversations", record=True):
            message = await self._message(conv, requester)
            if message is not None:
                messages.append(message)
        messages.sort(key=lambda m: m.created_at)
        raw_stats = raw.get("stats")
        stats: Mapping[str, Any] = raw_stats if isinstance(raw_stats, dict) else {}
        status_code = raw.get("status")
        group = raw.get("group_id")
        return Ticket(
            external_id=ticket_id,
            subject=str(raw.get("subject") or ""),
            status=str(status_code if status_code is not None else ""),
            created_at=created,
            requester=requester,
            messages=messages,
            priority=_PRIORITY.get(_int(raw.get("priority"))),
            channel=_SOURCE.get(_int(raw.get("source"))),
            group_id=None if group is None else str(group),
            assignee_email=await self._agent_email(raw.get("responder_id")),
            tags=[str(t) for t in raw.get("tags") or []],
            fields=dict(raw.get("custom_fields") or {}),
            updated_at=when(raw.get("updated_at")),
            resolved_at=when(stats.get("resolved_at")) or when(stats.get("closed_at")),
            csat=await self._csat(ticket_id) if _int(status_code) in (4, 5) else None,
        )

    async def _message(self, conv: Mapping[str, Any], requester: Contact) -> Message | None:
        text = str(conv.get("body_text") or "") or html_to_text(conv.get("body"))
        files = _attachments(conv)
        if not text.strip() and not files:
            return None
        created = when(conv.get("created_at")) or _EPOCH
        external_id = None if conv.get("id") is None else str(conv["id"])
        if conv.get("private"):
            kind = "note"
        elif conv.get("incoming"):
            kind = "customer"
        else:
            kind = "agent"
        if kind == "customer":
            sender = normalise_email(parseaddr(str(conv.get("from_email") or ""))[1])
            return Message(
                kind,
                text,
                created,
                external_id,
                requester.name,
                sender or requester.email,
                files,
            )
        email, name = (await self._agent_book()).get(str(conv.get("user_id")), (None, None))
        return Message(kind, text, created, external_id, name, email, files)

    async def _csat(self, ticket_id: str) -> Csat | None:
        if not self._csat_ok:
            return None
        try:
            data = await self._http.get(
                f"/api/v2/tickets/{segment(ticket_id)}/satisfaction_ratings", missing_ok=True
            )
        except SourceError as exc:
            if not exc.permanent:
                raise
            self._csat_ok = False  # the key may not read ratings: go on without
            return None
        for rating in _rows(data):
            values = rating.get("ratings") if isinstance(rating, dict) else None
            if not isinstance(values, dict) or not values:
                continue
            score = _CSAT.get(_int(values.get("default_question", next(iter(values.values())))))
            if score is not None:
                feedback = rating.get("feedback")
                return Csat(
                    score, str(feedback) if feedback else None, when(rating.get("created_at"))
                )
        return None

    # Articles and saved replies: a list of folders, then the rows of each

    async def _article_folders(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for category in await self._all("/api/v2/solutions/categories"):
            path = f"/api/v2/solutions/categories/{segment(category['id'])}/folders"
            out.extend((str(f["id"]), str(f.get("name") or f["id"])) for f in await self._all(path))
        return out

    async def _reply_folders(self) -> list[tuple[str, str]]:
        rows = await self._all("/api/v2/canned_response_folders")
        return [(str(f["id"]), str(f.get("name") or f["id"])) for f in rows]

    async def _nested(
        self, cursor: Mapping[str, Any] | None, folders: Any, path: str, build: Any
    ) -> Page[Any]:
        """One page of rows from folder `i`, page `p`; empty folders are passed over."""
        c: dict[str, Any] = (
            dict(cursor)
            if cursor
            else {
                "folders": [list(f) for f in await folders()],
                "i": 0,
                "p": 1,
            }
        )
        while c["i"] < len(c["folders"]):
            folder_id, name = c["folders"][c["i"]]
            data = await self._http.get(
                path.format(segment(folder_id)), {"per_page": PER_PAGE, "page": c["p"]}
            )
            rows = [r for r in _rows(data) if isinstance(r, dict) and "id" in r]
            more = len(_rows(data)) >= FULL_PAGE
            c = {**c, "p": c["p"] + 1} if more else {**c, "i": c["i"] + 1, "p": 1}
            if rows:
                done = c["i"] >= len(c["folders"])
                return Page([build(r, folder_id, name) for r in rows], None if done else c)
        return Page([], None)

    @staticmethod
    def _article(raw: Mapping[str, Any], folder_id: str, folder_name: str) -> Article:
        text = str(raw.get("description_text") or "") or html_to_text(raw.get("description"))
        return Article(
            external_id=str(raw["id"]),
            title=str(raw.get("title") or ""),
            body=text,
            category=Category(folder_id, folder_name),
            published=_int(raw.get("status")) == 2,
        )

    @staticmethod
    def _reply(raw: Mapping[str, Any], folder_id: str, folder_name: str) -> Reply:
        text = html_to_text(raw.get("content_html")) or html_to_text(raw.get("content"))
        title = str(raw.get("title") or "")
        return Reply(str(raw["id"]), slug(title) or f"reply-{raw['id']}", text)


def _attachments(raw: Mapping[str, Any]) -> list[Attachment]:
    out: list[Attachment] = []
    rows = raw.get("attachments")
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("attachment_url") or "")
        if not url.lower().startswith("https://"):
            continue
        size = item.get("size")
        out.append(
            Attachment(
                str(item.get("name") or "file"),
                url,
                size if isinstance(size, int) else None,
                str(item["content_type"]) if item.get("content_type") else None,
            )
        )
    return out


def _int(value: object) -> int:
    """A code as a number; 0 (a code no map knows) when it isn't one."""
    try:
        return int(str(value))
    except ValueError:
        return 0


def _contact(raw: Mapping[str, Any]) -> Contact:
    email = normalise_email(str(raw.get("email") or ""))
    name = str(raw.get("name") or "").strip() or (email or "").split("@")[0] or "Unknown"
    return Contact(str(raw["id"]), email, name)


def make(
    settings: Mapping[str, str],
    credentials: Mapping[str, str],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    guard: Guard | None = None,
) -> SourceConnector:
    """Raises `egress.BadAddress` for a domain that isn't a plain Freshdesk account name.
    Archived tickets are imported only when the setting `include_archived` is "true": finding
    them costs one request per ticket number (see the module docstring)."""
    host = egress.account_host("freshdesk", settings.get("domain", ""))
    http = SourceHttp(
        host,
        headers=basic(credentials["api_key"], "X"),
        guard=guard,
        transport=transport,
        label="Freshdesk",
    )
    wanted = settings.get("include_archived", "").strip().lower() in ("1", "true", "yes", "on")
    return FreshdeskConnector(http, host, include_archived=wanted)
