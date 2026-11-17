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
        self.thread_pages = 0  # > 0: every conversation has this many pages of threads
        self.token_status = 400  # what the token endpoint answers a wrong secret

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
                return httpx.Response(
                    self.token_status,
                    json={"error": "invalid_client", "error_description": "Bad client"},
                )
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
        parts = path.split("/")
        if path.startswith("/v2/conversations/") and path.endswith("/data"):
            # Help Scout's documented path: /v2/conversations/{cid}/attachments/{aid}/data.
            # The old /threads/{tid}/attachments/... form answers 404, as it did live (B-37).
            if len(parts) == 7 and parts[4] == "attachments" and parts[5] == "501":
                return httpx.Response(200, json=fx("attachment_501"))
            return httpx.Response(404, json={})
        if path.startswith("/v2/conversations/") and path.endswith("/threads"):
            if self.thread_pages:
                return self.thread_page(int(query.get("page", "1")))
            return httpx.Response(200, json=fx(f"threads_{parts[3]}"))
        if path.startswith("/v2/mailboxes/") and "saved-replies" in path:
            if len(parts) == 5:
                return httpx.Response(200, json=fx(f"saved_replies_{parts[3]}"))
            if not (FIXTURES / f"saved_reply_{parts[5]}.json").exists():
                return httpx.Response(404, json={})
            return httpx.Response(200, json=fx(f"saved_reply_{parts[5]}"))
        return httpx.Response(404, json={})

    def thread_page(self, number: int) -> httpx.Response:
        """Page `number` of a conversation with `thread_pages` pages of one thread each."""
        thread = {
            "id": 9000 + number,
            "type": "customer",
            "body": f"<p>Part {number}</p>",
            "createdAt": f"2025-01-01T00:{number % 60:02d}:00Z",
            "createdBy": {"type": "customer", "email": "ann@example.org"},
        }
        page = {"size": 1, "totalElements": self.thread_pages, "totalPages": self.thread_pages}
        return httpx.Response(
            200, json={"_embedded": {"threads": [thread]}, "page": {**page, "number": number}}
        )

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
    assert counts.replies == 5 and counts.articles == 2


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
    assert [m.kind for m in t.messages] == ["customer", "agent"]  # the agent sent a file
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
    # A place a person can open (the conversation), not an API path (B-37).
    assert customer.attachments[0].url == (
        "https://secure.helpscout.net/conversation/1001/1#attachment-501"
    )
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
    greeting, chat = page.items[:2]
    assert (greeting.external_id, greeting.shortcut) == ("5", "greeting")
    assert greeting.body == "Hello there,\n\nHow can we help?"
    assert chat.body == "Hi! How can we help?"
    assert [f for f in c.failures.take() if f.external_id in ("5", "6")] == []


def test_articles_with_a_docs_key() -> None:
    fake = Fake()
    c = connector(fake, docs_key="dk-1")
    assert c.warnings() == ["Spam conversations are not imported.", helpscout.RATINGS_WARNING]
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


# Regressions from the live tests (B-37/38/39) and the 17 Nov audit (C2, C6, C9, C18)


def test_attachment_bytes_come_from_the_documented_data_path() -> None:
    """B-37: the file is read from /v2/conversations/{cid}/attachments/{aid}/data (no
    thread in the path), the documented endpoint; the old path answered 404 live."""
    fake = Fake()
    c = connector(fake)
    receipt = run(c.page("tickets", None)).items[0].messages[0].attachments[0]
    assert run(c.fetch(receipt, 1000)) == b"hello world"
    assert fake.requests[-1].url.path == "/v2/conversations/1001/attachments/501/data"


def test_a_file_kept_as_a_link_points_at_the_conversation_not_the_api() -> None:
    """B-37: the link line a person reads names the file and the conversation's web address."""
    t = run(connector(Fake()).page("tickets", None)).items[0]
    gone = t.messages[0].attachments[1]
    line = rules.link_line(gone.name, gone.size, gone.url, "the source no longer has it")
    assert "gone.png" in line and "https://secure.helpscout.net/conversation/1001/1" in line
    assert "/v2/" not in line and "api.helpscout.net" not in line


