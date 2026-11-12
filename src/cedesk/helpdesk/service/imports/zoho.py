"""The Zoho Desk importer: contacts, tickets with their threads and comments, departments
and help center articles.

Zoho Desk API v1, GET only, at the data centre the Admin picks (`region`: `us`, `eu`, `in`,
`au`, `jp`; md 8.3: the API host differs per data centre). Sign-in is OAuth with a Self
Client (api-console.zoho.com): its client id and secret and a refresh token the Admin
generates with the scopes `Desk.tickets.READ`, `Desk.contacts.READ`, `Desk.basic.READ` and
`Desk.articles.READ`. `POST /oauth/v2/token` at `accounts.zoho.<dc>` with
`grant_type=refresh_token` gives an access token (`expires_in` 3600 s), sent as
`Authorization: Zoho-oauthtoken <token>` with the `orgId` header. It is fetched on first
use, again before it expires, and once more when a call is refused. The secret and the
tokens are never put in an error message.

Lists are paged with `from` (an index from 0) and `limit` (at most 100); an empty page is
a 204 with no body. Endpoints used:

- organisation: `GET /api/v1/organizations` (the one to read: `org_id` in the settings, or
  the account's only one)
- discover: `GET /api/v1/ticketsCount`, `GET /api/v1/contacts/count` (counted by paging when
  missing), articles counted by paging, departments
- groups: `GET /api/v1/departments`
- contacts: `GET /api/v1/contacts`
- tickets: `GET /api/v1/tickets` (`sortBy=createdTime`, `include=contacts,assignee`), then
  per ticket `GET /api/v1/tickets/{id}/threads` and each thread
  `GET /api/v1/tickets/{id}/threads/{threadId}` (its full text and files), and
  `GET /api/v1/tickets/{id}/comments`
- articles: `GET /api/v1/articles`, then `GET /api/v1/articles/{id}` (the answer)
- changes since a time (M9): `GET /api/v1/tickets` with `sortBy=-modifiedTime`; paging
  stops at the first ticket changed before the time.

Decisions:

- Status: Zoho's `statusType` (the fixed kind behind each custom status): `Open` -> open,
  `On Hold` -> pending, `Closed` -> resolved.
- Messages: a thread with `direction` `in` -> customer, `out` -> agent, `visibility`
  `private` -> note. A comment that isn't public -> note; a public one -> agent. HTML is
  stripped to text.
- Custom fields (`customFields`, label -> value) are kept as fields.
- Saved replies are not imported: Zoho Desk's templates are whole email templates, not
  saved replies. The dry run says so (`warnings`).

Not verified against a live account. Unsure of: `from` starting at 0 (the docs' examples
use 0); `contacts/count` existing; the article `category` object and `answer` field; the
attachment `href` being on the Desk host (a file elsewhere is downloaded without the
token).
"""

import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from cedesk.egress import BadAddress, Guard, fixed_host
from cedesk.helpdesk.domain import imports as rules
from cedesk.helpdesk.domain.imports import (
    AccountInfo,
    Article,
    Attachment,
    Category,
    Contact,
    Counts,
    Group,
    Message,
    Page,
    SourceError,
    Ticket,
)
from cedesk.helpdesk.service.imports.gorgias import strip_html
from cedesk.helpdesk.service.imports.http import SourceHttp, segment, when

LABEL = "Zoho Desk"
REGIONS = ("us", "eu", "in", "au", "jp")
TOKEN_PATH = "/oauth/v2/token"  # noqa: S105 (a URL path, not a secret)
TOKEN_MARGIN = 120  # seconds before expiry at which the token is fetched again
LIMIT = 100
COUNT_PAGES_MAX = 20
DETAIL_PAGES_MAX = 50
STATUS_MAP: Mapping[str, str] = {"open": "open", "on hold": "pending", "closed": "resolved"}


def _now() -> float:
    return time.monotonic()


