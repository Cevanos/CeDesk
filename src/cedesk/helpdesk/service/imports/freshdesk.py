"""The Freshdesk importer (md section 8): tickets with conversations, contacts, solution
articles and canned responses, read through the Freshdesk API v2 (read only, `GET` only).

Account: the `domain` setting (`acme` -> `acme.freshdesk.com`, built by `egress.account_host`)
and the `api_key` credential (HTTP Basic: the key as user name, `X` as password).

Sources used (developers.freshdesk.com/api):
- tickets list and its `include` (`requester`, `stats`, `description`; 2 credits each),
  `updated_since`, `order_by` (`created_at`, `due_by`, `updated_at`, `status`), `order_type`
  (`asc`, `desc`), pagination (page size at most 100, at most 300 pages = 30,000 tickets per
  query; without `updated_since` only the last 30 days): /api/#list_all_tickets
- ticket attributes (`cc_emails`, `reply_cc_emails`, `to_emails`, `fwd_emails`, `due_by`,
  `fr_escalated`, `is_escalated`, `type`, `company_id`): /api/#ticket_attributes
- ticket associations (`association_type` 1 parent, 2 child, 3 tracker, 4 related;
  `associated_tickets_list` on the ticket view): /api/#view_a_ticket
- archive tickets (view, conversations; closed tickets inactive 120 days): /api/#archive_tickets
- filter tickets (`/search/tickets`, `total`, archived excluded): /api/#filter_tickets
- conversations (`to_emails`, `cc_emails`, `bcc_emails`), satisfaction ratings, agents,
  contacts, companies, groups, solutions, canned responses, error codes (`403`
  `access_denied` / `require_feature`) and rate limits (`429` with `Retry-After`).

Endpoints: GET /api/v2/agents/me, /agents, /groups, /contacts, /contacts/{id},
/companies/{id}, /tickets, /tickets/{id}, /tickets/{id}/conversations,
/tickets/{id}/satisfaction_ratings, /tickets/archived/{id},
/tickets/archived/{id}/conversations, /search/tickets, /solutions/categories,
/solutions/categories/{id}/folders, /solutions/folders/{id}/subfolders,
/solutions/folders/{id}/articles, /canned_response_folders,
/canned_response_folders/{id}/responses.

Tickets
- The list is asked with `updated_since` far in the past, oldest update first, 100 per page,
  `include=requester,stats,description` (name, email, phone of the requester,
  `resolved_at`/`closed_at`, and the first message, which Freshdesk keeps on the ticket and
  not among its conversations).
- Paging by number over a list sorted by update time (C18): a ticket updated during the
  import moves to the end of the list, and every ticket after its old place moves one place
  up, so the first one of the next page would slip onto the page already read. The cursor
  therefore remembers the last update time it read (`edge_at`) and the ids read with exactly
  that time (`edge_ids`); before each page past the first it reads the page before again
  (ids and times only, 1 credit) and takes any ticket there it has not read yet, going back
  further while a whole page is new. A ticket already read that was updated comes again at
  the end (it is written again, not twice). `created_at` order would not shift on updates,
  but the 300-page cap can only be passed by moving `updated_since`, which needs update order.
- The list stops after 300 pages. When the cursor reaches page 300 and there are more, the
  window moves: the next query starts at `edge_at`; tickets with that time come back and are
  dropped by `edge_ids`. If a whole window shares one `updated_at` (30,000 tickets in one
  second) the window steps one second on and the ties beyond the cap are lost.
- Status codes arrive as numbers; `status_map` knows their string forms (2 open, 3 pending,
  4 and 5 resolved). A custom status code is open by the framework's default.
- Conversations: `private` is a note; `incoming` is the customer; anything else is an agent
  whose email comes from `/agents` by `user_id` (cached for the run). All pages of a ticket's
  conversations are read up to `CONVERSATION_PAGES`; past that the ticket is noted
  incomplete and counted (`history_capped`).
- Also kept: `to_emails`/`cc_emails`/`bcc_emails` of each conversation (and of the first
  message), the ticket's `cc_emails` + `reply_cc_emails` (`Ticket.cc`; `fwd_emails` are the
  recipients of a forward, which is a conversation with its own `to_emails`), `type`, `due_by`
  (`sla_due_at`), `fr_escalated` or `is_escalated` (`sla_breached`), the agent screen's address
  `https://{domain}.freshdesk.com/a/tickets/{id}` and the associations (`Ticket.links`).
- A date the source leaves out or garbles is taken from the nearest source date, else the
  import time is used and the ticket is noted incomplete (`rules.dated`).
- CSAT: `ratings` holds a number per survey question; the first one counts. Freshdesk's
  scale as documented by its examples is 103 extremely happy, 102 very happy, 101 happy, 100
  neutral, -101 unhappy, -102 very unhappy, -103 extremely unhappy; it is folded onto 1-5 as
  103 and 102 -> 5, 101 -> 4, 100 -> 3, -101 -> 2, -102 and -103 -> 1. Only resolved and
  closed tickets are asked. A 401/403 (the plan or the key's role has no ratings) switches
  ratings off for the run (kept in the cursor), counts every ticket left without one
  (`csat_unavailable`) and `warnings()` says so; any other refusal is noted against that one
  ticket only.

Archived tickets (on by default; the setting `include_archived = false` turns them off)
Freshdesk archives a closed ticket 120 days after its last activity. The list and the filter
API no longer return it, and the docs show no way to list archived tickets: only view,
delete and conversations by id. So after the live list ends, the importer walks the ticket
ids from 1 up to the highest id known (the highest one listed, or the newest ticket by
`created_at` if higher), then on past it until `ARCHIVE_TAIL` ids in a row are not archived
(an archive newer than every live ticket, or an account whose tickets are all archived).
`GET /tickets/archived/{id}` answering 404 is "no archived ticket with that number". Before
the walk one known live id is asked: a 401/403 there means the account can't use the archive
API, and that is noted in the run's failures (and in `warnings()` after `discover`), never
silent. A refusal on a single id later is noted against that id. This costs one request per
id, which `warnings()` says with the estimate.

Articles: solution categories -> folders (and their subfolders) -> articles; the `Category`
of an article is its folder (the finest level, keyed by the folder id), named
"Category / Folder" so the category name is kept. `status` 1 is a draft, 2 published;
`updated_at` is kept, and `created_at` stands in for the publish date of a published article
(Freshdesk documents no separate one). Replies: canned response folders -> responses; the
shortcut is the slug of the title.

Contacts: name, email, `phone` (else `mobile`), `other_emails` and the company's name
(`company_id`, looked up once per company and cached for the slice; a refusal turns the
lookups off without stopping the import).

`discover` counts exactly where it can (contacts, articles, replies by finding the last
page) and estimates tickets from the filter API's `total`, which excludes archived tickets.

Attachments: a ticket's `attachments` (first message) and each conversation's `attachments`
are lists of `{id, name, content_type, size, attachment_url}`. Entries without an https
`attachment_url` are skipped. `fetch` downloads the `attachment_url` through
`SourceHttp.download`, which sends the API key only to the account's own host, never to the
storage host (S3) the signed link points to or a redirect leads to. The signed link expires
(minutes); when a download comes back empty-handed the ticket is read again for a fresh link
and the download tried once more.

Changes since a time (`changed_tickets`, for the sync until go-live): the same `/tickets` list
with `updated_since` set to that time, oldest update first, same parsing and the same
look-back. Archived tickets are NOT looked at (no archive walk), so a ticket archived since is
not reported.

Not verified against a live account (UNVERIFIED): whether the ticket list (not only the single
ticket view) carries `attachments` and `association_type`; whether `updated_since` combines
with `order_by` as documented; the satisfaction scale beyond the values in Freshdesk's
examples; whether `per_page` is honoured on conversations, solutions and canned responses
(the code carries on while a page has 30 or more rows, so it works with either 30 or 100);
field names of solution articles and folders (`description`, `description_text`, `status`,
`created_at`, `updated_at`, `sub_folders_count`) and the subfolders path; contact fields
(`phone`, `mobile`, `other_emails`, `company_id`); whether the archive view answers 404 (as
assumed) or 403 for a live ticket; how long a signed `attachment_url` lives.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr
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
    RateLimited,
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
CONVERSATION_PAGES = 2000  # a safety limit on one ticket's conversations (200,000 at 100)
ARCHIVE_TAIL = 200  # ids tried past the highest known one before the archive walk ends
REFUSALS_IN_A_ROW = 20  # archive views refused in a row: the key, not the tickets
FOLDER_DEPTH = 5  # subfolder levels followed under a solution folder
EARLIEST = "2000-01-01T00:00:00Z"
STATUS_MAP = {"2": "open", "3": "pending", "4": "resolved", "5": "resolved"}
_PRIORITY = {1: "low", 2: "medium", 3: "high", 4: "urgent"}
_SOURCE = {
    1: "email", 2: "portal", 3: "phone", 5: "twitter", 6: "facebook", 7: "chat",
    9: "web", 10: "email",
}  # fmt: skip
_CSAT = {103: 5, 102: 5, 101: 4, 100: 3, -101: 2, -102: 1, -103: 1}
# `association_type` of the ticket -> what the tickets in its `associated_tickets_list` are to
# it: a parent (1) lists its children, a child (2) its parent, a tracker (3) the tickets it
# tracks, a related ticket (4) its tracker.
_ASSOCIATION = {1: "child", 2: "parent", 3: "related", 4: "related"}
_LIST_INCLUDE = "requester,stats,description"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_BLOCKS = frozenset(
    {"p", "div", "br", "li", "tr", "ul", "ol", "table", "blockquote", "h1", "h2", "h3", "h4", "h5"}
)
_ARCHIVE_REFUSED = (
    "Freshdesk refused its archived tickets API (401/403: the plan or the key's role doesn't "
    "allow it), so archived tickets were not imported."
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


def _refused(exc: SourceError) -> bool:
    """A 401/403: the account or the key's role may not read this (a plan without the
    feature answers 403 `require_feature` / `access_denied`)."""
    return exc.refused


def _fresh(raw: Mapping[str, Any], edge_at: str, edge_ids: set[int]) -> bool:
    """Not read yet: updated after the last time read, or at that time but not among the
    ids read with it. A row without a time is always taken (it may come twice, never lost)."""
    stamp = _stamp(raw.get("updated_at"))
    if not stamp:
        return True
    return stamp > edge_at or (stamp == edge_at and int(raw["id"]) not in edge_ids)


class FreshdeskConnector:
    source = "freshdesk"
    status_map: Mapping[str, str] = STATUS_MAP

    def __init__(self, http: SourceHttp, host: str, *, include_archived: bool = True) -> None:
        self._http = http
        self._host = host
        self._archived = include_archived
        self._agents: dict[str, tuple[str | None, str | None]] | None = None
        self._contacts: dict[str, Contact] = {}
        self._companies: dict[str, str | None] = {}
        self._companies_off = False
        self._csat_off = False  # the account may not read ratings (kept in the ticket cursor)
        self._archive_off = False  # the account may not use the archive API
        self._top_id = 0  # the newest ticket's id, when `discover` found it
        self._files: dict[str, tuple[str, bool, str]] = {}  # url -> ticket, archived, file id
        self.failures = rules.RecordFailures()  # tickets deleted or failing mid-import (P0-2)

    # Helpers

    async def _pages(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        record: bool = False,
        limit: int = LIST_PAGE_CAP,
    ) -> tuple[list[Any], bool]:
        """Every row of a paginated list (carries on while a page has 30 rows or more), and
        whether `limit` pages were read with more still to come. `record`: the list belongs
        to one ticket, so a 404 means that ticket is gone."""
        out: list[Any] = []
        for page in range(1, limit + 1):
            rows = _rows(
                await self._http.get(
                    path, {**(params or {}), "per_page": PER_PAGE, "page": page}, record=record
                )
            )
            out.extend(rows)
            if len(rows) < FULL_PAGE:
                return out, False
        return out, True

    async def _all(self, path: str, params: Mapping[str, Any] | None = None) -> list[Any]:
        return (await self._pages(path, params))[0]

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

    async def _company(self, company_id: object) -> str | None:
        """A company's name, once per company. A refusal turns the lookups off: the name is
        a nice-to-have, never a reason to stop."""
        if company_id is None or company_id == "" or self._companies_off:
            return None
        key = str(company_id)
        if key not in self._companies:
            try:
                raw = await self._http.get(f"/api/v2/companies/{segment(key)}", missing_ok=True)
            except SourceError as exc:
                if not exc.permanent:
                    raise
                self._companies_off = True
                return None
            name = str(raw.get("name") or "").strip() if isinstance(raw, dict) else ""
            self._companies[key] = name or None
        return self._companies[key]

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
        if self._archived:
            # For the dry run: what the archive walk will cost, and whether it can run at all.
            try:
                self._top_id = await self._newest_id()
                self._archive_off = await self._archive_refused(self._top_id or 1)
            except SourceError:
                pass  # the warning stays the general one; the run decides for itself
        return Counts(
            contacts=await self._count("/api/v2/contacts"),
            tickets=int(total or 0),  # estimate: the filter API leaves archived tickets out
            articles=articles,
            replies=replies,
            groups=len(await self.groups()),
        )

    def warnings(self) -> list[str]:
        out: list[str] = []
        if not self._archived:
            out.append(
                "Freshdesk archives closed tickets after 120 days and offers no way to list "
                "them; this connection has include_archived = false, so they are NOT imported. "
                "Leave the setting out (or set it to true) to include them: every ticket number "
                "is then tried, one request each, which is slow on a large account and uses its "
                "Freshdesk API quota."
            )
        elif self._archive_off:
            out.append(
                f"{_ARCHIVE_REFUSED[:-1]}: they are NOT imported. Freshdesk archives closed "
                "tickets after 120 days of no activity."
            )
        else:
            cost = (
                f" Up to ticket #{self._top_id} that is about {self._top_id + ARCHIVE_TAIL} "
                "requests"
                if self._top_id
                else " That is one request per ticket number"
            )
            out.append(
                "Archived tickets are included: Freshdesk offers no way to list them, so they "
                "are fetched by trying every ticket number up to the highest one found (and "
                f"{ARCHIVE_TAIL} past it), one request each.{cost}, slow on a large account and "
                "taken from its Freshdesk API quota (Growth 100, Pro 400, Enterprise 700 a "
                "minute). The ticket count shown leaves archived tickets out. To skip them, "
                "connect with the setting include_archived = false."
            )
        if self._csat_off:
            out.append(
                "Freshdesk refused satisfaction ratings (401/403: the plan or the key's role "
                "has no access), so tickets are imported without them; the run report counts "
                "them (csat_unavailable)."
            )
        if self._companies_off:
            out.append("Freshdesk refused company details, so contacts come without a company.")
        out.append(
            "Satisfaction ratings are folded onto 1 to 5 (happy 4, extremely or very happy 5, "
            "neutral 3, unhappy 2, very or extremely unhappy 1). Custom ticket statuses come "
            "in as open."
        )
        return out

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
        items: list[Contact] = []
        for row in rows:
            if isinstance(row, dict) and "id" in row:
                items.append(_contact(row, await self._company(row.get("company_id"))))
        return Page(items, {"page": number + 1} if len(rows) >= FULL_PAGE else None)

    async def _contact_by_id(self, contact_id: object) -> Contact:
        key = str(contact_id)
        if key not in self._contacts:
            raw = await self._http.get(f"/api/v2/contacts/{segment(key)}", missing_ok=True)
            if isinstance(raw, dict) and "id" in raw:
                self._contacts[key] = _contact(raw, await self._company(raw.get("company_id")))
            else:
                email, name = (await self._agent_book()).get(key, (None, None))
                self._contacts[key] = Contact(key, email, name or "Unknown requester")
        return self._contacts[key]

    # Tickets: the live list

    async def _list(
        self, since: str, number: int, *, full: bool = True, newest_first: bool = False
    ) -> tuple[list[dict[str, Any]], int]:
        """One page of the ticket list, and how many rows it had. `full` asks the includes
        (2 credits each); without them a page costs 1 credit."""
        params: dict[str, Any] = {
            "updated_since": since,
            "order_by": "created_at" if newest_first else "updated_at",
            "order_type": "desc" if newest_first else "asc",
            "per_page": 1 if newest_first else PER_PAGE,
            "page": number,
        }
        if full:
            params["include"] = _LIST_INCLUDE
        data = _rows(await self._http.get("/api/v2/tickets", params))
        return [r for r in data if isinstance(r, dict) and "id" in r], len(data)

    async def _newest_id(self) -> int:
        """The id of the newest live ticket (ids grow with creation)."""
        rows, _ = await self._list(EARLIEST, 1, full=False, newest_first=True)
        return max((_int(r["id"]) for r in rows), default=0)

    async def _looked_back(
        self, since: str, number: int, edge_at: str, edge_ids: set[int]
    ) -> list[dict[str, Any]]:
        """Tickets that slipped from page `number` onto pages already read (C18): read the
        pages before it again, ids and times only, back to one that holds a ticket already
        read; a page with new ones is read in full."""
        found: list[dict[str, Any]] = []
        for back in range(number - 1, 0, -1):
            light, _ = await self._list(since, back, full=False)
            new = {int(r["id"]) for r in light if _fresh(r, edge_at, edge_ids)}
            if new:
                full, _ = await self._list(since, back)
                found = [r for r in full if int(r["id"]) in new] + found
            if not light or len(new) < len(light):
                break
        return found

    def _window_cursor(
        self, c: Mapping[str, Any], edge_at: str, edge_ids: set[int], max_id: int
    ) -> dict[str, Any]:
        return {
            **{k: v for k, v in c.items() if k != "skip"},
            "edge_at": edge_at,
            "edge_ids": sorted(edge_ids),
            "max_id": max_id,
            "csat_off": self._csat_off,
        }

    async def _window_step(self, c: Mapping[str, Any]) -> tuple[list[Ticket], dict[str, Any], bool]:
        """One page of the live list after cursor `c`: its tickets, the next cursor, and
        whether the list ended."""
        since, number = str(c["since"]), int(c["page"])
        edge_at = str(c.get("edge_at") or "")
        edge_ids = {int(i) for i in c.get("edge_ids") or []}
        if c.get("csat_off"):
            self._csat_off = True
        rows, size = await self._list(since, number)
        back = await self._looked_back(since, number, edge_at, edge_ids) if number > 1 else []
        max_id = int(c.get("max_id") or 0)
        items: list[Ticket] = []
        taken: set[int] = set()
        for raw in back + rows:
            ticket_id = int(raw["id"])
            max_id = max(max_id, ticket_id)
            if ticket_id in taken or not _fresh(raw, edge_at, edge_ids):
                continue
            try:
                await self._read(items, raw, archived=False)
            except RateLimited:
                if not taken:
                    raise  # nothing done on this page: the run pauses and tries it again
                # Keep what was read. The edge only covers the tickets that were, so the next
                # look at this same page starts at the first one that was not.
                nxt = self._window_cursor(c, edge_at, edge_ids, max_id)
                return items, nxt, False
            taken.add(ticket_id)
            stamp = _stamp(raw.get("updated_at"))
            if stamp and stamp > edge_at:
                edge_at, edge_ids = stamp, set()
            if stamp == edge_at:
                edge_ids.add(ticket_id)
        nxt = self._window_cursor(c, edge_at, edge_ids, max_id)
        if size < FULL_PAGE:
            return items, nxt, True
        if number < LIST_PAGE_CAP:
            return items, {**nxt, "page": number + 1}, False
        # The cap: the next window starts where this one stopped. Tickets with that very time
        # are read again and dropped by id; a window that cannot move steps one second on.
        start = when(edge_at) or when(since)
        stuck = start is None or start == when(since)
        if start is None or stuck:
            start = (start or _EPOCH) + timedelta(seconds=1)
        return items, {**nxt, "since": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "page": 1}, False

    async def _listed_page(self, cursor: Mapping[str, Any] | None) -> Page[Any]:
        c: Mapping[str, Any] = cursor or {"phase": "list", "since": EARLIEST, "page": 1}
        items, nxt, ended = await self._window_step(c)
        if not ended:
            return Page(items, nxt)
        if not self._archived or self._archive_off:
            return Page(items, None)
        live = int(nxt["max_id"])
        top = max(live, await self._newest_id())
        return Page(
            items,
            {
                "phase": "archive",
                "next_id": 1,
                "max_id": top,
                "live_id": live or top,
                "misses": 0,
                "checked": False,
                "csat_off": self._csat_off,
            },
        )

    async def changed_tickets(
        self, since: datetime, cursor: Mapping[str, Any] | None
    ) -> Page[Ticket]:
        """Tickets updated at or after `since` (the live list only, never the archive)."""
        c: Mapping[str, Any] = cursor or {
            "since": since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "page": 1,
        }
        items, nxt, ended = await self._window_step(c)
        return Page(items, None if ended else nxt)

    # Tickets: the archive walk

    async def _archive_refused(self, probe: int) -> bool:
        """Whether the archive API is closed to this account, asked with an id that exists
        (a live ticket answers 404 there; UNVERIFIED). A temporary error is raised."""
        try:
            await self._http.get(f"/api/v2/tickets/archived/{segment(probe)}", missing_ok=True)
        except SourceError as exc:
            if not exc.permanent:
                raise
            return _refused(exc)
        return False

    async def _archived_one(self, ticket_id: int, refusals: list[int]) -> Mapping[str, Any] | None:
        """The archived ticket with this number, or None (404: none archived with it). A
        refusal or another error about this one id is noted against it; a temporary one is
        raised (the page is tried again) unless the run is isolating records."""
        try:
            raw = await self._http.get(
                f"/api/v2/tickets/archived/{segment(ticket_id)}", missing_ok=True
            )
        except SourceError as exc:
            if not exc.permanent:
                if self.failures.absorb("ticket", str(ticket_id), exc):
                    return None
                raise
            refusals[0] += 1
            if refusals[0] >= REFUSALS_IN_A_ROW:
                raise  # every id refused: the key lost its access; the run stops and says so
            gone = rules.RecordGone("Freshdesk refused to show this archived ticket.")
            self.failures.absorb("ticket", str(ticket_id), gone)
            return None
        refusals[0] = 0
        return raw if isinstance(raw, dict) and "id" in raw else None

    async def _archived_page(self, cursor: Mapping[str, Any]) -> Page[Any]:
        c = dict(cursor)
        if c.get("csat_off"):
            self._csat_off = True
        if not c.get("checked", True):
            if await self._archive_refused(int(c.get("live_id") or 0) or 1):
                self._archive_off = True
                self.failures.incomplete("ticket", "archived-tickets", _ARCHIVE_REFUSED)
                return Page([], None)
            c["checked"] = True
        ticket_id, top = int(c["next_id"]), int(c["max_id"])
        misses = int(c.get("misses") or 0)
        items: list[Ticket] = []
        refusals = [0]
        first = ticket_id
        for _ in range(rules.PAGE_MAX):
            if ticket_id > top and misses >= ARCHIVE_TAIL:
                break
            try:
                raw = await self._archived_one(ticket_id, refusals)
                if raw is not None:
                    misses = 0
                    await self._read(items, raw, archived=True)
                elif ticket_id > top:
                    misses += 1
            except RateLimited:
                if ticket_id == first:
                    raise  # nothing done on this page: the run pauses and tries it again
                break  # keep the ids done; the next page starts at the one that was cut
            ticket_id += 1
        if ticket_id > top and misses >= ARCHIVE_TAIL:
            return Page(items, None)
        more = {**c, "next_id": ticket_id, "misses": misses, "csat_off": self._csat_off}
        return Page(items, more)

    # Tickets: one ticket

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
        created = rules.dated(
            self.failures,
            "ticket",
            ticket_id,
            when(raw.get("created_at")),
            when(raw.get("updated_at")),
        )
        embedded = raw.get("requester")
        if isinstance(embedded, dict) and embedded.get("id") is not None:
            requester = _contact(embedded)
        elif raw.get("requester_id") is not None:
            requester = await self._contact_by_id(raw["requester_id"])
        else:
            requester = Contact(f"ticket-{ticket_id}", None, "Unknown requester")
        if requester.organization is None and raw.get("company_id") is not None:
            requester = replace(requester, organization=await self._company(raw["company_id"]))
        messages: list[Message] = []
        first = str(raw.get("description_text") or "") or html_to_text(raw.get("description"))
        first_files = self._attachments(raw, ticket_id, archived)
        if first.strip() or first_files:
            messages.append(
                Message(
                    "customer",
                    first,
                    created,
                    author_name=requester.name,
                    author_email=requester.email,
                    attachments=first_files,
                    to=_emails(raw.get("to_emails")),
                    cc=_emails(raw.get("cc_emails")),
                )
            )
        base = "/api/v2/tickets/archived" if archived else "/api/v2/tickets"
        convs, capped = await self._pages(
            f"{base}/{segment(ticket_id)}/conversations", record=True, limit=CONVERSATION_PAGES
        )
        if capped:
            rules.loss("history_capped")
            self.failures.incomplete(
                "ticket",
                ticket_id,
                f"Freshdesk lists more than {CONVERSATION_PAGES} pages of conversations on this "
                "ticket; the ones past them were not read.",
            )
        for conv in convs:
            if isinstance(conv, dict):
                message = await self._message(conv, requester, ticket_id, created, archived)
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
            updated_at=rules.valid_date(when(raw.get("updated_at"))),
            resolved_at=rules.first_date(
                when(stats.get("resolved_at")), when(stats.get("closed_at"))
            ),
            csat=await self._csat(ticket_id) if _int(status_code) in (4, 5) else None,
            ticket_type=str(raw.get("type") or "").strip() or None,
            source_url=f"https://{self._host}/a/tickets/{segment(ticket_id)}",
            cc=_emails([*_list_of(raw.get("cc_emails")), *_list_of(raw.get("reply_cc_emails"))]),
            links=await self._links(raw, ticket_id, archived),
            sla_due_at=rules.valid_date(when(raw.get("due_by"))),
            sla_breached=_breached(raw),
        )

    async def _links(self, raw: Mapping[str, Any], ticket_id: str, archived: bool) -> list[Link]:
        """The ticket's associations. The list rows carry `association_type` at most
        (UNVERIFIED); the ids are on the ticket view, asked only for an associated ticket."""
        relation = _ASSOCIATION.get(_int(raw.get("association_type")))
        if relation is None:
            return []
        others = raw.get("associated_tickets_list")
        if not isinstance(others, list) and not archived:
            view = await self._http.get(f"/api/v2/tickets/{segment(ticket_id)}", missing_ok=True)
            others = view.get("associated_tickets_list") if isinstance(view, dict) else None
        out: list[Link] = []
        for other in _list_of(others):
            if _int(other) > 0 and str(_int(other)) != ticket_id:
                out.append(Link(relation, str(_int(other))))
        return out

    async def _message(
        self,
        conv: Mapping[str, Any],
        requester: Contact,
        ticket_id: str,
        ticket_created: datetime,
        archived: bool,
    ) -> Message | None:
        text = str(conv.get("body_text") or "") or html_to_text(conv.get("body"))
        files = self._attachments(conv, ticket_id, archived)
        if not text.strip() and not files:
            return None
        created = rules.dated(
            self.failures,
            "ticket",
            ticket_id,
            when(conv.get("created_at")),
            when(conv.get("updated_at")),
            ticket_created,
            what="message date",
        )
        external_id = None if conv.get("id") is None else str(conv["id"])
        if conv.get("private"):
            kind = "note"
        elif conv.get("incoming"):
            kind = "customer"
        else:
            kind = "agent"
        to = _emails(conv.get("to_emails"))
        cc = _emails(conv.get("cc_emails"))
        bcc = _emails(conv.get("bcc_emails"))
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
                to=to,
                cc=cc,
                bcc=bcc,
            )
        email, name = (await self._agent_book()).get(str(conv.get("user_id")), (None, None))
        return Message(kind, text, created, external_id, name, email, files, to=to, cc=cc, bcc=bcc)

    async def _csat(self, ticket_id: str) -> Csat | None:
        if self._csat_off:
            rules.loss("csat_unavailable")
            return None
        try:
            data = await self._http.get(
                f"/api/v2/tickets/{segment(ticket_id)}/satisfaction_ratings", missing_ok=True
            )
        except SourceError as exc:
            if not exc.permanent:
                raise
            if _refused(exc):
                self._csat_off = True  # the plan or the key's role has no ratings
                rules.loss("csat_unavailable")
            else:
                self.failures.incomplete(
                    "ticket",
                    ticket_id,
                    "Freshdesk refused this ticket's satisfaction rating; imported without it.",
                )
            return None
        for rating in _rows(data):
            values = rating.get("ratings") if isinstance(rating, dict) else None
            if not isinstance(values, dict) or not values:
                continue
            score = _CSAT.get(_int(values.get("default_question", next(iter(values.values())))))
            if score is not None:
                feedback = rating.get("feedback")
                return Csat(
                    score,
                    str(feedback) if feedback else None,
                    rules.valid_date(when(rating.get("created_at"))),
                )
        return None

    # Files

    def _attachments(
        self, raw: Mapping[str, Any], ticket_id: str, archived: bool
    ) -> list[Attachment]:
        out = _attachments(raw)
        for item, file in zip(_file_rows(raw), out, strict=True):
            if item.get("id") is not None:
                self._files[file.url] = (ticket_id, archived, str(item["id"]))
        return out

    async def fetch(self, attachment: Attachment, limit: int) -> bytes | None:
        """The file's bytes. `SourceHttp.download` sends the key only to the account's own
        host, never to the storage host of a signed link. A signed link that expired since
        the ticket was read (the download comes back None) is asked for again, once."""
        data = await self._http.download(attachment.url, limit)
        if data is not None:
            return data
        origin = self._files.get(attachment.url)
        if origin is None or (attachment.size is not None and attachment.size > limit):
            return None
        fresh = await self._fresh_url(*origin)
        if fresh is None or fresh == attachment.url:
            return None
        return await self._http.download(fresh, limit)

    async def _fresh_url(self, ticket_id: str, archived: bool, file_id: str) -> str | None:
        base = "/api/v2/tickets/archived" if archived else "/api/v2/tickets"
        try:
            view = await self._http.get(f"{base}/{segment(ticket_id)}", missing_ok=True)
            holders: list[Any] = [view] if isinstance(view, dict) else []
            convs, _ = await self._pages(
                f"{base}/{segment(ticket_id)}/conversations", limit=CONVERSATION_PAGES
            )
            holders.extend(convs)
        except SourceError as exc:
            if not exc.permanent:
                raise
            return None
        for holder in holders:
            if isinstance(holder, dict):
                for item, file in zip(_file_rows(holder), _attachments(holder), strict=True):
                    if str(item.get("id")) == file_id:
                        return file.url
        return None

    # Articles and saved replies: a list of folders, then the rows of each

    async def _article_folders(self) -> list[tuple[str, str]]:
        """(folder id, "Category / Folder") for every folder and subfolder."""
        out: list[tuple[str, str]] = []
        for category in await self._all("/api/v2/solutions/categories"):
            path = f"/api/v2/solutions/categories/{segment(category['id'])}/folders"
            cat_name = str(category.get("name") or category["id"]).strip()
            for folder in await self._all(path):
                await self._folder_tree(folder, cat_name, out, 1)
        return out

    async def _folder_tree(
        self, folder: Mapping[str, Any], parent: str, out: list[tuple[str, str]], depth: int
    ) -> None:
        name = f"{parent} / {str(folder.get('name') or folder['id']).strip()}"
        out.append((str(folder["id"]), name))
        if depth >= FOLDER_DEPTH or _int(folder.get("sub_folders_count")) < 1:
            return
        subs = await self._http.get(
            f"/api/v2/solutions/folders/{segment(folder['id'])}/subfolders", missing_ok=True
        )
        for sub in _rows(subs):
            if isinstance(sub, dict) and "id" in sub:
                await self._folder_tree(sub, name, out, depth + 1)

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
        # The HTML keeps the article's headings, lists and links; text only when there is none.
        text = markdown.from_html(str(raw.get("description") or "")) or markdown.from_text(
            str(raw.get("description_text") or "")
        )
        published = _int(raw.get("status")) == 2
        return Article(
            external_id=str(raw["id"]),
            title=str(raw.get("title") or ""),
            body=text,
            category=Category(folder_id, folder_name),
            published=published,
            published_at=rules.valid_date(when(raw.get("created_at"))) if published else None,
            updated_at=rules.valid_date(when(raw.get("updated_at"))),
        )

    @staticmethod
    def _reply(raw: Mapping[str, Any], folder_id: str, folder_name: str) -> Reply:
        text = html_to_text(raw.get("content_html")) or html_to_text(raw.get("content"))
        title = str(raw.get("title") or "")
        return Reply(str(raw["id"]), slug(title) or f"reply-{raw['id']}", text)


def _file_rows(raw: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The attachment entries `_attachments` keeps, in the same order."""
    return [
        item
        for item in _list_of(raw.get("attachments"))
        if isinstance(item, dict)
        and str(item.get("attachment_url") or "").lower().startswith("https://")
    ]


