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
from cedesk.helpdesk.domain import saved_replies
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
        answer: httpx.Response = handler(request)
        return answer

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
    # replies: 6 macros, the tag-only one is left out (C17)
    assert (counts.contacts, counts.tickets, counts.replies, counts.groups) == (3, 5, 5, 2)
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
    assert query["limit"] == str(gorgias.TICKET_LIMIT) and query["trashed"] == "false"


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
        ("10", "order-delayed-because-of-the-car", "Your order is delayed."),
        ("11", "macro-11", "返品は30日以内です。"),
        ("12", "rembourse-rapide", "Remboursement envoyé."),
    ]


def test_every_reply_shortcut_is_one_a_saved_reply_accepts() -> None:
    """The old 60-character slug was refused by the shortcut rule (32 at most), and a name
    with no ASCII letters gave an empty slug: both macros were silently lost."""
    page = run(connector().page("replies", None))
    for reply in page.items:
        assert saved_replies.normalise_shortcut(reply.shortcut) == reply.shortcut
    assert gorgias.slug("!!!", "") == ""
    assert gorgias.slug("--Long " + "x" * 40, "1") == "long-" + "x" * 27


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


def test_attachments_are_parsed_and_unusable_ones_noted() -> None:
    losses = rules.Losses()
    c = connector()
    with rules.collecting(losses):
        page = run(c.changed_tickets(SINCE, None))
    first, second = page.items[0].messages  # the third message has no text and no files
    assert first.attachments == [
        rules.Attachment("photo.png", "https://files.example.com/a/photo.png", 2048, "image/png"),
        # kept, so the framework writes it as a link line instead of losing it (C20)
        rules.Attachment("plain-http.txt", "http://files.example.com/a/x.txt", 3, "text/plain"),
        rules.Attachment("file", "https://files.example.com/a/bare", None, None),
    ]
    assert losses.counts == {"message_without_text": 1}
    noted = c.failures.take()
    assert [(f.kind, f.external_id, f.category) for f in noted] == [("ticket", "200", "incomplete")]
    assert "1 attachment(s) had no address" in noted[0].error
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


# B-36: Gorgias answers a placeholder image for files it won't serve directly

PLACEHOLDER = b"\x89PNG\r\n\x1a\n" + b"\x00" * (gorgias.PLACEHOLDER_SIZE - 8)
PDF = b"%PDF-1.7\n" + b"0" * (48_213 - 9)
UPLOAD = "https://uploads.gorgias.io/Hna8kldnmlal/report-c9f158fc-062d-4b00-a826-e29b36852c9b.pdf"
SIGNED = "https://storage.example-cdn.com/Hna8kldnmlal/report.pdf?X-Signature=abc&Expires=99"
REPORT = rules.Attachment("report.pdf", UPLOAD, 48_213, "application/pdf")


