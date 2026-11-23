"""The Intercom importer, driven by a mock transport over JSON fixtures (no network)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from cedesk import egress
from cedesk.helpdesk.domain import imports as rules
from cedesk.helpdesk.service.imports import intercom

FIXTURES = Path(__file__).parent.parent / "fixtures" / "imports" / "intercom"
TOKEN = "tok-secret-123"


def fx(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


async def allow(host: str) -> None:
    return None


class Mock:
    def __init__(self, override: httpx.Response | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.override = override

    def body(self, request: httpx.Request) -> dict[str, Any]:
        return json.loads(request.content) if request.content else {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.override is not None:
            return self.override
        path = request.url.path
        after = request.url.params.get("starting_after")
        if request.method == "POST":
            posted = {
                "/conversations/search": "conversations_search_2"
                if self.body(request).get("pagination", {}).get("starting_after")
                else "conversations_search_1",
                "/tickets/search": "tickets_search",
            }
            if path not in posted:
                return httpx.Response(404, json={})
            return httpx.Response(200, json=fx(posted[path]))
        routes = {
            "/me": "me",
            "/admins": "admins",
            "/teams": "teams",
            "/contacts": "contacts_2" if after else "contacts_1",
            "/contacts/c1": "contact_c1",
            "/contacts/c2": "contact_c2",
            "/contacts/c3": "contact_c3",
            "/conversations": "conversations_list",
            "/conversations/501": "conversation_501",
            "/conversations/502": "conversation_502",
            "/conversations/503": "conversation_503",
            "/conversations/504": f"conversation_504_p{request.url.params.get('page', '1')}",
            "/tickets/601": "ticket_601",
            "/tickets/602": "ticket_602",
            "/articles": "articles",
            "/help_center/collections": "collections",
            "/macros": "macros",
        }
        if path not in routes:
            return httpx.Response(404, json={})
        return httpx.Response(200, json=fx(routes[path]))


def connector(mock: Mock, settings: dict[str, str] | None = None) -> rules.SourceConnector:
    return intercom.make(
        settings or {},
        {"token": TOKEN},
        transport=httpx.MockTransport(mock),
        guard=allow,
    )


async def all_tickets(c: Any) -> list[rules.Ticket]:
    out: list[rules.Ticket] = []
    cursor = None
    for _ in range(10):
        page = await c.page("tickets", cursor)
        out += page.items
        cursor = page.cursor
        if cursor is None:
            return out
    raise AssertionError("tickets never ended")


async def test_check_and_headers() -> None:
    mock = Mock()
    info = await connector(mock).check()
    assert info.name == "Acme Inc"
    req = mock.requests[0]
    assert req.url.host == "api.intercom.io"
    assert req.headers["Intercom-Version"] == intercom.API_VERSION
    assert req.headers["Authorization"] == f"Bearer {TOKEN}"


async def test_region_picks_host() -> None:
    mock = Mock()
    await connector(mock, {"region": "EU"}).check()
    assert mock.requests[0].url.host == "api.eu.intercom.io"


def test_bad_region() -> None:
    with pytest.raises(egress.BadAddress):
        intercom.make({"region": "mars"}, {"token": TOKEN})


async def test_discover_and_groups() -> None:
    c = connector(Mock())
    counts = await c.discover()
    # 2 conversations + 2 ticket objects; 2 macros
    assert counts == rules.Counts(contacts=3, tickets=4, articles=2, replies=2, groups=2)
    assert [(g.external_id, g.name) for g in await c.groups()] == [
        ("700", "Billing"),
        ("701", "Support"),
    ]


async def test_contacts_page_to_the_end() -> None:
    c = connector(Mock())
    first = await c.page("contacts", None)
    assert [x.external_id for x in first.items] == ["c1", "c2"]
    assert first.items[0].email == "ann@example.org"
    assert first.items[0].phone == "+15551234567"
    assert dict(first.items[0].attributes) == {"plan": "pro", "seats": 12}
    assert first.items[0].organization is None  # the list gives a company id, not its name
    assert first.items[1].email is None and first.items[1].name == "Contact c2"
    assert first.cursor == {"after": "cursor-2"}
    last = await c.page("contacts", first.cursor)
    assert [x.external_id for x in last.items] == ["c3"]
    assert last.cursor is None


async def test_a_contact_listed_twice_is_handed_over_once() -> None:
    """B-43: the list moving under the cursor must not count one contact twice."""

    class Twice(Mock):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/contacts" and request.url.params.get("starting_after"):
                self.requests.append(request)
                again = fx("contacts_2")
                again["data"].insert(0, fx("contacts_1")["data"][1])  # c2 again
                return httpx.Response(200, json=again)
            return super().__call__(request)

    c = connector(Twice())
    first = await c.page("contacts", None)
    last = await c.page("contacts", first.cursor)
    assert [x.external_id for x in last.items] == ["c3"]


async def test_tickets_map_parts_and_fields() -> None:
    c = connector(Mock())
    page = await c.page("tickets", None)
    assert page.cursor == {"phase": "tickets"} and len(page.items) == 2
    t = page.items[0]
    assert t.external_id == "501" and t.subject == "Refund & returns"
    assert t.status == "closed" and rules.status(t.status, c.status_map) == "resolved"
    assert t.created_at == datetime.fromtimestamp(1740823200, tz=UTC)
    assert t.resolved_at == datetime.fromtimestamp(1740830300, tz=UTC)
    assert t.priority == "high" and t.channel == "email"
    assert t.group_id == "700" and t.assignee_email == "sam@example.com"
    assert t.tags == ["VIP", "refund"]
    assert dict(t.fields) == {"order_id": "1042"}
    assert t.requester.email == "ann@example.org"
    assert t.csat is not None and t.csat.score == 5 and t.csat.comment == "Thanks!"
    assert list(t.cc) == ["bob@example.org"]  # the second contact of a group conversation
    assert list(t.links) == [rules.Link("related", "601")]
    assert t.sla_breached is True
    # events without a body are skipped; oldest first
    kinds = [(m.external_id, m.kind) for m in t.messages]
    assert kinds == [
        ("s1", "customer"),
        ("p5", "agent"),
        ("p2", "agent"),
        ("p3", "note"),
        ("p4", "customer"),
        ("p7", "note"),
        ("p8", "note"),
        ("p9", "note"),
    ]
    first, bot, agent, note, reply = t.messages[:5]
    assert first.body == "Where is my refund?\nOrder 1042 please"
    assert bot.author_name == "Fin" and bot.author_email is None
    assert agent.body == "Refund sent.\nAllow 3 days."
    assert agent.author_email == "sam@example.com"
    assert note.body == "Checked with finance" and note.author_email == "kim@example.com"
    assert reply.author_email == "ann@example.org"


async def test_staff_text_that_is_not_a_reply_stays_private() -> None:
    """C15: an assignment with a comment, a note-and-reopen and a part type the API does not
    name are notes, never public agent messages."""
    t = (await connector(Mock()).page("tickets", None)).items[0]
    by_id = {m.external_id: m for m in t.messages}
    assert by_id["p7"].kind == "note" and by_id["p7"].body == "VIP, please handle today"
    assert by_id["p8"].kind == "note"
    assert by_id["p9"].kind == "note"


async def test_conversation_parts_are_read_in_pages_oldest_first() -> None:
    """P0-4: without `page` Intercom gives only the 500 newest parts; every page is read."""
    mock = Mock()
    c: Any = connector(mock)
    ticket = await c._conversation("504")
    assert [m.external_id for m in ticket.messages] == ["s4", "q1", "q2", "q3", "q4"]
    assert [m.kind for m in ticket.messages] == ["customer", "agent", "customer", "note", "agent"]
    assert ticket.messages[-1].attachments[0].name == "log.txt"
    reads = [r for r in mock.requests if r.url.path == "/conversations/504"]
    assert [r.url.params["page"] for r in reads] == ["1", "2"]
    assert {r.url.params["per_page"] for r in reads} == {str(intercom.PARTS_PER_PAGE)}
    assert {r.headers["Intercom-Version"] for r in reads} == {intercom.NEWER_API_VERSION}
    assert ticket.priority is None
    assert c.failures.take() == []


async def test_a_history_past_the_safety_limit_is_noted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(intercom, "MAX_PART_PAGES", 1)
    c: Any = connector(Mock())
    with rules.collecting(rules.Losses()) as losses:
        ticket = await c._conversation("504")
    assert [m.external_id for m in ticket.messages] == ["s4", "q1", "q2"]  # kept, oldest
    assert losses.counts == {"history_capped": 1}
    [noted] = c.failures.take()
    assert (noted.external_id, noted.category) == ("504", "incomplete")


async def test_500_parts_without_paging_is_noted_as_capped() -> None:
    """A version that ignores `page` answers the 500 newest parts: never passed off as all."""
    many = fx("conversation_502")
    parts = [
        {
            "type": "conversation_part",
            "id": f"x{i}",
            "part_type": "comment",
            "body": f"<p>{i}</p>",
            "created_at": 1741000000 + i,
            "author": {"type": "user", "id": "c2"},
        }
        for i in range(intercom.PARTS_PER_PAGE)
    ]
    many["conversation_parts"] = {"type": "conversation_part.list", "conversation_parts": parts}

    class Old(Mock):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/conversations/502":
                return httpx.Response(200, json=many)
            return super().__call__(request)

    c: Any = connector(Old())
    with rules.collecting(rules.Losses()) as losses:
        ticket = await c._conversation("502")
    assert len(ticket.messages) == intercom.PARTS_PER_PAGE + 1
    assert losses.counts == {"history_capped": 1}
    assert [f.category for f in c.failures.take()] == ["incomplete"]


async def test_snoozed_is_pending_and_unassigned() -> None:
    c = connector(Mock())
    t = (await c.page("tickets", None)).items[1]
    assert rules.status(t.status, c.status_map) == "pending"
    assert t.priority is None and t.group_id is None and t.assignee_email is None
    assert t.csat is None and t.channel == "chat"
    assert t.subject == "Hi there"
    assert t.requester.email is None and t.requester.external_id == "c2"
    assert [m.kind for m in t.messages] == ["customer"]
    assert t.sla_breached is None and list(t.links) == [] and list(t.cc) == []


@pytest.mark.parametrize(
    ("raw", "mapped"),
    [
        ("priority", "high"),  # 2.14
        ("not_priority", "medium"),
        ("none", None),  # 2.16+
        ("urgent", "urgent"),
        ("low", "low"),
        (None, None),
    ],
)
def test_priority_words(raw: str | None, mapped: str | None) -> None:
    """P1-8: Intercom's `priority` is high, not the unknown word that became medium."""
    assert intercom._priority(raw) == mapped
    if mapped is not None:
        assert rules.priority(intercom._priority(raw)) == mapped


