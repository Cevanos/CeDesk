"""The Zendesk importer: driven with a mock transport over JSON fixtures (no network)."""

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from cedesk import egress
from cedesk.helpdesk.domain import imports as rules
from cedesk.helpdesk.domain.imports import RateLimited, SourceConnector, SourceError
from cedesk.helpdesk.service.imports import zendesk

FIXTURES = Path(__file__).parent.parent / "fixtures" / "imports" / "zendesk"
SECRET = "SECRET-TOKEN-123"


def fx(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return data


async def no_guard(host: str) -> None:
    return None


class Desk:
    """A fake Zendesk: routes by path and query, records every request."""

    def __init__(self, override: Callable[[httpx.Request], httpx.Response | None] | None = None):
        self.requests: list[httpx.Request] = []
        self.override = override

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.override and (answer := self.override(request)):
            return answer
        path, q = request.url.path, dict(request.url.params)
        after = q.get("page[after]")
        if path == "/api/v2/users/me.json":
            return httpx.Response(200, json=fx("me"))
        if path == "/api/v2/tickets/count.json":
            return httpx.Response(200, json=fx("tickets_count"))
        if path == "/api/v2/users/count.json":
            return httpx.Response(200, json=fx("users_count"))
        if path == "/api/v2/groups.json":
            return httpx.Response(200, json=fx("groups"))
        if path == "/api/v2/ticket_fields.json":
            return httpx.Response(200, json=fx("ticket_fields"))
        if path == "/api/v2/users.json":
            return httpx.Response(200, json=fx("users_end_2" if after else "users_end"))
        if path == "/api/v2/users/show_many.json":
            ids = set(q["ids"].split(","))
            users = [u for u in fx("users_show_many")["users"] if str(u["id"]) in ids]
            return httpx.Response(200, json={"users": users})
        if path == "/api/v2/incremental/tickets/cursor.json":
            return httpx.Response(
                200, json=fx("incremental_2" if "cursor" in q else "incremental_1")
            )
        if path == "/api/v2/tickets/101/comments.json":
            return httpx.Response(200, json=fx("comments_101_2" if after else "comments_101"))
        if path == "/api/v2/tickets/104/comments.json":
            return httpx.Response(200, json=fx("comments_104"))
        if path.startswith("/api/v2/tickets/") and path.endswith("/comments.json"):
            return httpx.Response(404, json={"error": "RecordNotFound"})  # archived
        if path == "/api/v2/help_center/articles.json":
            return httpx.Response(200, json=fx("articles"))
        if path == "/api/v2/macros.json":
            return httpx.Response(200, json=fx("macros"))
        return httpx.Response(500, json={"error": f"unrouted {path}"})


def connector(desk: Desk | None = None, subdomain: str = "acme") -> SourceConnector:
    return zendesk.make(
        {"subdomain": subdomain},
        {"email": "sam@acme.test", "token": SECRET},
        transport=httpx.MockTransport(desk or Desk()),
        guard=no_guard,
    )


def run[T](coro: Any) -> T:
    return asyncio.run(coro)  # type: ignore[no-any-return]


def all_tickets(c: SourceConnector) -> tuple[list[Any], int]:
    async def go() -> tuple[list[Any], int]:
        out: list[Any] = []
        cursor = None
        pages = 0
        while True:
            page = await c.page("tickets", cursor)
            out.extend(page.items)
            pages += 1
            if page.cursor is None:
                return out, pages
            cursor = page.cursor

    return run(go())


def test_check_ok_and_identity() -> None:
    desk = Desk()
    info = run(connector(desk).check())
    assert info.name == "acme.zendesk.com"
    auth = desk.requests[0].headers["authorization"]
    import base64

    assert base64.b64decode(auth.split()[1]).decode() == f"sam@acme.test/token:{SECRET}"


def test_check_refuses_an_end_user() -> None:
    desk = Desk(lambda r: httpx.Response(200, json=fx("me_enduser")))
    with pytest.raises(SourceError) as err:
        run(connector(desk).check())
    assert err.value.permanent


def test_discover_and_groups() -> None:
    c = connector()
    counts = run(c.discover())
    assert (counts.tickets, counts.contacts, counts.articles) == (3, 2, 2)
    assert counts.replies == 2 and counts.groups == 2  # the macro without a comment is not one
    groups = run(c.groups())
    assert [(g.external_id, g.name) for g in groups] == [("71", "Billing"), ("72", "Support")]


def test_contacts_follow_the_cursor() -> None:
    c = connector()
    first = run(c.page("contacts", None))
    assert first.cursor == {"after": "u2"}
    assert first.items[0].email == "ann@example.org" and first.items[1].email is None
    second = run(c.page("contacts", first.cursor))
    assert second.cursor is None and second.items[0].external_id == "3"


def test_tickets_across_two_pages_skip_deleted_and_handle_archived() -> None:
    desk = Desk()
    c = connector(desk)
    first = run(c.page("tickets", None))
    assert first.cursor == {"cursor": "cur1"}  # JSON-serialisable, resumable
    json.dumps(first.cursor)
    assert [t.external_id for t in first.items] == ["101", "103"]  # 102 is deleted
    second = run(c.page("tickets", first.cursor))
    assert second.cursor is None and [t.external_id for t in second.items] == ["104"]
    inc = [r for r in desk.requests if "incremental" in r.url.path]
    assert inc[0].url.params["start_time"] == "1" and "cursor" not in inc[0].url.params
    assert inc[1].url.params["cursor"] == "cur1"
    # the archived ticket's comments answered 404: its description is its one message
    old = first.items[1]
    assert [(m.kind, m.body) for m in old.messages] == [("customer", "Archived question")]
    assert old.created_at < datetime(2024, 6, 1, tzinfo=UTC)


def test_full_walk_ends_at_end_of_stream() -> None:
    tickets, pages = all_tickets(connector())
    assert pages == 2 and len(tickets) == 3


def test_comments_kinds_authors_and_order_of_calls() -> None:
    desk = Desk()
    t = run(connector(desk).page("tickets", None)).items[0]
    assert [(m.kind, m.author_email, m.body) for m in t.messages] == [
        ("customer", "ann@example.org", "Where is my refund?"),
        ("note", "sam@acme.test", "Checking with finance"),  # public: false
        ("agent", "lee@acme.test", "Refund sent."),  # second comments page
    ]
    assert t.messages[2].author_name == "Lee Agent" and t.messages[0].external_id == "1001"
    many = [r for r in desk.requests if r.url.path.endswith("show_many.json")]
    assert len(many) == 1  # one batch for the whole page


def test_ticket_fields_mapping() -> None:
    t = run(connector().page("tickets", None)).items[0]
    assert t.status == "solved" and t.priority == "high" and t.channel == "email"
    assert t.tags == ["vip", "Refund Request"]
    assert t.fields == {"Order ID": "A-1042"}  # the null one is left out
    assert t.group_id == "71" and t.assignee_email == "sam@acme.test"
    assert t.csat is not None and t.csat.score == 5 and t.csat.comment == "Thanks!"
    assert t.created_at == datetime(2025, 3, 1, 10, 0, tzinfo=UTC)
    assert t.updated_at == datetime(2025, 3, 2, 9, 0, tzinfo=UTC) == t.resolved_at
    assert t.requester.email == "ann@example.org" and t.requester.name == "Ann Customer"


def test_status_map_priority_channel_and_csat_variants() -> None:
    c = connector()
    for word, expected in [
        ("new", "open"), ("open", "open"), ("pending", "pending"), ("hold", "pending"),
        ("solved", "resolved"), ("closed", "resolved"),
    ]:  # fmt: skip
        assert rules.status(word, c.status_map) == expected
    assert c.source == "zendesk"
    tickets, _ = all_tickets(c)
    old, chat = tickets[1], tickets[2]
    assert old.csat is None  # unoffered
    assert old.channel == "web" and rules.priority(old.priority) == "medium"
    assert old.resolved_at is not None  # closed
    assert chat.channel == "chat" and chat.priority == "urgent"
    assert chat.csat is not None and chat.csat.score == 1
    assert chat.resolved_at is None and chat.assignee_email == "lee@acme.test"


def test_articles_with_drafts_and_category() -> None:
    page = run(connector().page("articles", None))
    assert page.cursor is None
    published, draft = page.items
    assert published.published and not draft.published
    assert published.category is not None and published.category.name == "Getting started"
    assert published.title == "Reset password"


def test_macro_replies() -> None:
    page = run(connector().page("replies", None))
    assert [(r.external_id, r.shortcut, r.body) for r in page.items] == [
        ("401", "billing-refund-done", "Your refund is on its way."),
        ("403", "html-one", "Hello there"),
    ]


def test_rate_limit_carries_retry_after() -> None:
    desk = Desk(lambda r: httpx.Response(429, headers={"Retry-After": "42"}))
    with pytest.raises(RateLimited) as err:
        run(connector(desk).page("tickets", None))
    assert err.value.retry_after == 42


def test_401_is_permanent_and_hides_credentials() -> None:
    desk = Desk(lambda r: httpx.Response(401, json={"error": "Couldn't authenticate you"}))
    with pytest.raises(SourceError) as err:
        run(connector(desk).check())
    assert err.value.permanent
    assert SECRET not in str(err.value) and "sam@acme.test" not in str(err.value)


def test_server_error_is_temporary_and_hides_credentials() -> None:
    desk = Desk(lambda r: httpx.Response(503))
    with pytest.raises(SourceError) as err:
        run(connector(desk).check())
    assert not err.value.permanent and SECRET not in str(err.value)


@pytest.mark.parametrize("bad", ["evil.com", "a.b", "", "10.0.0.1", "a@b"])
def test_bad_subdomain_is_refused(bad: str) -> None:
    with pytest.raises(egress.BadAddress):
        connector(subdomain=bad)


def test_every_request_is_a_get_to_the_account_host() -> None:
    desk = Desk()
    c = connector(desk)
    run(c.check())
    run(c.discover())
    for kind in rules.KINDS:
        run(c.page(kind, None))
    assert desk.requests and {r.method for r in desk.requests} == {"GET"}
    assert {r.url.host for r in desk.requests} == {"acme.zendesk.com"}
