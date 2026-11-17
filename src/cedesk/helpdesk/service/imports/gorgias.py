"""The Gorgias importer: customers, tickets with messages, teams and macros (saved replies).

Gorgias REST API, read only, Basic auth (account email + API key), under
`https://<domain>.gorgias.com/api`. Every list is cursor paged (`cursor`, `limit` up to 100,
`meta.next_cursor`; `meta.total_resources` is documented too).

Endpoints used (docs read: https://developers.gorgias.com/reference/list-tickets,
.../list-messages, .../list-ticket-messages, .../list-macros, .../list-teams,
.../list-customers, https://developers.gorgias.com/v1.1/reference/limitations):

- check: `GET /account`
- contacts: `GET /customers` (order `created_datetime:asc`)
- tickets: `GET /tickets` (`order_by=updated_datetime:asc`, `trashed=false`), then per ticket
  `GET /messages?ticket_id=` (`created_datetime:asc`; the per-ticket route is deprecated)
- groups: `GET /teams`; replies: `GET /macros`

Decisions:

- Status: `closed` -> resolved; `open` -> open; an open ticket with `snooze_datetime` set is
  given the word "snoozed", which `status_map` sends to pending.
- Trashed (`trashed_datetime` set) and spam (`spam` true) tickets are skipped. Deleted
  messages (`deleted_datetime`) and messages with no text are skipped.
- Messages: `public: false` -> note; `from_agent` -> agent (sender's email); else customer.
  `body_text` is preferred; HTML is stripped to text only when there is no text.
- Macros: only those with a `setResponseText` action (its `body_text`, else stripped
  `body_html`); the shortcut is a slug of the macro name. Archived macros are not listed.
- Articles: NOT imported (`Page([], None)`). The Help Center API lives under a separate
  per-help-center path whose reference pages could not be read, so no endpoint is guessed.
- discover: totals come from `meta.total_resources` of a `limit=1` request; when a response
  has none, records are counted by paging (100 per page) up to `COUNT_PAGES_MAX` pages and the
  count is then a lower bound. Ticket totals include trashed ones only if the source does.

- Attachments: each message's `attachments` list (`name`, `url`, `content_type`, `size`) is
  read as given (ticket message object, https://developers.gorgias.com/reference/list-messages);
  entries without an https URL are skipped. A message with files but no text is kept with the
  body "(attachment only)". `fetch` downloads the URL through `SourceHttp.download`.
- Changes since a time (`changed_tickets`): `GET /tickets` with `order_by=updated_datetime:desc`;
  there is no documented "updated since" filter, so paging stops (cursor None) at the first
  ticket whose `updated_datetime` (else `created_datetime`) is older than `since`.

Not verified against a live account. Unsure of: whether Gorgias attachment URLs are signed
public links or need the account's credentials (`download` sends them only to the account's
own host); that `size` is always present; the `updated_datetime:desc` order on `/tickets`
(`:asc` is what the full import uses; `:desc` is assumed to work the same way);
the `satisfaction_survey` field on a ticket
(`score` 1-5, `body_text`, `scored_datetime` assumed; the list reference does not show it);
the shape of `custom_fields` (a mapping of id -> {name?, value} or a list of such objects is
handled); `total_resources` being filled on every list; whether `/account` needs a
particular role (any 401/403 is a permanent error here).
"""

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from functools import partial
from html.parser import HTMLParser
from typing import Any, ClassVar

import httpx

from cedesk import egress
from cedesk.egress import Guard
from cedesk.helpdesk.domain import imports as rules
from cedesk.helpdesk.domain.imports import (
    AccountInfo,
    Attachment,
    Contact,
    Counts,
    Csat,
    Group,
    Message,
    Page,
    Reply,
    SourceConnector,
    Ticket,
)
from cedesk.helpdesk.service.imports.http import SourceHttp, basic, when

LIMIT = 100  # documented maximum
COUNT_PAGES_MAX = 20
MESSAGE_PAGES_MAX = 50  # 5,000 messages on one ticket is far past real use
STATUS_MAP: Mapping[str, str] = {"open": "open", "snoozed": "pending", "closed": "resolved"}