def file_host(download_answer: str) -> Any:
    """The live behaviour: the upload host answers 200 with the placeholder for a PDF; the
    documented download route redirects (307) to a signed URL that serves the real file."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "uploads.gorgias.io":
            return httpx.Response(200, content=PLACEHOLDER, headers={"content-type": "image/png"})
        if request.url.path.startswith("/api/attachment/download/"):
            if download_answer == "placeholder":
                return httpx.Response(200, content=PLACEHOLDER)
            return httpx.Response(307, headers={"location": SIGNED})
        if request.url.host == "storage.example-cdn.com":
            return httpx.Response(200, content=PDF)
        return httpx.Response(404)

    return handler


def test_fetch_reads_a_gorgias_upload_through_the_download_route() -> None:
    seen: list[httpx.Request] = []
    c = connector(file_host("redirect"), seen=seen)
    assert run(c.fetch(REPORT, rules.FILE_MAX)) == PDF
    assert [str(r.url) for r in seen] == [
        "https://acme.gorgias.com/api/attachment/download/Hna8kldnmlal/"
        "report-c9f158fc-062d-4b00-a826-e29b36852c9b.pdf",
        SIGNED,
    ]
    assert seen[0].headers["authorization"].startswith("Basic ")
    assert "authorization" not in seen[1].headers  # never to the storage host


def test_fetch_never_keeps_the_placeholder_image() -> None:
    def png(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=PLACEHOLDER)

    losses = rules.Losses()
    with rules.collecting(losses):
        # the download route answers the placeholder too: a link line, not a fake file
        with pytest.raises(rules.FileUnusable, match="file_placeholder"):
            run(connector(file_host("placeholder")).fetch(REPORT, rules.FILE_MAX))
        # a file not on the upload host, declared a CSV, answered with a PNG
        csv = rules.Attachment("rows.csv", "https://files.example.com/a/rows.csv", 120, "text/csv")
        with pytest.raises(rules.FileUnusable, match="file_placeholder"):
            run(connector(png).fetch(csv, rules.FILE_MAX))
        # declared as an image, but the PNG is the placeholder's size, far from 2,048 bytes
        image = rules.Attachment("p.png", "https://files.example.com/a/p.png", 2048, "image/png")
        with pytest.raises(rules.FileUnusable, match="file_placeholder"):
            run(connector(png).fetch(image, rules.FILE_MAX))
    assert losses.counts == {}  # the framework counts it, once


def test_fetch_keeps_a_real_image_and_files_without_a_declared_type() -> None:
    real = b"\x89PNG\r\n\x1a\n" + b"\x01" * 2040

    def files(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=real if request.url.path.endswith(".png") else b"hi")

    c = connector(files)
    image = rules.Attachment("p.png", "https://files.example.com/a/p.png", 2048, "image/png")
    assert run(c.fetch(image, rules.FILE_MAX)) == real
    bare = rules.Attachment("file", "https://files.example.com/a/bare")
    assert run(c.fetch(bare, rules.FILE_MAX)) == b"hi"
    assert gorgias.placeholder(rules.Attachment("x", "u", None, None), real) is False


def test_fetch_does_not_read_a_non_https_file_and_counts_it() -> None:
    seen: list[httpx.Request] = []
    losses = rules.Losses()
    with rules.collecting(losses):
        plain = rules.Attachment("x.txt", "http://files.example.com/a/x.txt", 3, "text/plain")
        with pytest.raises(rules.FileUnusable, match="file_unreachable"):
            run(connector(seen=seen).fetch(plain, rules.FILE_MAX))
    assert seen == [] and losses.counts == {}


# P1-18 / C17: the dry run says what is not brought


def test_warnings_say_articles_and_text_less_macros_are_not_imported() -> None:
    c = connector()
    before = c.warnings()
    assert any("Help Center articles are not imported" in w for w in before)
    assert any("Macros without a reply text" in w for w in before)
    run(c.discover())
    after = c.warnings()
    assert len(after) == 2
    assert after[1].startswith("1 macro without a reply text")
    assert KEY not in " ".join(after)


# C3: a page slightly out of order loses nothing


def test_changed_tickets_read_every_new_row_of_an_unordered_page() -> None:
    def unordered(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tickets":
            return httpx.Response(200, json=fixture("tickets_changed_unordered"))
        return route(request)

    page = run(connector(unordered).changed_tickets(SINCE, None))
    assert [t.external_id for t in page.items] == ["210", "212"]  # 211 is older than since
    assert page.cursor == {"cursor": "t9"}  # the last row is new: the next page may be too


# C6: a ticket past the safety limit is counted and noted, not silently cut


def test_a_history_past_the_safety_limit_is_counted_and_noted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert gorgias.MESSAGE_PAGES_MAX * gorgias.LIMIT >= 100_000
    monkeypatch.setattr(gorgias, "MESSAGE_PAGES_MAX", 2)
    calls: list[str | None] = []

    def endless(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/messages":
            calls.append(request.url.params.get("cursor"))
            body = fixture("messages_100")
            body["meta"]["next_cursor"] = f"m{len(calls)}"
            return httpx.Response(200, json=body)
        return route(request)

    losses = rules.Losses()
    c = connector(endless)
    with rules.collecting(losses):
        page = run(c.page("tickets", None))
    assert calls[:2] == [None, "m1"]
    assert losses.counts.get("history_capped") == 2  # tickets 100 and 101
    notes = [f for f in c.failures.take() if "safety limit" in f.error]
    assert [(f.external_id, f.category) for f in notes] == [
        ("100", "incomplete"),
        ("101", "incomplete"),
    ]
    assert len(page.items[0].messages) == 6  # 3 kept per page, both pages read


# C9: a missing date falls back to the nearest source date, never silently to "now"


def test_missing_dates_use_the_nearest_source_date_or_are_noted() -> None:
    def dates(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tickets":
            return httpx.Response(200, json=fixture("tickets_dates"))
        if request.url.path == "/api/messages":
            ticket = request.url.params.get("ticket_id")
            return httpx.Response(
                200, json=fixture("messages_dates" if ticket == "300" else "messages_empty")
            )
        return route(request)

    losses = rules.Losses()
    c = connector(dates)
    with rules.collecting(losses):
        opened, undated = run(c.page("tickets", None)).items
    assert opened.created_at.isoformat() == "2025-04-02T08:00:00+00:00"  # opened_datetime
    no_date, sent = opened.messages
    assert no_date.created_at == opened.created_at  # the ticket's own date
    assert sent.created_at.isoformat() == "2025-04-02T09:00:00+00:00"  # sent_datetime
    assert undated.created_at.year >= 2026  # the import time, and that is noted
    noted = c.failures.take()
    assert [(f.external_id, f.category) for f in noted] == [("301", "incomplete")]
    assert "no valid creation date" in noted[0].error
    assert losses.counts == {"date_replaced": 3, "date_missing": 1}


# C20: deleted messages are not left out silently


def test_deleted_messages_are_noted_on_their_ticket() -> None:
    c = connector()
    run(c.page("tickets", None))
    noted = c.failures.take()
    assert [(f.external_id, f.category, f.error) for f in noted] == [
        ("100", "incomplete", "1 message(s) deleted in Gorgias were left out.")
    ]


# Recipients, ticket address, contact channels


def test_recipients_ticket_cc_and_web_address() -> None:
    first = tickets()[0]
    from_customer, from_agent, note = first.messages
    assert from_customer.to == ["support@acme.com"]
    assert from_customer.cc == ["bo@example.com"] and from_customer.bcc == ["audit@acme.com"]
    assert from_agent.cc == ["bo@example.com", "cy@example.com"] and from_agent.bcc == []
    assert note.to == [] and note.cc == []
    assert first.cc == ["bo@example.com", "cy@example.com"]
    assert first.source_url == "https://acme.gorgias.com/app/ticket/100"
    assert first.ticket_type is None  # Gorgias tickets have no type field


def test_contact_phone_and_other_emails_from_channels() -> None:
    ann, nobody = run(connector().page("contacts", None)).items
    assert ann.phone == "+14155550123"
    assert list(ann.other_emails) == ["ann.lee@work.example"]
    assert nobody.phone is None and list(nobody.other_emails) == []


# B-61: a page is read whole before it is saved, so a page that costs more requests than the
# source allows in a window never finishes. Gorgias's trial: 40 requests every 20 seconds.


def test_a_page_of_tickets_fits_a_trials_request_quota() -> None:
    template = fixture("tickets_1")["data"][0]
    rows = [
        {**template, "id": 1000 + i, "updated_datetime": f"2025-03-01T10:{i % 60:02d}:00+00:00"}
        for i in range(60)
    ]
    used = 0

    def limited(request: httpx.Request) -> httpx.Response:
        nonlocal used
        used += 1
        if used > 40:
            return httpx.Response(429, headers={"Retry-After": "1"}, json={})
        if request.url.path == "/api/tickets":
            size = int(request.url.params["limit"])
            start = int(request.url.params.get("cursor") or 0)
            more = start + size < len(rows)
            body = {
                "data": rows[start : start + size],
                "meta": {"next_cursor": str(start + size) if more else None},
            }
            return httpx.Response(200, json=body)
        return route(request)

    found: list[Any] = []
    cursor: Any = None
    for _window in range(30):
        used = 0
        c = connector(limited)  # the worker builds a new connector on every pass
        try:
            while True:
                page = run(c.page("tickets", cursor))
                found.extend(page.items)
                cursor = page.cursor
                if cursor is None:
                    break
        except RateLimited:
            continue
        break
    else:
        raise AssertionError("never finished: the page costs more than a window allows")
    assert [t.external_id for t in found] == [str(1000 + i) for i in range(60)]
