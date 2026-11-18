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
    """The ticket list: `updated_since` (inclusive), sorted by `order_by` (`updated_at` or
    `created_at`, ties by id) in `order_type` order, paged."""

    def handle(request: httpx.Request) -> httpx.Response:
        since = request.url.params["updated_since"]
        by = request.url.params.get("order_by", "created_at")
        found = sorted(
            (r for r in rows if r["updated_at"] >= since),
            key=lambda r: (r[by], r["id"]),
            reverse=request.url.params.get("order_type", "desc") == "desc",
        )
        return paged(found)(request)

    return handle


def row(i: int, updated: datetime, *, status: int = 2) -> dict[str, Any]:
    """A ticket as the list sends it (only the fields these tests look at)."""
    return {
        "id": i,
        "subject": f"T{i}",
        "status": status,
        "priority": 1,
        "source": 1,
        "requester": {"id": 1, "name": "A", "email": "a@example.org"},
        "created_at": (datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=i)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "updated_at": updated.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


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
        "/api/v2/companies/7": fixture("company_7.json"),
    }


def solution_routes() -> dict[str, Any]:
    return {
        "/api/v2/solutions/categories": fixture("solution_categories.json"),
        "/api/v2/solutions/categories/1/folders": fixture("solution_folders.json"),
        "/api/v2/solutions/folders/10/subfolders": fixture("solution_subfolders_10.json"),
        "/api/v2/solutions/folders/10/articles": fixture("solution_articles_10.json"),
        "/api/v2/solutions/folders/11/articles": [],
        "/api/v2/solutions/folders/12/articles": fixture("solution_articles_12.json"),
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
            **solution_routes(),
            "/api/v2/canned_response_folders": fixture("canned_folders.json"),
            "/api/v2/canned_response_folders/20/responses": fixture("canned_responses_20.json"),
        }
    )
    desk = Desk(routes)
    counts = await connect(desk).discover()
    assert counts == rules.Counts(contacts=250, tickets=42, articles=3, replies=1, groups=2)
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
    assert ticket.requester == rules.Contact(
        "201", "ann@example.org", "Ann", phone="+44 7700 900123", organization="Globex Ltd"
    )  # the mobile when there is no phone; the ticket's company
    assert ticket.created_at == datetime(2025, 3, 1, 10, 0, tzinfo=UTC)
    assert ticket.updated_at == datetime(2025, 3, 2, 10, 0, tzinfo=UTC)
    assert ticket.resolved_at == datetime(2025, 3, 1, 12, 0, tzinfo=UTC)
    assert ticket.csat == rules.Csat(5, "Great", datetime(2025, 3, 1, 13, 0, tzinfo=UTC))
    assert ticket.ticket_type == "Question"
    assert ticket.source_url == "https://acme.freshdesk.com/a/tickets/1"
    assert list(ticket.cc) == ["ben@example.org", "carla@example.org"]  # cc + reply cc, once
    assert ticket.sla_due_at == datetime(2025, 3, 3, 10, 0, tzinfo=UTC)
    assert ticket.sla_breached is True  # fr_escalated
    assert list(ticket.links) == []
    assert list(ticket.messages[0].to) == ["support@acme.freshdesk.com"]
    assert list(ticket.messages[0].cc) == ["ben@example.org"]
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
    assert list(messages[2].to) == ["support@acme.freshdesk.com"]
    assert list(messages[3].to) == ["ann@example.org"] and list(messages[3].cc) == [
        "ben@example.org"
    ]
    assert list(messages[3].bcc) == ["audit@acme.example"]
    assert list(messages[1].bcc) == []  # `null` in the source


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


def resolved_rows(n: int) -> list[dict[str, Any]]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    return [row(i, start + timedelta(minutes=i), status=4) for i in range(1, n + 1)]


