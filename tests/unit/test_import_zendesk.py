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
            if q.get("start_time") not in (None, "1"):
                return httpx.Response(200, json=fx("incremental_changed"))
            return httpx.Response(
                200, json=fx("incremental_2" if "cursor" in q else "incremental_1")
            )
        if path == "/api/v2/tickets/105/comments.json":
            return httpx.Response(200, json=fx("comments_105"))
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


@pytest.fixture(autouse=True)
def _no_cached_tokens() -> None:
    zendesk._TOKENS.clear()


def connector(desk: Desk | None = None, subdomain: str = "acme") -> SourceConnector:
    return zendesk.make(
        {"subdomain": subdomain},
        {"email": "sam@acme.test", "token": SECRET},
        transport=httpx.MockTransport(desk or Desk()),
        guard=no_guard,
    )


def run(coro: Any) -> Any:
    return asyncio.run(coro)


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

    return asyncio.run(go())


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
        ("agent", None, ""),  # a file only, by a user Zendesk no longer returns (C2, C14)
        ("customer", "old.cc@example.org", "Me too, please refund"),  # unknown, by email
        ("agent", "former@acme.test", "Closing this out"),  # deleted agent keeps the role
    ]  # 1007 (a note with neither text nor a file) is left out
    assert t.messages[2].author_name == "Lee Agent" and t.messages[0].external_id == "1001"
    many = [r for r in desk.requests if r.url.path.endswith("show_many.json")]
    assert len(many) == 1  # one batch for the whole page
    assert many[0].url.params["include_deleted"] == "true"


def test_a_message_with_files_and_no_text_is_kept() -> None:
    t = run(connector().page("tickets", None)).items[0]
    (only_file,) = [m for m in t.messages if m.external_id == "1004"]
    assert only_file.body == "" and only_file.kind == "agent"
    assert only_file.author_name == zendesk.DELETED_USER
    assert only_file.created_at == datetime(2025, 3, 2, 9, 5, tzinfo=UTC)
    assert [(a.name, a.size, a.mime) for a in only_file.attachments] == [
        ("refund-proof.png", 5120, "image/png")
    ]


def test_a_message_with_neither_text_nor_files_is_counted() -> None:
    losses = rules.Losses()
    with rules.collecting(losses):
        run(connector().page("tickets", None))
    assert losses.counts.get("message_without_text") == 1


def test_an_unknown_author_is_never_silently_a_customer() -> None:
    """C14: a user show_many doesn't return is the customer only when it is the requester
    or the comment came in by email; otherwise it is an agent named "Deleted user"."""

    def nobody(r: httpx.Request) -> httpx.Response | None:
        if r.url.path.endswith("show_many.json"):
            return httpx.Response(200, json={"users": []})
        return None

    t = run(connector(Desk(nobody)).page("tickets", None)).items[0]
    seen = {m.external_id: (m.kind, m.author_name, m.author_email) for m in t.messages}
    assert seen["1001"] == ("customer", None, "ann@example.org")  # the requester
    assert seen["1003"] == ("agent", zendesk.DELETED_USER, None)  # by web, not requester
    assert seen["1005"] == ("customer", None, "old.cc@example.org")  # came in by email
    assert seen["1002"][0] == "note"


def test_email_recipients_of_a_comment() -> None:
    first = run(connector().page("tickets", None)).items[0].messages[0]
    assert list(first.to) == ["support@acme.zendesk.com", "billing@acme.test"]
    assert list(first.cc) == ["cy@example.org", "dee@example.org"]


def test_custom_fields_keep_zero_and_false_and_show_dropdown_labels() -> None:
    """C5: 0 and False are values; a dropdown's stored tag becomes its option's label; the
    null one and a field the account no longer lists are left out."""
    t = run(connector().page("tickets", None)).items[0]
    assert t.fields == {
        "Order ID": "A-1042",
        "Plan": "Gold plan",
        "Seats": 0,
        "Opted in": False,
        "Products": "Web app, Mobile app",
    }
    cleaned = rules.clean_fields(t.fields)
    assert cleaned["seats"] == "0" and cleaned["opted_in"] == "false"


