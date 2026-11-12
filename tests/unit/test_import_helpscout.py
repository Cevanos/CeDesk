"""The Help Scout importer, driven by a fake of the two Help Scout APIs (no network): the
OAuth token exchange, Bearer on every GET, a refresh on a 401, pages, threads, saved replies,
Docs articles with and without a key, and the errors."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from cedesk.helpdesk.domain import imports as rules
from cedesk.helpdesk.domain.imports import RateLimited, SourceError
from cedesk.helpdesk.service.imports import helpscout
from cedesk.helpdesk.service.imports.helpscout import HelpScoutConnector, make, plain_text

FIXTURES = Path(__file__).parent.parent / "fixtures" / "imports" / "helpscout"
SECRET = "s3cr3t-value"
DOCS = "docsapi.helpscout.net"


def fx(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


async def no_guard(host: str) -> None:
    return None


class Fake:
    """Routes by host, path and query; records every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.tokens = 0
        self.valid: str | None = "tok-1"
        self.limit_path: str | None = None

    def expire(self) -> None:
        self.valid = None  # every call is refused until a new token is issued

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, query = request.url.path, dict(request.url.params)
        if request.url.host == DOCS:
            return self.docs(request, path)
        if request.method == "POST":
            assert path == "/v2/oauth2/token"
            form = dict(httpx.QueryParams(request.content.decode()))
            assert form["grant_type"] == "client_credentials" and form["client_id"] == "app-1"
            if form["client_secret"] != SECRET:
                return httpx.Response(400, json={"error": "invalid_client"})
            self.tokens += 1
            self.valid = f"tok-{self.tokens}"
            return httpx.Response(200, json={**fx("token"), "access_token": self.valid})
        if request.headers.get("authorization") != f"Bearer {self.valid}":
            return httpx.Response(401, json={"error": "invalid_token"})
        if self.limit_path and path == self.limit_path:
            return httpx.Response(429, headers={"Retry-After": "42"})
        if path == "/v2/users/me":
            return httpx.Response(200, json=fx("user_me"))
        if path == "/v2/mailboxes":
            return httpx.Response(200, json=fx("mailboxes"))
        if path == "/v2/customers":
            return httpx.Response(200, json=fx("customers"))
        if path == "/v2/conversations":
            return httpx.Response(200, json=fx(f"conversations_p{query.get('page', '1')}"))
        if path.startswith("/v2/conversations/") and path.endswith("/data"):
            aid = path.split("/")[-2]
            if aid == "501":
                return httpx.Response(200, json=fx("attachment_501"))
            return httpx.Response(404, json={})
        if path.startswith("/v2/conversations/") and path.endswith("/threads"):
            return httpx.Response(200, json=fx(f"threads_{path.split('/')[3]}"))
        if path.startswith("/v2/mailboxes/") and "saved-replies" in path:
            parts = path.split("/")
            if len(parts) == 5:
                return httpx.Response(200, json=fx(f"saved_replies_{parts[3]}"))
            return httpx.Response(200, json=fx(f"saved_reply_{parts[5]}"))
        return httpx.Response(404, json={})

    def docs(self, request: httpx.Request, path: str) -> httpx.Response:
        assert request.method == "GET"
        if not request.headers.get("authorization", "").startswith("Basic "):
            return httpx.Response(401)
        if path == "/v1/collections":
            return httpx.Response(200, json=fx("docs_collections"))
        if path == "/v1/collections/c1/articles":
            return httpx.Response(200, json=fx("docs_articles"))
        if path == "/v1/collections/c1/categories":
            return httpx.Response(200, json=fx("docs_categories"))
        if path.startswith("/v1/articles/"):
            return httpx.Response(200, json=fx(f"docs_article_{path.rsplit('/', 1)[1]}"))
        return httpx.Response(404, json={})


def connector(
    fake: Fake, *, docs_key: str | None = None, secret: str = SECRET
) -> HelpScoutConnector:
    creds = {"app_id": "app-1", "app_secret": secret}
    if docs_key:
        creds["docs_key"] = docs_key
    return make({}, creds, transport=httpx.MockTransport(fake), guard=no_guard)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_token_exchange_then_bearer_on_gets() -> None:
    fake = Fake()
    info = run(connector(fake).check())
    assert info.name == "Help Scout (sam@example.com)"
    assert [r.method for r in fake.requests] == ["POST", "GET"]
    assert fake.requests[1].headers["authorization"] == "Bearer tok-1"
    assert fake.requests[1].url.path == "/v2/users/me"


