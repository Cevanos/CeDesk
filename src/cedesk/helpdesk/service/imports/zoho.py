"""The Zoho Desk importer: contacts, tickets with their threads, comments and tags,
departments, archived tickets (when asked for) and help center articles.

Zoho Desk API v1, GET only, at the data centre the Admin picks (`region`: `us`, `eu`, `in`,
`au`, `jp`; md 8.3: the API host differs per data centre). Checked against the official
reference (desk.zoho.com/DeskAPIDocument) on 17 Nov 2026; what it doesn't confirm is marked
UNVERIFIED.

Sign-in is OAuth with a Self Client (api-console.zoho.com): its client id and secret and a
refresh token the Admin generates with the scopes `Desk.tickets.READ`, `Desk.contacts.READ`,
`Desk.basic.READ` and `Desk.articles.READ` (archived tickets: also `Desk.search.READ`).
`POST /oauth/v2/token` at `accounts.zoho.<dc>` with `grant_type=refresh_token` gives an
access token (`expires_in` 3600 s), sent as `Authorization: Zoho-oauthtoken <token>` with
the `orgId` header. Zoho allows only 10 access-token requests per 10 minutes
(zoho.com/accounts/protocol/oauth/token-limits.html) and answers more with
`{"error": "Access Denied", "error_description": "You have made too many requests
continuously..."}`, so a token is reused until 2 minutes before it expires: within one
connector and, because the framework builds a new connector for every slice of a run,
across connectors in this process (`_TOKENS`, keyed by a hash of the client id, secret,
refresh token and accounts host; the token itself is never logged). That throttle answer is
`RateLimited` (the run waits 10 minutes), a bad refresh token or client is a permanent error
that says which, and the secret and tokens are never put in an error message.

Paging (`from`, `limit`): the reference's ranges differ per list and are followed exactly:
tickets and contacts `limit` 1-100 (`from` range not given: 0 is sent, as the live test of
16 Nov did without error; UNVERIFIED whether it counts from 0 or 1, either way nothing is
skipped, at worst one ticket is read twice and deduplicated by id), departments `from` >= 0
and `limit` up to 200, threads `from` >= 0 and `limit` 1-200, comments `from` >= 0 and
`limit` 1-100, archived tickets `from` 0-4999 and `limit` 1-100, articles `from` > 0 and
`limit` 1-50 (B-46: `from=0&limit=100` is refused with 422). An empty page is a 204.

Endpoints used:

- organisation: `GET /organizations` (the one to read: `org_id` in the settings, or the
  account's only one)
- discover: `GET /ticketsCount`; contacts: `GET /views?module=contacts` for the "All
  Contacts" view, then `GET /contacts/count?viewId=` (`viewId` is required: without it Zoho
  answers 422, B-45); articles counted by paging. A count Zoho refuses is counted by paging
  instead (at most `COUNT_PAGES_MAX` pages; past that the dry run says it is a lower bound).
- groups: `GET /departments` (every page)
- contacts: `GET /contacts?include=accounts`
- tickets: `GET /tickets` (`sortBy=createdTime`, `include=contacts,assignee`); per ticket
  `GET /tickets/{id}` (the list has no `modifiedTime` and no custom fields), `GET
  /tickets/{id}/tags` (skipped when `tagCount` is 0), `GET /tickets/{id}/threads` and each
  `GET /tickets/{id}/threads/{threadId}` (full text, files, to/cc/bcc), and `GET
  /tickets/{id}/comments` (with their files; skipped when `commentCount` is 0)
- archived tickets (setting `include_archived`): after the live list, `GET
  /tickets/archivedTickets?departmentId=` for every department
- articles: `GET /articles`, then `GET /articles/{id}` (the `answer`)
- changes since a time (M9): `GET /tickets?sortBy=-modifiedTime` (not in the reference's
  `sortBy` list, but it worked live on 16 Nov). The list rows carry no `modifiedTime` (B-49),
  so each row's ticket is read (`GET /tickets/{id}`) and paging stops at the first ticket
  whose real modified time is before `since`; a ticket without one never stops it.

API credits (the reference's "API Credits"): a list call costs 3 credits (more deep into a
list: 5 past record 2,000, 10 past 10,000, 50 past 50,000), a single record 1, a count 50;
a trial has 5,000 a day. When the day's credits are used up Zoho answers 429 with
`Retry-After` (documented), which `SourceHttp` turns into `RateLimited`; the run waits and
goes on. Too many calls at once is also a 429 (`TOO_MANY_REQUESTS`).

Decisions:

- Status: Zoho's `statusType` (the fixed kind behind each custom status): `Open` -> open,
  `On Hold` -> pending, `Closed` -> resolved.
- Messages: a thread with `direction` `in` -> customer, `out` -> agent, `visibility`
  `private` -> note. A comment that isn't public -> note; a public one -> agent. HTML is
  stripped to text. A thread whose detail is gone keeps Zoho's summary, says so in its text,
  and the ticket is noted `incomplete`.
- Custom fields: `customFields` (label -> value) when Zoho gives them, else `cf` (API name ->
  value); `category` and `subCategory` are kept as fields too. `classification` is the type.
- Dates: a ticket without a valid created date falls back on its first response or modified
  time, a message on the ticket's date (`rules.dated`: never silent).
- Saved replies are not imported: Zoho Desk's templates are whole email templates, not
  saved replies. The dry run says so (`warnings`).

UNVERIFIED: whether `GET /tickets/{id}` and the thread calls work for an archived ticket (an
archived ticket whose detail is refused is imported from its list row); the `account` object
on a contact listed with `include=accounts` (documented on a ticket's contact); the shape of
a thread's `fullContentURL` answer (read when `isContentTruncated`; when it can't be read the
ticket is noted `incomplete`); the attachment `href` being on the Desk host (a file elsewhere
is downloaded without the token).
"""

