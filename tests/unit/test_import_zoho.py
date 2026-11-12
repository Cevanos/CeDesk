"""The Zoho Desk importer, driven by recorded-shape JSON and `httpx.MockTransport`; no network."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from cedesk.helpdesk.domain import imports as rules
from cedesk.helpdesk.domain.imports import Attachment, RateLimited, SourceError
from cedesk.helpdesk.service.imports import zoho
from cedesk.helpdesk.service.imports.connectors import build_connector

FIXTURES = Path(__file__).parent.parent / "fixtures" / "imports" / "zoho"
SECRET = "zoho-client-secret-123"
REFRESH = "1000.refresh-token-456"
DESK, ACCOUNTS = "desk.zoho.in", "accounts.zoho.in"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


async def allow(host: str) -> None:
    return None


def route(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if request.url.host == ACCOUNTS:
        return httpx.Response(200, json=fixture("token"))
    if path.endswith("/content"):
        return httpx.Response(200, content=b"%PDF-label")
    if path == "/api/v1/organizations":
        return httpx.Response(200, json=fixture("organizations"))
    if request.headers.get("orgId") != "6001":
        return httpx.Response(400, json={"errorCode": "INVALID_ORG"})
    simple = {
        "/api/v1/departments": "departments", "/api/v1/contacts": "contacts",
        "/api/v1/tickets/z1/threads": "threads_z1", "/api/v1/tickets/z1/comments": "comments_z1",
        "/api/v1/articles": "articles",
    }  # fmt: skip
    if path == "/api/v1/ticketsCount":
        return httpx.Response(200, json={"count": 2})
    if path == "/api/v1/tickets":
        return httpx.Response(200, json=fixture("tickets"))
    if path in simple:
        return httpx.Response(200, json=fixture(simple[path]))
    for prefix, name in (
        ("/api/v1/tickets/z1/threads/", "thread_"),
        ("/api/v1/articles/", "article_"),
    ):
        if path.startswith(prefix) and (FIXTURES / f"{name}{path[len(prefix) :]}.json").exists():
            return httpx.Response(200, json=fixture(f"{name}{path[len(prefix) :]}"))
    if path.startswith("/api/v1/tickets/z2/"):
        return httpx.Response(204)  # an empty list
    return httpx.Response(404, json={"errorCode": "URL_NOT_FOUND"})


def connector(
    handler: Any = route, seen: list[httpx.Request] | None = None, **settings: str
) -> Any:
    def wrapped(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return handler(request)

    return zoho.make(
        {"region": "in", **settings},
        {"client_id": "1000.CLIENT", "client_secret": SECRET, "refresh_token": REFRESH},
        transport=httpx.MockTransport(wrapped),
        guard=allow,
    )


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def tickets() -> list[Any]:
    page = run(connector().page("tickets", None))
    assert page.cursor is None  # fewer than a full page
    return list(page.items)


def test_sign_in_with_the_refresh_token_then_the_org() -> None:
    seen: list[httpx.Request] = []
    assert run(connector(seen=seen).check()).name == "Acme Support (desk.zoho.in)"
    token = seen[0]
    assert (token.method, token.url.host, token.url.path) == ("POST", ACCOUNTS, "/oauth/v2/token")
    form = dict(x.split("=", 1) for x in token.content.decode().split("&"))
    assert form["grant_type"] == "refresh_token" and form["refresh_token"] == REFRESH
    api = seen[-1]
    assert api.url.host == DESK and api.headers["Authorization"] == "Zoho-oauthtoken zat-1"


def test_an_account_with_several_orgs_must_name_one() -> None:
    def two(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/organizations":
            orgs = [
                {"id": "6001", "companyName": "Acme Support"},
                {"id": "7002", "companyName": "Acme EU"},
            ]
            return httpx.Response(200, json={"data": orgs})
        return route(request)

    with pytest.raises(SourceError) as err:
        run(connector(two).check())
    assert err.value.permanent and "org_id" in str(err.value) and "7002" in str(err.value)
    assert run(connector(two, org_id="6001").check()).name == "Acme Support (desk.zoho.in)"
    with pytest.raises(SourceError):
        run(connector(two, org_id="9999").check())


def test_a_bad_refresh_token_is_permanent_and_never_shown() -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        if request.url.host == ACCOUNTS:
            return httpx.Response(200, json={"error": "invalid_code"})
        return route(request)

    with pytest.raises(SourceError) as err:
        run(connector(refused).check())
    assert err.value.permanent
    assert SECRET not in str(err.value) and REFRESH not in str(err.value)


def test_an_expired_token_is_renewed_once() -> None:
    tokens = iter(["zat-1", "zat-2"])
    issued: list[str] = []

    def rotating(request: httpx.Request) -> httpx.Response:
        if request.url.host == ACCOUNTS:
            issued.append(next(tokens))
            return httpx.Response(200, json={"access_token": issued[-1], "expires_in": 3600})
        if request.url.path != "/api/v1/organizations" and request.headers[
            "Authorization"
        ].endswith("zat-1"):
            return httpx.Response(401, json={"errorCode": "INVALID_OAUTH"})
        return route(request)

    c = connector(rotating)
    run(c.check())  # zat-1 worked here; later Zoho stops taking it
    assert [g.name for g in run(c.groups())] == ["Billing", "Shipping"]
    assert issued == ["zat-1", "zat-2"]


def test_discover_uses_counts_and_pages_when_missing() -> None:
    counts = run(connector().discover())
    assert (counts.tickets, counts.contacts, counts.articles, counts.replies, counts.groups) == (
        2, 2, 2, 0, 2)  # fmt: skip


def test_contacts_and_paging_by_from(monkeypatch: pytest.MonkeyPatch) -> None:
    page = run(connector().page("contacts", None))
    assert [(c.external_id, c.email, c.name) for c in page.items] == [
        ("c1", "priya@example.org", "Priya Sharma"),
        ("c2", None, "Ravi"),
    ]
    monkeypatch.setattr(zoho, "LIMIT", 2)
    seen: list[httpx.Request] = []
    full = run(connector(seen=seen).page("contacts", {"from": 4}))
    assert full.cursor == {"from": 6}
    assert seen[-1].url.params["from"] == "4" and seen[-1].url.params["limit"] == "2"


def test_status_people_fields_and_dates() -> None:
    z1, z2 = tickets()
    assert (z1.status, z2.status) == ("on hold", "closed")
    assert [rules.status(t.status, zoho.STATUS_MAP) for t in (z1, z2)] == ["pending", "resolved"]
    assert rules.priority(z1.priority) == "high" and rules.channel(z2.channel) == "web"
    assert (z1.group_id, z2.group_id) == ("d1", None)
    assert z1.assignee_email == "neha@acme.com" and z1.requester.email == "priya@example.org"
    assert (z2.requester.external_id, z2.requester.email) == ("c9", None)
    assert z1.fields == {"Order": "1042"}
    assert z2.resolved_at == datetime(2025, 2, 1, 9, tzinfo=UTC)


def test_threads_and_comments_become_messages_in_order() -> None:
    z1, z2 = tickets()
    assert [(m.kind, m.external_id, m.body) for m in z1.messages] == [
        ("customer", "thread-t1", "Where is my order 1042?"),
        ("note", "thread-t3", "Checked with the courier"),
        ("note", "comment-k1", "Call the courier"),
        ("agent", "thread-t2", "Sorry!\n\nIt ships today."),
        ("agent", "comment-k2", "Public update"),
    ]
    assert (z1.messages[0].author_name, z1.messages[0].author_email) == (
        "Priya Sharma", "priya@example.org")  # fmt: skip
    assert z2.messages == []  # Zoho answered 204: nothing there


def test_files_and_fetch_with_the_token_on_the_desk_host() -> None:
    seen: list[httpx.Request] = []
    c = connector(seen=seen)
    agent = next(m for m in run(c.page("tickets", None)).items[0].messages if m.attachments)
    href = "https://desk.zoho.in/api/v1/tickets/z1/threads/t2/attachments/a1/content"
    assert agent.attachments == [Attachment("label.pdf", href, 2048)]  # the http one is skipped
    assert run(c.fetch(agent.attachments[0], 1000)) == b"%PDF-label"
    assert seen[-1].headers["Authorization"] == "Zoho-oauthtoken zat-1"
    assert run(c.fetch(agent.attachments[0], 3)) is None


def test_articles_with_their_category_and_replies_said_so() -> None:
    c = connector()
    page = run(c.page("articles", None))
    assert [(a.external_id, a.title, a.body, a.category.name, a.published) for a in page.items] == [
        ("art1", "Refunds", "Steps to get a refund", "Billing", True)]  # fmt: skip
    assert run(c.page("replies", None)).items == []
    assert "aren't imported" in c.warnings()[0]


def test_changes_newest_first_stop_at_an_older_one() -> None:
    seen: list[httpx.Request] = []
    page = run(connector(seen=seen).changed_tickets(datetime(2025, 3, 1, tzinfo=UTC), None))
    assert [t.external_id for t in page.items] == ["z1"] and page.cursor is None
    asked = next(r for r in seen if r.url.path == "/api/v1/tickets")
    assert asked.url.params["sortBy"] == "-modifiedTime"


def test_a_429_waits() -> None:
    def limited(request: httpx.Request) -> httpx.Response:
        if request.url.host == ACCOUNTS:
            return route(request)
        return httpx.Response(429, headers={"Retry-After": "9"})

    with pytest.raises(RateLimited) as waited:
        run(connector(limited).check())
    assert waited.value.retry_after == 9


def test_it_only_reads_and_only_the_two_zoho_hosts() -> None:
    seen: list[httpx.Request] = []
    c = connector(seen=seen)
    run(c.check())
    run(c.discover())
    for kind in ("contacts", "tickets", "articles", "replies"):
        run(c.page(kind, None))
    posts = [r for r in seen if r.method != "GET"]
    assert {(r.method, r.url.host, r.url.path) for r in posts} == {
        ("POST", ACCOUNTS, "/oauth/v2/token")}  # fmt: skip
    assert {r.url.host for r in seen} == {DESK, ACCOUNTS}


@pytest.mark.parametrize(
    ("settings", "creds", "said"),
    [
        ({"region": "moon"}, {"client_id": "x", "client_secret": "y", "refresh_token": "z"},
         "data centre"),
        ({}, {"client_id": "x", "client_secret": "y", "refresh_token": "z"}, "region"),
        ({"region": "in"}, {"client_id": "x", "client_secret": "y"}, "refresh_token"),
    ],
)  # fmt: skip
def test_connecting_checks_region_and_credentials(
    settings: dict[str, str], creds: dict[str, str], said: str
) -> None:
    with pytest.raises(SourceError) as err:
        run(build_connector("zoho", settings, json.dumps(creds)))
    assert err.value.permanent and said in str(err.value)
