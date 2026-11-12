"""The Gorgias importer, driven by recorded-shape JSON and `httpx.MockTransport`; no network."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from cedesk import egress
from cedesk.helpdesk.domain import imports as rules
from cedesk.helpdesk.domain.imports import RateLimited, SourceError
from cedesk.helpdesk.service.imports import gorgias

FIXTURES = Path(__file__).parent.parent / "fixtures" / "imports" / "gorgias"
KEY = "sekret-api-key-123"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


async def allow(host: str) -> None:
    return None


def route(request: httpx.Request) -> httpx.Response:
    path, query = request.url.path, request.url.params
    cursor = query.get("cursor")
    if path == "/api/account":
        return httpx.Response(200, json=fixture("account"))
    if path == "/api/customers":
        return httpx.Response(200, json=fixture("customers_2" if cursor else "customers_1"))
    if path == "/api/teams":
        return httpx.Response(200, json=fixture("teams"))
    if path == "/api/macros":
        return httpx.Response(200, json=fixture("macros"))
    if path == "/api/tickets":
        if query.get("order_by") == "updated_datetime:desc":
            return httpx.Response(200, json=fixture("tickets_changed"))
        return httpx.Response(200, json=fixture("tickets_2" if cursor else "tickets_1"))
    if path == "/api/messages":
        name = {"100": "messages_100", "200": "messages_attach"}.get(
            query.get("ticket_id", ""), "messages_empty"
        )
        return httpx.Response(200, json=fixture(name))
    return httpx.Response(404, json={})


def connector(handler: Any = route, seen: list[httpx.Request] | None = None) -> Any:
    def wrapped(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return handler(request)

    return gorgias.make(
        {"domain": "acme"},
        {"email": "me@acme.com", "api_key": KEY},
        transport=httpx.MockTransport(wrapped),
        guard=allow,
    )


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def tickets() -> list[Any]:
    c = connector()
    first = run(c.page("tickets", None))
    second = run(c.page("tickets", first.cursor))
    assert second.cursor is None
    return [*first.items, *second.items]


def test_check_reads_the_account_with_basic_auth_under_api() -> None:
    seen: list[httpx.Request] = []
    info = run(connector(seen=seen).check())
    assert info.name == "acme.gorgias.com"
    assert str(seen[0].url) == "https://acme.gorgias.com/api/account"
    assert seen[0].headers["authorization"].startswith("Basic ")


def test_discover_uses_totals_and_counts_by_paging_without_them() -> None:
    counts = run(connector().discover())
    assert (counts.contacts, counts.tickets, counts.replies, counts.groups) == (3, 5, 3, 2)
    assert counts.articles == 0

    def no_totals(request: httpx.Request) -> httpx.Response:
        body = route(request).json()
        body["meta"].pop("total_resources", None)
        return httpx.Response(200, json=body)

    assert run(connector(no_totals).discover()).contacts == 3  # 2 + 1 across two pages


def test_groups_are_the_teams() -> None:
    groups = run(connector().groups())
    assert [(g.external_id, g.name) for g in groups] == [("10", "Billing"), ("11", "Support")]


def test_contacts_page_by_cursor() -> None:
    c = connector()
    first = run(c.page("contacts", None))
    assert first.cursor == {"cursor": "c2"}
    assert first.items[0].email == "ann@example.com" and first.items[0].name == "Ann Lee"
    assert first.items[1].email is None
    second = run(c.page("contacts", first.cursor))
    assert second.cursor is None
    assert [x.external_id for x in second.items] == ["3"]


def test_tickets_page_by_cursor_and_skip_spam_and_trashed() -> None:
    found = tickets()
    assert [t.external_id for t in found] == ["100", "101", "104"]


def test_tickets_ask_for_updated_order_and_no_trashed() -> None:
    seen: list[httpx.Request] = []
    run(connector(seen=seen).page("tickets", None))
    query = seen[0].url.params
    assert query["order_by"] == "updated_datetime:asc"
    assert query["limit"] == "100" and query["trashed"] == "false"


def test_messages_kinds_emails_and_text_versus_html() -> None:
    messages = tickets()[0].messages
    assert [m.kind for m in messages] == ["customer", "agent", "note"]  # deleted one dropped
    assert messages[0].body == "Where is it?" and messages[0].author_email == "ann@example.com"
    assert messages[1].author_email == "agent@acme.com"
    assert messages[1].body == "Hello\nIt ships today."  # html only: stripped, style dropped
    assert messages[2].body == "internal note"


def test_status_priority_channel_tags_assignee_team_dates_fields_csat() -> None:
    open_one, snoozed, closed = tickets()
    c = connector()
    assert rules.status(open_one.status, c.status_map) == "open"
    assert snoozed.status == "snoozed" and rules.status(snoozed.status, c.status_map) == "pending"
    assert rules.status(closed.status, c.status_map) == "resolved"
    assert open_one.priority == "high" and rules.priority(closed.priority) == "urgent"
    assert rules.channel(snoozed.channel) == "chat"
    assert rules.channel(closed.channel) == "web"  # help-center: the customer used a web page
    assert open_one.tags == ["VIP Order", "refund"]
    assert open_one.assignee_email == "agent@acme.com" and open_one.group_id == "10"
    assert open_one.requester.external_id == "1" and open_one.requester.email == "ann@example.com"
    assert open_one.created_at.isoformat() == "2025-03-01T10:00:00+00:00"
    assert open_one.updated_at is not None and open_one.resolved_at is None
    assert closed.resolved_at is not None and closed.resolved_at.day == 8
    assert open_one.fields == {"Order Number": "A-1"}
    assert open_one.csat is not None and open_one.csat.score == 4
    assert open_one.csat.comment == "Good" and open_one.csat.rated_at is not None
    assert snoozed.csat is None and snoozed.requester.email is None


def test_macros_become_replies_and_others_are_left_out() -> None:
    page = run(connector().page("replies", None))
    assert [(r.external_id, r.shortcut, r.body) for r in page.items] == [
        ("7", "refund-policy", "Refunds take 5 days."),
        ("9", "html-only", "Hi there"),
    ]


def test_articles_are_documented_empty() -> None:
    page = run(connector().page("articles", None))
    assert list(page.items) == [] and page.cursor is None


def test_a_429_is_rate_limited_with_retry_after() -> None:
    def limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "7"})

    with pytest.raises(RateLimited) as caught:
        run(connector(limited).page("contacts", None))
    assert caught.value.retry_after == 7


def test_a_401_is_permanent_and_never_shows_the_key() -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": f"bad {KEY}"})

    with pytest.raises(SourceError) as caught:
        run(connector(refused).check())
    assert caught.value.permanent
    assert KEY not in str(caught.value) and "me@acme.com" not in str(caught.value)


def test_a_server_error_is_temporary_and_clean() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with pytest.raises(SourceError) as caught:
        run(connector(down).check())
    assert not caught.value.permanent and KEY not in str(caught.value)


@pytest.mark.parametrize("domain", ["evil.com", "a.b", "", "acme.evil.com"])
def test_a_bad_domain_is_refused(domain: str) -> None:
    with pytest.raises(egress.BadAddress):
        gorgias.make({"domain": domain}, {"email": "e", "api_key": "k"})


SINCE = datetime(2025, 6, 5, tzinfo=UTC)


def test_changed_tickets_ask_newest_first_and_stop_at_an_older_one() -> None:
    seen: list[httpx.Request] = []
    c = connector(seen=seen)
    assert isinstance(c, rules.ChangeSource)
    page = run(c.changed_tickets(SINCE, None))
    assert seen[0].url.path == "/api/tickets"
    assert seen[0].url.params["order_by"] == "updated_datetime:desc"
    assert [t.external_id for t in page.items] == ["200"]  # spam skipped, 202 and 203 too old
    assert page.cursor is None  # the stop: "t9" is not followed
    assert {r.method for r in seen} == {"GET"}


def test_changed_tickets_continue_when_all_are_new_enough() -> None:
    page = run(connector().changed_tickets(datetime(2025, 1, 1, tzinfo=UTC), {"cursor": "t5"}))
    assert [t.external_id for t in page.items] == ["200", "202", "203"]
    assert page.cursor == {"cursor": "t9"}


def test_attachments_are_parsed_and_unusable_ones_skipped() -> None:
    page = run(connector().changed_tickets(SINCE, None))
    first, second = page.items[0].messages  # the third message has no text and no files
    assert first.attachments == [
        rules.Attachment("photo.png", "https://files.example.com/a/photo.png", 2048, "image/png"),
        rules.Attachment("file", "https://files.example.com/a/bare", None, None),
    ]
    assert second.body == "(attachment only)"
    assert second.attachments[0].name == "doc.pdf" and second.attachments[0].mime == (
        "application/pdf"
    )


def test_fetch_returns_bytes_and_none_when_gone() -> None:
    def files(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/a/photo.png":
            return httpx.Response(200, content=b"PNGDATA")
        return httpx.Response(404)

    c = connector(files)
    assert isinstance(c, rules.FileSource)
    here = rules.Attachment("p", "https://files.example.com/a/photo.png")
    gone = rules.Attachment("g", "https://files.example.com/a/missing.png")
    assert run(c.fetch(here, 1000)) == b"PNGDATA"
    assert run(c.fetch(here, 3)) is None  # bigger than the limit
    assert run(c.fetch(gone, 1000)) is None


def test_every_request_is_a_get() -> None:
    seen: list[httpx.Request] = []
    c = connector(seen=seen)
    run(c.check())
    run(c.discover())
    run(c.groups())
    for kind in ("contacts", "tickets", "replies", "articles"):
        run(c.page(kind, None))
    assert seen and {r.method for r in seen} == {"GET"}
    assert {r.url.host for r in seen} == {"acme.gorgias.com"}