import hashlib
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import Any
from urllib.parse import urlsplit

import httpx

from cedesk.egress import BadAddress, Guard, check_public, fixed_host, pinned
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
    RateLimited,
    RecordGone,
    SourceError,
    Ticket,
)
from cedesk.helpdesk.service.imports.gorgias import strip_html
from cedesk.helpdesk.service.imports.http import (
    TIMEOUT_SECONDS,
    USER_AGENT,
    SourceHttp,
    retry_after,
    segment,
    when,
)

LABEL = "Zoho Desk"
REGIONS = ("us", "eu", "in", "au", "jp")
TOKEN_PATH = "/oauth/v2/token"  # noqa: S105 (a URL path, not a secret)
TOKEN_MARGIN = 120  # seconds before expiry at which the token is fetched again
TOKEN_THROTTLE_WAIT = 600  # Zoho: 10 access-token requests per 10 minutes
TOKEN_CACHE_MAX = 256
LIMIT = 100  # tickets, contacts, comments, threads (1-100 / 1-200 in the reference)
ARTICLE_LIMIT = 50  # articles: `limit` 1-50
ARTICLE_FROM = 1  # articles: `from` > 0
DEPARTMENT_LIMIT = 200  # departments: `limit` up to 200
DEPARTMENT_PAGES_MAX = 50
ARCHIVE_FROM_MAX = 4999  # archived tickets: `from` 0-4999
COUNT_PAGES_MAX = 20
DETAIL_PAGES_MAX = 2000  # a safety limit only: 200,000 threads or comments on one ticket
STATUS_MAP: Mapping[str, str] = {"open": "open", "on hold": "pending", "closed": "resolved"}
_EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_CODE = re.compile(r"^[A-Za-z][A-Za-z _]{0,39}$")


def _now() -> float:
    return time.monotonic()


@dataclass
class _Token:
    value: str
    expires_at: float  # `_now()` time at which it is fetched again


_TOKENS: dict[str, _Token] = {}
"""Access tokens of this process, by `_token_key`: one per Self Client and refresh token,
shared by every connector built for it (B-47)."""
_ORGS: dict[str, tuple[str, str | None]] = {}
"""The organisation found for an account that named none, by the same key."""


def _token_key(client_id: str, client_secret: str, refresh_token: str, host: str) -> str:
    raw = "\0".join((client_id, client_secret, refresh_token, host)).encode()
    return hashlib.sha256(raw).hexdigest()


def _remember(key: str, token: _Token) -> None:
    if len(_TOKENS) >= TOKEN_CACHE_MAX:
        now = _now()
        for old in [k for k, t in _TOKENS.items() if t.expires_at <= now] or list(_TOKENS)[:1]:
            _TOKENS.pop(old, None)
    _TOKENS[key] = token