async def test_a_403_on_ratings_is_counted_warned_and_kept_off_for_the_run() -> None:
    """C16: a plan without ratings answers 403. The import goes on, every resolved ticket
    left without its rating is counted, the warnings say so, and the cursor keeps ratings
    off so a later slice (a new connector) doesn't ask again."""
    forbidden = {
        "code": "access_denied",
        "message": "You are not authorized to perform this action.",
    }
    desk = Desk(
        base_routes()
        | {
            "/api/v2/tickets": ticket_list(resolved_rows(40)),
            "/api/v2/tickets/1/satisfaction_ratings": lambda r: httpx.Response(403, json=forbidden),
        }
    )
    connector = connect(desk, archived=False)
    with rules.collecting(rules.Losses()) as losses:
        page = await connector.page("tickets", None)
    assert len(page.items) == 40 and all(t.csat is None for t in page.items)
    assert losses.counts == {"csat_unavailable": 40}
    asked = [p for p in desk.paths() if p.endswith("/satisfaction_ratings")]
    assert asked == ["/api/v2/tickets/1/satisfaction_ratings"]  # asked once, then off
    assert any(
        "satisfaction ratings" in w and "csat_unavailable" in w for w in connector.warnings()
    )
    assert page.cursor is not None and page.cursor["csat_off"] is True
    assert not connector.failures.take()  # a plan limit, not a ticket's failure
    later = connect(desk, archived=False)
    await later.page("tickets", page.cursor)
    assert [p for p in desk.paths() if p.endswith("/satisfaction_ratings")] == asked


async def test_another_refusal_on_one_tickets_ratings_is_noted_for_that_ticket_only() -> None:
    """C16: a 400 on one ticket's ratings must not switch ratings off for the rest."""
    desk = Desk(
        base_routes()
        | {
            "/api/v2/tickets": ticket_list(resolved_rows(2)),
            "/api/v2/tickets/1/satisfaction_ratings": lambda r: httpx.Response(400, json={}),
            "/api/v2/tickets/2/satisfaction_ratings": fixture("ratings_1.json"),
        }
    )
    connector = connect(desk, archived=False)
    items, _ = await drain(connector, "tickets")
    assert items[0].csat is None and items[1].csat is not None and items[1].csat.score == 5
    noted = connector.failures.take()
    assert [(f.external_id, f.category) for f in noted] == [("1", "incomplete")]
    assert "satisfaction" in noted[0].error and KEY not in noted[0].error


async def test_tickets_across_pages_then_the_archive_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fd, "ARCHIVE_TAIL", 3)
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
    # one probe of a live id first, then every id up to the highest seen, then 3 past it
    assert probes[0] == "/api/v2/tickets/archived/120"
    assert probes[1:] == [f"/api/v2/tickets/archived/{i}" for i in range(1, 124)]


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
    assert old.requester == rules.Contact(
        "202",
        "bob@example.org",
        "Bob",
        phone="+1 555 0100",
        other_emails=("bob.b@example.net",),  # the main address is not repeated
        organization="Globex Ltd",
    )
    assert old.assignee_email == "kim@example.com" and old.priority == "low"
    assert [m.kind for m in old.messages] == ["customer", "agent"]
    assert old.messages[1].author_email == "kim@example.com"
    assert any("archived" in w for w in connect(Desk(routes)).warnings())


def refuse_archive(request: httpx.Request) -> httpx.Response | None:
    if request.url.path.startswith("/api/v2/tickets/archived/"):
        return httpx.Response(403, json={"code": "require_feature", "message": "not on plan"})
    return None


