"""The Freshdesk importer: driven by an httpx.MockTransport that routes by URL path, with a
no-op egress guard. No network, no real credentials."""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from cedesk import egress
from cedesk.helpdesk.domain import imports as rules
from cedesk.helpdesk.domain.imports import RateLimited, SourceError
from cedesk.helpdesk.service.imports import freshdesk as fd

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "imports" / "freshdesk"
KEY = "test-key-not-real"
Route = Callable[[httpx.Request], httpx.Response]


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def paged(rows: list[Any]) -> Route:
    """A list endpoint that honours `per_page` and `page`."""

    def handle(request: httpx.Request) -> httpx.Response:
        size = int(request.url.params.get("per_page", "30"))
        number = int(request.url.params.get("page", "1"))
        return httpx.Response(200, json=rows[(number - 1) * size : number * size])

    return handle


def ticket_list(rows: list[dict[str, Any]]) -> Route:
    """The ticket list: `updated_since` (inclusive), oldest update first, paged."""

    def handle(request: httpx.Request) -> httpx.Response:
        since = request.url.params["updated_since"]
        found = sorted(
            (r for r in rows if r["updated_at"] >= since), key=lambda r: (r["updated_at"], r["id"])
        )
        return paged(found)(request)

    return handle


class Desk:
    """Routes by path; anything not routed is a 404. Keeps every request it saw."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        route = self.routes.get(request.url.path)
        if route is None and request.url.path.endswith("/conversations"):
            route = []  # a ticket nobody replied to
        if route is None:
            return httpx.Response(404, json={"description": "not found"})
        if callable(route):
            result: httpx.Response = route(request)
            return result
        return paged(route)(request) if isinstance(route, list) else httpx.Response(200, json=route)

    def paths(self) -> list[str]:
        return [r.url.path for r in self.seen]


async def no_guard(host: str) -> None:
    return None


def connect(
    desk: Desk | Route, domain: str = "acme", *, archived: bool = True
) -> fd.FreshdeskConnector:
    """Archived tickets are opt-in in production; most tests here exercise them."""
    connector = fd.make(
        {"domain": domain, "include_archived": "true" if archived else "false"},
        {"api_key": KEY},
        transport=httpx.MockTransport(desk),
        guard=no_guard,
    )
    assert isinstance(connector, fd.FreshdeskConnector)
    return connector


async def drain(connector: fd.FreshdeskConnector, kind: str) -> tuple[list[Any], list[Any]]:
    items: list[Any] = []
    cursors: list[Any] = []
    cursor: Any = None
    for _ in range(50):
        page = await connector.page(kind, cursor)
        items.extend(page.items)
        cursor = page.cursor
        if cursor is None:
            return items, cursors
        json.dumps(cursor)  # the cursor must survive being stored
        cursors.append(cursor)
    raise AssertionError("never ended")


def base_routes() -> dict[str, Any]:
    return {
        "/api/v2/agents/me": {"id": 11, "contact": {"name": "Sam", "email": "sam@example.com"}},
        "/api/v2/agents": fixture("agents.json"),
        "/api/v2/groups": fixture("groups.json"),
        "/api/v2/tickets/1/conversations": fixture("conversations_1.json"),
        "/api/v2/tickets/1/satisfaction_ratings": fixture("ratings_1.json"),
    }


async def test_check_says_which_account() -> None:
    desk = Desk(base_routes())
    info = await connect(desk).check()
    assert info.name == "acme.freshdesk.com"
    assert desk.seen[0].url.host == "acme.freshdesk.com"


async def test_the_key_goes_as_basic_auth_with_x_as_password() -> None:
    desk = Desk(base_routes())
    await connect(desk).check()
    import base64

    token = desk.seen[0].headers["authorization"].split()[1]
    assert base64.b64decode(token).decode() == f"{KEY}:X"


async def test_groups() -> None:
    groups = await connect(Desk(base_routes())).groups()
    assert [(g.external_id, g.name) for g in groups] == [("5", "Billing"), ("6", "Support")]


async def test_discover_counts_and_estimates() -> None:
    routes = base_routes()
    routes.update(
        {
            "/api/v2/search/tickets": {"results": [], "total": 42},
            "/api/v2/contacts": [{"id": i, "name": f"C{i}"} for i in range(250)],
            "/api/v2/solutions/categories": fixture("solution_categories.json"),
            "/api/v2/solutions/categories/1/folders": fixture("solution_folders.json"),
            "/api/v2/solutions/folders/10/articles": fixture("solution_articles_10.json"),
            "/api/v2/solutions/folders/11/articles": [],
            "/api/v2/canned_response_folders": fixture("canned_folders.json"),
            "/api/v2/canned_response_folders/20/responses": fixture("canned_responses_20.json"),
        }
    )
    desk = Desk(routes)
    counts = await connect(desk).discover()
    assert counts == rules.Counts(contacts=250, tickets=42, articles=2, replies=1, groups=2)
    search = next(r for r in desk.seen if r.url.path == "/api/v2/search/tickets")
    assert search.url.params["query"].startswith('"created_at:>')


async def test_contacts_across_pages() -> None:
    rows = [{"id": i, "name": f"Name {i}", "email": f"c{i}@Example.org"} for i in range(1, 131)]
    rows.append({"id": 999, "name": "", "email": None})
    routes = base_routes() | {"/api/v2/contacts": rows}
    items, cursors = await drain(connect(Desk(routes)), "contacts")
    assert len(items) == 131 and len(cursors) == 2  # the 31 left may have a next page
    assert items[0] == rules.Contact("1", "c1@example.org", "Name 1")
    assert items[-1].email is None and items[-1].external_id == "999"


def ticket_routes(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        **base_routes(),
        "/api/v2/tickets": ticket_list([fixture("ticket_1.json")]),
        **(extra or {}),
    }


async def test_a_ticket_is_mapped_in_full() -> None:
    desk = Desk(ticket_routes())
    items, _ = await drain(connect(desk), "tickets")
    ticket = items[0]
    assert ticket.external_id == "1" and ticket.subject == "Where is my order"
    assert ticket.status == "4" and rules.status(ticket.status, fd.STATUS_MAP) == "resolved"
    assert ticket.priority == "high" and ticket.channel == "chat"
    assert ticket.group_id == "5" and ticket.assignee_email == "sam@example.com"
    assert ticket.tags == ["vip", "refund"]
    assert rules.clean_fields(ticket.fields) == {"order_id": "1042"}
    assert ticket.requester == rules.Contact("201", "ann@example.org", "Ann")
    assert ticket.created_at == datetime(2025, 3, 1, 10, 0, tzinfo=UTC)
    assert ticket.updated_at == datetime(2025, 3, 2, 10, 0, tzinfo=UTC)
    assert ticket.resolved_at == datetime(2025, 3, 1, 12, 0, tzinfo=UTC)
    assert ticket.csat == rules.Csat(5, "Great", datetime(2025, 3, 1, 13, 0, tzinfo=UTC))
    first = next(r for r in desk.seen if r.url.path == "/api/v2/tickets")
    params = first.url.params
    assert params["order_by"] == "updated_at" and params["order_type"] == "asc"
    assert params["per_page"] == "100" and "requester" in params["include"]
    assert params["updated_since"] < "2005"


async def test_conversations_are_notes_customers_and_agents() -> None:
    items, _ = await drain(connect(Desk(ticket_routes())), "tickets")
    messages = items[0].messages
    assert [m.kind for m in messages] == ["customer", "note", "customer", "agent"]
    assert messages[0].body == "Where is it?"  # the description is the first message
    assert messages[1].body == "Check with warehouse"
    assert messages[1].author_email == "sam@example.com"  # a note by an agent
    assert messages[2].body == "Any news?" and messages[2].author_email == "ann@example.org"
    assert messages[3].author_email == "sam@example.com" and messages[3].author_name == "Sam Agent"


def test_status_codes_and_unknown_custom_status() -> None:
    for code, want in (("2", "open"), ("3", "pending"), ("4", "resolved"), ("5", "resolved")):
        assert rules.status(code, fd.STATUS_MAP) == want
    assert rules.status("17", fd.STATUS_MAP) == "open"
    assert fd.FreshdeskConnector.source == "freshdesk"


async def test_priority_and_channel_codes() -> None:
    rows = []
    for number, (prio, src) in enumerate([(1, 1), (2, 2), (3, 3), (4, 9), (None, 10)], start=1):
        rows.append(
            {
                "id": number,
                "subject": "s",
                "status": 2,
                "priority": prio,
                "source": src,
                "requester": {"id": 1, "email": "a@example.org", "name": "A"},
                "created_at": "2025-01-01T00:00:00Z",
                "updated_at": f"2025-01-0{number}T00:00:00Z",
            }
        )
    routes = base_routes() | {"/api/v2/tickets": ticket_list(rows)}
    items, _ = await drain(connect(Desk(routes)), "tickets")
    assert [t.priority for t in items] == ["low", "medium", "high", "urgent", None]
    assert [t.channel for t in items] == ["email", "portal", "phone", "web", "email"]
    assert all(t.csat is None for t in items)  # open tickets are not asked for a rating


@pytest.mark.parametrize(
    ("value", "score"), [(103, 5), (102, 5), (101, 4), (100, 3), (-101, 2), (-102, 1), (-103, 1)]
)
async def test_csat_scale_is_folded_onto_one_to_five(value: int, score: int) -> None:
    rating = [{"ratings": {"default_question": value}, "created_at": "2025-03-01T13:00:00Z"}]
    routes = ticket_routes({"/api/v2/tickets/1/satisfaction_ratings": rating})
    items, _ = await drain(connect(Desk(routes)), "tickets")
    assert items[0].csat is not None and items[0].csat.score == score


async def test_a_key_that_may_not_read_ratings_does_not_stop_the_import() -> None:
    routes = ticket_routes(
        {"/api/v2/tickets/1/satisfaction_ratings": lambda r: httpx.Response(403, json={})}
    )
    items, _ = await drain(connect(Desk(routes)), "tickets")
    assert len(items) == 1 and items[0].csat is None


async def test_tickets_across_pages_then_the_archive_walk() -> None:
    rows = [
        {
            "id": i,
            "subject": f"T{i}",
            "status": 2,
            "requester": {"id": 1, "name": "A"},
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": (datetime(2025, 1, 1, tzinfo=UTC) + timedelta(minutes=i)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        }
        for i in range(1, 121)
    ]
    desk = Desk(base_routes() | {"/api/v2/tickets": ticket_list(rows)})
    items, cursors = await drain(connect(desk), "tickets")
    assert [t.external_id for t in items] == [str(i) for i in range(1, 121)]
    assert [c.get("phase") for c in cursors] == ["list", "archive", "archive"]
    probes = [p for p in desk.paths() if p.startswith("/api/v2/tickets/archived/")]
    assert len(probes) == 120  # every id up to the highest seen, once


async def test_the_window_moves_at_the_page_cap_without_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fd, "LIST_PAGE_CAP", 2)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    rows = [
        {
            "id": i,
            "subject": f"T{i}",
            "status": 2,
            "requester": {"id": 1, "name": "A"},
            "created_at": "2025-01-01T00:00:00Z",
            # two tickets share each second, so the edge of a window is a tie
            "updated_at": (start + timedelta(seconds=i // 2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        for i in range(1, 251)
    ]
    desk = Desk(base_routes() | {"/api/v2/tickets": ticket_list(rows)})
    items, cursors = await drain(connect(desk), "tickets")
    ids = [t.external_id for t in items]
    assert len(ids) == len(set(ids)) == 250
    assert any(c.get("since", fd.EARLIEST) != fd.EARLIEST for c in cursors)
    since = [r.url.params["updated_since"] for r in desk.seen if r.url.path == "/api/v2/tickets"]
    assert len(set(since)) > 1  # the window did move


async def test_an_archived_ticket_is_read_by_id() -> None:
    live = {
        "id": 4,
        "subject": "Live",
        "status": 2,
        "requester": {"id": 1, "name": "A"},
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-06-01T00:00:00Z",
    }
    routes = base_routes() | {
        "/api/v2/tickets": ticket_list([live]),
        "/api/v2/tickets/archived/3": fixture("archived_3.json"),
        "/api/v2/tickets/archived/3/conversations": fixture("archived_3_conversations.json"),
        "/api/v2/contacts/202": fixture("contact_202.json"),
    }
    items, _ = await drain(connect(Desk(routes)), "tickets")
    old = next(t for t in items if t.external_id == "3")
    assert old.subject == "Old problem" and rules.status(old.status, fd.STATUS_MAP) == "resolved"
    assert old.requester == rules.Contact("202", "bob@example.org", "Bob")
    assert old.assignee_email == "kim@example.com" and old.priority == "low"
    assert [m.kind for m in old.messages] == ["customer", "agent"]
    assert old.messages[1].author_email == "kim@example.com"
    assert any("archived" in w for w in connect(Desk(routes)).warnings())  # type: ignore[attr-defined]


async def test_a_plan_without_the_archive_api_drops_the_walk() -> None:
    live = {
        "id": 4,
        "subject": "Live",
        "status": 2,
        "requester": {"id": 1, "name": "A"},
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-06-01T00:00:00Z",
    }
    routes = base_routes() | {
        "/api/v2/tickets": ticket_list([live]),
        "/api/v2/tickets/archived/1": lambda r: httpx.Response(403, json={}),
    }
    items, _ = await drain(connect(Desk(routes)), "tickets")
    assert [t.external_id for t in items] == ["4"]


async def test_articles_come_with_their_folder_and_status() -> None:
    routes = base_routes() | {
        "/api/v2/solutions/categories": fixture("solution_categories.json"),
        "/api/v2/solutions/categories/1/folders": fixture("solution_folders.json"),
        "/api/v2/solutions/folders/10/articles": fixture("solution_articles_10.json"),
        "/api/v2/solutions/folders/11/articles": [],
    }
    items, _ = await drain(connect(Desk(routes)), "articles")
    assert [a.external_id for a in items] == ["100", "101"]
    assert items[0].title == "Reset password" and items[0].body == "Click reset."
    assert items[0].category == rules.Category("10", "Getting started")
    assert items[0].published is True and items[1].published is False
    assert items[1].body == "wip"  # no description_text: the HTML is turned into text


async def test_canned_responses_become_replies() -> None:
    routes = base_routes() | {
        "/api/v2/canned_response_folders": fixture("canned_folders.json"),
        "/api/v2/canned_response_folders/20/responses": fixture("canned_responses_20.json"),
    }
    items, _ = await drain(connect(Desk(routes)), "replies")
    assert items == [rules.Reply("300", "thanks-bye", "Thanks\nBye")]


async def test_an_unknown_kind_is_an_empty_last_page() -> None:
    page = await connect(Desk(base_routes())).page("nothing", None)
    assert list(page.items) == [] and page.cursor is None


def test_html_to_text() -> None:
    html = "<style>x{}</style><p>Hi&nbsp;<b>there</b></p><ul><li>one</li><li>two</li></ul>a<br>b"
    assert fd.html_to_text(html) == "Hi there\n- one\n- two\na\nb"
    assert fd.html_to_text(None) == ""


async def test_a_429_is_rate_limited_with_retry_after() -> None:
    desk: Route = lambda r: httpx.Response(429, headers={"Retry-After": "34"})  # noqa: E731
    with pytest.raises(RateLimited) as caught:
        await connect(desk).check()
    assert caught.value.retry_after == 34


async def test_a_401_is_permanent_and_never_shows_the_key() -> None:
    desk: Route = lambda r: httpx.Response(401, json={"code": "invalid_credentials"})  # noqa: E731
    with pytest.raises(SourceError) as caught:
        await connect(desk).check()
    assert caught.value.permanent is True
    assert KEY not in str(caught.value)


async def test_a_server_error_is_temporary() -> None:
    desk: Route = lambda r: httpx.Response(503)  # noqa: E731
    with pytest.raises(SourceError) as caught:
        await connect(desk).check()
    assert caught.value.permanent is False


@pytest.mark.parametrize("domain", ["evil.com", "a.b", "", "127.0.0.1"])
def test_a_bad_domain_is_refused_before_any_call(domain: str) -> None:
    with pytest.raises(egress.BadAddress):
        fd.make({"domain": domain}, {"api_key": KEY})


def test_a_full_address_is_reduced_to_the_account() -> None:
    connector = fd.make({"domain": "https://Acme.freshdesk.com/a/tickets"}, {"api_key": KEY})
    assert isinstance(connector, fd.FreshdeskConnector)
    assert connector._host == "acme.freshdesk.com"


async def test_every_request_is_a_get() -> None:
    routes = ticket_routes(
        {
            "/api/v2/search/tickets": {"total": 1},
            "/api/v2/contacts": [{"id": 1, "name": "A"}],
            "/api/v2/solutions/categories": fixture("solution_categories.json"),
            "/api/v2/solutions/categories/1/folders": fixture("solution_folders.json"),
            "/api/v2/solutions/folders/10/articles": fixture("solution_articles_10.json"),
            "/api/v2/solutions/folders/11/articles": [],
            "/api/v2/canned_response_folders": fixture("canned_folders.json"),
            "/api/v2/canned_response_folders/20/responses": fixture("canned_responses_20.json"),
        }
    )
    desk = Desk(routes)
    connector = connect(desk)
    await connector.check()
    await connector.discover()
    await connector.groups()
    for kind in rules.KINDS:
        await drain(connector, kind)
    assert desk.seen and {r.method for r in desk.seen} == {"GET"}


async def test_archived_tickets_are_not_walked_unless_asked_for() -> None:
    rows = [
        {
            "id": i,
            "subject": f"T{i}",
            "status": 2,
            "requester": {"id": 1, "name": "A"},
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-02T00:00:00Z",
        }
        for i in range(1, 6)
    ]
    desk = Desk(base_routes() | {"/api/v2/tickets": ticket_list(rows)})
    connector = connect(desk, archived=False)
    items, cursors = await drain(connector, "tickets")
    assert len(items) == 5 and all(c.get("phase") != "archive" for c in cursors)
    assert not [p for p in desk.paths() if "/archived/" in p]  # not one probe was sent
    assert "NOT imported" in connector.warnings()[0]
    assert "include_archived" in connector.warnings()[0]
    assert "included" in connect(desk, archived=True).warnings()[0]