def _str(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _data(found: object) -> list[Mapping[str, Any]]:
    rows = found.get("data") if isinstance(found, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _next(start: int, rows: Sequence[Any], limit: int | None = None) -> dict[str, int] | None:
    return {"from": start + len(rows)} if len(rows) >= (limit or LIMIT) else None


def _int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _none(value: object) -> bool:
    """Zoho says there are none (`"0"`, the way it writes counts)."""
    return _int(value) == 0


def _text(html: object) -> str:
    return rules.body(strip_html(html) if isinstance(html, str) else "")


def _addresses(raw: object) -> list[str]:
    """Email addresses from Zoho's `to` / `cc` / `bcc` text (`a@x.com,"B" <b@y.com>`)."""
    out: list[str] = []
    for found in _EMAIL.findall(raw if isinstance(raw, str) else ""):
        if found.lower() not in (x.lower() for x in out):
            out.append(found)
    return out


def contact_from(row: Mapping[str, Any]) -> Contact:
    email = _str(row.get("email"))
    name = " ".join(x for x in (_str(row.get("firstName")), _str(row.get("lastName"))) if x)
    account = row.get("account")
    second = _str(row.get("secondaryEmail"))
    custom = row.get("cf")
    return Contact(
        str(row["id"]),
        email,
        name or (email or "").split("@")[0] or "Unknown",
        phone=_str(row.get("phone")) or _str(row.get("mobile")),
        other_emails=(second,) if second and second != email else (),
        organization=_str(account.get("accountName")) if isinstance(account, dict) else None,
        attributes={str(k): v for k, v in custom.items() if v not in (None, "")}
        if isinstance(custom, dict)
        else {},
    )


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
        include_archived: bool = False,
    ) -> None:
        if region not in REGIONS:
            raise BadAddress(f"Pick the Zoho data centre: {', '.join(REGIONS)}.")
        self._region = region
        self._host = fixed_host("zoho", region)
        self._accounts_host = fixed_host("zoho", f"accounts-{region}")
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._key = _token_key(client_id, client_secret, refresh_token, self._accounts_host)
        self._org_id = org_id
        self._org_name: str | None = None
        self._transport = transport
        self._guard = guard
        self._archived = include_archived
        self._token: str | None = None
        self._expires_at = 0.0
        self._mints = 0  # access tokens this connector asked Zoho for
        self._departments: list[Group] | None = None
        self._notes: list[str] = []  # what discover found worth saying (`warnings`)
        self.failures = rules.RecordFailures()  # tickets deleted or cut short (P0-2)

    # Sign-in

    async def _exchange(self) -> Any:
        """`POST /oauth/v2/token`, read whatever Zoho answers: its errors come as a JSON
        body (on a 200 or a 400), and only the body says which error it is. Like
        `SourceHttp`: the host is checked first, https only, no redirect followed."""
        try:
            await (self._guard or check_public)(self._accounts_host)
        except BadAddress as exc:
            raise SourceError(str(exc), permanent=True) from None
        form = {
            "grant_type": "refresh_token",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": self._refresh_token,
        }
        try:
            async with httpx.AsyncClient(
                timeout=TIMEOUT_SECONDS, follow_redirects=False, transport=pinned(self._transport)
            ) as client:
                response = await client.post(
                    f"https://{self._accounts_host}{TOKEN_PATH}",
                    data=form,
                    headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise SourceError(
                f"{LABEL} sign-in can't be reached: {type(exc).__name__}", permanent=False
            ) from None
        status = response.status_code
        if status == 429:
            raise RateLimited(retry_after(response.headers) or TOKEN_THROTTLE_WAIT)
        if status in (408, 425) or status >= 500:
            raise SourceError(
                f"{LABEL} sign-in answered {status}; will retry.",
                permanent=False,
                retry_after=retry_after(response.headers),
            )
        if 300 <= status < 400:
            raise SourceError(f"{LABEL} sign-in sent us elsewhere; not followed.", permanent=True)
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise SourceError(f"{LABEL} sign-in refused the request ({status}).", permanent=True)
        return data

    def _refused(self, data: Mapping[str, Any]) -> Exception:
        """What a token answer without a token means. Only Zoho's own error code is quoted,
        never the body's other text."""
        error = str(data.get("error") or "")
        said = f"{error} {data.get('error_description') or ''}".lower()
        if "access denied" in said or "too many requests" in said:
            # Zoho's throttle: 10 token requests per 10 minutes. It passes; wait it out.
            return RateLimited(TOKEN_THROTTLE_WAIT)
        if error == "invalid_code":
            return SourceError(
                f"{LABEL} refused the refresh token (invalid_code): it is wrong, was revoked, "
                f"or was made in another data centre than '{self._region}'. Generate a new one "
                "in the Self Client (api-console.zoho.com) and connect again.",
                permanent=True,
            )
        if error == "invalid_client":
            return SourceError(
                f"{LABEL} doesn't know this client id and secret (invalid_client) in the "
                f"'{self._region}' data centre: check them and the data centre.",
                permanent=True,
            )
        code = f" ({error})" if _CODE.match(error) else ""
        return SourceError(
            f"{LABEL} refused these credentials{code} (check the client id, secret and "
            "refresh token, and its data centre).",
            permanent=True,
        )

    async def _login(self, *, fresh: bool = False) -> str:
        """The access token: this process's one for these credentials while it is good,
        else a new one. `fresh`: the last one was refused, get another."""
        cached = _TOKENS.get(self._key)
        if cached is not None and _now() < cached.expires_at:
            if not fresh or cached.value != self._token:  # another connector renewed it
                self._token, self._expires_at = cached.value, cached.expires_at
                return cached.value
            _TOKENS.pop(self._key, None)
        self._mints += 1
        data = await self._exchange()
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise self._refused(data)
        try:
            lifetime = float(data.get("expires_in", 3600))
        except (TypeError, ValueError):
            lifetime = 3600.0
        self._token = token
        self._expires_at = _now() + max(60.0, lifetime - TOKEN_MARGIN)
        _remember(self._key, _Token(token, self._expires_at))
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
            await self._login(fresh=fresh)
        assert self._token is not None
        if with_org and not self._org_id:
            await self._find_org()
        return self._api(self._token, with_org=with_org)

    async def _get(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        missing_ok: bool = False,
        record: bool = False,
    ) -> Any:
        """A GET with the token got on first use and when it has expired; one whose
        credentials are refused (401/403) with a token that was not just got from Zoho (an
        older one, or this process's cached one) is tried once more with a new one. Any other
        refusal (a 404, a 422) is not about the token and never costs a new one (B-47).
        `record`: see `SourceHttp.get`."""
        mints = self._mints
        api = await self._client()
        try:
            return await api.get(path, params, missing_ok=missing_ok, record=record)
        except SourceError as exc:
            if not exc.permanent or self._mints != mints or isinstance(exc, RecordGone):
                raise
            if not exc.refused:  # only a 401/403 costs a new token
                raise
        api = await self._client(fresh=True)
        return await api.get(path, params, missing_ok=missing_ok, record=record)

    async def _find_org(self) -> None:
        known = _ORGS.get(self._key)
        if known is not None:
            self._org_id, self._org_name = known
            return
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
        _ORGS[self._key] = (self._org_id, self._org_name)

    # Interface

    def warnings(self) -> list[str]:
        out = [
            "Zoho Desk's templates are whole email templates, not saved replies, so they "
            "aren't imported.",
            "Zoho Desk counts API credits per day (5,000 on a trial; Enterprise 100,000 plus "
            "1,000 per user). A ticket costs about 10 credits plus 1 per message; when the "
            "day's credits are used up the import waits for Zoho and goes on.",
        ]
        if self._archived:
            out.append(
                "Archived tickets are imported too (include_archived). Listing them needs the "
                "scope Desk.search.READ and costs API credits; Zoho lists at most 5,000 per "
                "department through its API."
            )
        else:
            out.append(
                "Archived tickets aren't imported: Zoho Desk keeps them apart and reading them "
                "costs extra API credits. Set include_archived to bring them."
            )
        return out + self._notes

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

    async def _counted(self, path: str, params: Mapping[str, Any] | None = None) -> int | None:
        """A count endpoint's answer (Zoho writes it as a number or as text), or None when
        Zoho refuses the call (a 422 for a missing parameter, an endpoint this account
        lacks): the caller counts by paging instead. Bad credentials still stop."""
        try:
            found = await self._get(path, params, missing_ok=True)
        except RateLimited:
            raise
        except SourceError as exc:
            if not exc.permanent:
                raise
            return None  # bad credentials fail the paging that follows, and stop there
        return _int(found.get("count")) if isinstance(found, dict) else None

    async def _paged_count(self, kind: str, path: str, first: int, limit: int) -> int:
        total, start = 0, first
        for _ in range(COUNT_PAGES_MAX):
            rows = _data(await self._get(path, {"from": start, "limit": limit}))
            total += len(rows)
            nxt = _next(start, rows, limit)
            if nxt is None:
                return total
            start = nxt["from"]
        self._notes.append(
            f"Zoho Desk has at least {total} {kind}: counting them page by page stopped there "
            "to save API credits. The import reads them all."
        )
        return total

    async def _contacts_count(self) -> int:
        """`GET /contacts/count` needs a `viewId` (B-45): the "All Contacts" view's."""
        views = await self._counted_views()
        if views is not None:
            found = await self._counted("/contacts/count", {"viewId": views})
            if found is not None:
                return found
        return await self._paged_count("contacts", "/contacts", 0, LIMIT)

    async def _counted_views(self) -> str | None:
        try:
            found = await self._get("/views", {"module": "contacts"}, missing_ok=True)
        except RateLimited:
            raise
        except SourceError as exc:
            if not exc.permanent:
                raise
            return None
        views = _data(found)
        # UNVERIFIED: the system view's name ("All Contacts"); any other view counts fewer.
        for view in views:
            name = str(view.get("name") or "").strip().lower()
            if view.get("isCustomView") is not True and name == "all contacts":
                return str(view["id"]) if view.get("id") else None
        return None

    async def discover(self) -> Counts:
        self._notes = []
        tickets = await self._counted("/ticketsCount")
        if tickets is None:
            tickets = await self._paged_count("tickets", "/tickets", 0, LIMIT)
        return Counts(
            contacts=await self._contacts_count(),
            tickets=tickets,
            articles=await self._paged_count("articles", "/articles", ARTICLE_FROM, ARTICLE_LIMIT),
            replies=0,
            groups=len(await self.groups()),
        )

    async def groups(self) -> Sequence[Group]:
        if self._departments is None:
            found: list[Group] = []
            start = 0
            for _ in range(DEPARTMENT_PAGES_MAX):
                rows = _data(
                    await self._get("/departments", {"from": start, "limit": DEPARTMENT_LIMIT})
                )
                found += [
                    Group(str(r["id"]), str(r.get("name") or r["id"])) for r in rows if r.get("id")
                ]
                nxt = _next(start, rows, DEPARTMENT_LIMIT)
                if nxt is None:
                    break
                start = nxt["from"]
            self._departments = found
        return list(self._departments)

    async def page(self, kind: str, cursor: Mapping[str, Any] | None) -> Page[Any]:
        start = int(cursor["from"]) if cursor and cursor.get("from") else 0
        if kind == "contacts":
            rows = _data(
                await self._get("/contacts", {"from": start, "limit": LIMIT, "include": "accounts"})
            )
            contacts = [contact_from(r) for r in rows if r.get("id") is not None]
            return Page(contacts, _next(start, rows))
        if kind == "tickets":
            if cursor and cursor.get("archived"):
                return await self._archived_page(str(cursor["archived"]), start)
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
            items: list[Ticket] = []
            for r in rows:
                if r.get("id") is not None:
                    await self._read(items, r)
            nxt = _next(start, rows)
            if nxt is None and self._archived:
                return Page(items, await self._first_archive())
            return Page(items, nxt)
        if kind == "articles":
            first = int(cursor["from"]) if cursor and cursor.get("from") else ARTICLE_FROM
            rows = _data(
                await self._get("/articles", {"from": max(first, 1), "limit": ARTICLE_LIMIT})
            )
            found: list[Article] = []
            for r in rows:
                if r.get("id") is not None:
                    article = await self.failures.guard(
                        "article", str(r["id"]), partial(self._article, r)
                    )
                    if article is not None:
                        found.append(article)
            return Page(found, _next(max(first, 1), rows, ARTICLE_LIMIT))
        return Page([], None)  # replies: see the module docstring

    async def _department_ids(self) -> list[str]:
        return sorted(g.external_id for g in await self.groups())

    async def _first_archive(self) -> dict[str, Any] | None:
        ids = await self._department_ids()
        return {"archived": ids[0], "from": 0} if ids else None

    async def _archived_page(self, department: str, start: int) -> Page[Ticket]:
        """One page of a department's archived tickets, then the next department's."""
        rows = _data(
            await self._get(
                "/tickets/archivedTickets",
                {
                    "from": start,
                    "limit": LIMIT,
                    "departmentId": department,
                    "include": "contacts,assignee",
                },
            )
        )
        items: list[Ticket] = []
        for r in rows:
            if r.get("id") is not None:
                await self._read(items, r, archived=True)
        nxt = _next(start, rows)
        if nxt is not None and nxt["from"] <= ARCHIVE_FROM_MAX:
            return Page(items, {"archived": department, "from": nxt["from"]})
        if nxt is not None:
            rules.loss("archive_capped")
            self._notes.append(
                f"Zoho Desk lists at most 5,000 archived tickets per department through its "
                f"API: department {department} has more, and the rest were not read."
            )
        later = [d for d in await self._department_ids() if d > department]
        return Page(items, {"archived": later[0], "from": 0} if later else None)

    async def changed_tickets(
        self, since: datetime, cursor: Mapping[str, Any] | None
    ) -> Page[Ticket]:
        """Newest change first. The list rows have no `modifiedTime` (B-49), so each row's
        ticket is read for it; paging stops at the first ticket whose real modified time is
        before `since`. A ticket with no valid modified time is taken and never stops it."""
        start = int(cursor["from"]) if cursor and cursor.get("from") else 0
        rows = _data(
            await self._get(
                "/tickets",
                {
                    "from": start,
                    "limit": LIMIT,
                    "sortBy": "-modifiedTime",  # UNVERIFIED in the reference; worked live
                    "include": "contacts,assignee",
                },
            )
        )
        items: list[Ticket] = []
        nxt = _next(start, rows)
        for row in rows:
            if row.get("id") is None:
                continue
            tid = str(row["id"])
            full = await self.failures.guard("ticket", tid, partial(self._detail, row))
            if full is None:
                continue  # gone since it was listed: noted
            changed = rules.valid_date(when(full.get("modifiedTime")))
            if changed is not None and changed < since:
                nxt = None  # ordered by change time: everything after is older still
                break
            await self._read(items, full, detailed=True)
        return Page(items, nxt)

    # Records

    async def _article(self, row: Mapping[str, Any]) -> Article:
        """The article with its answer. One that is gone raises `RecordGone` (noted); one
        with an empty answer is kept: an empty article is still the customer's."""
        detail = await self._get(f"/articles/{segment(row['id'])}", record=True)
        source: Mapping[str, Any] = {**row, **detail} if isinstance(detail, dict) else row
        category = source.get("category")
        return Article(
            str(row["id"]),
            rules.subject(_str(source.get("title")) or "(untitled)")[:200],
            _text(source.get("answer")),
            Category(str(category["id"]), str(category.get("name") or category["id"]))
            if isinstance(category, dict) and category.get("id")
            else None,
            str(source.get("status") or "Published").lower() == "published",
            locale=_str(source.get("locale")),
            updated_at=rules.valid_date(when(source.get("modifiedTime"))),
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

    async def _paged(self, tid: str, path: str, *, record: bool = False) -> list[Mapping[str, Any]]:
        """Every page of a ticket's list. `record`: a 404 means that ticket is gone. Past
        `DETAIL_PAGES_MAX` pages the rest is not read, and that is counted and noted."""
        out: list[Mapping[str, Any]] = []
        start = 0
        for _ in range(DETAIL_PAGES_MAX):
            rows = _data(await self._get(path, {"from": start, "limit": LIMIT}, record=record))
            out += rows
            nxt = _next(start, rows)
            if nxt is None:
                return out
            start = nxt["from"]
        rules.loss("history_capped")
        self.failures.incomplete(
            "ticket", tid,
            f"{LABEL}: this ticket has more than {DETAIL_PAGES_MAX * LIMIT} entries in one "
            "list; the rest were not read.",
        )  # fmt: skip
        return out

    async def _full_text(self, tid: str, row: Mapping[str, Any]) -> str | None:
        """The whole text of a thread Zoho cut (`isContentTruncated`), from its
        `fullContentURL` on the Desk host. UNVERIFIED: that answer's shape (`content`)."""
        url = row.get("fullContentURL")
        parts = urlsplit(url) if isinstance(url, str) else None
        if parts is None or parts.hostname != self._host or not parts.path.startswith("/api/v1/"):
            return None
        try:
            found = await self._get(parts.path[len("/api/v1") :], missing_ok=True)
        except RateLimited:
            raise
        except SourceError as exc:
            if not exc.permanent:
                raise
            return None
        text = _text(found.get("content")) if isinstance(found, dict) else ""
        return text or None

    async def _messages(self, tid: str, opened: datetime, row: Mapping[str, Any]) -> list[Message]:
        out: list[Message] = []
        for ref in await self._paged(tid, f"/tickets/{segment(tid)}/threads", record=True):
            if ref.get("id") is None:
                continue
            try:
                detail = await self._get(
                    f"/tickets/{segment(tid)}/threads/{segment(ref['id'])}", record=True
                )
            except RecordGone:
                detail = None
            full: Mapping[str, Any] = {**ref, **detail} if isinstance(detail, dict) else ref
            text = _text(full.get("content"))
            if detail is None or not isinstance(detail, dict):
                summary = rules.body(_str(ref.get("summary")))
                text = (
                    f"{summary}\n\n[Only {LABEL}'s summary of this message could be read.]"
                    if summary
                    else ""
                )
                self.failures.incomplete(
                    "ticket", tid, f"{LABEL} no longer has one of its threads (404): only "
                    "that thread's summary was imported.",
                )  # fmt: skip
            elif full.get("isContentTruncated") is True:
                whole = await self._full_text(tid, full)
                if whole is not None:
                    text = whole
                else:
                    self.failures.incomplete(
                        "ticket", tid, f"{LABEL} cut the text of one of its threads and its "
                        "full text could not be read.",
                    )  # fmt: skip
            files = self._files(full.get("attachments"))
            if not text and not files:
                rules.loss("message_without_text")
                continue
            raw_author = full.get("author")
            author: Mapping[str, Any] = raw_author if isinstance(raw_author, dict) else {}
            kind = (
                "note"
                if str(full.get("visibility") or "").lower() == "private"
                else "customer"
                if str(full.get("direction") or "").lower() == "in"
                else "agent"
            )
            out.append(
                Message(
                    kind=kind,
                    body=text or "(attachment only)",
                    created_at=rules.dated(
                        self.failures,
                        "ticket",
                        tid,
                        when(full.get("createdTime")),
                        opened,
                        what="message date",
                    ),
                    external_id=f"thread-{ref['id']}",
                    author_name=_str(author.get("name")),
                    author_email=_str(author.get("email")) or _str(full.get("fromEmailAddress")),
                    attachments=files,
                    to=_addresses(full.get("to")),
                    cc=_addresses(full.get("cc")),
                    bcc=_addresses(full.get("bcc")),
                )
            )
        comments = (
            []
            if _none(row.get("commentCount"))
            else await self._paged(tid, f"/tickets/{segment(tid)}/comments", record=True)
        )
        for com in comments:
            if com.get("id") is None:
                continue
            text = _text(com.get("content"))
            files = self._files(com.get("attachments"))
            if not text and not files:
                rules.loss("message_without_text")
                continue
            raw_who = com.get("commenter")
            who: Mapping[str, Any] = raw_who if isinstance(raw_who, dict) else {}
            out.append(
                Message(
                    kind="agent" if com.get("isPublic") is True else "note",
                    body=text or "(attachment only)",
                    created_at=rules.dated(
                        self.failures,
                        "ticket",
                        tid,
                        when(com.get("commentedTime")),
                        opened,
                        what="message date",
                    ),
                    external_id=f"comment-{com['id']}",
                    author_name=_str(who.get("name")),
                    author_email=_str(who.get("email")),
                    attachments=files,
                )
            )
        out.sort(key=lambda m: m.created_at)
        return out

    async def _tags(self, tid: str, row: Mapping[str, Any]) -> list[str]:
        if _none(row.get("tagCount")):
            return []
        found = await self._get(f"/tickets/{segment(tid)}/tags", missing_ok=True)
        rows = found.get("tags") if isinstance(found, dict) else None
        if not isinstance(rows, list):
            rows = _data(found)
        names = [_str(t.get("name")) for t in rows if isinstance(t, dict)]
        return [n for n in names if n]

    async def _detail(self, row: Mapping[str, Any], *, archived: bool = False) -> dict[str, Any]:
        """The list row with the ticket's own record over it (`modifiedTime`, custom fields
        and the rest the list leaves out). A live ticket that is gone raises `RecordGone`; an
        archived one whose record is refused keeps its list row (UNVERIFIED that the call
        serves archived tickets)."""
        path = f"/tickets/{segment(row['id'])}"
        params = {"include": "contacts,assignee"}
        if archived:
            try:
                found = await self._get(path, params, missing_ok=True)
            except RateLimited:
                raise
            except SourceError as exc:
                if not exc.permanent:
                    raise
                found = None
        else:
            found = await self._get(path, params, record=True)
        return {**row, **found} if isinstance(found, dict) else dict(row)

    async def _read(
        self,
        items: list[Ticket],
        row: Mapping[str, Any],
        *,
        archived: bool = False,
        detailed: bool = False,
    ) -> None:
        """Adds the ticket to `items`, or notes it in `failures` when it was deleted after it
        was listed (or keeps failing while the run isolates records)."""
        ticket = await self.failures.guard(
            "ticket", str(row["id"]), partial(self._ticket, row, archived, detailed)
        )
        if ticket is not None:
            items.append(ticket)

    async def _ticket(self, listed: Mapping[str, Any], archived: bool, detailed: bool) -> Ticket:
        row = dict(listed) if detailed else await self._detail(listed, archived=archived)
        tid = str(row["id"])
        contact = row.get("contact")
        requester = (
            contact_from(contact)
            if isinstance(contact, dict) and contact.get("id") is not None
            else Contact(str(row["contactId"]), _str(row.get("email")), "Unknown")
            if row.get("contactId")
            else Contact(f"ticket-{tid}", _str(row.get("email")), "Unknown")
        )
        assignee = row.get("assignee")
        opened = rules.dated(
            self.failures, "ticket", tid,
            when(row.get("createdTime")), when(row.get("customerResponseTime")),
            when(row.get("modifiedTime")),
        )  # fmt: skip
        try:
            messages = await self._messages(tid, opened, row)
        except RecordGone:
            if not archived:
                raise  # a live ticket deleted since it was listed: noted and left out
            # UNVERIFIED that Zoho serves an archived ticket's threads: keep what the list
            # gave, and say its history is missing.
            messages = []
            self.failures.incomplete(
                "ticket", tid, f"{LABEL} did not serve this archived ticket's messages (404)."
            )
        labels = row.get("customFields")
        api_names = row.get("cf")
        raw_fields = (
            labels if isinstance(labels, dict) and labels
            else api_names if isinstance(api_names, dict)
            else {}
        )  # fmt: skip
        fields = {str(k): v for k, v in raw_fields.items() if v not in (None, "")}
        for key, name in (("category", "category"), ("subCategory", "sub_category")):
            if _str(row.get(key)) and name not in fields:
                fields[name] = row[key]
        copied: list[str] = []
        for message in messages:
            copied += [x for x in message.cc if x.lower() not in (c.lower() for c in copied)]
        return Ticket(
            external_id=tid,
            subject=rules.subject(row.get("subject")),
            status=str(row.get("statusType") or "Open").strip().lower(),
            created_at=opened,
            requester=requester,
            messages=messages,
            priority=_str(row.get("priority")),
            channel=_str(row.get("channel")),
            group_id=_str(str(row["departmentId"])) if row.get("departmentId") else None,
            assignee_email=_str(assignee.get("email")) if isinstance(assignee, dict) else None,
            tags=await self._tags(tid, row),
            fields=fields,
            updated_at=rules.valid_date(when(row.get("modifiedTime"))),
            resolved_at=rules.valid_date(when(row.get("closedTime"))),
            ticket_type=_str(row.get("classification")),
            source_url=_str(row.get("webUrl")),
            cc=copied,
            sla_due_at=rules.valid_date(when(row.get("dueDate"))),
        )


def make(
    settings: Mapping[str, str],
    credentials: Mapping[str, str],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    guard: Guard | None = None,
) -> ZohoDeskConnector:
    """`settings`: `region` (us, eu, in, au, jp), `org_id` (only for an account with
    several organisations) and `include_archived` ("true" to read archived tickets too).
    Raises `egress.BadAddress` for an unknown region."""
    wanted = settings.get("include_archived", "").strip().lower() in ("1", "true", "yes", "on")
    return ZohoDeskConnector(
        settings.get("region", "").strip().lower(),
        credentials.get("client_id", ""),
        credentials.get("client_secret", ""),
        credentials.get("refresh_token", ""),
        (settings.get("org_id") or "").strip() or None,
        transport=transport,
        guard=guard,
        include_archived=wanted,
    )
