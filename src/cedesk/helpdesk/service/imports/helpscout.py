"""The Help Scout importer: tickets, contacts, saved replies and (with a Docs key) articles.

Two Help Scout APIs are read, both GET only (the token exchange is the one allowed POST):

- Mailbox API 2.0 (`api.helpscout.net`, OAuth2 **client credentials**): the credentials are an
  app id and secret (`app_id`, `app_secret`; Your Profile > My Apps). `POST /v2/oauth2/token`
  with `grant_type=client_credentials`, `client_id`, `client_secret` gives an access token
  (`expires_in` 172800 s = 2 days, no refresh token). It is fetched on the first call, again
  when it is about to expire, and once more when a call is refused (401 = expired). It rides
  as `Authorization: Bearer`. The token and secret are never put in an error message.
- Docs API (`docsapi.helpscout.net`, Basic auth, the Docs API key as user): only when the
  optional credential `docs_key` is given; without it articles are skipped (`warnings()`).

Endpoints: `GET /v2/users/me` (check), `/v2/mailboxes` (groups), `/v2/customers` (contacts),
`/v2/conversations?status=all&sortField=modifiedAt&sortOrder=asc&page=N` and
`/v2/conversations/{id}/threads?page=N` (tickets), `/v2/mailboxes/{id}/saved-replies` and
`/v2/mailboxes/{id}/saved-replies/{replyId}` (replies: the list holds only a preview, so the
body is read per reply), Docs `/v1/collections`, `/v1/collections/{id}/articles`,
`/v1/collections/{id}/categories`, `/v1/articles/{id}` (articles).

Mapping: `status` active/open -> open, pending -> pending, closed -> resolved; **spam
conversations are not imported** (counted in `warnings()`). A mailbox is a group. Thread
`customer` -> customer; `note` -> note; any other thread with a body is agent when
`createdBy.type == "user"`, else customer; `lineitem` (status/assignee changes) and drafts
are skipped. HTML is reduced to plain text. No satisfaction rating: the conversation list
does not carry one (ratings are a separate endpoint, not read).

Attachments: a thread holds `_embedded.attachments: [{id, filename, mimeType, size, _links}]`.
`Attachment.url` is the API path `/v2/conversations/{cid}/threads/{tid}/attachments/{aid}/data`
(built from the ids, not taken from `_links`); `fetch` reads it with the authenticated `get`
(it answers JSON `{"data": "<base64>"}`), decodes it, and returns None when missing (404),
undecodable, or over the limit (`size` is checked before the call). Changes since a time:
`changed_tickets` adds `modifiedSince=<ISO 8601 UTC>` to the same conversations list.

Docs: https://developer.helpscout.com/mailbox-api/overview/authentication/ ,
/mailbox-api/endpoints/conversations/attachments/ (attachment data) ,
/mailbox-api/endpoints/conversations/list/ , /mailbox-api/endpoints/conversations/threads/list/ ,
/mailbox-api/endpoints/customers/list/ , /mailbox-api/endpoints/inboxes/saved_Replies/
saved-replies-list/ and -get/ , /mailbox-api/overview/rate-limiting/ ,
/docs-api/articles/list/ , /docs-api/articles/get/ , /docs-api/collections/list/

Not verified against a live account. Unsure of: the exact shape of `_embedded.emails` in the
customer list (also handled when absent); the Docs `/v1/collections/{id}/categories` path and
`categories.items` shape; the Docs auth form (key as Basic user, password "X"); whether
`status=all` includes spam; the thread `page` parameter (assumed 1-based like the others);
the exact doc page of the attachment data endpoint and that `_links.data.href` matches the
built path; whether `modifiedSince` is inclusive and combines with `sortField=modifiedAt`
(the list `userUpdatedAt` vs `modifiedAt` distinction); the thread `_embedded.attachments`
key on every thread type; the saved-reply list may be a bare array or
`_embedded.savedReplies` (both read). Help Scout sends `X-RateLimit-Retry-After` on a
429 (the shared `retry_after` reads it).
"""

import base64
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any, ClassVar

