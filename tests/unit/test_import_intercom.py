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

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.override is not None:
            return self.override
        path = request.url.path
        after = request.url.params.get("starting_after")
        routes = {
            "/me": "me",
            "/admins": "admins",
            "/teams": "teams",
            "/contacts": "contacts_2" if after else "contacts_1",
            "/contacts/c1": "contact_c1",
            "/contacts/c2": "contact_c2",
            "/conversations": "conversations_list",
            "/conversations/501": "conversation_501",
            "/conversations/502": "conversation_502",
            "/articles": "articles",
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
    assert counts == rules.Counts(contacts=3, tickets=2, articles=2, replies=0, groups=2)
    assert [(g.external_id, g.name) for g in await c.groups()] == [
        ("700", "Billing"),
        ("701", "Support"),
    ]


async def test_contacts_page_to_the_end() -> None:
    c = connector(Mock())
    first = await c.page("contacts", None)
    assert [x.external_id for x in first.items] == ["c1", "c2"]
    assert first.items[0].email == "ann@example.org"
    assert first.items[1].email is None and first.items[1].name == "Contact c2"
    assert first.cursor == {"after": "cursor-2"}
    last = await c.page("contacts", first.cursor)
    assert [x.external_id for x in last.items] == ["c3"]
    assert last.cursor is None


async def test_tickets_map_parts_and_fields() -> None:
    c = connector(Mock())
    page = await c.page("tickets", None)
    assert page.cursor is None and len(page.items) == 2
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
    # assignment and close events (no body) are skipped; oldest first
    assert [m.kind for m in t.messages] == ["customer", "agent", "agent", "note", "customer"]
    first, bot, agent, note, reply = t.messages
    assert first.body == "Where is my refund?\nOrder 1042 please"
    assert bot.author_name == "Fin" and bot.author_email is None
    assert agent.body == "Refund sent.\nAllow 3 days."
    assert agent.author_email == "sam@example.com"
    assert note.body == "Checked with finance" and note.author_email == "kim@example.com"
    assert reply.author_email == "ann@example.org"


async def test_snoozed_is_pending_and_unassigned() -> None:
    c = connector(Mock())
    t = (await c.page("tickets", None)).items[1]
    assert rules.status(t.status, c.status_map) == "pending"
    assert t.priority is None and t.group_id is None and t.assignee_email is None
    assert t.csat is None and t.channel == "chat"
    assert t.subject == "Hi there"
    assert t.requester.email is None and t.requester.external_id == "c2"
    assert [m.kind for m in t.messages] == ["customer"]


async def test_articles_and_replies() -> None:
    c = connector(Mock())
    page = await c.page("articles", None)
    assert page.cursor is None
    pub, draft = page.items
    assert pub.published and pub.body == "<p>Click reset</p>" and pub.category is not None
    assert pub.category.external_id == "55"
    assert not draft.published and draft.category is None
    assert await c.page("replies", None) == rules.Page([], None)


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


async def test_only_get_requests() -> None:
    mock = Mock()
    c = connector(mock)
    await c.check()
    await c.discover()
    for kind in rules.KINDS:
        await c.page(kind, None)
    assert mock.requests and {r.method for r in mock.requests} == {"GET"}


FILE_URL = "https://downloads.intercomcdn.com/i/o/1/screen.png"


class Changes(Mock):
    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/conversations":
            self.requests.append(request)
            return httpx.Response(200, json=fx("conversations_changed"))
        if request.url.path == "/conversations/503":
            self.requests.append(request)
            return httpx.Response(200, json=fx("conversation_503"))
        return super().__call__(request)


async def test_changed_tickets_stop_at_older_and_parse_attachments() -> None:
    mock = Changes()
    c = connector(mock)
    assert isinstance(c, rules.ChangeSource)
    since = datetime.fromtimestamp(1740850000, tz=UTC)
    page = await c.changed_tickets(since, {"after": "cursor-1"})  # type: ignore[attr-defined]
    assert [t.external_id for t in page.items] == ["503"]
    assert page.cursor is None
    assert mock.requests[0].url.params["starting_after"] == "cursor-1"
    assert {r.method for r in mock.requests} == {"GET"}
    assert not any(
        r.url.path in ("/conversations/501", "/conversations/502") for r in mock.requests
    )
    first, second = page.items[0].messages
    assert list(first.attachments) == [
        rules.Attachment("screen.png", FILE_URL, 2048, "image/png")
    ]  # the http:// one is skipped
    assert second.body == "(attachment)"
    assert list(second.attachments) == [
        rules.Attachment(
            "invoice.pdf",
            "https://downloads.intercomcdn.com/i/o/2/invoice.pdf",
            5000,
            "application/pdf",
        )
    ]


async def test_changed_tickets_keeps_paging_when_all_are_new() -> None:
    c = connector(Changes())
    since = datetime.fromtimestamp(1, tz=UTC)
    page = await c.changed_tickets(since, None)  # type: ignore[attr-defined]
    assert page.cursor == {"after": "cursor-x"}


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