class Refusing(Desk):
    """A desk whose account may not use the archive API (403 on every archived id)."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        refused = refuse_archive(request)
        if refused is not None:
            self.seen.append(request)
            return refused
        return super().__call__(request)


async def test_a_plan_without_the_archive_api_is_noted_not_silent() -> None:
    """C10: before, a 403 on the first archived id dropped the walk and said nothing."""
    start = datetime(2025, 6, 1, tzinfo=UTC)
    desk = Refusing(base_routes() | {"/api/v2/tickets": ticket_list([row(4, start)])})
    connector = connect(desk)
    items, _ = await drain(connector, "tickets")
    assert [t.external_id for t in items] == ["4"]
    probes = [p for p in desk.paths() if "/archived/" in p]
    assert probes == ["/api/v2/tickets/archived/4"]  # the live id asked once, then no walk
    noted = connector.failures.take()
    assert [(f.kind, f.external_id, f.category) for f in noted] == [
        ("ticket", "archived-tickets", "incomplete")
    ]
    assert "archived tickets were not imported" in noted[0].error
    assert "NOT imported" in connector.warnings()[0]


async def test_the_dry_run_says_the_archive_api_is_refused_or_what_the_walk_costs() -> None:
    routes = base_routes() | {
        "/api/v2/search/tickets": {"results": [], "total": 1},
        "/api/v2/contacts": [],
        "/api/v2/solutions/categories": [],
        "/api/v2/canned_response_folders": [],
        "/api/v2/tickets": ticket_list([row(i, datetime(2025, 6, 1, tzinfo=UTC)) for i in (3, 9)]),
    }
    refused = connect(Refusing(routes))
    await refused.discover()
    assert "refused its archived tickets API" in refused.warnings()[0]
    open_desk = Desk(routes)
    allowed = connect(open_desk)
    await allowed.discover()
    first = allowed.warnings()[0]
    assert "Archived tickets are included" in first and "#9" in first
    assert f"about {9 + fd.ARCHIVE_TAIL} requests" in first
    newest = next(r for r in open_desk.seen if r.url.path == "/api/v2/tickets")
    assert newest.url.params["order_by"] == "created_at"
    assert newest.url.params["order_type"] == "desc"


async def test_archived_tickets_above_the_highest_live_id_are_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C10: an archived ticket newer than every live one (#7 and #12 here; #4 is the only
    live ticket, #8-#11 were deleted) used to be out of the walk's range."""
    monkeypatch.setattr(fd, "ARCHIVE_TAIL", 5)
    archived = fixture("archived_3.json")
    routes = base_routes() | {
        "/api/v2/tickets": ticket_list([row(4, datetime(2025, 6, 1, tzinfo=UTC))]),
        "/api/v2/tickets/archived/7": archived | {"id": 7},
        "/api/v2/tickets/archived/12": archived | {"id": 12},
        "/api/v2/contacts/202": fixture("contact_202.json"),
    }
    desk = Desk(routes)
    items, _ = await drain(connect(desk), "tickets")
    assert [t.external_id for t in items] == ["4", "7", "12"]
    probes = [
        int(p.rsplit("/", 1)[1]) for p in desk.paths() if "/archived/" in p and p[-1].isdigit()
    ]
    assert max(probes) == 12 + 5  # five misses past the last archived ticket end the walk


async def test_an_account_whose_tickets_are_all_archived_is_walked() -> None:
    archived = fixture("archived_3.json")
    routes = base_routes() | {
        "/api/v2/tickets": ticket_list([]),
        "/api/v2/tickets/archived/2": archived | {"id": 2},
        "/api/v2/contacts/202": fixture("contact_202.json"),
    }
    items, _ = await drain(connect(Desk(routes)), "tickets")
    assert [t.external_id for t in items] == ["2"]


async def test_a_refusal_on_one_archived_id_is_noted_and_the_walk_goes_on() -> None:
    archived = fixture("archived_3.json")
    routes = base_routes() | {
        "/api/v2/tickets": ticket_list([row(4, datetime(2025, 6, 1, tzinfo=UTC))]),
        "/api/v2/tickets/archived/1": lambda r: httpx.Response(403, json={}),
        "/api/v2/tickets/archived/3": archived,
        "/api/v2/contacts/202": fixture("contact_202.json"),
    }
    connector = connect(Desk(routes))
    items, _ = await drain(connector, "tickets")
    assert [t.external_id for t in items] == ["4", "3"]
    noted = connector.failures.take()
    assert [(f.external_id, f.category) for f in noted] == [("1", "not_found")]
    assert KEY not in noted[0].error


def test_archived_tickets_are_on_unless_turned_off() -> None:
    on = fd.make({"domain": "acme"}, {"api_key": KEY})
    off = fd.make({"domain": "acme", "include_archived": "false"}, {"api_key": KEY})
    assert isinstance(on, fd.FreshdeskConnector) and isinstance(off, fd.FreshdeskConnector)
    assert on._archived is True and off._archived is False


async def test_articles_come_with_their_folder_and_status() -> None:
    items, _ = await drain(connect(Desk(base_routes() | solution_routes())), "articles")
    assert [a.external_id for a in items] == ["100", "101", "102"]
    assert items[0].title == "Reset password" and items[0].body == "Click reset."
    # the category's name is kept with the folder's; a subfolder's article is not lost
    assert items[0].category == rules.Category("10", "Basics / Getting started")
    assert items[2].category == rules.Category("12", "Basics / Getting started / Accounts")
    assert items[0].published is True and items[1].published is False
    assert items[1].body == "wip"  # no description_text: the HTML is turned into text
    assert items[0].updated_at == datetime(2024, 3, 5, 16, 20, tzinfo=UTC)
    assert items[0].published_at == datetime(2024, 1, 11, 9, 0, tzinfo=UTC)
    assert items[1].published_at is None  # a draft was never published


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
            **solution_routes(),
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