def test_token_is_reused_and_refreshed_when_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, clock = Fake(), [1000.0]
    monkeypatch.setattr(helpscout, "_now", lambda: clock[0])
    c = connector(fake)
    run(c.check())
    run(c.check())
    assert fake.tokens == 1
    clock[0] += 172800  # past the two days
    fake.expire()
    run(c.check())
    assert fake.tokens == 2


def test_a_401_refreshes_the_token_once() -> None:
    fake = Fake()
    c = connector(fake)
    run(c.check())
    fake.expire()
    assert run(c.check()).name.startswith("Help Scout")
    assert fake.tokens == 2
    assert fake.requests[-1].headers["authorization"] == "Bearer tok-2"


def test_a_401_with_a_fresh_token_is_not_retried_forever() -> None:
    class Stubborn(Fake):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.host != DOCS:
                self.requests.append(request)
                return httpx.Response(401)
            return super().__call__(request)

    fake = Stubborn()
    c = connector(fake)
    with pytest.raises(SourceError) as err:
        run(c.check())
    assert err.value.permanent and fake.tokens == 1  # a fresh token refused: no second try
    assert "tok-" not in str(err.value)


def test_bad_credentials_are_a_permanent_error_without_secrets() -> None:
    with pytest.raises(SourceError) as err:
        run(connector(Fake(), secret="wrong-secret").check())
    assert err.value.permanent
    assert "wrong-secret" not in str(err.value) and SECRET not in str(err.value)


def test_rate_limit_carries_retry_after() -> None:
    fake = Fake()
    fake.limit_path = "/v2/customers"
    with pytest.raises(RateLimited) as err:
        run(connector(fake).page("contacts", None))
    assert err.value.retry_after == 42


def test_groups_are_mailboxes() -> None:
    groups = run(connector(Fake()).groups())
    assert [(g.external_id, g.name) for g in groups] == [("100", "Support"), ("200", "Billing")]


def test_discover_counts() -> None:
    counts = run(connector(Fake(), docs_key="dk").discover())
    assert (counts.tickets, counts.contacts, counts.groups) == (3, 2, 2)
    assert counts.replies == 2 and counts.articles == 2


def test_contacts() -> None:
    page = run(connector(Fake()).page("contacts", None))
    assert page.cursor is None
    ann, bo = page.items
    assert (ann.external_id, ann.email, ann.name) == ("7", "ann@example.org", "Ann Lee")
    assert bo.email is None and bo.name == "Bo"


def test_tickets_across_two_pages_with_threads() -> None:
    c = connector(Fake())
    first = run(c.page("tickets", None))
    assert first.cursor == {"page": 2}
    assert [t.external_id for t in first.items] == ["1001"]  # the spam one is left out
    assert c.spam_skipped == 1
    second = run(c.page("tickets", first.cursor))
    assert second.cursor is None and [t.external_id for t in second.items] == ["1003"]
    json.dumps(first.cursor)  # serialisable


def test_ticket_fields_and_mapping() -> None:
    t = run(connector(Fake()).page("tickets", None)).items[0]
    assert t.subject == "Refund please"
    assert t.status == "closed" and rules.status(t.status, helpscout.STATUS_MAP) == "resolved"
    assert t.group_id == "100" and t.assignee_email == "sam@example.com"
    assert t.channel == "email" and list(t.tags) == ["VIP Customer", "refund"]
    assert t.fields == {"Order ID": "1042"}
    assert (t.requester.external_id, t.requester.email) == ("7", "ann@example.org")
    assert t.created_at.isoformat() == "2025-03-01T10:00:00+00:00"
    assert t.resolved_at is not None and t.resolved_at.day == 2
    assert t.updated_at is not None and t.updated_at.minute == 30
    assert t.csat is None


def test_threads_kinds_html_and_order() -> None:
    t = run(connector(Fake()).page("tickets", None)).items[0]
    assert [m.kind for m in t.messages] == ["customer", "agent", "note"]  # lineitem, empty gone
    customer, agent, note = t.messages
    assert customer.body == "Where is my refund?" and customer.author_email == "ann@example.org"
    assert agent.body == "Hi Ann,\n\nRefund sent & done.\nThanks"
    assert agent.author_email == "sam@example.com" and agent.author_name == "Sam Agent"
    assert note.body == "Checked the order." and note.external_id == "14"


def test_chat_ticket_and_status_map() -> None:
    c = connector(Fake())
    t = run(c.page("tickets", {"page": 2})).items[0]
    assert t.channel == "chat" and t.group_id == "200" and t.assignee_email is None
    assert rules.status(t.status, c.status_map) == "open"
    assert [m.kind for m in t.messages] == ["customer"]
    assert t.requester.email is None
    for word, want in (("active", "open"), ("open", "open"), ("pending", "pending")):
        assert rules.status(word, c.status_map) == want