def test_ticket_extras_from_the_export() -> None:
    tickets, _ = all_tickets(connector())
    first, task = tickets[0], tickets[2]
    assert first.ticket_type == "incident"
    assert first.source_url == "https://acme.zendesk.com/agent/tickets/101"
    assert list(first.cc) == ["cy@example.org"]  # collaborators + email CCs once; 77 unknown
    assert list(first.followers) == ["lee@acme.test"]
    assert list(first.links) == [rules.Link("parent", "99")]  # the incident's problem
    assert first.sla_due_at is None
    assert first.requester.phone == "+1 555 0100"
    assert first.requester.organization == "Example Org"  # side-loaded organizations
    assert task.ticket_type == "task"
    assert task.sla_due_at == datetime(2025, 4, 5, 17, 0, tzinfo=UTC)
    assert not task.links and not task.cc


def test_a_ticket_without_created_at_is_kept_and_noted() -> None:
    """C9: no created_at is no longer a temporary error that retries the page forever."""
    page = fx("incremental_1")
    del page["tickets"][0]["created_at"]

    def export(r: httpx.Request) -> httpx.Response | None:
        return httpx.Response(200, json=page) if "incremental" in r.url.path else None

    c = connector(Desk(export))
    t = run(c.page("tickets", None)).items[0]
    assert t.created_at == datetime(2025, 3, 2, 9, 0, tzinfo=UTC)  # its updated_at
    noted = c.failures.take()  # type: ignore[attr-defined]
    assert ("101", "incomplete") not in [(f.external_id, f.category) for f in noted]
    page["tickets"][0].pop("updated_at")
    c = connector(Desk(export))
    t = run(c.page("tickets", None)).items[0]
    assert t.created_at == datetime(2025, 3, 1, 10, 0, tzinfo=UTC)  # its first comment
    del page["tickets"][2]["created_at"], page["tickets"][2]["updated_at"]
    c = connector(Desk(export))
    run(c.page("tickets", None))  # 103 has no comments (404) and no date at all
    noted = c.failures.take()  # type: ignore[attr-defined]
    assert [f.error for f in noted if f.external_id == "103" and "date" in f.error]