def _attachments(raw: Mapping[str, Any]) -> list[Attachment]:
    out: list[Attachment] = []
    for item in _file_rows(raw):
        size = item.get("size")
        out.append(
            Attachment(
                str(item.get("name") or "file"),
                str(item["attachment_url"]),
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


def _list_of(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _breached(raw: Mapping[str, Any]) -> bool | None:
    """Freshdesk escalates a ticket when an SLA time is missed (`fr_escalated`: the first
    response; `is_escalated`: any). None when the ticket says neither."""
    if "fr_escalated" not in raw and "is_escalated" not in raw:
        return None
    return bool(raw.get("fr_escalated") or raw.get("is_escalated"))


def _emails(value: object) -> list[str]:
    """Addresses from a list such as `cc_emails` (`"Ann <ann@example.org>"` or a bare one),
    normalised, without repeats; anything that isn't an address is left out."""
    out: list[str] = []
    for item in _list_of(value):
        email = normalise_email(parseaddr(str(item))[1])
        if email and email not in out:
            out.append(email)
    return out


def _contact(raw: Mapping[str, Any], organization: str | None = None) -> Contact:
    email = normalise_email(str(raw.get("email") or ""))
    name = str(raw.get("name") or "").strip() or (email or "").split("@")[0] or "Unknown"
    phone = str(raw.get("phone") or raw.get("mobile") or "").strip() or None
    others = tuple(e for e in _emails(raw.get("other_emails")) if e != email)
    return Contact(
        str(raw["id"]), email, name, phone=phone, other_emails=others, organization=organization
    )


def make(
    settings: Mapping[str, str],
    credentials: Mapping[str, str],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    guard: Guard | None = None,
) -> SourceConnector:
    """Raises `egress.BadAddress` for a domain that isn't a plain Freshdesk account name.
    Archived tickets are imported unless the setting `include_archived` says "false" (or
    0/no/off): finding them costs one request per ticket number (see the module docstring),
    which the dry run's warnings spell out."""
    host = egress.account_host("freshdesk", settings.get("domain", ""))
    http = SourceHttp(
        host,
        headers=basic(credentials["api_key"], "X"),
        guard=guard,
        transport=transport,
        label="Freshdesk",
    )
    unwanted = settings.get("include_archived", "").strip().lower() in ("0", "false", "no", "off")
    return FreshdeskConnector(http, host, include_archived=not unwanted)
