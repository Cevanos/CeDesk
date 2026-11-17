"""The Zendesk Support importer (md section 8.2): reads tickets, end-users, Help Center
articles and macro saved replies over Zendesk's REST API, read only.

The host is `<subdomain>.zendesk.com` built by `cedesk.egress.account_host`.

Sign-in (C0 / P0-1). Zendesk stopped new API tokens on 27 Oct 2026 and turns every API token
off by 30 Apr 2027; the replacement is OAuth. Three ways are accepted, the first one whose
keys are given wins:

1. **OAuth client credentials** (`client_id` + `client_secret`): the recommended one. An
   Admin creates a *Confidential* OAuth client (Admin Center > Apps and integrations > APIs >
   OAuth clients > Add OAuth client); `client_id` is the client's *Identifier*, the secret is
   shown once on save. CeDesk POSTs `grant_type=client_credentials&scope=read` to
   `/oauth/tokens` on the account's host and sends the access token as `Authorization:
   Bearer`. The token acts as the Admin who created the client (it stops working if that
   person is deleted or downgraded). No refresh token is issued: when the token expires
   (`expires_in`, 1,800 s by default for clients made after 30 Apr 2026, or none for older
   ones) the same request is made again; it is fetched again before expiry and once more
   when a call answers 401/403 with a token that was not just got. Tokens are kept in this
   process (keyed by a hash of host, id and secret) so each worker pass does not mint a new one.
2. **OAuth access token** (`access_token`): a token the Admin made some other way (an
   authorization-code flow, the OAuth Tokens API). It is used as is, as `Bearer`. It can't be
   renewed here: a refresh token rotates on every use (the old one stops working), and
   CeDesk's sealed credentials can't be rewritten by a connector, so a refresh token is not
   taken. An expired token is a permanent "refused these credentials" error.
3. **API token** (`email` + `token`), for accounts that still have one: HTTP Basic
   `"{email}/token:{token}"`.

Endpoints (all GET but the token request):

- check: `/api/v2/users/me.json` (an end-user is refused: the credentials must be an agent's);
- discover: `/api/v2/tickets/count.json`, `/api/v2/users/count.json?role=end-user`,
  `/api/v2/help_center/articles.json?per_page=1` (its `count`), macros and `/api/v2/groups.json`;
- contacts: `/api/v2/users.json?role=end-user&include=organizations` (cursor pagination);
- tickets: the incremental export `/api/v2/incremental/tickets/cursor.json?include=organizations`
  (`start_time` on the first call, then `cursor`, until `end_of_stream`), because closed
  tickets older than 120 days are archived and absent from the normal list; per ticket
  `/api/v2/tickets/{id}/comments.json` (cursor pagination); requesters, assignees, comment
  authors, CCs and followers from `/api/v2/users/show_many.json?ids=&include_deleted=true`
  (batches of 100, cached for the life of the connector); field titles and dropdown option
  labels from `/api/v2/ticket_fields.json` (read once, cached);
- articles: `/api/v2/help_center/articles.json?include=sections,categories`;
- replies: `/api/v2/macros.json?active=true`, those with a `comment_value` action.

Decisions:
- tickets with status `deleted` are skipped (the export includes them);
- a comments request that answers 404 (archived or deleted since) keeps the ticket, with its
  description as its one customer message, noted `incomplete` (P0-2);
- a ticket's history is read up to `_HISTORY_PAGES` pages of 100 comments (1,000,000); past
  that the rest is not read, counted as `history_capped` and the ticket noted `incomplete`
  (C6). The small lists (groups, fields, macros) are read to their end;
- a ticket without a valid `created_at` takes `updated_at`, then its first comment's time,
  then the import time, and is noted (`rules.dated`, C9);
- a comment with files and no text is kept with an empty body (C2); one with neither is
  counted (`message_without_text`);
- `public: false` comments are notes; agent/admin authors are agent messages; end-users are
  customers. Deleted users are asked for with `include_deleted=true`, so they keep their role.
  A user Zendesk doesn't return at all (C14) is never made a customer silently: the ticket's
  requester is the customer; a public comment that came in by email is a customer's (with
  the email's `from` address); any other public comment is an agent's, named "Deleted user"
  (agents write in the agent workspace or by API; end-users on someone else's ticket write by
  email). UNVERIFIED against a live account with a permanently deleted agent;
- custom fields keep 0 and false (C5); a dropdown's or multi-select's stored tag is shown as
  its option's label (`custom_field_options` `value` -> `name`);
- CSAT: good -> 5, bad -> 1, offered/unoffered -> none;
- `resolved_at` is `updated_at` for solved/closed tickets (the export has no solved time);
- also kept: `type` -> `ticket_type`; `collaborator_ids` + `email_cc_ids` -> `cc` and
  `follower_ids` -> `followers` (as emails); `problem_id` -> a `parent` link (an incident's
  problem); `due_at` (a task's due date) -> `sla_due_at`; the agent URL
  `https://<host>/agent/tickets/<id>` -> `source_url`; user `phone`; the organization's name;
  an email comment's `via.source.to.address` (+ `from.original_recipients`) -> `to` and
  `via.source.to.email_ccs` -> `cc`.

Docs used:
- https://developer.zendesk.com/api-reference/ticketing/oauth/grant_type_tokens/ (`POST
  /oauth/tokens`, client_credentials for confidential clients, no refresh token; refresh
  tokens rotate; `expires_in` 300..172,800 s)
- https://developer.zendesk.com/documentation/authentication/api-tokens-to-oauth/
- https://support.zendesk.com/hc/en-us/articles/4408845965210 (creating an OAuth client;
  client-credentials tokens act on behalf of the admin who created the client)
- https://support.zendesk.com/hc/en-us/articles/10851263566234 (API token end of life)
- https://developer.zendesk.com/api-reference/ticketing/ticket-management/incremental_exports/
- https://developer.zendesk.com/documentation/api-basics/working-with-data/side_loading/
- https://developer.zendesk.com/api-reference/ticketing/tickets/tickets/
- https://developer.zendesk.com/api-reference/ticketing/tickets/ticket_comments/
- https://developer.zendesk.com/api-reference/ticketing/tickets/ticket_fields/
- https://developer.zendesk.com/documentation/ticketing/reference-guides/via-object-reference/
- https://developer.zendesk.com/api-reference/ticketing/users/users/ (`include_deleted`)
- https://developer.zendesk.com/api-reference/help_center/help-center-api/articles/
- https://developer.zendesk.com/api-reference/ticketing/business-rules/macros/

Not verified against a live account (UNVERIFIED): the `role=end-user` filter on
`users/count`; `include=sections,categories` with cursor pagination on articles;
`include=organizations` on the cursor ticket export and on `users.json` (the docs say ticket
exports take sideloads and list `organizations` as a ticket sideload; the top-level key
`organizations` is assumed); the shape of `via.source.to.email_ccs` (strings or objects with
`address`; both are read); whether `start_time=1` is accepted; the shape of `comment_value`
in macros; whether `content_url` needs the account's credentials; that a client-credentials
token is accepted on `content_url` downloads like an API token; the incremental export's
10 requests per minute.
"""

