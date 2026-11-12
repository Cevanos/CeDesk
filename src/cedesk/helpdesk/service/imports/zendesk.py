"""The Zendesk Support importer (md section 8.2): reads tickets, end-users, Help Center
articles and macro saved replies over Zendesk's REST API, read only.

Auth is an API token: HTTP Basic `"{email}/token:{token}"`. The host is `<subdomain>.zendesk.com`
built by `cedesk.egress.account_host`.

Endpoints (all GET):

- check: `/api/v2/users/me.json` (an end-user is refused: the token must be an agent's);
- discover: `/api/v2/tickets/count.json`, `/api/v2/users/count.json?role=end-user`,
  `/api/v2/help_center/articles.json?per_page=1` (its `count`), macros and `/api/v2/groups.json`;
- contacts: `/api/v2/users.json?role=end-user` (cursor pagination);
- tickets: the incremental export `/api/v2/incremental/tickets/cursor.json` (`start_time` on the
  first call, then `cursor`, until `end_of_stream`), because closed tickets older than 120 days
  are archived and absent from the normal list; per ticket `/api/v2/tickets/{id}/comments.json`
  (cursor pagination), authors/requesters/assignees from `/api/v2/users/show_many.json?ids=`
  (batches of 100, cached for the life of the connector), field titles from
  `/api/v2/ticket_fields.json` (read once, cached);
- articles: `/api/v2/help_center/articles.json?include=sections,categories`;
- replies: `/api/v2/macros.json?active=true`, those with a `comment_value` action.

Decisions: tickets with status `deleted` are skipped (the export includes them). A comments
request that answers 404 (an archived ticket the comments endpoint no longer serves) is treated
as "no comments" (`missing_ok=True`), and the ticket's description is used as its one customer
message so the ticket is not empty. `public: false` comments are notes; agent/admin authors are
agent messages, everyone else customer. CSAT: good -> 5, bad -> 1, offered/unoffered -> none.
`resolved_at` is `updated_at` for solved/closed tickets (the export has no solved time).

Docs used:
- https://developer.zendesk.com/api-reference/ticketing/ticket-management/incremental_exports/
- https://developer.zendesk.com/api-reference/ticketing/tickets/ticket_comments/
- https://developer.zendesk.com/api-reference/ticketing/users/users/
- https://developer.zendesk.com/api-reference/help_center/help-center-api/articles/
- https://developer.zendesk.com/api-reference/ticketing/business-rules/macros/

Not verified against a live account. Unsure of: the `role=end-user` filter on `users/count`;
`include=sections,categories` combined with cursor pagination on articles; whether comments
cursor responses always carry `meta.has_more`/`meta.after_cursor` (the legacy `next_page` is
followed too); whether `start_time=1` is accepted for "from the beginning"; the exact shape of
`comment_value` in macros (a string, or a list whose last item is the text; both are read);
that the incremental export is limited to 10 requests per minute (each page is 100 tickets).
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from cedesk import egress
from cedesk.egress import Guard
from cedesk.helpdesk.domain import imports as rules
from cedesk.helpdesk.domain.imports import (
    AccountInfo,
    Article,
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
from cedesk.helpdesk.service.imports.http import SourceHttp, basic, segment, when

STATUS_MAP = {
    "new": "open", "open": "open", "pending": "pending", "hold": "pending",
    "solved": "resolved", "closed": "resolved",
}  # fmt: skip
_AGENT_ROLES = ("agent", "admin")
_SIZE = rules.PAGE_MAX
_MAX_PAGES = 100  # a bound on loops that follow a source's own cursor
_BATCH = 100  # show_many takes at most 100 ids
_SKIPPED_STATUS = "deleted"


@dataclass(frozen=True)
class _User:
    name: str
    email: str | None
    role: str


def _after(data: Any) -> str | None:
    """The cursor of the next page of a cursor-paginated list, or None on the last page."""
    meta = data.get("meta") if isinstance(data, Mapping) else None
    if isinstance(meta, Mapping) and meta.get("has_more"):
        token = meta.get("after_cursor")
        return str(token) if token else None
    return None


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "reply"


def _text(value: Any) -> str:
    """A macro's comment text: a string, or a list whose last item is the text."""
    if isinstance(value, (list, tuple)):
        value = value[-1] if value else ""
    return str(value or "")