def _str(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _data(found: object) -> list[Mapping[str, Any]]:
    rows = found.get("data") if isinstance(found, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _next(start: int, rows: Sequence[Any]) -> dict[str, int] | None:
    return {"from": start + len(rows)} if len(rows) >= LIMIT else None


def _text(html: object) -> str:
    return rules.body(strip_html(html) if isinstance(html, str) else "")


def contact_from(row: Mapping[str, Any]) -> Contact:
    email = _str(row.get("email"))
    name = " ".join(x for x in (_str(row.get("firstName")), _str(row.get("lastName"))) if x)
    return Contact(str(row["id"]), email, name or (email or "").split("@")[0] or "Unknown")


class ZohoDeskConnector:
    source = "zoho"
    status_map: Mapping[str, str] = STATUS_MAP

    def __init__(
        self,
        region: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        org_id: str | None,
        *,
        transport: httpx.AsyncBaseTransport | None,
        guard: Guard | None,
    ) -> None:
        if region not in REGIONS:
            raise BadAddress(f"Pick the Zoho data centre: {', '.join(REGIONS)}.")
        self._host = fixed_host("zoho", region)
        self._accounts = SourceHttp(
            fixed_host("zoho", f"accounts-{region}"),
            headers={}, guard=guard, transport=transport, label=LABEL,
        )  # fmt: skip
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._org_id = org_id
        self._org_name: str | None = None
        self._transport = transport
        self._guard = guard
        self._token: str | None = None
        self._expires_at = 0.0
        self._departments: list[Group] | None = None

    # Sign-in

    async def _login(self) -> str:
        data = await self._accounts.exchange_token(
            TOKEN_PATH,
            {
                "grant_type": "refresh_token",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._refresh_token,
            },
        )
        token = data.get("access_token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            # Zoho answers 200 with {"error": "invalid_code"} for a bad refresh token.
            raise SourceError(
                f"{LABEL} refused these credentials (check the client id, secret and refresh "
                "token, and its data centre).",
                permanent=True,
            )
        try:
            lifetime = float(data.get("expires_in", 3600))
        except (TypeError, ValueError):
            lifetime = 3600.0
        self._token = token
        self._expires_at = _now() + lifetime - TOKEN_MARGIN
        return token

    def _api(self, token: str, *, with_org: bool = True) -> SourceHttp:
        headers = {"Authorization": f"Zoho-oauthtoken {token}"}
        if with_org and self._org_id:
            headers["orgId"] = self._org_id
        return SourceHttp(
            self._host, headers=headers, guard=self._guard, transport=self._transport,
            base_path="/api/v1", label=LABEL,
        )  # fmt: skip

    async def _client(self, *, with_org: bool = True, fresh: bool = False) -> SourceHttp:
        if fresh or self._token is None or _now() >= self._expires_at:
            await self._login()
        assert self._token is not None
        if with_org and not self._org_id:
            await self._find_org()
        return self._api(self._token, with_org=with_org)

    async def _get(
        self, path: str, params: Mapping[str, Any] | None = None, *, missing_ok: bool = False
    ) -> Any:
        """A GET with the token got on first use and when it has expired; one refused with
        a token that was not just got is tried once more with a new one."""
        was = self._token
        api = await self._client()
        try:
            return await api.get(path, params, missing_ok=missing_ok)
        except SourceError as exc:
            if not exc.permanent or self._token != was or was is None:
                raise
        api = await self._client(fresh=True)
        return await api.get(path, params, missing_ok=missing_ok)

    async def _find_org(self) -> None:
        api = await self._client(with_org=False)
        orgs = _data(await api.get("/organizations"))
        if not orgs:
            raise SourceError(f"{LABEL} shows no organisation for this account.", permanent=True)
        if len(orgs) > 1:
            names = ", ".join(f"{o.get('companyName')} ({o.get('id')})" for o in orgs[:5])
            raise SourceError(
                f"This {LABEL} account has several organisations: give the one to import "
                f"('org_id'): {names}.",
                permanent=True,
            )
        self._org_id = str(orgs[0].get("id"))
        self._org_name = _str(orgs[0].get("companyName"))

    # Interface

    def warnings(self) -> list[str]:
        return [
            "Zoho Desk's templates are whole email templates, not saved replies, so they "
            "aren't imported."
        ]

    async def check(self) -> AccountInfo:
        if self._org_id and self._org_name is None:
            api = await self._client(with_org=False)
            for org in _data(await api.get("/organizations")):
                if str(org.get("id")) == self._org_id:
                    self._org_name = _str(org.get("companyName"))
            if self._org_name is None:
                raise SourceError(f"{LABEL} has no organisation {self._org_id}.", permanent=True)
        else:
            await self._client()
        return AccountInfo(f"{self._org_name or self._org_id} ({self._host})")

    async def _count(self, path: str, count_path: str | None) -> int:
        if count_path is not None:
            found = await self._get(count_path, missing_ok=True)
            count = found.get("count") if isinstance(found, dict) else None
            if isinstance(count, int) and not isinstance(count, bool):
                return count
        total, start = 0, 0
        for _ in range(COUNT_PAGES_MAX):
            rows = _data(await self._get(path, {"from": start, "limit": LIMIT}))
            total += len(rows)
            nxt = _next(start, rows)
            if nxt is None:
                break
            start = nxt["from"]
        return total

    async def discover(self) -> Counts:
        return Counts(
            contacts=await self._count("/contacts", "/contacts/count"),
            tickets=await self._count("/tickets", "/ticketsCount"),
            articles=await self._count("/articles", None),
            replies=0,
            groups=len(await self.groups()),
        )

    async def groups(self) -> Sequence[Group]:
        if self._departments is None:
            rows = _data(await self._get("/departments", {"from": 0, "limit": LIMIT}))
            self._departments = [
                Group(str(r["id"]), str(r.get("name") or r["id"])) for r in rows if r.get("id")
            ]
        return list(self._departments)

    async def page(self, kind: str, cursor: Mapping[str, Any] | None) -> Page[Any]:
        start = int(cursor["from"]) if cursor and cursor.get("from") else 0
        if kind == "contacts":
            rows = _data(await self._get("/contacts", {"from": start, "limit": LIMIT}))
            contacts = [contact_from(r) for r in rows if r.get("id") is not None]
            return Page(contacts, _next(start, rows))
        if kind == "tickets":
            rows = _data(
                await self._get(
                    "/tickets",
                    {
                        "from": start,
                        "limit": LIMIT,
                        "sortBy": "createdTime",
                        "include": "contacts,assignee",
                    },
                )
            )
            items = [await self._ticket(r) for r in rows if r.get("id") is not None]
            return Page(items, _next(start, rows))
        if kind == "articles":
            rows = _data(await self._get("/articles", {"from": start, "limit": LIMIT}))
            found = [await self._article(r) for r in rows if r.get("id") is not None]
            return Page([a for a in found if a is not None], _next(start, rows))
        return Page([], None)  # replies: see the module docstring

    async def changed_tickets(
        self, since: datetime, cursor: Mapping[str, Any] | None
    ) -> Page[Ticket]:
        """Newest change first; paging stops at the first ticket changed before `since`."""
        start = int(cursor["from"]) if cursor and cursor.get("from") else 0
        rows = _data(
            await self._get(
                "/tickets",
                {
                    "from": start,
                    "limit": LIMIT,
                    "sortBy": "-modifiedTime",
                    "include": "contacts,assignee",
                },
            )
        )
        items: list[Ticket] = []
        nxt = _next(start, rows)
        for row in rows:
            changed = when(row.get("modifiedTime")) or when(row.get("createdTime"))
            if changed is not None and changed < since:
                nxt = None  # ordered by change time: everything after is older still
                break
            if row.get("id") is not None:
                items.append(await self._ticket(row))
        return Page(items, nxt)

    # Records

    async def _article(self, row: Mapping[str, Any]) -> Article | None:
        detail = await self._get(f"/articles/{segment(row['id'])}", missing_ok=True)
        source = detail if isinstance(detail, dict) else row
        body = _text(source.get("answer"))
        title = _str(source.get("title"))
        if not body or not title:
            return None
        category = source.get("category")
        return Article(
            str(row["id"]),
            title,
            body,
            Category(str(category["id"]), str(category.get("name") or category["id"]))
            if isinstance(category, dict) and category.get("id")
            else None,
            str(source.get("status") or "Published").lower() == "published",
        )

    @staticmethod
    def _files(raw: object) -> list[Attachment]:
        out: list[Attachment] = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            href = item.get("href")
            if not isinstance(href, str) or not href.lower().startswith("https://"):
                continue
            size = item.get("size")
            try:
                size_int = int(size) if size not in (None, "") else None
            except (TypeError, ValueError):
                size_int = None
            out.append(Attachment(_str(item.get("name")) or "file", href, size_int))
        return out

    async def fetch(self, attachment: Attachment, limit: int) -> bytes | None:
        """The token goes only to the Desk host; a file served elsewhere is read without it."""
        api = await self._client()
        return await api.download(attachment.url, limit)

    async def _paged(self, path: str) -> list[Mapping[str, Any]]:
        out: list[Mapping[str, Any]] = []
        start = 0
        for _ in range(DETAIL_PAGES_MAX):
            rows = _data(await self._get(path, {"from": start, "limit": LIMIT}))
            out += rows
            nxt = _next(start, rows)
            if nxt is None:
                break
            start = nxt["from"]
        return out

    async def _messages(self, tid: str) -> list[Message]:
        out: list[Message] = []
        for ref in await self._paged(f"/tickets/{segment(tid)}/threads"):
            if ref.get("id") is None:
                continue
            detail = await self._get(
                f"/tickets/{segment(tid)}/threads/{segment(ref['id'])}", missing_ok=True
            )
            row = detail if isinstance(detail, dict) else ref
            text = _text(row.get("content")) or rules.body(_str(ref.get("summary")))
            files = self._files(row.get("attachments"))
            if not text and not files:
                continue
            raw_author = row.get("author")
            author: Mapping[str, Any] = raw_author if isinstance(raw_author, dict) else {}
            kind = (
                "note"
                if str(row.get("visibility") or ref.get("visibility") or "").lower() == "private"
                else "customer"
                if str(row.get("direction") or ref.get("direction") or "").lower() == "in"
                else "agent"
            )
            out.append(
                Message(
                    kind=kind,
                    body=text or "(attachment only)",
                    created_at=when(row.get("createdTime") or ref.get("createdTime"))
                    or datetime.now(UTC),
                    external_id=f"thread-{ref['id']}",
                    author_name=_str(author.get("name")),
                    author_email=_str(author.get("email")),
                    attachments=files,
                )
            )
        for row in await self._paged(f"/tickets/{segment(tid)}/comments"):
            text = _text(row.get("content"))
            if row.get("id") is None or not text:
                continue
            raw_who = row.get("commenter")
            who: Mapping[str, Any] = raw_who if isinstance(raw_who, dict) else {}
            out.append(
                Message(
                    kind="agent" if row.get("isPublic") is True else "note",
                    body=text,
                    created_at=when(row.get("commentedTime")) or datetime.now(UTC),
                    external_id=f"comment-{row['id']}",
                    author_name=_str(who.get("name")),
                    author_email=_str(who.get("email")),
                )
            )
        out.sort(key=lambda m: m.created_at)
        return out

    async def _ticket(self, row: Mapping[str, Any]) -> Ticket:
        tid = str(row["id"])
        contact = row.get("contact")
        requester = (
            contact_from(contact)
            if isinstance(contact, dict) and contact.get("id") is not None
            else Contact(str(row["contactId"]), None, "Unknown")
            if row.get("contactId")
            else Contact(f"ticket-{tid}", None, "Unknown")
        )
        assignee = row.get("assignee")
        fields = row.get("customFields")
        return Ticket(
            external_id=tid,
            subject=rules.subject(row.get("subject")),
            status=str(row.get("statusType") or "Open").strip().lower(),
            created_at=when(row.get("createdTime")) or datetime.now(UTC),
            requester=requester,
            messages=await self._messages(tid),
            priority=_str(row.get("priority")),
            channel=_str(row.get("channel")),
            group_id=_str(str(row["departmentId"])) if row.get("departmentId") else None,
            assignee_email=_str(assignee.get("email")) if isinstance(assignee, dict) else None,
            fields={str(k): v for k, v in fields.items() if v not in (None, "")}
            if isinstance(fields, dict)
            else {},
            updated_at=when(row.get("modifiedTime")),
            resolved_at=when(row.get("closedTime")),
        )


def make(
    settings: Mapping[str, str],
    credentials: Mapping[str, str],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    guard: Guard | None = None,
) -> ZohoDeskConnector:
    """`settings`: `region` (us, eu, in, au, jp) and `org_id` (only for an account with
    several organisations). Raises `egress.BadAddress` for an unknown region."""
    return ZohoDeskConnector(
        settings.get("region", "").strip().lower(),
        credentials.get("client_id", ""),
        credentials.get("client_secret", ""),
        credentials.get("refresh_token", ""),
        (settings.get("org_id") or "").strip() or None,
        transport=transport,
        guard=guard,
    )