def test_fetch_reads_only_ids_from_its_own_web_addresses() -> None:
    fake = Fake()
    c = connector(fake)
    for url in (
        "https://secure.helpscout.net.evil.example/conversation/1001/1#attachment-501",
        "http://secure.helpscout.net/conversation/1001/1#attachment-501",
        "https://secure.helpscout.net/conversation/1001/1#attachment-501/../x",
        "https://secure.helpscout.net/conversation/10%2F01/1#attachment-501",
        "/v2/conversations/1001/threads/11/attachments/501/data",
    ):
        assert run(c.fetch(rules.Attachment("x", url), 1000)) is None
    assert fake.requests == []  # nothing was asked of the API


def test_saved_reply_names_become_valid_unique_shortcuts() -> None:
    """B-38: a name with spaces or symbols was passed as the shortcut and every such reply
    was skipped by CeDesk's shortcut rule."""
    from cedesk.helpdesk.domain.saved_replies import SHORTCUT

    c = connector(Fake())
    replies = {r.external_id: r for r in run(c.page("replies", None)).items}
    assert set(replies) == {"5", "6", "7", "8", "9"}
    assert all(SHORTCUT.match(r.shortcut) for r in replies.values())
    assert replies["6"].shortcut == "chat-only"
    assert replies["7"].shortcut == "refund-eu-30-days"
    assert replies["8"].shortcut == "refund-eu-30-days-2"  # unique within the page
    assert replies["9"].shortcut == "reply-9"  # no Latin letter or digit in the name
    assert replies["8"].body == "Our refund window is 30 days."


def test_a_saved_reply_without_its_detail_is_noted_incomplete() -> None:
    """C20: the detail call for reply 7 answers 404; its preview is kept, and noted."""
    c = connector(Fake())
    replies = {r.external_id: r for r in run(c.page("replies", None)).items}
    assert replies["7"].body == "Refunds in the EU are"
    noted = c.failures.take()
    assert [(f.kind, f.external_id, f.category) for f in noted] == [("reply", "7", "incomplete")]
    assert "Refunds" not in noted[0].error  # no customer text in the note


@pytest.mark.parametrize("status", [400, 401])
def test_a_wrong_app_secret_says_check_the_app_id_and_secret(status: int) -> None:
    """B-39: the token endpoint answers 400 (or 401) to a wrong app id or secret."""
    fake = Fake()
    fake.token_status = status
    with pytest.raises(SourceError) as err:
        run(connector(fake, secret="wrong-secret").check())
    assert err.value.permanent
    assert str(err.value) == (
        "Help Scout refused these credentials (check the App ID and App Secret)."
    )


def test_a_message_with_files_and_no_text_is_kept() -> None:
    """C2 / P0-5: thread 32 holds only a file; it was dropped with its file."""
    t = run(connector(Fake()).page("tickets", {"page": 2})).items[0]
    first, agent = t.messages
    assert first.kind == "customer" and first.body == "Hi there"
    assert agent.kind == "agent" and agent.body == ""
    assert agent.author_email == "sam@example.com" and agent.author_name == "Sam Agent"
    assert agent.created_at == datetime(2025, 3, 3, 8, 2, tzinfo=UTC)
    assert [(a.name, a.size) for a in agent.attachments] == [("label.png", 20191)]
    assert agent.attachments[0].url == (
        "https://secure.helpscout.net/conversation/1003/3#attachment-601"
    )  # no `_links.web` in this answer: built from the id and number


def test_recipients_and_the_conversation_web_address() -> None:
    """C6 / P1-19: thread to/cc/bcc and conversation cc/bcc were not read."""
    t = run(connector(Fake()).page("tickets", None)).items[0]
    reply = next(m for m in t.messages if m.external_id == "13")
    assert list(reply.to) == ["ann@example.org"]
    assert list(reply.cc) == ["boss@example.org", "ops@example.org"]
    assert list(reply.bcc) == ["audit@example.org"]
    assert list(t.cc) == ["boss@example.org", "audit@example.org"]
    assert t.source_url == "https://secure.helpscout.net/conversation/1001/1"