def _plain(html: str) -> str:
    return re.sub(r"[ \t]+", " ", re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>|</p>", "\n", html)))


def _channel(raw: str | None) -> str:
    low = (raw or "").lower()
    if low.startswith(("sunshine_conversations", "native_messaging")):
        return "chat"
    if low in ("web_widget", "help_center", "mobile", "mobile_sdk"):
        return "web"
    return rules.channel(low)


class ZendeskConnector:
    source = "zendesk"
    status_map: Mapping[str, str] = STATUS_MAP

    def __init__(self, http: SourceHttp) -> None:
        self._http = http
        self._users: dict[int, _User] = {}
        self._field_titles: dict[int, str] | None = None

    # Connection

    async def check(self) -> AccountInfo:
        me = (await self._http.get("/api/v2/users/me.json")).get("user") or {}
        if me.get("role") not in _AGENT_ROLES:
            raise SourceError(
                "These credentials belong to an end-user. Use an agent's email and API token.",
                permanent=True,
            )
        return AccountInfo(self._http.host)

    async def discover(self) -> Counts:
        tickets = await self._count("/api/v2/tickets/count.json", None)
        contacts = await self._count("/api/v2/users/count.json", {"role": "end-user"})
        articles = await self._http.get(
            "/api/v2/help_center/articles.json", {"per_page": 1}, missing_ok=True
        )
        found = articles.get("count") if isinstance(articles, Mapping) else 0
        return Counts(
            contacts=contacts,
            tickets=tickets,
            articles=int(found or 0),
            replies=len(await self._macros()),
            groups=len(await self.groups()),
        )

    async def _count(self, path: str, params: Mapping[str, Any] | None) -> int:
        data = await self._http.get(path, params)
        count = data.get("count") if isinstance(data, Mapping) else None
        value = count.get("value") if isinstance(count, Mapping) else count
        return int(value or 0)

    async def groups(self) -> Sequence[Group]:
        out: list[Group] = []
        for item in await self._list("/api/v2/groups.json", "groups"):
            out.append(Group(str(item["id"]), str(item.get("name") or item["id"])))
        return out

    async def _list(
        self, path: str, key: str, params: Mapping[str, Any] | None = None
    ) -> list[Mapping[str, Any]]:
        """Every row of a cursor-paginated list (for the small ones: groups, fields, macros)."""
        out: list[Mapping[str, Any]] = []
        token: str | None = None
        for _ in range(_MAX_PAGES):
            data = await self._http.get(
                path, {**(params or {}), "page[size]": _SIZE, "page[after]": token}
            )
            out.extend(data.get(key) or [])
            token = _after(data)
            if token is None:
                break
        return out

    # Pages

    async def page(self, kind: str, cursor: Mapping[str, Any] | None) -> Page[Any]:
        if kind == "contacts":
            return await self._contacts(cursor)
        if kind == "tickets":
            return await self._tickets(cursor)
        if kind == "articles":
            return await self._articles(cursor)
        if kind == "replies":
            return await self._replies(cursor)
        return Page([], None)

    @staticmethod
    def _token(cursor: Mapping[str, Any] | None) -> str | None:
        return str(cursor["after"]) if cursor and cursor.get("after") else None

    async def _contacts(self, cursor: Mapping[str, Any] | None) -> Page[Contact]:
        data = await self._http.get(
            "/api/v2/users.json",
            {"role": "end-user", "page[size]": _SIZE, "page[after]": self._token(cursor)},
        )
        items = []
        for raw in data.get("users") or []:
            user = self._remember(raw)
            items.append(Contact(str(raw["id"]), user.email, user.name))
        token = _after(data)
        return Page(items, {"after": token} if token else None)

    async def _articles(self, cursor: Mapping[str, Any] | None) -> Page[Article]:
        data = await self._http.get(
            "/api/v2/help_center/articles.json",
            {
                "include": "sections,categories",
                "page[size]": _SIZE,
                "page[after]": self._token(cursor),
            },
            missing_ok=True,
        )
        if not isinstance(data, Mapping):
            return Page([], None)  # no Help Center on this account
        categories = {c["id"]: str(c.get("name") or "") for c in data.get("categories") or []}
        sections = {s["id"]: s for s in data.get("sections") or []}
        items = []
        for raw in data.get("articles") or []:
            section = sections.get(raw.get("section_id"))
            category: Category | None = None
            if section is not None:
                cat_id = section.get("category_id")
                if cat_id in categories:
                    category = Category(str(cat_id), categories[cat_id])
                else:
                    category = Category(str(section["id"]), str(section.get("name") or ""))
            items.append(
                Article(
                    external_id=str(raw["id"]),
                    title=rules.subject(raw.get("title")),
                    body=rules.body(raw.get("body")),
                    category=category,
                    published=not raw.get("draft", False),
                )
            )
        token = _after(data)
        return Page(items, {"after": token} if token else None)

    async def _macros(self) -> list[Reply]:
        out: list[Reply] = []
        for macro in await self._list("/api/v2/macros.json", "macros", {"active": "true"}):
            text = ""
            for action in macro.get("actions") or []:
                if action.get("field") == "comment_value":
                    text = _text(action.get("value"))
                elif action.get("field") == "comment_value_html" and not text:
                    text = _plain(_text(action.get("value")))
            if text.strip():
                title = str(macro.get("title") or macro["id"])
                out.append(Reply(str(macro["id"]), _slug(title), rules.body(text)))
        return out

    async def _replies(self, cursor: Mapping[str, Any] | None) -> Page[Reply]:
        # Macros are few and need filtering client side, so they are read together and cut
        # into pages by offset.
        items = await self._macros()
        start = int(cursor["offset"]) if cursor else 0
        chunk = items[start : start + _SIZE]
        end = start + len(chunk)
        return Page(chunk, {"offset": end} if end < len(items) else None)

    # Tickets

    async def _tickets(self, cursor: Mapping[str, Any] | None) -> Page[Ticket]:
        """One page of the incremental export; the cursor is `{"cursor": <after_cursor>}`,
        None once `end_of_stream` is true."""
        if cursor and cursor.get("cursor"):
            params: dict[str, Any] = {"cursor": str(cursor["cursor"])}
        else:
            params = {"start_time": 1}
        data = await self._http.get(
            "/api/v2/incremental/tickets/cursor.json", {**params, "per_page": _SIZE}
        )
        raw_tickets = [t for t in data.get("tickets") or [] if t.get("status") != _SKIPPED_STATUS]
        titles = await self._titles()
        comments = {int(t["id"]): await self._comments(int(t["id"])) for t in raw_tickets}
        ids: set[int] = set()
        for t in raw_tickets:
            ids.update(i for i in (t.get("requester_id"), t.get("assignee_id")) if i)
            ids.update(int(c["author_id"]) for c in comments[int(t["id"])] if c.get("author_id"))
        await self._resolve(ids)
        items = [self._ticket(t, comments[int(t["id"])], titles) for t in raw_tickets]
        after = data.get("after_cursor")
        done = bool(data.get("end_of_stream")) or not after
        return Page(items, None if done else {"cursor": str(after)})

    async def _comments(self, ticket_id: int) -> list[Mapping[str, Any]]:
        out: list[Mapping[str, Any]] = []
        token: str | None = None
        path = f"/api/v2/tickets/{segment(ticket_id)}/comments.json"
        for _ in range(_MAX_PAGES):
            data = await self._http.get(
                path, {"page[size]": _SIZE, "page[after]": token}, missing_ok=True
            )
            if data is None:
                break  # archived (or gone): no comments
            out.extend(data.get("comments") or [])
            token = _after(data)
            if token is None:
                break
        return out

    def _ticket(
        self,
        raw: Mapping[str, Any],
        comments: Sequence[Mapping[str, Any]],
        titles: Mapping[int, str],
    ) -> Ticket:
        created = when(raw.get("created_at"))
        if created is None:
            raise SourceError(f"Ticket {raw.get('id')} has no created_at.", permanent=False)
        updated = when(raw.get("updated_at"))
        raw_status = str(raw.get("status") or "")
        requester_id = raw.get("requester_id")
        requester = self._users.get(int(requester_id)) if requester_id else None
        contact = Contact(
            str(requester_id or 0),
            requester.email if requester else None,
            (requester.name if requester else "") or "Unknown",
        )
        messages = [m for c in comments if (m := self._message(c)) is not None]
        if not messages and raw.get("description"):
            messages = [Message("customer", rules.body(raw["description"]), created)]
        assignee = self._users.get(int(raw["assignee_id"])) if raw.get("assignee_id") else None
        fields: dict[str, Any] = {}
        for cf in raw.get("custom_fields") or []:
            title = titles.get(int(cf["id"]))
            value = cf.get("value")
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value)
            if title and value not in (None, "", False):
                fields[title] = value
        return Ticket(
            external_id=str(raw["id"]),
            subject=rules.subject(raw.get("subject") or raw.get("raw_subject")),
            status=raw_status,
            created_at=created,
            requester=contact,
            messages=messages,
            priority=raw.get("priority"),
            channel=_channel((raw.get("via") or {}).get("channel")),
            group_id=str(raw["group_id"]) if raw.get("group_id") else None,
            assignee_email=assignee.email if assignee else None,
            tags=[str(t) for t in raw.get("tags") or []],
            fields=fields,
            updated_at=updated,
            resolved_at=updated if rules.status(raw_status, STATUS_MAP) == "resolved" else None,
            csat=self._csat(raw.get("satisfaction_rating")),
        )

    def _message(self, raw: Mapping[str, Any]) -> Message | None:
        at = when(raw.get("created_at"))
        text = rules.body(raw.get("plain_body") or raw.get("body"))
        if at is None or not text:
            return None
        author = self._users.get(int(raw["author_id"])) if raw.get("author_id") else None
        if raw.get("public") is False:
            kind = "note"
        elif author is not None and author.role in _AGENT_ROLES:
            kind = "agent"
        else:
            kind = "customer"
        return Message(
            kind=kind,
            body=text,
            created_at=at,
            external_id=str(raw["id"]) if raw.get("id") is not None else None,
            author_name=(author.name or None) if author else None,
            author_email=author.email if author else None,
        )

    @staticmethod
    def _csat(raw: Any) -> Csat | None:
        if not isinstance(raw, Mapping):
            return None
        score = str(raw.get("score") or "")
        if score.startswith("good"):
            return Csat(5, raw.get("comment") or None)
        if score.startswith("bad"):
            return Csat(1, raw.get("comment") or None)
        return None  # offered / unoffered

    # Users and fields

    def _remember(self, raw: Mapping[str, Any]) -> _User:
        user = _User(
            str(raw.get("name") or ""),
            (str(raw["email"]).strip().lower() if raw.get("email") else None),
            str(raw.get("role") or "end-user"),
        )
        self._users[int(raw["id"])] = user
        return user

    async def _resolve(self, ids: set[int]) -> None:
        """Fills the user cache for `ids` with batched `show_many` calls. A user Zendesk no
        longer returns (deleted) is cached as unknown so it is not asked for again."""
        missing = sorted(i for i in ids if i not in self._users)
        for start in range(0, len(missing), _BATCH):
            batch = missing[start : start + _BATCH]
            data = await self._http.get(
                "/api/v2/users/show_many.json", {"ids": ",".join(str(i) for i in batch)}
            )
            for raw in data.get("users") or []:
                self._remember(raw)
            for i in batch:
                self._users.setdefault(i, _User("", None, "end-user"))

    async def _titles(self) -> dict[int, str]:
        if self._field_titles is None:
            fields = await self._list("/api/v2/ticket_fields.json", "ticket_fields")
            self._field_titles = {
                int(f["id"]): str(f.get("title") or f.get("raw_title") or f["id"]) for f in fields
            }
        return self._field_titles


def make(
    settings: Mapping[str, str],
    credentials: Mapping[str, str],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    guard: Guard | None = None,
) -> SourceConnector:
    """Raises `egress.BadAddress` for a subdomain that isn't a plain account name."""
    host = egress.account_host("zendesk", settings.get("subdomain", ""))
    http = SourceHttp(
        host,
        headers=basic(f"{credentials['email']}/token", credentials["token"]),
        guard=guard,
        transport=transport,
        label="Zendesk",
    )
    return ZendeskConnector(http)