def test_thread_attachments_are_parsed() -> None:
    t = run(connector(Fake()).page("tickets", None)).items[0]
    customer = t.messages[0]
    assert [(a.name, a.size, a.mime) for a in customer.attachments] == [
        ("receipt.pdf", 11, "application/pdf"),
        ("gone.png", 5, "image/png"),
    ]  # the one without an id is skipped
    assert customer.attachments[0].url == ("/v2/conversations/1001/threads/11/attachments/501/data")
    assert t.messages[1].attachments == []


def test_fetch_decodes_base64_with_the_bearer_token() -> None:
    fake = Fake()
    c = connector(fake)
    t = run(c.page("tickets", None)).items[0]
    got = run(c.fetch(t.messages[0].attachments[0], 1000))
    assert got == b"hello world"
    last = fake.requests[-1]
    assert last.url.path.endswith("/attachments/501/data")
    assert last.headers["authorization"].startswith("Bearer ")


def test_fetch_is_none_when_missing_or_too_big() -> None:
    fake = Fake()
    c = connector(fake)
    first, gone = run(c.page("tickets", None)).items[0].messages[0].attachments
    assert run(c.fetch(gone, 1000)) is None  # the source answers 404
    before = len(fake.requests)
    assert run(c.fetch(first, 5)) is None  # size 11 > 5: no call at all
    assert len(fake.requests) == before
    nosize = rules.Attachment("x", first.url)
    assert run(c.fetch(nosize, 5)) is None  # decoded bytes are over the limit
    assert run(c.fetch(rules.Attachment("x", "https://evil.example/f"), 1000)) is None


def test_changed_tickets_sends_modified_since() -> None:
    fake = Fake()
    c = connector(fake)
    since = datetime(2025, 3, 1, 9, 30, tzinfo=UTC)
    page = run(c.changed_tickets(since, None))
    assert [t.external_id for t in page.items] == ["1001"] and page.cursor == {"page": 2}
    listing = next(r for r in fake.requests if r.url.path == "/v2/conversations")
    assert listing.url.params["modifiedSince"] == "2025-03-01T09:30:00Z"
    assert listing.method == "GET"
    assert run(c.changed_tickets(since, page.cursor)).cursor is None


def test_replies_use_the_saved_reply_body() -> None:
    c = connector(Fake())
    page = run(c.page("replies", None))
    assert page.cursor == {"m": 1, "o": 0}  # the Billing mailbox has none: the next page is empty
    assert run(c.page("replies", page.cursor)).cursor is None
    greeting, chat = page.items
    assert (greeting.external_id, greeting.shortcut) == ("5", "Greeting")
    assert greeting.body == "Hello there,\n\nHow can we help?"
    assert chat.body == "Hi! How can we help?"


def test_articles_with_a_docs_key() -> None:
    fake = Fake()
    c = connector(fake, docs_key="dk-1")
    assert c.warnings() == ["Spam conversations are not imported."]
    page = run(c.page("articles", None))
    assert page.cursor is None
    live, draft = page.items
    assert live.title == "Reset password" and live.body == "Click Forgot." and live.published
    assert live.category is not None and live.category.name == "Account"
    assert not draft.published and draft.category is None
    docs = [r for r in fake.requests if r.url.host == DOCS]
    assert docs and all(r.method == "GET" for r in docs)
    assert docs[0].headers["authorization"].startswith("Basic ")


def test_articles_without_a_docs_key_warn_and_make_no_call() -> None:
    fake = Fake()
    c = connector(fake)
    assert "Add a Docs API key to import help articles." in c.warnings()
    page = run(c.page("articles", None))
    assert list(page.items) == [] and page.cursor is None
    assert fake.requests == []
    assert run(connector(Fake()).discover()).articles == 0


def test_only_the_token_request_is_not_a_get_and_the_secret_goes_nowhere_else() -> None:
    fake = Fake()
    c = connector(fake, docs_key="dk-1")
    for kind in rules.KINDS:
        run(c.page(kind, None))
    run(c.discover())
    posts = [r for r in fake.requests if r.method != "GET"]
    assert len(posts) == 1 and posts[0].url.path == "/v2/oauth2/token"
    for r in fake.requests:
        if r.method == "GET":
            assert SECRET not in str(r.url) and SECRET not in r.headers.get("authorization", "")


def test_plain_text_helper() -> None:
    html = "<p>a&nbsp;b</p><script>x()</script><ul><li>one</li><li>two</li></ul>"
    assert plain_text(html) == "a b\n\none\ntwo"
    assert plain_text(None) == ""
