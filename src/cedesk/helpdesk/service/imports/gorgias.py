"""The Gorgias importer: customers, tickets with messages, teams and macros (saved replies).

Gorgias REST API, read only, Basic auth (account email + API key), under
`https://<domain>.gorgias.com/api`. Every list is cursor paged (`cursor`, `limit` up to 100,
`meta.next_cursor`; `meta.total_resources` is documented too).

Endpoints used (docs read 17 Nov 2026: https://developers.gorgias.com/reference/list-tickets,
.../list-messages, .../list-macros, .../list-teams, .../list-customers, .../download-file,
https://developers.gorgias.com/v1.1/reference/limitations and the changelog entry "Resource
URLs returned through the API will not be directly accessible anymore"):

- check: `GET /account`
- contacts: `GET /customers` (order `created_datetime:asc`)
- tickets: `GET /tickets` (`order_by=updated_datetime:asc`, `trashed=false`), then per ticket
  `GET /messages?ticket_id=` (`created_datetime:asc`; the per-ticket route is deprecated)
- groups: `GET /teams`; replies: `GET /macros` (archived ones are not listed by default)
- files: `GET /attachment/download/{domain_hash}/{resource_name}` (Basic auth, answers 307 to a
  signed, timed URL)

Decisions:

- Status: `closed` -> resolved; `open` -> open; an open ticket with `snooze_datetime` set is
  given the word "snoozed", which `status_map` sends to pending.
- Trashed (`trashed_datetime` set) and spam (`spam` true) tickets are skipped. Deleted
  messages (`deleted_datetime`) are left out, and the ticket is noted incomplete with how many
  (C20). A message with neither text nor a file is left out and counted
  (`message_without_text`).
- Messages: `public: false` -> note; `from_agent` -> agent (sender's email); else customer.
  `body_text` is preferred; HTML is stripped to text only when there is no text. `source.to`,
  `source.cc`, `source.bcc` (lists of `{name, address}`; cc/bcc only on email) become the
  message's recipients; the ticket's cc is every address copied on any of its messages.
- Dates: `rules.dated`: a ticket's `created_datetime`, else `opened_datetime`, its first
  message, `updated_datetime`; a message's `created_datetime`, else `sent_datetime`, else the
  ticket's creation (or opening). With none, the import time is used and the ticket is
  noted incomplete.
- History: up to `MESSAGE_PAGES_MAX` pages of 100 messages per ticket (a safety limit); past
  it the ticket is noted incomplete and `history_capped` is counted.
- Macros: only those with a `setResponseText` action (its `body_text`, else stripped
  `body_html`); the shortcut is an ASCII slug of the macro name cut to the 32 characters a
  shortcut may have (`macro-<id>` when the name has no letters or digits). `discover` counts
  only these, and `warnings()` says how many were left out.
- Articles: NOT imported (`Page([], None)`): the Gorgias REST API reference has no Help Center
  article endpoint (API-LIMITED). `warnings()` says so in the dry run.
- discover: totals come from `meta.total_resources` of a `limit=1` request; when a response
  has none, records are counted by paging (100 per page) up to `COUNT_PAGES_MAX` pages and the
  count is then a lower bound. Ticket totals include trashed ones only if the source does.

- Attachments: each message's `attachments` list (`name`, `url`, `content_type`, `size`). An
  entry with no URL is noted on the ticket (C20). A non-https URL is kept (the framework writes
  a link line) but never fetched (`file_unreachable`). `fetch` turns a Gorgias upload URL
  (`https://uploads.gorgias.io/<hash>/<name>`) into the documented download route on the
  account's host, which takes the account's credentials and redirects to a signed URL;
  `SourceHttp.download` sends the credentials only to the account's host, never to the
  redirect's. Live finding (B-36): the upload host answers 200 with one 55,782-byte PNG
  ("This file does not exist or is private") for non-image files. So whatever comes back is
  checked against what Gorgias declared: PNG bytes for a file declared as another type, or
  PNG bytes whose size is far from the declared size, are that placeholder and are NOT kept:
  `fetch` returns None (the framework keeps a link line) and counts `file_placeholder`.
- Changes since a time (`changed_tickets`): `GET /tickets` with `order_by=updated_datetime:desc`
  (a documented value). There is no documented "updated since" filter (API-LIMITED), so every
  row of a page is looked at: rows updated at or after `since` are read (also when a page is
  out of order), and paging stops only when the page's last dated row is older than `since`.

Not verified against a live account (UNVERIFIED): that the download route serves every
attachment (PDF, text, CSV); the `uploads.gorgias.com` host (only `uploads.gorgias.io` is in
the changelog); that the API really sorts `updated_datetime:desc` as documented; the customer
`channels` list (`type`, `address`; not in the customer reference, read when present) used
for phone and other emails; the web address of a ticket (`/app/ticket/<id>`); the
`satisfaction_survey` field on a ticket (`score` 1-5, `body_text`, `scored_datetime` assumed;
the list reference does not show it); the shape of `custom_fields` (a mapping of id ->
{name?, value} or a list of such objects is handled); `total_resources` being filled on every
list; whether `/account` needs a particular role (any 401/403 is a permanent error here).
Ticket type: the ticket reference has no type field, so `ticket_type` is not filled.
"""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import datetime
from functools import partial
from html.parser import HTMLParser
from typing import Any, ClassVar
from urllib.parse import urlsplit

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
MACRO_PAGES_MAX = 100  # macros are counted one by one (text or not): 10,000 at most
MESSAGE_PAGES_MAX = 2_000  # 200,000 messages on one ticket: a safety limit, not a real one
SHORTCUT_MAX = 32  # what a saved reply's shortcut may hold (domain/saved_replies.py)
STATUS_MAP: Mapping[str, str] = {"open": "open", "snoozed": "pending", "closed": "resolved"}