async def test_a_priority_conversation_is_high() -> None:
    old = fx("conversation_501")
    old["priority"] = "priority"

    class V214(Mock):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/conversations/501":
                return httpx.Response(200, json=old)
            return super().__call__(request)

    t = (await connector(V214()).page("tickets", None)).items[0]
    assert rules.priority(t.priority) == "high"


async def test_ticket_objects_follow_conversations() -> None:
    """P1-17: Intercom Ticket objects are imported after the conversations."""
    mock = Mock()
    c = connector(mock)
    tickets = await all_tickets(c)
    assert [t.external_id for t in tickets] == ["501", "502", "601", "602"]
    customer, back = tickets[2], tickets[3]
    assert customer.subject == "App crashes on login" and customer.ticket_type == "Bug report"
    assert rules.status(customer.status, c.status_map) == "resolved"
    assert customer.resolved_at == datetime.fromtimestamp(1741100400, tz=UTC)
    assert customer.requester.email == "ann@example.org"
    assert customer.assignee_email == "sam@example.com" and customer.group_id == "701"
    assert dict(customer.fields) == {"order_number": "A-77"}
    assert list(customer.links) == [rules.Link("related", "501")]
    assert [(m.kind, m.body) for m in customer.messages] == [
        ("customer", "The app crashes when I log in"),
        ("agent", "We are on it"),
        ("note", "Escalated to eng"),
        ("customer", "Thanks"),
    ]
    # a back-office ticket is internal: nothing in it is a public reply
    assert rules.status(back.status, c.status_map) == "open"
    assert {m.kind for m in back.messages} == {"note"}
    searches = [r for r in mock.requests if r.method == "POST"]
    assert [r.url.path for r in searches] == ["/tickets/search"]
    assert json.loads(searches[0].content)["query"] == {
        "field": "updated_at",
        "operator": ">",
        "value": 0,
    }