import httpx

from cedesk.egress import Guard, fixed_host
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
    Reply,
    SourceError,
    Ticket,
)
from cedesk.helpdesk.service.imports.http import SourceHttp, basic, bearer, segment, when

LABEL = "Help Scout"
TOKEN_PATH = "/v2/oauth2/token"  # noqa: S105 (a URL path, not a secret)
TOKEN_MARGIN = 300  # seconds before expiry at which the token is fetched again
THREAD_PAGES_MAX = 40  # a conversation longer than this many thread pages is cut
REPLIES_PER_PAGE = 50  # each reply costs one GET (the list has only a preview)
ARTICLES_PER_PAGE = 50
STATUS_MAP: Mapping[str, str] = {
    "active": "open",
    "open": "open",
    "pending": "pending",
    "closed": "resolved",
}
DOCS_KEY_WARNING = "Add a Docs API key to import help articles."


def _now() -> float:
    return time.monotonic()


class _Text(HTMLParser):
    _BREAKS: ClassVar[set[str]] = {"br", "p", "div", "h1", "h2", "h3", "h4", "blockquote"}
    _STARTS: ClassVar[set[str]] = {"li", "tr"}  # a line break before, none after

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self._BREAKS or tag in self._STARTS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in self._BREAKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def plain_text(raw: str | None) -> str:
    """HTML as plain text: tags dropped, block ends become line breaks, entities decoded,
    runs of blank lines collapsed."""
    if not raw:
        return ""
    parser = _Text()
    parser.feed(raw)
    parser.close()
    lines = [" ".join(line.split()) for line in unescape("".join(parser.parts)).split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def _name(first: Any, last: Any) -> str:
    return " ".join(str(p).strip() for p in (first, last) if p and str(p).strip())


def _items(data: Any, key: str) -> list[Any]:
    """The list under `_embedded.<key>` of a HAL answer (a bare list is returned as is)."""
    if isinstance(data, list):
        return data
    embedded = data.get("_embedded") if isinstance(data, dict) else None
    found = embedded.get(key) if isinstance(embedded, dict) else None
    return found if isinstance(found, list) else []


def _total_pages(data: Any) -> int:
    page = data.get("page") if isinstance(data, dict) else None
    try:
        return int(page.get("totalPages", 1)) if isinstance(page, dict) else 1
    except (TypeError, ValueError):
        return 1


def _total(data: Any) -> int:
    page = data.get("page") if isinstance(data, dict) else None
    try:
        return int(page.get("totalElements", 0)) if isinstance(page, dict) else 0
    except (TypeError, ValueError):
        return 0


def _page_no(cursor: Mapping[str, Any] | None, key: str = "page") -> int:
    try:
        return max(1, int(cursor[key])) if cursor else 1
    except (KeyError, TypeError, ValueError):
        return 1


class HelpScoutConnector:
    source = "helpscout"
    status_map: Mapping[str, str] = STATUS_MAP

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        docs_key: str | None,
        *,
        transport: httpx.AsyncBaseTransport | None,
        guard: Guard | None,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._transport = transport
        self._guard = guard
        self._api_host = fixed_host("helpscout", "api")
        self._token_http = SourceHttp(
            self._api_host, headers={}, guard=guard, transport=transport, label=LABEL
        )
        self._api: SourceHttp | None = None
        self._expires_at = 0.0
        self._docs: SourceHttp | None = None
        if docs_key:
            self._docs = SourceHttp(
                fixed_host("helpscout", "docs"),
                headers=basic(docs_key, "X"),
                guard=guard,
                transport=transport,
                label=f"{LABEL} Docs",
            )
        self.spam_skipped = 0
        self._mailboxes: list[Group] | None = None
        self._collections: list[tuple[str, str]] | None = None
        self._categories: dict[str, dict[str, str]] = {}

    # Token

    async def _login(self) -> SourceHttp:
        """Exchanges the app id and secret for an access token and returns a client that
        sends it. Nothing here puts the secret or the token in a message."""
        data = await self._token_http.exchange_token(
            TOKEN_PATH,
            {
                "grant_type": "client_credentials",
                "client_id": self._app_id,
                "client_secret": self._app_secret,
            },
        )
        token = data.get("access_token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise SourceError(f"{LABEL} sent no access token.", permanent=True)
        try:
            lifetime = float(data.get("expires_in", 172800))
        except (TypeError, ValueError):
            lifetime = 172800.0
        self._expires_at = _now() + lifetime - TOKEN_MARGIN
        self._api = SourceHttp(
            self._api_host,
            headers=bearer(token),
            guard=self._guard,
            transport=self._transport,
            label=LABEL,
        )
        return self._api

    async def _get(
        self, path: str, params: Mapping[str, Any] | None = None, *, missing_ok: bool = False
    ) -> Any:
        """A Mailbox API GET. The token is got on first use and when it has expired; a call
        refused with a token that was not just got is retried once with a new one."""
        fresh = False
        api = self._api
        if api is None or _now() >= self._expires_at:
            api = await self._login()
            fresh = True
        try:
            return await api.get(path, params, missing_ok=missing_ok)
        except SourceError as exc:
            if not exc.permanent or fresh:
                raise
        api = await self._login()
        return await api.get(path, params, missing_ok=missing_ok)

    async def _docs_get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        assert self._docs is not None
        return await self._docs.get(path, params)

    # Interface

    def warnings(self) -> list[str]:
        out = ["Spam conversations are not imported."]
        if self._docs is None:
            out.append(DOCS_KEY_WARNING)
        return out

    async def check(self) -> AccountInfo:
        me = await self._get("/v2/users/me")
        email = str(me.get("email", "")).strip() if isinstance(me, dict) else ""
        return AccountInfo(f"{LABEL} ({email})" if email else LABEL)

    async def groups(self) -> Sequence[Group]:
        if self._mailboxes is None:
            found: list[Group] = []
            number, pages = 1, 1
            while number <= pages:
                data = await self._get("/v2/mailboxes", {"page": number})
                pages = _total_pages(data)
                for box in _items(data, "mailboxes"):
                    if isinstance(box, dict) and box.get("id") is not None:
                        found.append(Group(str(box["id"]), str(box.get("name") or box["id"])))
                number += 1
            self._mailboxes = found
        return list(self._mailboxes)

    async def discover(self) -> Counts:
        conversations = _total(await self._get("/v2/conversations", {"status": "all", "page": 1}))
        customers = _total(await self._get("/v2/customers", {"page": 1}))
        mailboxes = await self.groups()
        replies = 0
        for box in mailboxes:
            replies += len(_items(await self._saved_replies(box.external_id), "savedReplies"))
        articles = 0
        if self._docs is not None:
            for collection_id, _ in await self._collection_list():
                data = await self._docs_get(
                    f"/v1/collections/{segment(collection_id)}/articles", {"pageSize": 1}
                )
                articles += int(_docs_block(data, "articles").get("count", 0) or 0)
        return Counts(
            contacts=customers,
            tickets=conversations,
            articles=articles,
            replies=replies,
            groups=len(mailboxes),
        )

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

    # Contacts

    async def _contacts(self, cursor: Mapping[str, Any] | None) -> Page[Contact]:
        number = _page_no(cursor)
        data = await self._get(
            "/v2/customers", {"sortField": "modifiedAt", "sortOrder": "asc", "page": number}
        )
        out: list[Contact] = []
        for raw in _items(data, "customers")[: rules.PAGE_MAX]:
            if isinstance(raw, dict) and raw.get("id") is not None:
                email = _first_email(raw)
                name = _name(raw.get("firstName"), raw.get("lastName")) or email
                out.append(Contact(str(raw["id"]), email, name or f"Customer {raw['id']}"))
        more = number < _total_pages(data) and bool(out)
        return Page(out, {"page": number + 1} if more else None)

    # Tickets

    async def changed_tickets(
        self, since: datetime, cursor: Mapping[str, Any] | None
    ) -> Page[Ticket]:
        """Conversations modified at or after `since` (`modifiedSince`, ISO 8601 UTC)."""
        stamp = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return await self._tickets(cursor, {"modifiedSince": stamp})

    async def fetch(self, attachment: Attachment, limit: int) -> bytes | None:
        """The file's bytes: the `data` endpoint answers JSON `{"data": "<base64>"}`, which is
        decoded here. None when it is gone, not an API path of ours, or bigger than `limit`
        (the size the source gave is checked before downloading)."""
        if attachment.size is not None and attachment.size > limit:
            return None
        if not attachment.url.startswith("/v2/"):
            return None
        data = await self._get(attachment.url, missing_ok=True)
        encoded = data.get("data") if isinstance(data, dict) else None
        if not isinstance(encoded, str) or len(encoded) * 3 // 4 > limit + 3:
            return None
        try:
            raw = base64.b64decode(encoded, validate=True)
        except ValueError:
            return None
        return raw if len(raw) <= limit else None

    async def _tickets(
        self, cursor: Mapping[str, Any] | None, extra: Mapping[str, Any] | None = None
    ) -> Page[Ticket]:
        number = _page_no(cursor)
        data = await self._get(
            "/v2/conversations",
            {
                "status": "all",
                "sortField": "modifiedAt",
                "sortOrder": "asc",
                "page": number,
                **(extra or {}),
            },
        )
        raws = _items(data, "conversations")
        out: list[Ticket] = []
        for raw in raws[: rules.PAGE_MAX]:
            if not isinstance(raw, dict) or raw.get("id") is None:
                continue
            if str(raw.get("status", "")).lower() == "spam":
                self.spam_skipped += 1
                continue
            out.append(await self._ticket(raw))
        more = number < _total_pages(data) and bool(raws)
        return Page(out, {"page": number + 1} if more else None)

    async def _ticket(self, raw: Mapping[str, Any]) -> Ticket:
        cid = str(raw["id"])
        created = when(raw.get("createdAt")) or when(raw.get("userUpdatedAt"))
        messages = await self._threads(cid)
        assignee = raw.get("assignee")
        email = assignee.get("email") if isinstance(assignee, dict) else None
        tags: list[str] = []
        for tag in raw.get("tags") or []:
            text = (
                tag if isinstance(tag, str) else (tag or {}).get("tag") or (tag or {}).get("name")
            )
            if text:
                tags.append(str(text))
        fields: dict[str, Any] = {}
        for item in raw.get("customFields") or []:
            if isinstance(item, dict) and item.get("name"):
                value = (
                    item.get("text") if item.get("text") not in (None, "") else item.get("value")
                )
                fields[str(item["name"])] = value
        mailbox = raw.get("mailboxId")
        kind = raw.get("type")
        return Ticket(
            external_id=cid,
            subject=rules.subject(raw.get("subject")),
            status=str(raw.get("status") or ""),
            created_at=created or (messages[0].created_at if messages else _epoch()),
            requester=_requester(raw, cid),
            messages=messages,
            channel=rules.channel(str(kind)) if kind else None,
            group_id=str(mailbox) if mailbox is not None else None,
            assignee_email=str(email) if email else None,
            tags=tags,
            fields=fields,
            updated_at=when(raw.get("userUpdatedAt")),
            resolved_at=when(raw.get("closedAt")),
        )

    async def _threads(self, cid: str) -> list[Message]:
        out: list[Message] = []
        number, pages = 1, 1
        while number <= pages and number <= THREAD_PAGES_MAX:
            data = await self._get(f"/v2/conversations/{segment(cid)}/threads", {"page": number})
            pages = _total_pages(data)
            for raw in _items(data, "threads"):
                message = _message(raw, cid)
                if message is not None:
                    out.append(message)
            number += 1
        out.sort(key=lambda m: m.created_at)  # the list comes newest first
        return out

    # Articles (Docs API)

    async def _collection_list(self) -> list[tuple[str, str]]:
        if self._collections is None:
            found: list[tuple[str, str]] = []
            number, pages = 1, 1
            while number <= pages:
                data = await self._docs_get("/v1/collections", {"page": number})
                block = _docs_block(data, "collections")
                pages = int(block.get("pages", 1) or 1)
                for item in block.get("items") or []:
                    if isinstance(item, dict) and item.get("id"):
                        found.append((str(item["id"]), str(item.get("name") or "")))
                number += 1
            self._collections = found
        return self._collections

    async def _category_names(self, collection_id: str) -> dict[str, str]:
        if collection_id not in self._categories:
            names: dict[str, str] = {}
            number, pages = 1, 1
            while number <= pages:
                data = await self._docs_get(
                    f"/v1/collections/{segment(collection_id)}/categories", {"page": number}
                )
                block = _docs_block(data, "categories")
                pages = int(block.get("pages", 1) or 1)
                for item in block.get("items") or []:
                    if isinstance(item, dict) and item.get("id"):
                        names[str(item["id"])] = str(item.get("name") or "")
                number += 1
            self._categories[collection_id] = names
        return self._categories[collection_id]

    async def _articles(self, cursor: Mapping[str, Any] | None) -> Page[Article]:
        if self._docs is None:
            return Page([], None)
        collections = await self._collection_list()
        index = int(cursor["c"]) if cursor and "c" in cursor else 0
        number = _page_no(cursor, "p")
        while index < len(collections):
            collection_id = collections[index][0]
            listing = _docs_block(
                await self._docs_get(
                    f"/v1/collections/{segment(collection_id)}/articles",
                    {"page": number, "pageSize": ARTICLES_PER_PAGE, "status": "all"},
                ),
                "articles",
            )
            refs = [r for r in listing.get("items") or [] if isinstance(r, dict) and r.get("id")]
            out: list[Article] = []
            if refs:
                names = await self._category_names(collection_id)
                for ref in refs:
                    found = await self._article(str(ref["id"]), names)
                    if found is not None:
                        out.append(found)
            if number < int(listing.get("pages", 1) or 1):
                return Page(out, {"c": index, "p": number + 1})
            index, number = index + 1, 1
            if out or index >= len(collections):
                return Page(out, {"c": index, "p": 1} if index < len(collections) else None)
        return Page([], None)

    async def _article(self, article_id: str, names: Mapping[str, str]) -> Article | None:
        assert self._docs is not None
        data = await self._docs_get(f"/v1/articles/{segment(article_id)}")
        raw = data.get("article") if isinstance(data, dict) else None
        if not isinstance(raw, dict):
            return None
        category = None
        for cat_id in raw.get("categories") or []:
            if str(cat_id) in names:
                category = Category(str(cat_id), names[str(cat_id)])
                break
        return Article(
            external_id=article_id,
            title=rules.subject(raw.get("name")),
            body=rules.body(plain_text(raw.get("text"))),
            category=category,
            published=str(raw.get("status", "")).lower() == "published",
        )

    # Saved replies

    async def _saved_replies(self, mailbox_id: str) -> Any:
        return await self._get(
            f"/v2/mailboxes/{segment(mailbox_id)}/saved-replies", {"includeChatReplies": "true"}
        )

    async def _replies(self, cursor: Mapping[str, Any] | None) -> Page[Reply]:
        boxes = await self.groups()
        index = int(cursor["m"]) if cursor and "m" in cursor else 0
        offset = int(cursor["o"]) if cursor and "o" in cursor else 0
        while index < len(boxes):
            box = boxes[index].external_id
            listed = [
                r
                for r in _items(await self._saved_replies(box), "savedReplies")
                if isinstance(r, dict) and r.get("id") is not None
            ]
            chunk = listed[offset : offset + REPLIES_PER_PAGE]
            end = offset + len(chunk)
            nxt: Mapping[str, Any] | None = {"m": index, "o": end}
            if end >= len(listed):
                nxt = {"m": index + 1, "o": 0} if index + 1 < len(boxes) else None
            out: list[Reply] = []
            for ref in chunk:
                detail = await self._get(
                    f"/v2/mailboxes/{segment(box)}/saved-replies/{segment(ref['id'])}",
                    missing_ok=True,
                )
                reply = _reply(ref, detail)
                if reply is not None:
                    out.append(reply)
            if out or nxt is None:
                return Page(out, nxt)
            index, offset = index + 1, 0
        return Page([], None)


def _epoch() -> Any:
    return when(0)


def _docs_block(data: Any, key: str) -> dict[str, Any]:
    """The `{page, pages, count, items}` block the Docs API wraps its lists in."""
    block = data.get(key) if isinstance(data, dict) else None
    return block if isinstance(block, dict) else {}


def _first_email(customer: Mapping[str, Any]) -> str | None:
    emails = _items(customer, "emails")
    for item in emails:
        value = item.get("value") if isinstance(item, dict) else None
        if value:
            return str(value).strip().lower()
    direct = customer.get("email")
    return str(direct).strip().lower() if direct else None


def _requester(raw: Mapping[str, Any], cid: str) -> Contact:
    person = raw.get("primaryCustomer")
    if not isinstance(person, dict) or person.get("id") is None:
        person = {}
    email = person.get("email")
    email = str(email).strip().lower() if email else None
    name = _name(person.get("first"), person.get("last")) or email or "Customer"
    external = str(person["id"]) if person.get("id") is not None else f"conversation-{cid}"
    return Contact(external, email, name)


def _message(raw: Any, cid: str) -> Message | None:
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("type", "")).lower()
    if kind == "lineitem" or str(raw.get("state", "")).lower() == "draft":
        return None
    text = rules.body(plain_text(raw.get("body")))
    at = when(raw.get("createdAt"))
    if not text or at is None:
        return None
    author = raw.get("createdBy")
    author = author if isinstance(author, dict) else {}
    if kind == "note":
        role = "note"
    elif kind == "customer":
        role = "customer"
    else:
        role = "agent" if author.get("type") == "user" else "customer"
    email = author.get("email")
    thread_id = raw.get("id")
    return Message(
        kind=role,
        body=text,
        created_at=at,
        external_id=str(thread_id) if thread_id is not None else None,
        author_name=_name(author.get("first"), author.get("last")) or None,
        author_email=str(email) if email else None,
        attachments=_attachments(raw, cid),
    )


def _attachments(thread: Mapping[str, Any], cid: str) -> list[Attachment]:
    """`_embedded.attachments` of a thread. `url` is the API path of the file's `data`
    endpoint (built from the ids, so it always stays on the account's API host). An entry
    without an id, or on a thread without one, is skipped."""
    thread_id = thread.get("id")
    out: list[Attachment] = []
    if thread_id is None:
        return out
    for item in _items(thread, "attachments"):
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        name = str(item.get("filename") or "").strip() or f"attachment-{item['id']}"
        size = item.get("size")
        mime = item.get("mimeType")
        out.append(
            Attachment(
                name=name,
                url=(
                    f"/v2/conversations/{segment(cid)}/threads/{segment(thread_id)}"
                    f"/attachments/{segment(item['id'])}/data"
                ),
                size=size if isinstance(size, int) and not isinstance(size, bool) else None,
                mime=str(mime) if mime else None,
            )
        )
    return out


def _reply(ref: Mapping[str, Any], detail: Any) -> Reply | None:
    source = detail if isinstance(detail, dict) else ref
    text = plain_text(source.get("text")) or plain_text(source.get("chatText"))
    text = text or str(ref.get("preview") or ref.get("chatPreview") or "").strip()
    name = str(ref.get("name") or "").strip()
    if not text or not name:
        return None
    return Reply(str(ref["id"]), name, rules.body(text))


def make(
    settings: Mapping[str, str],
    credentials: Mapping[str, str],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    guard: Guard | None = None,
) -> HelpScoutConnector:
    """`credentials`: `app_id` and `app_secret` (required), `docs_key` (optional)."""
    return HelpScoutConnector(
        credentials["app_id"],
        credentials["app_secret"],
        credentials.get("docs_key") or None,
        transport=transport,
        guard=guard,
    )