def attachment_routes() -> dict[str, Any]:
    return {
        **base_routes(),
        "/api/v2/tickets": ticket_list([fixture("ticket_2_attachments.json")]),
        "/api/v2/tickets/2/conversations": fixture("conversations_2_attachments.json"),
    }


async def test_attachments_are_read_from_the_ticket_and_conversations() -> None:
    items, _ = await drain(connect(Desk(attachment_routes())), "tickets")
    first, second = items[0].messages
    assert list(first.attachments) == [
        rules.Attachment("photo.png", "https://s3.example.com/files/photo.png", 2048, "image/png")
    ]  # the http and the url-less entries are skipped
    assert list(second.attachments) == [
        rules.Attachment("log.txt", "https://s3.example.com/files/log.txt", 10, "text/plain")
    ]


async def test_fetch_returns_bytes_or_none_when_the_file_is_gone() -> None:
    desk = Desk({"/files/a.bin": lambda r: httpx.Response(200, content=b"data")})
    connector = connect(desk)
    assert isinstance(connector, rules.FileSource)
    ok = await connector.fetch(rules.Attachment("a", "https://s3.example.com/files/a.bin"), 100)
    gone = await connector.fetch(rules.Attachment("b", "https://s3.example.com/files/b.bin"), 100)
    big = await connector.fetch(rules.Attachment("a", "https://s3.example.com/files/a.bin"), 2)
    assert ok == b"data" and gone is None and big is None


async def test_changed_tickets_sends_updated_since_and_parses_the_page() -> None:
    desk = Desk(attachment_routes())
    connector = connect(desk)
    assert isinstance(connector, rules.ChangeSource)
    since = datetime(2025, 3, 15, 8, 30, tzinfo=UTC)
    page = await connector.changed_tickets(since, None)
    assert [t.external_id for t in page.items] == ["2"] and page.cursor is None
    sent = next(r for r in desk.seen if r.url.path == "/api/v2/tickets")
    assert sent.url.params["updated_since"] == "2025-03-15T08:30:00Z"
    assert {r.method for r in desk.seen} == {"GET"}
    assert not [p for p in desk.paths() if "/archived/" in p]


async def test_changed_tickets_pages_with_a_json_cursor() -> None:
    rows = [
        {
            "id": i,
            "subject": f"T{i}",
            "status": 2,
            "requester": {"id": 1, "name": "A"},
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-06-01T00:00:00Z",
        }
        for i in range(1, 131)
    ]
    desk = Desk(base_routes() | {"/api/v2/tickets": ticket_list(rows)})
    connector = connect(desk)
    since = datetime(2025, 5, 1, tzinfo=UTC)
    first = await connector.changed_tickets(since, None)
    assert len(first.items) == 100 and first.cursor is not None
    json.dumps(first.cursor)
    second = await connector.changed_tickets(since, first.cursor)
    assert len(second.items) == 30 and second.cursor is not None  # 30 may have a next page


# C18: tickets updated during the import


class Moving:
    """The ticket list, where ticket `moved` is updated (it goes to the end of the update
    order) right after page 1 was served: every ticket after its old place moves one up."""

    def __init__(self, rows: list[dict[str, Any]], moved: int) -> None:
        self.rows = rows
        self.moved = moved
        self.served_first = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        response = ticket_list(self.rows)(request)
        if request.url.params.get("page") == "1" and not self.served_first:
            self.served_first = True
            for r in self.rows:
                if r["id"] == self.moved:
                    r["updated_at"] = "2026-01-01T00:00:00Z"
        return response


async def test_a_ticket_updated_mid_import_does_not_hide_its_neighbour() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    rows = [row(i, start + timedelta(minutes=i)) for i in range(1, 151)]
    desk = Desk(base_routes() | {"/api/v2/tickets": Moving(rows, moved=5)})
    items, _ = await drain(connect(desk, archived=False), "tickets")
    ids = [int(t.external_id) for t in items]
    assert set(ids) == set(range(1, 151))  # #101 slipped onto page 1 and is still read
    assert ids.count(5) == 2 and len(ids) == 151  # #5 again, with its update, at the end
    light = [
        r for r in desk.seen if r.url.path == "/api/v2/tickets" and "include" not in r.url.params
    ]
    assert light  # the look-back reads ids and times only (1 credit a page)