class _Text(HTMLParser):
    _BREAKS: ClassVar[frozenset[str]] = frozenset(
        {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self._BREAKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in self._BREAKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def strip_html(raw: str) -> str:
    """Plain text of an HTML fragment: tags dropped, block ends become line breaks."""
    parser = _Text()
    parser.feed(raw)
    parser.close()
    text = "".join(parser.parts)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def _text(*candidates: object) -> str:
    """The first non-blank text among `candidates`; the last candidate is HTML."""
    for value in candidates[:-1]:
        if isinstance(value, str) and value.strip():
            return value.strip()
    html = candidates[-1]
    return strip_html(html) if isinstance(html, str) else ""


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]


def _id(value: object) -> str | None:
    return None if value is None else str(value)


class GorgiasConnector:
    source = "gorgias"
    status_map: Mapping[str, str] = STATUS_MAP

    def __init__(self, http: SourceHttp) -> None:
        self._http = http
        self.failures = rules.RecordFailures()  # tickets deleted mid-import (P0-2)

    # Reading

    async def _list(
        self, path: str, params: Mapping[str, Any], *, record: bool = False
    ) -> tuple[list[Any], str | None]:
        """`record`: the list belongs to one ticket, so a 404 means that ticket is gone."""
        found = await self._http.get(path, params, record=record)
        data = found.get("data") if isinstance(found, dict) else None
        meta = found.get("meta") if isinstance(found, dict) else None
        nxt = meta.get("next_cursor") if isinstance(meta, dict) else None
        rows = [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
        return rows, (str(nxt) if nxt else None)

    async def check(self) -> AccountInfo:
        found = await self._http.get("/account")
        domain = found.get("domain") if isinstance(found, dict) else None
        return AccountInfo(f"{domain or self._http.host.split('.')[0]}.gorgias.com")

    async def _count(self, path: str) -> int:
        found = await self._http.get(path, {"limit": 1})
        meta = found.get("meta") if isinstance(found, dict) else None
        total = meta.get("total_resources") if isinstance(meta, dict) else None
        if isinstance(total, int) and not isinstance(total, bool):
            return total
        count = 0
        cursor: str | None = None
        for _ in range(COUNT_PAGES_MAX):
            rows, cursor = await self._list(path, {"limit": LIMIT, "cursor": cursor})
            count += len(rows)
            if cursor is None:
                break
        return count

    async def discover(self) -> Counts:
        return Counts(
            contacts=await self._count("/customers"),
            tickets=await self._count("/tickets"),
            articles=0,
            replies=await self._count("/macros"),
            groups=await self._count("/teams"),
        )

    async def groups(self) -> Sequence[Group]:
        out: list[Group] = []
        cursor: str | None = None
        for _ in range(COUNT_PAGES_MAX):
            rows, cursor = await self._list("/teams", {"limit": LIMIT, "cursor": cursor})
            out += [Group(str(r["id"]), str(r.get("name") or r["id"])) for r in rows if "id" in r]
            if cursor is None:
                break
        return out

    async def page(self, kind: str, cursor: Mapping[str, Any] | None) -> Page[Any]:
        after = str(cursor["cursor"]) if cursor and cursor.get("cursor") else None
        if kind == "contacts":
            return await self._contacts(after)
        if kind == "tickets":
            return await self._tickets(after)
        if kind == "replies":
            return await self._replies(after)
        return Page([], None)  # articles: see the module docstring

    # Contacts

    @staticmethod
    def _contact(raw: Mapping[str, Any]) -> Contact:
        email = raw.get("email")
        email = email.strip() if isinstance(email, str) and email.strip() else None
        name = str(raw.get("name") or "").strip() or (email or "").split("@")[0]
        return Contact(str(raw["id"]), email, name or "Unknown")

    async def _contacts(self, after: str | None) -> Page[Contact]:
        rows, nxt = await self._list(
            "/customers", {"limit": LIMIT, "cursor": after, "order_by": "created_datetime:asc"}
        )
        items = [self._contact(r) for r in rows if r.get("id") is not None]
        return Page(items, {"cursor": nxt} if nxt else None)

    # Replies

    async def _replies(self, after: str | None) -> Page[Reply]:
        rows, nxt = await self._list(
            "/macros", {"limit": LIMIT, "cursor": after, "order_by": "created_datetime:asc"}
        )
        items: list[Reply] = []
        for row in rows:
            name = str(row.get("name") or "")
            text = ""
            for action in row.get("actions") or []:
                if isinstance(action, dict) and action.get("name") == "setResponseText":
                    args = action.get("arguments")
                    if isinstance(args, dict):
                        text = _text(args.get("body_text"), args.get("body_html"))
                        break
            shortcut = slug(name)
            if row.get("id") is not None and text and shortcut:
                items.append(Reply(str(row["id"]), shortcut, rules.body(text)))
        return Page(items, {"cursor": nxt} if nxt else None)

    # Tickets

    async def _tickets(self, after: str | None) -> Page[Ticket]:
        rows, nxt = await self._list(
            "/tickets",
            {
                "limit": LIMIT,
                "cursor": after,
                "order_by": "updated_datetime:asc",
                "trashed": "false",
            },
        )
        items: list[Ticket] = []
        for row in rows:
            if row.get("id") is None or row.get("spam") or row.get("trashed_datetime"):
                continue
            await self._read(items, row)
        return Page(items, {"cursor": nxt} if nxt else None)

    async def _messages(self, ticket_id: object) -> list[Message]:
        out: list[Message] = []
        cursor: str | None = None
        for _ in range(MESSAGE_PAGES_MAX):
            rows, cursor = await self._list(
                "/messages",
                {
                    "ticket_id": str(ticket_id),
                    "limit": LIMIT,
                    "cursor": cursor,
                    "order_by": "created_datetime:asc",
                },
                record=True,
            )
            for row in rows:
                message = self._message(row)
                if message is not None:
                    out.append(message)
            if cursor is None:
                break
        return out

    @staticmethod
    def _message(row: Mapping[str, Any]) -> Message | None:
        if row.get("deleted_datetime"):
            return None
        text = rules.body(_text(row.get("body_text"), row.get("body_html")))
        files = GorgiasConnector._attachments(row.get("attachments"))
        if not text and not files:
            return None
        text = text or "(attachment only)"
        raw_sender = row.get("sender")
        sender: Mapping[str, Any] = raw_sender if isinstance(raw_sender, dict) else {}
        kind = (
            "note"
            if row.get("public") is False
            else "agent"
            if row.get("from_agent")
            else "customer"
        )
        email = sender.get("email")
        return Message(
            kind=kind,
            body=text,
            created_at=when(row.get("created_datetime")) or datetime.now(UTC),
            external_id=_id(row.get("id")),
            author_name=str(sender["name"]) if sender.get("name") else None,
            author_email=email if isinstance(email, str) and email else None,
            attachments=files,
        )

    @staticmethod
    def _attachments(raw: object) -> list[Attachment]:
        """`attachments: [{name, url, content_type, size}]`; entries without an https URL
        are skipped."""
        out: list[Attachment] = []
        if not isinstance(raw, list):
            return out
        for item in raw:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not isinstance(url, str) or not url.lower().startswith("https://"):
                continue
            size = item.get("size")
            mime = item.get("content_type")
            name = item.get("name")
            out.append(
                Attachment(
                    name=name.strip() if isinstance(name, str) and name.strip() else "file",
                    url=url,
                    size=size if isinstance(size, int) and not isinstance(size, bool) else None,
                    mime=mime if isinstance(mime, str) and mime else None,
                )
            )
        return out

    async def fetch(self, attachment: Attachment, limit: int) -> bytes | None:
        return await self._http.download(attachment.url, limit)

    async def changed_tickets(
        self, since: datetime, cursor: Mapping[str, Any] | None
    ) -> Page[Ticket]:
        """Newest-updated first; paging stops at the first ticket older than `since`."""
        after = str(cursor["cursor"]) if cursor and cursor.get("cursor") else None
        rows, nxt = await self._list(
            "/tickets",
            {
                "limit": LIMIT,
                "cursor": after,
                "order_by": "updated_datetime:desc",
                "trashed": "false",
            },
        )
        items: list[Ticket] = []
        for row in rows:
            changed = when(row.get("updated_datetime")) or when(row.get("created_datetime"))
            if changed is not None and changed < since:
                nxt = None  # ordered by update time: everything after is older still
                break
            if row.get("id") is None or row.get("spam") or row.get("trashed_datetime"):
                continue
            await self._read(items, row)
        return Page(items, {"cursor": nxt} if nxt else None)

    @staticmethod
    def _fields(raw: object) -> dict[str, Any]:
        out: dict[str, Any] = {}
        items: list[Any] = []
        if isinstance(raw, dict):
            for key, value in raw.items():
                if isinstance(value, dict):
                    items.append({"name": value.get("name") or f"field_{key}", **value})
                else:
                    out[f"field_{key}" if str(key).isdigit() else str(key)] = value
        elif isinstance(raw, list):
            items = [i for i in raw if isinstance(i, dict)]
        for item in items:
            key = item.get("name") or f"field_{item.get('id')}"
            out[str(key)] = item.get("value")
        return out

    @staticmethod
    def _csat(raw: object) -> Csat | None:
        if not isinstance(raw, dict):
            return None
        score = raw.get("score")
        if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5:
            return None
        comment = raw.get("body_text")
        return Csat(
            score,
            comment.strip() if isinstance(comment, str) and comment.strip() else None,
            when(raw.get("scored_datetime")),
        )

    async def _read(self, items: list[Ticket], row: Mapping[str, Any]) -> None:
        """Adds the ticket to `items`, or notes it in `failures` when it was deleted after it
        was listed (or keeps failing while the run isolates records)."""
        ticket = await self.failures.guard("ticket", str(row["id"]), partial(self._ticket, row))
        if ticket is not None:
            items.append(ticket)

    async def _ticket(self, row: Mapping[str, Any]) -> Ticket:
        tid = str(row["id"])
        customer = row.get("customer")
        requester = (
            self._contact(customer)
            if isinstance(customer, dict) and customer.get("id") is not None
            else Contact(f"ticket-{tid}", None, "Unknown")
        )
        raw_status = str(row.get("status") or "open").lower()
        if raw_status == "open" and row.get("snooze_datetime"):
            raw_status = "snoozed"
        assignee = row.get("assignee_user")
        team = row.get("assignee_team")
        email = assignee.get("email") if isinstance(assignee, dict) else None
        tags = row.get("tags") or []
        return Ticket(
            external_id=tid,
            subject=rules.subject(row.get("subject")),
            status=raw_status,
            created_at=when(row.get("created_datetime")) or datetime.now(UTC),
            requester=requester,
            messages=await self._messages(tid),
            priority=row.get("priority"),
            channel=row.get("channel"),
            group_id=_id(team.get("id")) if isinstance(team, dict) else None,
            assignee_email=email if isinstance(email, str) and email else None,
            tags=[str(t["name"]) for t in tags if isinstance(t, dict) and t.get("name")],
            fields=self._fields(row.get("custom_fields")),
            updated_at=when(row.get("updated_datetime")),
            resolved_at=when(row.get("closed_datetime")),
            csat=self._csat(row.get("satisfaction_survey")),
        )


def make(
    settings: Mapping[str, str],
    credentials: Mapping[str, str],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    guard: Guard | None = None,
) -> SourceConnector:
    """Raises `egress.BadAddress` for a `domain` that isn't a Gorgias account name."""
    host = egress.account_host("gorgias", settings.get("domain", ""))
    http = SourceHttp(
        host,
        headers=basic(credentials.get("email", ""), credentials.get("api_key", "")),
        guard=guard,
        transport=transport,
        base_path="/api",
        label="Gorgias",
    )
    return GorgiasConnector(http)