async def test_ticket_objects_refused_are_noted_not_fatal() -> None:
    class NoTickets(Mock):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/tickets/search":
                return httpx.Response(403, json={"type": "error.list"})
            return super().__call__(request)

    c: Any = connector(NoTickets())
    page = await c.page("tickets", {"phase": "tickets"})
    assert page == rules.Page([], None)
    [noted] = c.failures.take()
    assert noted.category == "incomplete" and noted.external_id == "intercom-tickets"
    assert TOKEN not in noted.error
    counts = await connector(NoTickets()).discover()
    assert counts.tickets == 2


async def test_articles_name_their_collection() -> None:
    """B-44: the category is the collection's name, not "Collection <id>"."""
    c = connector(Mock())
    page = await c.page("articles", None)
    assert page.cursor is None
    pub, draft = page.items
    assert pub.published and pub.body == "Click reset" and pub.category is not None
    assert pub.category == rules.Category("55", "Account & billing")
    assert not draft.published and draft.category is None


async def test_articles_keep_an_id_category_without_collections() -> None:
    class NoCollections(Mock):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/help_center/collections":
                return httpx.Response(403, json={})
            return super().__call__(request)

    pub = (await connector(NoCollections()).page("articles", None)).items[0]
    assert pub.category == rules.Category("55", "Collection 55")