import hashlib
import re
import time
from collections.abc import Mapping, Sequence
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
from cedesk.helpdesk.service.imports.http import SourceHttp, basic, bearer, segment, when

LABEL = "Zendesk"
STATUS_MAP = {
    "new": "open", "open": "open", "pending": "pending", "hold": "pending",
    "solved": "resolved", "closed": "resolved",
}  # fmt: skip
_AGENT_ROLES = ("agent", "admin")
_UNKNOWN_ROLE = "unknown"  # a user Zendesk returns nothing for, even with include_deleted
DELETED_USER = "Deleted user"
_SIZE = rules.PAGE_MAX
_HISTORY_PAGES = 10_000  # comments pages of one ticket (x100): a safety limit, counted if hit
_LIST_PAGES = 100_000  # small lists are read to their end; this only stops a looping cursor
_BATCH = 100  # show_many takes at most 100 ids
_SKIPPED_STATUS = "deleted"
TOKEN_PATH = "/oauth/tokens"  # noqa: S105 (a URL path, not a secret)
TOKEN_SCOPE = "read"  # noqa: S105 (an OAuth scope name, not a secret)
TOKEN_MARGIN = 120  # seconds before expiry at which the token is fetched again
TOKEN_DEFAULT_LIFETIME = 1800.0  # when Zendesk gives no expires_in (older clients: no expiry)
WHERE = (
    "an OAuth client's id and secret (Admin Center > Apps and integrations > APIs > OAuth "
    "clients > Add OAuth client: Client kind Confidential, scope read; client_id is its "
    "Identifier, client_secret is shown once on save), or an OAuth access_token, or, while "
    "Zendesk still accepts them (until 30 Apr 2027), your email and an API token"
)