async def test_an_untouched_list_is_read_once_with_one_cheap_look_back_per_page() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    rows = [row(i, start + timedelta(minutes=i)) for i in range(1, 251)]
    desk = Desk(base_routes() | {"/api/v2/tickets": ticket_list(rows)})
    items, _ = await drain(connect(desk, archived=False), "tickets")
    assert [int(t.external_id) for t in items] == list(range(1, 251))
    full = [r for r in desk.seen if r.url.path == "/api/v2/tickets" and "include" in r.url.params]
    pages = [r.url.params["page"] for r in full]
    assert pages == ["1", "2", "3", "4"]  # no page read twice in full


async def test_a_sync_page_also_looks_back() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    rows = [row(i, start + timedelta(minutes=i)) for i in range(1, 151)]
    connector = connect(Desk(base_routes() | {"/api/v2/tickets": Moving(rows, moved=7)}))
    since = datetime(2024, 12, 1, tzinfo=UTC)
    first = await connector.changed_tickets(since, None)
    assert first.cursor is not None
    second = await connector.changed_tickets(since, first.cursor)
    ids = [int(t.external_id) for t in [*first.items, *second.items]]
    assert set(ids) == set(range(1, 151))


# C6: a ticket's whole history


def endless(request: httpx.Request) -> httpx.Response:
    """Conversations that always fill the page."""
    page = int(request.url.params["page"])
    return httpx.Response(
        200,
        json=[
            {
                "id": page * 1000 + n,
                "body_text": f"part {page}.{n}",
                "incoming": True,
                "private": False,
                "user_id": 1,
                "created_at": "2025-01-02T00:00:00Z",
                "updated_at": "2025-01-02T00:00:00Z",
            }
            for n in range(100)
        ],
    )


async def test_conversations_are_read_past_the_list_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fd, "LIST_PAGE_CAP", 2)  # used to cap a ticket's conversations too

    def three_pages(request: httpx.Request) -> httpx.Response:
        if int(request.url.params["page"]) > 3:
            return httpx.Response(200, json=[])
        return endless(request)

    routes = base_routes() | {
        "/api/v2/tickets": ticket_list([row(1, datetime(2025, 1, 1, tzinfo=UTC))]),
        "/api/v2/tickets/1/conversations": three_pages,
    }
    connector = connect(Desk(routes), archived=False)
    items, _ = await drain(connector, "tickets")
    assert len(items[0].messages) == 300
    assert not connector.failures.take()


async def test_a_history_past_the_safety_limit_is_counted_and_noted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fd, "CONVERSATION_PAGES", 3)
    routes = base_routes() | {
        "/api/v2/tickets": ticket_list([row(1, datetime(2025, 1, 1, tzinfo=UTC))]),
        "/api/v2/tickets/1/conversations": endless,
    }
    connector = connect(Desk(routes), archived=False)
    with rules.collecting(rules.Losses()) as losses:
        items, _ = await drain(connector, "tickets")
    assert len(items[0].messages) == 300  # kept: what was read is imported
    assert losses.counts == {"history_capped": 1}
    noted = connector.failures.take()
    assert [(f.external_id, f.category) for f in noted] == [("1", "incomplete")]


# Dates


async def test_a_missing_date_falls_back_to_a_source_date_never_silently() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    no_created = row(1, start) | {"created_at": None}
    no_dates = row(2, start) | {"created_at": None, "updated_at": ""}
    routes = base_routes() | {
        "/api/v2/tickets": lambda r: httpx.Response(200, json=[no_created, no_dates]),
        "/api/v2/tickets/1/conversations": [
            {"id": 1, "body_text": "undated", "incoming": True, "private": False}
        ],
    }
    connector = connect(Desk(routes), archived=False)
    with rules.collecting(rules.Losses()) as losses:
        items, _ = await drain(connector, "tickets")
    assert items[0].created_at == start  # the update time stands in
    assert items[0].messages[0].created_at == start  # the ticket's time stands in
    assert items[1].created_at > datetime(2026, 1, 1, tzinfo=UTC)  # the import time, noted
    assert items[1].created_at.year != 1970
    assert losses.counts == {"date_replaced": 2, "date_missing": 1}
    noted = connector.failures.take()
    assert [(f.external_id, f.category) for f in noted] == [("2", "incomplete")]