async def test_macros_become_saved_replies() -> None:
    mock = Mock()
    page = await connector(mock).page("replies", None)
    assert page.cursor is None
    assert page.items == [
        rules.Reply(
            "m1", "refund-how-long", 'Hi {{user.name|fallback:"there"}}, refunds take 3 days.'
        ),
        rules.Reply("m2", "macro-m2", "Closing this now."),
    ]
    [read] = [r for r in mock.requests if r.url.path == "/macros"]
    assert read.headers["Intercom-Version"] == intercom.NEWER_API_VERSION


def test_plain_text() -> None:
    assert intercom.plain_text("<p>a&amp;b</p><p>c<br/>d</p><script>x</script>") == "a&b\nc\nd"


async def test_rate_limited_with_retry_after() -> None:
    res = httpx.Response(429, headers={"Retry-After": "42"})
    with pytest.raises(rules.RateLimited) as err:
        await connector(Mock(res)).check()
    assert err.value.retry_after == 42


async def test_rate_limited_with_reset_header() -> None:
    reset = str(int(datetime.now(UTC).timestamp()) + 30)
    res = httpx.Response(429, headers={"X-RateLimit-Reset": reset})
    with pytest.raises(rules.RateLimited) as err:
        await connector(Mock(res)).check()
    assert err.value.retry_after is not None and 1 <= err.value.retry_after <= 30


async def test_unauthorized_is_permanent_and_hides_token() -> None:
    with pytest.raises(rules.SourceError) as err:
        await connector(Mock(httpx.Response(401, json={}))).check()
    assert err.value.permanent and TOKEN not in str(err.value)


async def test_reads_only() -> None:
    mock = Mock()
    c = connector(mock)
    await c.check()
    await c.discover()
    for kind in rules.KINDS:
        await c.page(kind, None)
    await c.page("tickets", {"phase": "tickets"})
    assert mock.requests
    for r in mock.requests:
        assert r.method == "GET" or (r.method == "POST" and r.url.path.endswith("/search"))


FILE_URL = "https://downloads.intercomcdn.com/i/o/1/screen.png"


async def test_changed_tickets_search_every_page_and_parse_attachments() -> None:
    """P1-11: a sync look asks Intercom's search for everything updated since, to the end of
    its cursor, instead of stopping at the first older record of an unordered list."""
    mock = Mock()
    c = connector(mock)
    assert isinstance(c, rules.ChangeSource)
    since = datetime.fromtimestamp(1740800000, tz=UTC)
    first = await c.changed_tickets(since, None)
    # 502 (older than its neighbours) is not taken as the end of the changes
    assert [t.external_id for t in first.items] == ["502", "503"]
    assert first.cursor == {"after": "WzE3NDA5MDAwMDAsIjUwMyJd"}
    second = await c.changed_tickets(since, first.cursor)
    assert [t.external_id for t in second.items] == ["501"]
    assert second.cursor == {"phase": "tickets"}
    third = await c.changed_tickets(since, second.cursor)
    assert [t.external_id for t in third.items] == ["601", "602"] and third.cursor is None
    searches = [json.loads(r.content) for r in mock.requests if r.method == "POST"]
    assert searches[0]["query"] == {"field": "updated_at", "operator": ">", "value": 1740799999}
    assert "starting_after" not in searches[0]["pagination"]
    assert searches[1]["pagination"]["starting_after"] == "WzE3NDA5MDAwMDAsIjUwMyJd"
    assert [r.url.path for r in mock.requests if r.method == "POST"] == [
        "/conversations/search",
        "/conversations/search",
        "/tickets/search",
    ]
    assert not any(r.url.path == "/conversations" for r in mock.requests)

    files = first.items[1].messages
    assert list(files[0].attachments) == [
        rules.Attachment("screen.png", FILE_URL, 2048, "image/png")
    ]  # the http:// one is skipped
    assert files[1].body == "(attachment)"
    assert list(files[1].attachments) == [
        rules.Attachment(
            "invoice.pdf",
            "https://downloads.intercomcdn.com/i/o/2/invoice.pdf",
            5000,
            "application/pdf",
        )
    ]