def test_a_history_past_the_safety_limit_is_counted_and_noted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C6: the cap is high (1,000,000 comments), and reaching it is never silent."""
    monkeypatch.setattr(zendesk, "_HISTORY_PAGES", 3)

    def endless(r: httpx.Request) -> httpx.Response | None:
        if r.url.path == "/api/v2/tickets/101/comments.json":
            return httpx.Response(200, json=fx("comments_101"))  # always has_more
        return None

    desk = Desk(endless)
    c = connector(desk)
    losses = rules.Losses()
    with rules.collecting(losses):
        run(c.page("tickets", None))
    assert losses.counts.get("history_capped") == 1
    calls = [r for r in desk.requests if r.url.path == "/api/v2/tickets/101/comments.json"]
    assert len(calls) == 3
    noted = c.failures.take()  # type: ignore[attr-defined]
    assert ("101", "incomplete") in [(f.external_id, f.category) for f in noted]


def test_small_lists_are_read_to_their_end() -> None:
    """C6: groups (and fields, macros) were cut at 100 pages."""

    def many_groups(r: httpx.Request) -> httpx.Response | None:
        if r.url.path != "/api/v2/groups.json":
            return None
        n = int(r.url.params.get("page[after]") or 0)
        more = n < 149
        return httpx.Response(
            200,
            json={
                "groups": [{"id": 1000 + n, "name": f"Group {n}"}],
                "meta": {"has_more": more, "after_cursor": str(n + 1) if more else None},
            },
        )

    assert len(run(connector(Desk(many_groups)).groups())) == 150


def test_ticket_fields_mapping() -> None:
    t = run(connector().page("tickets", None)).items[0]
    assert t.status == "solved" and t.priority == "high" and t.channel == "email"
    assert t.tags == ["vip", "Refund Request"]
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


def test_attachments_are_parsed_and_unusable_ones_skipped() -> None:
    since = datetime(2025, 5, 1, tzinfo=UTC)
    page = run(connector().changed_tickets(since, None))  # type: ignore[attr-defined]
    (msg,) = page.items[0].messages
    assert [(a.name, a.url, a.size, a.mime) for a in msg.attachments] == [
        (
            "receipt.pdf",
            "https://acme.zendesk.com/attachments/token/abc/?name=receipt.pdf",
            2048,
            "application/pdf",
        )
    ]


def test_fetch_returns_bytes_and_none_when_gone() -> None:
    def files(r: httpx.Request) -> httpx.Response | None:
        if r.url.path == "/attachments/ok":
            return httpx.Response(200, content=b"hello")
        if r.url.path == "/attachments/gone":
            return httpx.Response(404)
        return None

    c = connector(Desk(files))
    ok = rules.Attachment("a", "https://acme.zendesk.com/attachments/ok")
    gone = rules.Attachment("b", "https://acme.zendesk.com/attachments/gone")
    assert isinstance(c, rules.FileSource)
    assert run(c.fetch(ok, 1000)) == b"hello"
    assert run(c.fetch(gone, 1000)) is None


def test_changed_tickets_start_at_since_and_parse_the_page() -> None:
    desk = Desk()
    c = connector(desk)
    assert isinstance(c, rules.ChangeSource)
    since = datetime(2025, 5, 1, 9, 0, tzinfo=UTC)
    page = run(c.changed_tickets(since, None))
    assert page.cursor is None and [t.external_id for t in page.items] == ["105"]
    inc = [r for r in desk.requests if "incremental" in r.url.path]
    assert inc[0].url.params["start_time"] == str(int(since.timestamp()))
    assert {r.method for r in desk.requests} == {"GET"}


# OAuth (C0 / P0-1): Zendesk makes no new API tokens since 27 Oct 2026

CLIENT_SECRET = "zd-client-SECRET-0042"


class OAuthDesk(Desk):
    """The fake Zendesk with its token endpoint: hands out `tok-1`, `tok-2`, ... and accepts
    on the API only the newest token (or `stale` ones listed as still good)."""

    def __init__(self, *, token_status: int = 200, expires_in: int | None = 1800) -> None:
        super().__init__()
        self.issued: list[str] = []
        self.token_status = token_status
        self.expires_in = expires_in
        self.revoked: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/tokens":
            self.requests.append(request)
            if self.token_status != 200:
                return httpx.Response(
                    self.token_status,
                    json={"error": "invalid_client", "error_description": "bad client"},
                )
            token = f"tok-{len(self.issued) + 1}"
            self.issued.append(token)
            return httpx.Response(
                200,
                json={
                    "access_token": token,
                    "token_type": "bearer",
                    "scope": "read",
                    "expires_in": self.expires_in,
                },
            )
        sent = request.headers.get("authorization", "")
        if not sent.startswith("Bearer ") or sent[7:] in self.revoked:
            self.requests.append(request)
            return httpx.Response(401, json={"error": "Couldn't authenticate you"})
        return super().__call__(request)


def oauth(desk: Desk, creds: dict[str, str] | None = None) -> SourceConnector:
    return zendesk.make(
        {"subdomain": "acme"},
        {"client_id": "cedesk_import", "client_secret": CLIENT_SECRET} if creds is None else creds,
        transport=httpx.MockTransport(desk),
        guard=no_guard,
    )


def test_client_credentials_get_a_token_and_sign_calls_with_it() -> None:
    desk = OAuthDesk()
    info = run(oauth(desk).check())
    assert info.name == "acme.zendesk.com"
    token_call, me = desk.requests[0], desk.requests[1]
    assert token_call.method == "POST" and token_call.url.host == "acme.zendesk.com"
    assert token_call.url.path == "/oauth/tokens"
    form = dict(httpx.QueryParams(token_call.content.decode()))
    assert form == {
        "grant_type": "client_credentials",
        "client_id": "cedesk_import",
        "client_secret": CLIENT_SECRET,
        "scope": "read",
    }
    assert me.headers["authorization"] == "Bearer tok-1"
    assert {r.method for r in desk.requests[1:]} == {"GET"}  # the token call is the only POST


def test_the_token_is_reused_across_connectors_until_it_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker pass builds a new connector; it must not mint a token every time."""
    clock = [1000.0]
    monkeypatch.setattr(zendesk, "_now", lambda: clock[0])
    desk = OAuthDesk(expires_in=1800)
    run(oauth(desk).check())
    run(oauth(desk).discover())
    assert desk.issued == ["tok-1"]
    clock[0] += 1800 - zendesk.TOKEN_MARGIN + 1  # close to expiry: fetched again
    run(oauth(desk).check())
    assert desk.issued == ["tok-1", "tok-2"]