# Contacts, associations


async def test_contacts_bring_phone_other_emails_and_company() -> None:
    rows = [
        fixture("contact_202.json"),
        {"id": 203, "name": "Cy", "email": None, "mobile": "+49 151 0000", "company_id": 99},
    ]
    routes = base_routes() | {"/api/v2/contacts": rows}
    items, _ = await drain(connect(Desk(routes)), "contacts")
    assert items[0].phone == "+1 555 0100" and items[0].organization == "Globex Ltd"
    assert list(items[0].other_emails) == ["bob.b@example.net"]
    assert items[1].phone == "+49 151 0000" and items[1].organization is None  # 404


async def test_a_refused_company_lookup_does_not_stop_contacts() -> None:
    routes = base_routes() | {
        "/api/v2/contacts": [fixture("contact_202.json")],
        "/api/v2/companies/7": lambda r: httpx.Response(403, json={}),
    }
    connector = connect(Desk(routes))
    items, _ = await drain(connector, "contacts")
    assert items[0].organization is None
    assert any("company" in w for w in connector.warnings())


async def test_associations_become_links() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    parent = row(1, start) | {"association_type": 1, "associated_tickets_list": [2, 3]}
    child = row(2, start + timedelta(minutes=1)) | {"association_type": 2}  # list not given
    desk = Desk(
        base_routes()
        | {
            "/api/v2/tickets": ticket_list([parent, child]),
            "/api/v2/tickets/2": row(2, start)
            | {"association_type": 2, "associated_tickets_list": [1]},
        }
    )
    items, _ = await drain(connect(desk, archived=False), "tickets")
    assert list(items[0].links) == [rules.Link("child", "2"), rules.Link("child", "3")]
    assert list(items[1].links) == [rules.Link("parent", "1")]
    assert desk.paths().count("/api/v2/tickets/2") == 1  # only the one without its list


# Files


async def test_the_key_never_goes_to_the_storage_host() -> None:
    desk = Desk({"/files/a.bin": lambda r: httpx.Response(200, content=b"data")})
    connector = connect(desk)
    await connector.fetch(rules.Attachment("a", "https://s3.example.com/files/a.bin"), 100)
    sent = desk.seen[-1]
    assert sent.url.host == "s3.example.com" and "authorization" not in sent.headers