def test_contacts_keep_phone_other_emails_and_organization() -> None:
    ann, bo = run(connector(Fake()).page("contacts", None)).items
    assert ann.email == "ann@example.org" and list(ann.other_emails) == ["ann.lee@home.example"]
    assert ann.phone == "+1 555-0100" and ann.organization == "Acme, Inc"
    assert bo.phone is None and bo.organization is None and list(bo.other_emails) == []


def test_a_long_conversation_is_read_past_the_old_40_page_cap() -> None:
    """C6: 45 pages of threads were cut at 40, silently."""
    fake = Fake()
    fake.thread_pages = 45
    c = connector(fake)
    t = run(c.page("tickets", None)).items[0]
    assert len(t.messages) == 45
    assert c.failures.take() == []


def test_the_safety_limit_is_counted_and_the_ticket_noted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(helpscout, "THREAD_PAGES_MAX", 3)
    fake = Fake()
    fake.thread_pages = 5
    c = connector(fake)
    with rules.collecting(rules.Losses()) as losses:
        t = run(c.page("tickets", None)).items[0]
    assert len(t.messages) == 3
    assert losses.counts == {"history_capped": 1}
    noted = c.failures.take()
    assert [(f.kind, f.external_id, f.category) for f in noted] == [
        ("ticket", "1001", "incomplete")
    ]


def test_missing_dates_are_never_1970() -> None:
    """C9: a conversation and thread with no dates became 1 Jan 1970."""

    class Undated(Fake):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if request.method == "GET" and path == "/v2/conversations":
                self.requests.append(request)
                conv = {
                    "id": 1004,
                    "number": 4,
                    "status": "active",
                    "type": "email",
                    "mailboxId": 100,
                    "subject": "No date",
                    "primaryCustomer": {"id": 7, "email": "ann@example.org"},
                }
                page = {"size": 1, "totalElements": 1, "totalPages": 1, "number": 1}
                return httpx.Response(
                    200, json={"_embedded": {"conversations": [conv]}, "page": page}
                )
            if request.method == "GET" and path == "/v2/conversations/1004/threads":
                self.requests.append(request)
                thread = {"id": 41, "type": "customer", "body": "Hello", "createdBy": {}}
                page = {"size": 25, "totalElements": 1, "totalPages": 1, "number": 1}
                return httpx.Response(200, json={"_embedded": {"threads": [thread]}, "page": page})
            return super().__call__(request)

    c = connector(Undated())
    with rules.collecting(rules.Losses()) as losses:
        t = run(c.page("tickets", None)).items[0]
    assert t.created_at.year >= 2026 and t.messages[0].created_at.year >= 2026
    assert losses.counts.get("date_missing", 0) >= 1
    assert ("ticket", "1004", "incomplete") in {
        (f.kind, f.external_id, f.category) for f in c.failures.take()
    }


def test_lists_are_paged_in_a_stable_order() -> None:
    """C18: sorting by modifiedAt let a record updated mid-import move between pages."""
    fake = Fake()
    c = connector(fake)
    run(c.page("tickets", None))
    run(c.page("contacts", None))
    run(c.changed_tickets(datetime(2025, 3, 1, tzinfo=UTC), None))
    lists = [r for r in fake.requests if r.url.path == "/v2/conversations"]
    for r in lists:
        assert (r.url.params["sortField"], r.url.params["sortOrder"]) == ("number", "asc")
    assert "modifiedSince" in lists[-1].url.params
    customers = next(r for r in fake.requests if r.url.path == "/v2/customers")
    assert customers.url.params["sortField"] == "createdAt"


def test_the_dry_run_says_ratings_are_not_imported() -> None:
    assert helpscout.RATINGS_WARNING in connector(Fake()).warnings()