def test_a_refused_token_is_replaced_once() -> None:
    desk = OAuthDesk()
    c = oauth(desk)
    run(c.check())
    desk.revoked.add("tok-1")  # revoked in Admin Center, or expired early
    run(c.check())
    assert desk.issued == ["tok-1", "tok-2"]


def test_a_refused_new_token_is_a_permanent_error_not_a_loop() -> None:
    desk = OAuthDesk()
    desk.revoked.update({"tok-1", "tok-2", "tok-3"})
    with pytest.raises(SourceError) as err:
        run(oauth(desk).check())
    assert err.value.permanent and desk.issued == ["tok-1"]  # just got: not tried again


@pytest.mark.parametrize("status", [400, 401, 403])
def test_a_bad_client_is_permanent_and_never_echoes_the_secret(status: int) -> None:
    desk = OAuthDesk(token_status=status)
    with pytest.raises(SourceError) as err:
        run(oauth(desk).check())
    assert err.value.permanent and "OAuth client" in str(err.value)
    assert CLIENT_SECRET not in str(err.value) and "cedesk_import" not in str(err.value)


def test_a_token_answer_without_expiry_is_still_used() -> None:
    desk = OAuthDesk(expires_in=None)  # a client made before 30 Apr 2026: no expiry
    run(oauth(desk).check())
    assert desk.requests[1].headers["authorization"] == "Bearer tok-1"


def test_a_given_access_token_is_sent_as_bearer() -> None:
    desk = Desk()
    run(oauth(desk, {"access_token": "pasted-token"}).check())
    assert desk.requests[0].headers["authorization"] == "Bearer pasted-token"
    assert all(r.url.path != "/oauth/tokens" for r in desk.requests)


def test_client_credentials_win_over_an_old_api_token() -> None:
    desk = OAuthDesk()
    both = {
        "client_id": "cedesk_import", "client_secret": CLIENT_SECRET,
        "email": "sam@acme.test", "token": SECRET,
    }  # fmt: skip
    run(oauth(desk, both).check())
    assert desk.requests[0].url.path == "/oauth/tokens"


@pytest.mark.parametrize(
    ("creds", "missing"),
    [
        ({}, "missing client_id, client_secret"),
        ({"client_id": "x"}, "missing client_secret"),
        ({"client_secret": CLIENT_SECRET}, "missing client_id"),
        ({"email": "sam@acme.test"}, "missing token"),
        ({"token": SECRET}, "missing email"),
    ],
)
def test_an_incomplete_sign_in_is_named(creds: dict[str, str], missing: str) -> None:
    with pytest.raises(SourceError) as err:
        oauth(Desk(), creds)
    assert err.value.permanent and missing in str(err.value)
    assert "OAuth client" in str(err.value) and "API token" in str(err.value)
    assert CLIENT_SECRET not in str(err.value) and SECRET not in str(err.value)


def test_files_on_the_account_host_get_the_oauth_token() -> None:
    desk = OAuthDesk()

    def files(r: httpx.Request) -> httpx.Response | None:
        return httpx.Response(200, content=b"pdf") if r.url.path == "/attachments/x" else None

    desk.override = files
    c = oauth(desk)
    got = run(c.fetch(rules.Attachment("x", "https://acme.zendesk.com/attachments/x"), 100))  # type: ignore[attr-defined]
    assert got == b"pdf"
    assert desk.requests[-1].headers["authorization"] == "Bearer tok-1"