async def test_an_expired_signed_link_is_asked_for_again_once() -> None:
    old = "https://s3.example.com/files/log.txt"
    new = "https://s3.example.com/files/log.txt?X-Amz-Signature=fresh"
    conv = fixture("conversations_2_attachments.json")
    fresh_conv = [
        {**conv[0], "attachments": [{**conv[0]["attachments"][0], "attachment_url": new}]}
    ]
    served = {"n": 0}

    def conversations(request: httpx.Request) -> httpx.Response:
        served["n"] += 1
        return httpx.Response(200, json=conv if served["n"] == 1 else fresh_conv)

    def file(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("X-Amz-Signature") == "fresh":
            return httpx.Response(200, content=b"log")
        return httpx.Response(
            403, text="<Error><Code>AccessDenied</Code>Request has expired</Error>"
        )

    routes = attachment_routes() | {
        "/api/v2/tickets/2/conversations": conversations,
        "/api/v2/tickets/2": fixture("ticket_2_attachments.json"),
        "/files/log.txt": file,
    }
    connector = connect(Desk(routes), archived=False)
    items, _ = await drain(connector, "tickets")
    attachment = items[0].messages[1].attachments[0]
    assert attachment.url == old
    assert await connector.fetch(attachment, 100) == b"log"
    gone = rules.Attachment("x", "https://s3.example.com/files/unknown.bin")
    assert await connector.fetch(gone, 100) is None  # not from a ticket read here: no retry


# A source with a per-window request quota (Freshdesk's trial: 50 a minute). The worker builds a
# new connector on every pass and, after a 429, waits for Retry-After before the next one.


class Quota:
    """Answers 429 once `per_window` requests were made in the current window."""

    def __init__(self, inner: Desk, per_window: int) -> None:
        self.inner = inner
        self.per_window = per_window
        self.used = 0

    def new_window(self) -> None:
        self.used = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.used += 1
        if self.used > self.per_window:
            return httpx.Response(429, headers={"Retry-After": "1"}, json={})
        return self.inner(request)


async def drain_across_windows(quota: Quota, kind: str, windows: int = 40) -> list[Any]:
    """The worker's loop: pages until a 429, then a new window and a new connector."""
    items: list[Any] = []
    cursor: Any = None
    for _ in range(windows):
        quota.new_window()
        connector = connect(quota)
        try:
            while True:
                page = await connector.page(kind, cursor)
                items.extend(page.items)
                cursor = page.cursor
                if cursor is None:
                    return items
                json.dumps(cursor)
        except RateLimited:
            continue
    raise AssertionError("never finished: a rate-limited page starts again from its first request")


async def test_the_archive_walk_keeps_its_place_when_a_429_cuts_a_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page of the walk is 100 ids, one request each. With 40 requests a window it can
    never be read whole; the ids done before the 429 must be kept (B-60)."""
    monkeypatch.setattr(fd, "ARCHIVE_TAIL", 5)
    archived = fixture("archived_3.json")
    routes = base_routes() | {
        "/api/v2/tickets": ticket_list([row(130, datetime(2025, 6, 1, tzinfo=UTC))]),
        "/api/v2/tickets/archived/7": archived | {"id": 7},
        "/api/v2/tickets/archived/90": archived | {"id": 90},
        "/api/v2/contacts/202": fixture("contact_202.json"),
    }
    desk = Desk(routes)
    items = await drain_across_windows(Quota(desk, 40), "tickets")
    assert sorted(int(t.external_id) for t in items) == [7, 90, 130]
    probes = [
        int(p.rsplit("/", 1)[1])
        for p in desk.paths()
        if p.rsplit("/", 1)[-1].isdigit() and "/archived/" in p
    ]
    assert max(probes) == 130 + 5


async def test_the_live_list_keeps_its_place_when_a_429_cuts_a_page() -> None:
    """30 tickets cost about 60 requests (conversations, ratings); 20 a window. The tickets
    read before the 429 are kept and the next window goes on after them, none twice."""
    start = datetime(2025, 1, 1, tzinfo=UTC)
    rows = [row(i, start + timedelta(minutes=i)) for i in range(1, 31)]
    desk = Desk(base_routes() | {"/api/v2/tickets": ticket_list(rows)})
    items = await drain_across_windows(Quota(desk, 20), "tickets")
    ids = [int(t.external_id) for t in items]
    assert ids == list(range(1, 31)), "every ticket once, in order"


async def test_a_429_before_any_ticket_of_a_page_is_read_still_pauses_the_run() -> None:
    desk = Desk(
        base_routes() | {"/api/v2/tickets": ticket_list([row(1, datetime(2025, 6, 1, tzinfo=UTC))])}
    )
    quota = Quota(desk, 0)
    with pytest.raises(RateLimited) as caught:
        await connect(quota).page("tickets", None)
    assert caught.value.retry_after == 1


async def test_the_files_on_a_tickets_first_message_come_from_the_ticket_view() -> None:
    """Freshdesk's ticket list has no `attachments`; only `GET /tickets/{id}` has them. A file
    on the first message was never read (checked against a live account, 18 Nov)."""
    full = fixture("ticket_2_attachments.json")
    listed = {k: v for k, v in full.items() if k != "attachments"}  # as the list sends it
    routes = {
        **base_routes(),
        "/api/v2/tickets": ticket_list([listed]),
        "/api/v2/tickets/2": full,
        "/api/v2/tickets/2/conversations": fixture("conversations_2_attachments.json"),
    }
    desk = Desk(routes)
    items, _ = await drain(connect(desk, archived=False), "tickets")
    first = items[0].messages[0]
    assert [a.name for a in first.attachments] == ["photo.png"]
    assert "/api/v2/tickets/2" in desk.paths()


async def test_a_ticket_deleted_after_it_was_listed_has_no_view_and_is_still_read() -> None:
    full = fixture("ticket_2_attachments.json")
    listed = {k: v for k, v in full.items() if k != "attachments"}
    routes = {
        **base_routes(),
        "/api/v2/tickets": ticket_list([listed]),
        "/api/v2/tickets/2/conversations": fixture("conversations_2_attachments.json"),
    }  # no view route: a 404
    items, _ = await drain(connect(Desk(routes), archived=False), "tickets")
    assert list(items[0].messages[0].attachments) == []