# Access tokens got with client credentials, kept for this process: sha256(host, id, secret)
# -> (token, monotonic time it is fetched again). Never logged.
_TOKENS: dict[str, tuple[str, float]] = {}


@dataclass(frozen=True)
class _User:
    name: str
    email: str | None
    role: str
    phone: str | None = None
    organization_id: int | None = None


@dataclass(frozen=True)
class _Field:
    title: str
    options: Mapping[str, str] = field(default_factory=dict)  # stored tag -> label


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


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _obj(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _address(value: Any) -> str | None:
    """An email address from `via.source` data: a string, or an object with `address`."""
    if isinstance(value, Mapping):
        value = value.get("address")
    if isinstance(value, str) and "@" in value:
        return value.strip().lower()
    return None


def _addresses(values: Any) -> list[str]:
    out: list[str] = []
    for item in values if isinstance(values, list) else []:
        found = _address(item)
        if found and found not in out:
            out.append(found)
    return out


def _now() -> float:
    return time.monotonic()


class _Auth:
    """How calls are signed: fixed headers (API token, given access token), or a token got
    with client credentials and got again when it expires or is refused."""

    def __init__(
        self,
        *,
        headers: Mapping[str, str] | None = None,
        client: tuple[str, str] | None = None,
    ) -> None:
        self._headers = dict(headers or {})
        self._client = client

    @property
    def renewable(self) -> bool:
        return self._client is not None

    def _key(self, host: str) -> str:
        assert self._client is not None
        cid, secret = self._client
        return hashlib.sha256(f"{host}\0{cid}\0{secret}".encode()).hexdigest()

    def cached(self, host: str) -> str | None:
        if self._client is None:
            return None
        found = _TOKENS.get(self._key(host))
        return found[0] if found and _now() < found[1] else None

    async def headers(self, token_http: SourceHttp, *, fresh: bool = False) -> dict[str, str]:
        if self._client is None:
            return dict(self._headers)
        host = token_http.host
        token = None if fresh else self.cached(host)
        if token is None:
            token = await self._fetch(token_http)
        return bearer(token)

    def forget(self, host: str) -> None:
        if self._client is not None:
            _TOKENS.pop(self._key(host), None)

    async def _fetch(self, token_http: SourceHttp) -> str:
        assert self._client is not None
        cid, secret = self._client
        refused = SourceError(
            f"{LABEL} refused this OAuth client (check the client id, which is the client's "
            "Identifier, and its secret; the client must be Confidential and allow the read "
            "scope, and the Admin who created it must still be an Admin).",
            permanent=True,
        )
        try:
            data = await token_http.exchange_token(
                TOKEN_PATH,
                {
                    "grant_type": "client_credentials",
                    "client_id": cid,
                    "client_secret": secret,
                    "scope": TOKEN_SCOPE,
                },
            )
        except SourceError as exc:
            if exc.permanent:
                raise refused from None
            raise
        token = data.get("access_token") if isinstance(data, Mapping) else None
        if not isinstance(token, str) or not token:
            raise refused
        try:
            lifetime = float(data.get("expires_in") or TOKEN_DEFAULT_LIFETIME)
        except (TypeError, ValueError):
            lifetime = TOKEN_DEFAULT_LIFETIME
        _TOKENS[self._key(token_http.host)] = (
            token,
            _now() + max(lifetime - TOKEN_MARGIN, lifetime / 2),
        )
        return token


class ZendeskConnector:
    source = "zendesk"
    status_map: Mapping[str, str] = STATUS_MAP

    def __init__(
        self,
        host: str,
        auth: _Auth,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        guard: Guard | None = None,
    ) -> None:
        self._host = host
        self._auth = auth
        self._transport = transport
        self._guard = guard
        self._token_http = SourceHttp(
            host, headers={}, guard=guard, transport=transport, label=LABEL
        )
        self._users: dict[int, _User] = {}
        self._orgs: dict[int, str] = {}
        self._fields: dict[int, _Field] | None = None
        self.failures = rules.RecordFailures()  # tickets that keep failing (P0-2)

    # Calls

    async def _api(self, *, fresh: bool = False) -> SourceHttp:
        headers = await self._auth.headers(self._token_http, fresh=fresh)
        return SourceHttp(
            self._host, headers=headers, guard=self._guard, transport=self._transport,
            label=LABEL,
        )  # fmt: skip

    async def _get(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        missing_ok: bool = False,
        record: bool = False,
    ) -> Any:
        """A GET. With client credentials, one refused (401/403) with a token that was not
        just got is tried once more with a new token (a record that is gone is not a
        refusal)."""
        had = self._auth.cached(self._host)
        api = await self._api()
        try:
            return await api.get(path, params, missing_ok=missing_ok, record=record)
        except SourceError as exc:
            if (
                not self._auth.renewable
                or had is None
                or not exc.permanent
                or isinstance(exc, RecordGone)
            ):
                raise
        self._auth.forget(self._host)
        api = await self._api(fresh=True)
        return await api.get(path, params, missing_ok=missing_ok, record=record)

    # Connection

    async def check(self) -> AccountInfo:
        me = (await self._get("/api/v2/users/me.json")).get("user") or {}
        if me.get("role") not in _AGENT_ROLES:
            raise SourceError(
                "These credentials belong to an end-user. Use an agent's or an Admin's.",
                permanent=True,
            )
        return AccountInfo(self._host)

    async def discover(self) -> Counts:
        tickets = await self._count("/api/v2/tickets/count.json", None)
        contacts = await self._count("/api/v2/users/count.json", {"role": "end-user"})
        articles = await self._get(
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
        data = await self._get(path, params)
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
        """Every row of a cursor-paginated list (the small ones: groups, fields, macros), to
        its end. Stops only if Zendesk hands back a cursor it already gave (a loop)."""
        out: list[Mapping[str, Any]] = []
        token: str | None = None
        seen: set[str] = set()
        for _ in range(_LIST_PAGES):
            data = await self._get(
                path, {**(params or {}), "page[size]": _SIZE, "page[after]": token}
            )
            out.extend(data.get(key) or [])
            token = _after(data)
            if token is None or token in seen:
                break
            seen.add(token)
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

    def _keep_orgs(self, data: Any) -> None:
        for org in (data.get("organizations") if isinstance(data, Mapping) else None) or []:
            oid = _int(org.get("id")) if isinstance(org, Mapping) else None
            if oid is not None and org.get("name"):
                self._orgs[oid] = str(org["name"])

    def _contact(self, external_id: str, user: _User | None, org_id: Any = None) -> Contact:
        oid = (user.organization_id if user else None) or _int(org_id)
        return Contact(
            external_id,
            user.email if user else None,
            (user.name if user else "") or "Unknown",
            phone=user.phone if user else None,
            organization=self._orgs.get(oid) if oid is not None else None,
        )

    async def _contacts(self, cursor: Mapping[str, Any] | None) -> Page[Contact]:
        data = await self._get(
            "/api/v2/users.json",
            {
                "role": "end-user",
                "include": "organizations",
                "page[size]": _SIZE,
                "page[after]": self._token(cursor),
            },
        )
        self._keep_orgs(data)
        items = []
        for raw in data.get("users") or []:
            user = self._remember(raw)
            items.append(self._contact(str(raw["id"]), user))
        token = _after(data)
        return Page(items, {"after": token} if token else None)

    async def _articles(self, cursor: Mapping[str, Any] | None) -> Page[Article]:
        data = await self._get(
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
        return await self._export({"start_time": 1}, cursor)

    async def changed_tickets(
        self, since: datetime, cursor: Mapping[str, Any] | None
    ) -> Page[Ticket]:
        """Tickets created or updated at or after `since`: the same incremental export,
        started at `since` (Unix seconds) instead of the beginning."""
        return await self._export({"start_time": int(since.timestamp())}, cursor)

    async def _export(
        self, first: Mapping[str, Any], cursor: Mapping[str, Any] | None
    ) -> Page[Ticket]:
        if cursor and cursor.get("cursor"):
            params: dict[str, Any] = {"cursor": str(cursor["cursor"])}
        else:
            params = dict(first)
        data = await self._get(
            "/api/v2/incremental/tickets/cursor.json",
            {**params, "per_page": _SIZE, "include": "organizations"},
        )
        self._keep_orgs(data)
        raw_tickets = [t for t in data.get("tickets") or [] if t.get("status") != _SKIPPED_STATUS]
        fields = await self._ticket_fields()
        comments: dict[int, list[Mapping[str, Any]]] = {}
        for t in raw_tickets:
            read = await self.failures.guard(
                "ticket", str(t["id"]), partial(self._comments, int(t["id"]))
            )
            if read is not None:
                comments[int(t["id"])] = read
        raw_tickets = [t for t in raw_tickets if int(t["id"]) in comments]
        ids: set[int] = set()
        for t in raw_tickets:
            ids.update(_people(t))
            ids.update(i for c in comments[int(t["id"])] if (i := _int(c.get("author_id"))))
        await self._resolve(ids)
        items = [self._ticket(t, comments[int(t["id"])], fields) for t in raw_tickets]
        after = data.get("after_cursor")
        done = bool(data.get("end_of_stream")) or not after
        return Page(items, None if done else {"cursor": str(after)})

    async def _comments(self, ticket_id: int) -> list[Mapping[str, Any]]:
        out: list[Mapping[str, Any]] = []
        token: str | None = None
        path = f"/api/v2/tickets/{segment(ticket_id)}/comments.json"
        for _ in range(_HISTORY_PAGES):
            try:
                data = await self._get(
                    path, {"page[size]": _SIZE, "page[after]": token}, record=True
                )
            except RecordGone:  # archived (or deleted since): the ticket is kept, noted
                self.failures.incomplete(
                    "ticket", str(ticket_id),
                    "Zendesk sent no comments for this ticket (404): only what was read "
                    "(or its description) was imported.",
                )  # fmt: skip
                break
            out.extend(data.get("comments") or [])
            token = _after(data)
            if token is None:
                break
        else:  # the safety limit was reached with more pages to read
            rules.loss("history_capped")
            self.failures.incomplete(
                "ticket", str(ticket_id),
                f"Zendesk ticket history passed {_HISTORY_PAGES * _SIZE:,} comments; the "
                "rest was not read.",
            )  # fmt: skip
        return out

    def _emails(self, ids: Any) -> list[str]:
        out: list[str] = []
        for raw in ids if isinstance(ids, list) else []:
            uid = _int(raw)
            user = self._users.get(uid) if uid is not None else None
            if user is not None and user.email and user.email not in out:
                out.append(user.email)
        return out

    def _ticket(
        self,
        raw: Mapping[str, Any],
        comments: Sequence[Mapping[str, Any]],
        fields: Mapping[int, _Field],
    ) -> Ticket:
        tid = str(raw["id"])
        first_comment = when(comments[0].get("created_at")) if comments else None
        created = rules.dated(
            self.failures, "ticket", tid,
            when(raw.get("created_at")), when(raw.get("updated_at")), first_comment,
        )  # fmt: skip
        updated = when(raw.get("updated_at"))
        raw_status = str(raw.get("status") or "")
        requester_id = _int(raw.get("requester_id"))
        requester = self._users.get(requester_id) if requester_id else None
        contact = self._contact(str(requester_id or 0), requester, raw.get("organization_id"))
        messages = [
            m for c in comments if (m := self._message(c, created, requester_id)) is not None
        ]
        if not messages and raw.get("description"):
            messages = [Message("customer", rules.body(raw["description"]), created)]
        assignee_id = _int(raw.get("assignee_id"))
        assignee = self._users.get(assignee_id) if assignee_id else None
        cc = self._emails(raw.get("collaborator_ids"))
        cc += [e for e in self._emails(raw.get("email_cc_ids")) if e not in cc]
        problem = _int(raw.get("problem_id"))
        return Ticket(
            external_id=tid,
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
            fields=self._field_values(raw.get("custom_fields"), fields),
            updated_at=updated,
            resolved_at=updated if rules.status(raw_status, STATUS_MAP) == "resolved" else None,
            csat=self._csat(raw.get("satisfaction_rating")),
            ticket_type=str(raw["type"]) if raw.get("type") else None,
            source_url=f"https://{self._host}/agent/tickets/{segment(tid)}",
            cc=cc,
            followers=self._emails(raw.get("follower_ids")),
            links=[Link("parent", str(problem))] if problem else [],
            sla_due_at=when(raw.get("due_at")),
        )

    @staticmethod
    def _field_values(raw: Any, fields: Mapping[int, _Field]) -> dict[str, Any]:
        """Custom field values under their titles. 0 and False are values (C5); only None
        and "" are left out. A dropdown's stored tag becomes its option's label."""
        out: dict[str, Any] = {}
        for cf in raw if isinstance(raw, list) else []:
            fid = _int(cf.get("id")) if isinstance(cf, Mapping) else None
            found = fields.get(fid) if fid is not None else None
            if found is None:
                continue
            value = cf.get("value")
            if isinstance(value, list):
                value = ", ".join(found.options.get(str(v), str(v)) for v in value)
            elif isinstance(value, str) and value in found.options:
                value = found.options[value]
            if value is None or value == "":
                continue
            out[found.title] = value
        return out

    def _author(
        self, raw: Mapping[str, Any], requester_id: int | None
    ) -> tuple[str, str | None, str | None]:
        """(kind for a public comment, author name, author email). See the module docstring
        for how a user Zendesk returned nothing for is placed (C14)."""
        author_id = _int(raw.get("author_id"))
        user = self._users.get(author_id) if author_id is not None else None
        if user is not None and user.role != _UNKNOWN_ROLE:
            kind = "agent" if user.role in _AGENT_ROLES else "customer"
            return kind, user.name or None, user.email
        via = _obj(raw.get("via"))
        sender = _address(_obj(via.get("source")).get("from"))
        if author_id is not None and author_id == requester_id:
            return "customer", None, sender
        if str(via.get("channel") or "") == "email":
            return "customer", None, sender
        return "agent", DELETED_USER, None

    def _message(
        self, raw: Mapping[str, Any], fallback: datetime, requester_id: int | None
    ) -> Message | None:
        at = rules.first_date(when(raw.get("created_at"))) or fallback
        text = rules.body(raw.get("plain_body") or raw.get("body"))
        attachments = self._attachments(raw.get("attachments"))
        if not text and not attachments:
            rules.loss("message_without_text")
            return None
        kind, name, email = self._author(raw, requester_id)
        if raw.get("public") is False:
            kind = "note"
        source = _obj(_obj(raw.get("via")).get("source"))
        to_part, from_part = _obj(source.get("to")), _obj(source.get("from"))
        to = [a for a in [_address(to_part.get("address"))] if a]
        to += [a for a in _addresses(from_part.get("original_recipients")) if a not in to]
        return Message(
            kind=kind,
            body=text,
            created_at=at,
            external_id=str(raw["id"]) if raw.get("id") is not None else None,
            author_name=name,
            author_email=email,
            attachments=attachments,
            to=to,
            cc=_addresses(to_part.get("email_ccs")),
        )

    @staticmethod
    def _attachments(raw: Any) -> list[Attachment]:
        out: list[Attachment] = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, Mapping):
                continue
            url = str(item.get("content_url") or "")
            if not url.startswith("https://"):
                continue
            size = item.get("size")
            out.append(
                Attachment(
                    name=str(item.get("file_name") or "file"),
                    url=url,
                    size=size if isinstance(size, int) and not isinstance(size, bool) else None,
                    mime=str(item["content_type"]) if item.get("content_type") else None,
                )
            )
        return out

    async def fetch(self, attachment: Attachment, limit: int) -> bytes | None:
        """The credentials go only to the account's host; a file served elsewhere is read
        without them (`SourceHttp.download`)."""
        return await (await self._api()).download(attachment.url, limit)

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
            phone=str(raw["phone"]).strip() if raw.get("phone") else None,
            organization_id=_int(raw.get("organization_id")),
        )
        self._users[int(raw["id"])] = user
        return user

    async def _resolve(self, ids: set[int]) -> None:
        """Fills the user cache for `ids` with batched `show_many` calls, deleted users
        included (`include_deleted=true`, so they keep their role). A user Zendesk returns
        nothing for is cached with the role `unknown` (never `end-user`, C14) so it is not
        asked for again."""
        missing = sorted(i for i in ids if i not in self._users)
        for start in range(0, len(missing), _BATCH):
            batch = missing[start : start + _BATCH]
            data = await self._get(
                "/api/v2/users/show_many.json",
                {"ids": ",".join(str(i) for i in batch), "include_deleted": "true"},
            )
            for raw in data.get("users") or []:
                self._remember(raw)
            for i in batch:
                self._users.setdefault(i, _User("", None, _UNKNOWN_ROLE))

    async def _ticket_fields(self) -> dict[int, _Field]:
        if self._fields is None:
            found: dict[int, _Field] = {}
            for f in await self._list("/api/v2/ticket_fields.json", "ticket_fields"):
                options = {
                    str(o["value"]): str(o.get("name") or o["value"])
                    for o in f.get("custom_field_options") or []
                    if isinstance(o, Mapping) and o.get("value") is not None
                }
                found[int(f["id"])] = _Field(
                    str(f.get("title") or f.get("raw_title") or f["id"]), options
                )
            self._fields = found
        return self._fields


def _people(raw: Mapping[str, Any]) -> set[int]:
    """Every user id a ticket names: requester, assignee, CCs, followers."""
    out: set[int] = set()
    for key in ("requester_id", "assignee_id"):
        if uid := _int(raw.get(key)):
            out.add(uid)
    for key in ("collaborator_ids", "email_cc_ids", "follower_ids"):
        values = raw.get(key)
        out.update(i for v in (values if isinstance(values, list) else []) if (i := _int(v)))
    return out


def auth_from(credentials: Mapping[str, str]) -> _Auth:
    """The sign-in the Admin gave (see the module docstring). Raises a permanent
    `SourceError` naming what is missing; the message never holds a credential."""

    def missing(*keys: str) -> SourceError:
        return SourceError(f"{LABEL} needs {WHERE}: missing {', '.join(keys)}.", permanent=True)

    def has(key: str) -> bool:
        return bool((credentials.get(key) or "").strip())

    if has("client_id") or has("client_secret"):
        lacking = [k for k in ("client_id", "client_secret") if not has(k)]
        if lacking:
            raise missing(*lacking)
        return _Auth(client=(credentials["client_id"].strip(), credentials["client_secret"]))
    if has("access_token"):
        return _Auth(headers=bearer(credentials["access_token"].strip()))
    if has("email") or has("token"):
        lacking = [k for k in ("email", "token") if not has(k)]
        if lacking:
            raise missing(*lacking)
        return _Auth(headers=basic(f"{credentials['email'].strip()}/token", credentials["token"]))
    raise missing("client_id", "client_secret")


def make(
    settings: Mapping[str, str],
    credentials: Mapping[str, str],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    guard: Guard | None = None,
) -> SourceConnector:
    """Raises `egress.BadAddress` for a subdomain that isn't a plain account name and a
    permanent `SourceError` when no complete sign-in is given."""
    host = egress.account_host("zendesk", settings.get("subdomain", ""))
    return ZendeskConnector(host, auth_from(credentials), transport=transport, guard=guard)