async def test_a_conversation_gone_is_noted() -> None:
    """C20: a 404 on a listed conversation is noted, not silently skipped."""

    class Gone(Mock):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/conversations/501":
                return httpx.Response(404, json={"type": "error.list"})
            return super().__call__(request)

    c: Any = connector(Gone())
    page = await c.page("tickets", None)
    assert [t.external_id for t in page.items] == ["502"]
    [noted] = c.failures.take()
    assert (noted.kind, noted.external_id, noted.category) == ("ticket", "501", "not_found")


async def test_a_zero_creation_date_is_never_1970() -> None:
    """C9: `created_at` 0 used to become 1 Jan 1970."""
    zero = fx("conversation_502")
    zero["created_at"] = 0

    class Zero(Mock):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/conversations/502":
                return httpx.Response(200, json=zero)
            return super().__call__(request)

    c: Any = connector(Zero())
    t = await c._conversation("502")
    assert t.created_at == datetime.fromtimestamp(1741000100, tz=UTC)  # its update time


async def test_fetch_returns_bytes_or_none() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("gone.png"):
            return httpx.Response(404)
        return httpx.Response(200, content=b"PNGDATA")

    c = intercom.make({}, {"token": TOKEN}, transport=httpx.MockTransport(handler), guard=allow)
    assert isinstance(c, rules.FileSource)
    ok = await c.fetch(rules.Attachment("screen.png", FILE_URL), 1000)  # type: ignore[attr-defined]
    assert ok == b"PNGDATA"
    assert "Authorization" not in seen[0].headers  # not the account's host
    gone = rules.Attachment("gone.png", "https://downloads.intercomcdn.com/gone.png")
    assert await c.fetch(gone, 1000) is None  # type: ignore[attr-defined]
    assert await c.fetch(rules.Attachment("screen.png", FILE_URL), 3) is None  # type: ignore[attr-defined]


async def test_an_expired_file_link_is_fetched_again_from_a_fresh_read() -> None:
    """B-42: Intercom's signed file links expire; the conversation is read once more and the
    same file is fetched from its new link."""
    old_url = "https://downloads.intercomcdn.com/i/o/4/log.txt?expires=1"
    new_url = "https://downloads.intercomcdn.com/i/o/4/log.txt?expires=2"
    fresh = {"read": 0}

    class Expiring(Mock):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if request.url.host == "downloads.intercomcdn.com":
                if str(request.url) == new_url:
                    return httpx.Response(200, content=b"log body")
                return httpx.Response(403)  # expired signature
            if request.url.path == "/conversations/504" and fresh["read"] >= 2:
                data = fx(f"conversation_504_p{request.url.params.get('page', '1')}")
                for part in data["conversation_parts"]["conversation_parts"]:
                    for att in part["attachments"]:
                        att["url"] = new_url
                self.requests.append(request)
                return httpx.Response(200, json=data)
            if request.url.path == "/conversations/504":
                fresh["read"] += 1
            return super().__call__(request)

    mock = Expiring()
    c: Any = connector(mock)
    ticket = await c._conversation("504")
    [att] = ticket.messages[-1].attachments
    assert att.url == old_url
    assert await c.fetch(att, 1000) == b"log body"
    # a file still gone after the fresh read is None, and the record is not read a third time
    reads = len([r for r in mock.requests if r.url.path == "/conversations/504"])
    assert (
        await c.fetch(rules.Attachment("x.png", "https://downloads.intercomcdn.com/x"), 9) is None
    )
    assert len([r for r in mock.requests if r.url.path == "/conversations/504"]) == reads