UPLOAD_HOSTS = frozenset({"uploads.gorgias.io", "uploads.gorgias.com"})
DOWNLOAD_PATH = "/api/attachment/download"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PLACEHOLDER_SIZE = 55_782  # the "file does not exist or is private" PNG seen live (B-36)
SIZE_SLACK = 4096  # bytes a real file may differ from its declared size before it is doubted


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


def slug(name: str, external_id: str = "") -> str:
    """A saved-reply shortcut from a macro name: ASCII letters and digits joined by `-`, at
    most 32 long (`Remboursé rapide!` -> `rembourse-rapide`). A name with neither (all
    symbols, or a script with no ASCII form) gives `macro-<id>`, so no macro is lost to it."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    text = text[:SHORTCUT_MAX].strip("-")
    if text:
        return text
    safe = re.sub(r"[^a-z0-9]+", "-", external_id.lower()).strip("-")
    return f"macro-{safe}"[:SHORTCUT_MAX].strip("-") if safe else ""


def _id(value: object) -> str | None:
    return None if value is None else str(value)


def _addresses(raw: object) -> list[str]:
    """Email addresses of a `source.to/cc/bcc` list (`[{name, address}]`)."""
    out: list[str] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        address = item.get("address") if isinstance(item, dict) else None
        if isinstance(address, str) and "@" in address:
            address = address.strip()
            if address and address not in out:
                out.append(address)
    return out


def placeholder(attachment: Attachment, data: bytes) -> bool:
    """True when `data` is Gorgias's "file does not exist or is private" image rather than
    the file declared on the message: a PNG came back for a file declared as another type, or
    a PNG whose size is far from the declared one (or exactly the placeholder's size when the
    file was declared otherwise). Bytes that are not a PNG are never doubted."""
    if not data.startswith(PNG_MAGIC):
        return False
    declared = (attachment.mime or "").strip().lower()
    if declared and declared != "image/png":
        return True
    size = attachment.size
    if size is None or size <= 0:
        return False
    if len(data) == PLACEHOLDER_SIZE and size != PLACEHOLDER_SIZE:
        return True
    return abs(len(data) - size) > max(SIZE_SLACK, size // 2)


class GorgiasConnector:
    source = "gorgias"
    status_map: Mapping[str, str] = STATUS_MAP

    def __init__(self, http: SourceHttp) -> None:
        self._http = http
        self.failures = rules.RecordFailures()  # tickets deleted mid-import (P0-2)
        self._macros_without_text: int | None = None  # set by `discover`

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

    async def _count_replies(self) -> int:
        """Macros that will become saved replies (C17): a total would count the ones without
        text too, so they are read and checked. Those left out are kept for `warnings()`."""
        usable = without = 0
        cursor: str | None = None
        for _ in range(MACRO_PAGES_MAX):
            rows, cursor = await self._list("/macros", {"limit": LIMIT, "cursor": cursor})
            for row in rows:
                if self._reply(row) is not None:
                    usable += 1
                else:
                    without += 1
            if cursor is None:
                break
        self._macros_without_text = without
        return usable

    async def discover(self) -> Counts:
        return Counts(
            contacts=await self._count("/customers"),
            tickets=await self._count("/tickets"),
            articles=0,
            replies=await self._count_replies(),
            groups=await self._count("/teams"),
        )

    def warnings(self) -> list[str]:
        """Lines the dry run shows before anything is brought (P1-18)."""
        out = [
            "Help Center articles are not imported: the Gorgias API offers no way to read "
            "them. Copy them into CeDesk by hand.",
        ]
        n = self._macros_without_text
        if n is None:
            out.append(
                "Macros without a reply text (only actions such as tags or assignment) are "
                "not imported. Archived macros are not imported either."
            )
        elif n:
            out.append(
                f"{n} macro{'s' if n != 1 else ''} without a reply text (only actions such as "
                "tags or assignment) will not be imported; the count of saved replies leaves "
                "them out. Archived macros are not imported either."
            )
        return out

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
        phone: str | None = None
        others: list[str] = []
        channels = raw.get("channels")
        for item in channels if isinstance(channels, list) else []:
            if not isinstance(item, dict):
                continue
            address = item.get("address")
            if not isinstance(address, str) or not address.strip():
                continue
            address = address.strip()
            kind = str(item.get("type") or "").lower()
            if kind == "phone" and phone is None:
                phone = address
            elif (
                kind == "email"
                and "@" in address
                and address.lower() != (email or "").lower()
                and address not in others
            ):
                others.append(address)
        if email is None and others:
            email = others.pop(0)
        return Contact(str(raw["id"]), email, name or "Unknown", phone=phone, other_emails=others)

    async def _contacts(self, after: str | None) -> Page[Contact]:
        rows, nxt = await self._list(
            "/customers", {"limit": LIMIT, "cursor": after, "order_by": "created_datetime:asc"}
        )
        items = [self._contact(r) for r in rows if r.get("id") is not None]
        return Page(items, {"cursor": nxt} if nxt else None)

    # Replies

    @staticmethod
    def _reply(row: Mapping[str, Any]) -> Reply | None:
        """The saved reply a macro becomes, or None when it has no reply text."""
        if row.get("id") is None:
            return None
        text = ""
        for action in row.get("actions") or []:
            if isinstance(action, dict) and action.get("name") == "setResponseText":
                args = action.get("arguments")
                if isinstance(args, dict):
                    text = _text(args.get("body_text"), args.get("body_html"))
                    break
        shortcut = slug(str(row.get("name") or ""), str(row["id"]))
        if not text or not shortcut:
            return None
        return Reply(str(row["id"]), shortcut, rules.body(text))

    async def _replies(self, after: str | None) -> Page[Reply]:
        rows, nxt = await self._list(
            "/macros", {"limit": LIMIT, "cursor": after, "order_by": "created_datetime:asc"}
        )
        items = [r for r in (self._reply(row) for row in rows) if r is not None]
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

    async def _messages(self, tid: str, fallback: datetime | None) -> list[Message]:
        out: list[Message] = []
        deleted = no_url = 0
        cursor: str | None = None
        for _ in range(MESSAGE_PAGES_MAX):
            rows, cursor = await self._list(
                "/messages",
                {
                    "ticket_id": tid,
                    "limit": LIMIT,
                    "cursor": cursor,
                    "order_by": "created_datetime:asc",
                },
                record=True,
            )
            for row in rows:
                if row.get("deleted_datetime"):
                    deleted += 1
                    continue
                files, missing = self._attachments(row.get("attachments"))
                no_url += missing
                message = self._message(tid, row, files, fallback)
                if message is not None:
                    out.append(message)
            if cursor is None:
                break
        if cursor is not None:  # the safety limit was reached (C6)
            rules.loss("history_capped")
            self.failures.incomplete(
                "ticket",
                tid,
                f"Only the first {MESSAGE_PAGES_MAX * LIMIT} messages were read (a safety "
                "limit); later ones were not imported.",
            )
        if deleted:
            self.failures.incomplete(
                "ticket", tid, f"{deleted} message(s) deleted in Gorgias were left out."
            )
        if no_url:
            self.failures.incomplete(
                "ticket",
                tid,
                f"{no_url} attachment(s) had no address in Gorgias and were left out.",
            )
        return out

    def _message(
        self,
        tid: str,
        row: Mapping[str, Any],
        files: list[Attachment],
        fallback: datetime | None,
    ) -> Message | None:
        text = rules.body(_text(row.get("body_text"), row.get("body_html")))
        if not text and not files:
            rules.loss("message_without_text")
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
        raw_source = row.get("source")
        source: Mapping[str, Any] = raw_source if isinstance(raw_source, dict) else {}
        created = rules.dated(
            self.failures,
            "ticket",
            tid,
            when(row.get("created_datetime")),
            when(row.get("sent_datetime")),
            fallback,
            what="message date",
        )
        return Message(
            kind=kind,
            body=text,
            created_at=created,
            external_id=_id(row.get("id")),
            author_name=str(sender["name"]) if sender.get("name") else None,
            author_email=email if isinstance(email, str) and email else None,
            attachments=files,
            to=_addresses(source.get("to")),
            cc=_addresses(source.get("cc")),
            bcc=_addresses(source.get("bcc")),
        )

    @staticmethod
    def _attachments(raw: object) -> tuple[list[Attachment], int]:
        """`attachments: [{name, url, content_type, size}]` and how many had no URL at all.
        A non-https URL is kept: the framework writes it as a link line (`fetch` won't read
        it)."""
        out: list[Attachment] = []
        missing = 0
        if not isinstance(raw, list):
            return out, missing
        for item in raw:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not isinstance(url, str) or not url.strip():
                missing += 1
                continue
            size = item.get("size")
            mime = item.get("content_type")
            name = item.get("name")
            out.append(
                Attachment(
                    name=name.strip() if isinstance(name, str) and name.strip() else "file",
                    url=url.strip(),
                    size=size if isinstance(size, int) and not isinstance(size, bool) else None,
                    mime=mime if isinstance(mime, str) and mime else None,
                )
            )
        return out, missing

    def _download_url(self, url: str) -> str:
        """The documented download route for a Gorgias upload URL: the path after the host
        (`/<domain_hash>/<resource_name>`) under `/api/attachment/download` on the account's
        host. Any other URL is fetched as it is."""
        parts = urlsplit(url)
        segments = parts.path.split("/")[1:]
        if (parts.hostname or "").lower() in UPLOAD_HOSTS and len(segments) >= 2 and all(segments):
            return f"https://{self._http.host}{DOWNLOAD_PATH}{parts.path}"
        return url

    async def fetch(self, attachment: Attachment, limit: int) -> bytes | None:
        if not attachment.url.lower().startswith("https://"):
            rules.loss("file_unreachable")
            return None
        data = await self._http.download(self._download_url(attachment.url), limit)
        if data is not None and placeholder(attachment, data):
            rules.loss("file_placeholder")
            return None
        return data

    async def changed_tickets(
        self, since: datetime, cursor: Mapping[str, Any] | None
    ) -> Page[Ticket]:
        """Newest-updated first (`updated_datetime:desc`, documented). Every row of a page is
        looked at, so a page slightly out of order loses nothing; paging stops only when the
        page's last row with an update time is older than `since` (with that sort, every
        later page is older still). A row without an update time is read, to be safe."""
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
        last: datetime | None = None
        for row in rows:
            changed = rules.valid_date(when(row.get("updated_datetime")))
            if changed is not None:
                last = changed
                if changed < since:
                    continue
            if row.get("id") is None or row.get("spam") or row.get("trashed_datetime"):
                continue
            await self._read(items, row)
        if last is not None and last < since:
            nxt = None
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
        own = rules.first_date(when(row.get("created_datetime")), when(row.get("opened_datetime")))
        messages = await self._messages(tid, own)
        created = rules.dated(
            self.failures,
            "ticket",
            tid,
            when(row.get("created_datetime")),
            when(row.get("opened_datetime")),
            messages[0].created_at if messages else None,
            when(row.get("updated_datetime")),
        )
        cc: list[str] = []
        for message in messages:
            cc += [a for a in message.cc if a not in cc]
        return Ticket(
            external_id=tid,
            subject=rules.subject(row.get("subject")),
            status=raw_status,
            created_at=created,
            requester=requester,
            messages=messages,
            priority=row.get("priority"),
            channel=row.get("channel"),
            group_id=_id(team.get("id")) if isinstance(team, dict) else None,
            assignee_email=email if isinstance(email, str) and email else None,
            tags=[str(t["name"]) for t in tags if isinstance(t, dict) and t.get("name")],
            fields=self._fields(row.get("custom_fields")),
            updated_at=when(row.get("updated_datetime")),
            resolved_at=when(row.get("closed_datetime")),
            csat=self._csat(row.get("satisfaction_survey")),
            source_url=f"https://{self._http.host}/app/ticket/{tid}",
            cc=cc,
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
