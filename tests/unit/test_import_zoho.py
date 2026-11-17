"""The Zoho Desk importer, driven by recorded-shape JSON and `httpx.MockTransport`; no network.

The fake follows the Zoho Desk API reference (desk.zoho.com/DeskAPIDocument) where the live
trial of 16 Nov tripped the importer: `contacts/count` needs a `viewId` (422 without, B-45),
articles take `from` > 0 and `limit` 1-50 (422 otherwise, B-46), the token endpoint throttles
(B-47), tags have their own call (B-48) and the ticket list has no `modifiedTime` (B-49)."""

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from cedesk.helpdesk.domain import imports as rules
from cedesk.helpdesk.domain.imports import Attachment, RateLimited, SourceError
from cedesk.helpdesk.service.imports import zoho
from cedesk.helpdesk.service.imports.connectors import build_connector

FIXTURES = Path(__file__).parent.parent / "fixtures" / "imports" / "zoho"
SECRET = "zoho-client-secret-123"
REFRESH = "1000.refresh-token-456"
DESK, ACCOUNTS = "desk.zoho.in", "accounts.zoho.in"
THROTTLED = {
    "error_description": "You have made too many requests continuously. Please try again after "
    "some time.",
    "error": "Access Denied",
    "status": "failure",
}


@pytest.fixture(autouse=True)
def no_cached_tokens() -> Iterator[None]:
    """Each test signs in afresh: the process-wide token cache starts empty."""
    zoho._TOKENS.clear()
    zoho._ORGS.clear()
    yield
    zoho._TOKENS.clear()
    zoho._ORGS.clear()


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


async def allow(host: str) -> None:
    return None


def unprocessable(message: str) -> httpx.Response:
    return httpx.Response(422, json={"errorCode": "UNPROCESSABLE_ENTITY", "message": message})


def route(request: httpx.Request) -> httpx.Response:
    path, params = request.url.path, request.url.params
    if request.url.host == ACCOUNTS:
        return httpx.Response(200, json=fixture("token"))
    if path.endswith("/content"):
        return httpx.Response(200, content=b"%PDF-label")
    if path == "/api/v1/organizations":
        return httpx.Response(200, json=fixture("organizations"))
    if request.headers.get("orgId") != "6001":
        return httpx.Response(400, json={"errorCode": "INVALID_ORG"})
    if "limit" in params and int(params["limit"]) > 100 and path != "/api/v1/departments":
        return unprocessable("limit exceeds the range 1-100")
    simple = {
        "/api/v1/departments": "departments", "/api/v1/contacts": "contacts",
        "/api/v1/tickets/z1/threads": "threads_z1", "/api/v1/tickets/z1/comments": "comments_z1",
        "/api/v1/tickets/z1/tags": "tags_z1",
    }  # fmt: skip
    if path == "/api/v1/ticketsCount":
        return httpx.Response(200, json={"count": 2})
    if path == "/api/v1/contacts/count":
        if params.get("viewId") != "6000000011111":
            return unprocessable("The mandatory parameter viewId is missing.")
        return httpx.Response(200, json={"count": "2"})  # Zoho writes this one as text
    if path == "/api/v1/views" and params.get("module") == "contacts":
        return httpx.Response(200, json=fixture("views_contacts"))
    if path == "/api/v1/articles":
        if int(params.get("from", "1")) < 1:
            return unprocessable("from exceeds the range >0")
        if int(params.get("limit", "10")) > 50:
            return unprocessable("limit exceeds the range 1-50")
        return httpx.Response(200, json=fixture("articles"))
    if path == "/api/v1/tickets":
        return httpx.Response(200, json=fixture("tickets"))
    if path == "/api/v1/tickets/archivedTickets":
        found = FIXTURES / f"archived_{params.get('departmentId')}.json"
        if params.get("from") == "0" and found.exists():
            return httpx.Response(200, json=fixture(found.stem))
        return httpx.Response(204)
    if path in simple:
        return httpx.Response(200, json=fixture(simple[path]))
    for prefix, name in (
        ("/api/v1/tickets/z1/threads/", "thread_"),
        ("/api/v1/articles/", "article_"),
        ("/api/v1/tickets/", "ticket_"),
    ):
        rest = path[len(prefix) :]
        if (
            path.startswith(prefix)
            and "/" not in rest
            and (FIXTURES / f"{name}{rest}.json").exists()
        ):
            return httpx.Response(200, json=fixture(f"{name}{rest}"))
    if path.startswith("/api/v1/tickets/z2/"):
        return httpx.Response(204)  # an empty list
    return httpx.Response(404, json={"errorCode": "URL_NOT_FOUND"})


def connector(
    handler: Any = route, seen: list[httpx.Request] | None = None, **settings: str
) -> Any:
    def wrapped(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        response: httpx.Response = handler(request)
        return response

    return zoho.make(
        {"region": "in", **settings},
        {"client_id": "1000.CLIENT", "client_secret": SECRET, "refresh_token": REFRESH},
        transport=httpx.MockTransport(wrapped),
        guard=allow,
    )


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def tickets() -> list[Any]:
    page = run(connector().page("tickets", None))
    assert page.cursor is None  # fewer than a full page
    return list(page.items)


def token_calls(seen: list[httpx.Request]) -> int:
    return sum(1 for r in seen if r.url.host == ACCOUNTS)


# Sign-in


def test_sign_in_with_the_refresh_token_then_the_org() -> None:
    seen: list[httpx.Request] = []
    assert run(connector(seen=seen).check()).name == "Acme Support (desk.zoho.in)"
    token = seen[0]
    assert (token.method, token.url.host, token.url.path) == ("POST", ACCOUNTS, "/oauth/v2/token")
    form = dict(x.split("=", 1) for x in token.content.decode().split("&"))
    assert form["grant_type"] == "refresh_token" and form["refresh_token"] == REFRESH
    api = seen[-1]
    assert api.url.host == DESK and api.headers["Authorization"] == "Zoho-oauthtoken zat-1"


def test_an_account_with_several_orgs_must_name_one() -> None:
    def two(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/organizations":
            orgs = [
                {"id": "6001", "companyName": "Acme Support"},
                {"id": "7002", "companyName": "Acme EU"},
            ]
            return httpx.Response(200, json={"data": orgs})
        return route(request)

    with pytest.raises(SourceError) as err:
        run(connector(two).check())
    assert err.value.permanent and "org_id" in str(err.value) and "7002" in str(err.value)
    assert run(connector(two, org_id="6001").check()).name == "Acme Support (desk.zoho.in)"
    with pytest.raises(SourceError):
        run(connector(two, org_id="9999").check())


def test_a_bad_refresh_token_is_permanent_says_so_and_is_never_shown() -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        if request.url.host == ACCOUNTS:
            return httpx.Response(200, json={"error": "invalid_code"})
        return route(request)

    with pytest.raises(SourceError) as err:
        run(connector(refused).check())
    assert err.value.permanent
    assert "refresh token" in str(err.value) and "invalid_code" in str(err.value)
    assert "'in'" in str(err.value)  # the data centre it was used against
    assert SECRET not in str(err.value) and REFRESH not in str(err.value)


def test_a_bad_client_is_named_and_an_unknown_error_quotes_only_its_code() -> None:
    def answer(body: dict[str, str], status: int = 200) -> Any:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == ACCOUNTS:
                return httpx.Response(status, json=body)
            return route(request)

        return handler

    with pytest.raises(SourceError) as err:
        run(connector(answer({"error": "invalid_client"})).check())
    assert err.value.permanent and "client id and secret" in str(err.value)
    with pytest.raises(SourceError) as err:
        run(connector(answer({"error": "<b>" + REFRESH + "</b>"}, 400)).check())
    assert err.value.permanent and REFRESH not in str(err.value)


def test_the_token_throttle_waits_instead_of_failing_the_run() -> None:
    """B-47: past 10 token requests in 10 minutes Zoho answers 400 "Access Denied ... too
    many requests continuously"; that was a permanent "(400)" and failed the run."""

    def throttled(request: httpx.Request) -> httpx.Response:
        if request.url.host == ACCOUNTS:
            return httpx.Response(400, json=THROTTLED)
        return route(request)

    with pytest.raises(RateLimited) as waited:
        run(connector(throttled).check())
    assert waited.value.retry_after == zoho.TOKEN_THROTTLE_WAIT == 600


def test_one_token_serves_every_connector_built_for_the_account() -> None:
    """B-47: the framework builds a connector per slice; each asked Zoho for a new token."""
    seen: list[httpx.Request] = []
    for _ in range(5):
        c = connector(seen=seen)
        run(c.check())
        run(c.groups())
    assert token_calls(seen) == 1
    assert {r.headers["Authorization"] for r in seen if r.url.host == DESK} == {
        "Zoho-oauthtoken zat-1"}  # fmt: skip

    # another refresh token is another account: its own token
    def watched(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return route(request)

    other = zoho.make(
        {"region": "in"},
        {"client_id": "1000.CLIENT", "client_secret": SECRET, "refresh_token": "1000.other"},
        transport=httpx.MockTransport(watched),
        guard=allow,
    )
    run(other.check())
    assert token_calls(seen) == 2
    assert all(REFRESH not in key and SECRET not in key for key in zoho._TOKENS)


def test_a_cached_token_is_renewed_when_it_runs_out(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(zoho, "_now", lambda: clock[0])
    seen: list[httpx.Request] = []
    run(connector(seen=seen).check())
    clock[0] += 3600 - zoho.TOKEN_MARGIN - 1
    run(connector(seen=seen).groups())
    assert token_calls(seen) == 1
    clock[0] += 2
    run(connector(seen=seen).groups())
    assert token_calls(seen) == 2


def test_an_expired_token_is_renewed_once() -> None:
    tokens = iter(["zat-1", "zat-2"])
    issued: list[str] = []

    def rotating(request: httpx.Request) -> httpx.Response:
        if request.url.host == ACCOUNTS:
            issued.append(next(tokens))
            return httpx.Response(200, json={"access_token": issued[-1], "expires_in": 3600})
        if request.url.path != "/api/v1/organizations" and request.headers[
            "Authorization"
        ].endswith("zat-1"):
            return httpx.Response(401, json={"errorCode": "INVALID_OAUTH"})
        return route(request)

    c = connector(rotating)
    run(c.check())  # zat-1 worked here; later Zoho stops taking it
    assert [g.name for g in run(c.groups())] == ["Billing", "Shipping"]
    assert issued == ["zat-1", "zat-2"]
    # The next connector takes zat-2 from the cache: no third token.
    assert [g.name for g in run(connector(rotating).groups())] == ["Billing", "Shipping"]
    assert issued == ["zat-1", "zat-2"]


def test_a_refusal_that_isnt_about_the_token_costs_no_new_token() -> None:
    """B-47: any permanent error (a 404, a 422) used to fetch a new token and try again."""
    seen: list[httpx.Request] = []

    def broken(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/departments":
            return unprocessable("departments are unavailable")
        return route(request)

    c = connector(broken, seen=seen)
    run(c.check())
    with pytest.raises(SourceError):
        run(c.groups())
    assert token_calls(seen) == 1


# Discover


def test_discover_counts_contacts_through_the_all_contacts_view() -> None:
    """B-45: `GET /contacts/count` without `viewId` is a 422 and failed the dry run."""
    seen: list[httpx.Request] = []
    counts = run(connector(seen=seen).discover())
    assert (counts.tickets, counts.contacts, counts.articles, counts.replies, counts.groups) == (
        2, 2, 2, 0, 2)  # fmt: skip
    asked = next(r for r in seen if r.url.path == "/api/v1/contacts/count")
    assert asked.url.params["viewId"] == "6000000011111"


def test_a_count_zoho_refuses_is_counted_by_paging() -> None:
    def no_counts(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("/api/v1/views", "/api/v1/ticketsCount"):
            return unprocessable("The mandatory parameter viewId is missing.")
        return route(request)

    counts = run(connector(no_counts).discover())
    assert (counts.tickets, counts.contacts, counts.articles) == (2, 2, 2)


def test_a_count_cut_short_says_it_is_a_lower_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zoho, "COUNT_PAGES_MAX", 1)
    monkeypatch.setattr(zoho, "LIMIT", 2)

    def no_counts(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/views":
            return httpx.Response(404, json={"errorCode": "URL_NOT_FOUND"})
        return route(request)

    c = connector(no_counts)
    assert run(c.discover()).contacts == 2
    assert any("at least 2 contacts" in w for w in c.warnings())


def test_the_dry_run_speaks_of_credits_archived_tickets_and_templates() -> None:
    said = connector().warnings()
    assert "aren't imported" in said[0]
    assert any("API credits" in w and "5,000" in w for w in said)
    assert any("Archived tickets aren't imported" in w for w in said)
    assert any("Desk.search.READ" in w for w in connector(include_archived="true").warnings())


def test_every_department_is_read() -> None:
    """C6: only the first 100 departments were read."""
    many = [{"id": f"d{i}", "name": f"Dept {i}"} for i in range(250)]

    def lots(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/departments":
            start = int(request.url.params["from"])
            limit = int(request.url.params["limit"])
            assert limit <= 200
            page = many[start : start + limit]
            return httpx.Response(200, json={"data": page}) if page else httpx.Response(204)
        return route(request)

    assert len(run(connector(lots).groups())) == 250


# Contacts


def test_contacts_and_paging_by_from(monkeypatch: pytest.MonkeyPatch) -> None:
    page = run(connector().page("contacts", None))
    priya, ravi = page.items
    assert [(c.external_id, c.email, c.name) for c in page.items] == [
        ("c1", "priya@example.org", "Priya Sharma"),
        ("c2", None, "Ravi"),
    ]
    assert (priya.phone, priya.organization, priya.other_emails) == (
        "+91 80 4000 1000", "Sharma Traders", ("priya.s@example.net",))  # fmt: skip
    assert (ravi.phone, ravi.organization) == ("+91 98450 00002", None)  # the mobile
    monkeypatch.setattr(zoho, "LIMIT", 2)
    seen: list[httpx.Request] = []
    full = run(connector(seen=seen).page("contacts", {"from": 4}))
    assert full.cursor == {"from": 6}
    asked = seen[-1].url.params
    assert (asked["from"], asked["limit"], asked["include"]) == ("4", "2", "accounts")


# Tickets


def test_status_people_fields_tags_and_dates() -> None:
    z1, z2 = tickets()
    assert (z1.status, z2.status) == ("on hold", "closed")
    assert [rules.status(t.status, zoho.STATUS_MAP) for t in (z1, z2)] == ["pending", "resolved"]
    assert rules.priority(z1.priority) == "high" and rules.channel(z2.channel) == "web"
    assert (z1.group_id, z2.group_id) == ("d1", None)
    assert z1.assignee_email == "neha@acme.com" and z1.requester.email == "priya@example.org"
    assert z1.requester.organization == "Sharma Traders"
    assert (z2.requester.external_id, z2.requester.email) == ("c9", None)
    assert z1.fields == {"cf_order": "1042", "category": "Delivery"}  # from the ticket's record
    assert z2.resolved_at == datetime(2025, 2, 1, 9, tzinfo=UTC)
    assert z1.updated_at == datetime(2025, 3, 9, 9, tzinfo=UTC)  # the list has no modifiedTime
    assert (z1.ticket_type, z1.sla_due_at) == ("Problem", datetime(2025, 3, 3, 10, tzinfo=UTC))
    assert z1.source_url == "https://desk.zoho.in/support/acme/ShowHomePage.do#Cases/dv/z1"


def test_tags_are_read_for_each_ticket_that_has_some() -> None:
    """B-48: tags were never read."""
    seen: list[httpx.Request] = []
    z1, z2 = run(connector(seen=seen).page("tickets", None)).items
    assert (list(z1.tags), list(z2.tags)) == (["VIP", "late delivery"], [])
    paths = {r.url.path for r in seen}
    assert "/api/v1/tickets/z1/tags" in paths
    assert "/api/v1/tickets/z2/tags" not in paths  # its tagCount is 0: no credits spent
    assert "/api/v1/tickets/z2/comments" not in paths  # commentCount 0


def test_threads_and_comments_become_messages_in_order() -> None:
    z1, z2 = tickets()
    assert [(m.kind, m.external_id, m.body) for m in z1.messages] == [
        ("customer", "thread-t1", "Where is my order 1042?"),
        ("note", "thread-t3", "Checked with the courier"),
        ("note", "comment-k1", "Call the courier"),
        ("agent", "thread-t2", "Sorry!\n\nIt ships today."),
        ("agent", "comment-k2", "Public update"),
    ]
    assert (z1.messages[0].author_name, z1.messages[0].author_email) == (
        "Priya Sharma", "priya@example.org")  # fmt: skip
    assert z2.messages == []  # Zoho answered 204: nothing there


def test_email_recipients_of_threads_are_kept() -> None:
    z1, _ = tickets()
    reply = next(m for m in z1.messages if m.external_id == "thread-t2")
    assert (list(reply.to), list(reply.cc), list(reply.bcc)) == (
        ["priya@example.org"], ["ops@sharma-traders.example", "priya@example.org"],
        ["audit@acme.com"])  # fmt: skip
    assert list(z1.cc) == ["ops@sharma-traders.example", "priya@example.org"]


def test_files_of_threads_and_comments_and_fetch_with_the_token_on_the_desk_host() -> None:
    seen: list[httpx.Request] = []
    c = connector(seen=seen)
    z1 = run(c.page("tickets", None)).items[0]
    agent = next(m for m in z1.messages if m.external_id == "thread-t2")
    href = "https://desk.zoho.in/api/v1/tickets/z1/threads/t2/attachments/a1/content"
    assert agent.attachments == [Attachment("label.pdf", href, 2048)]  # the http one is skipped
    comment = next(m for m in z1.messages if m.external_id == "comment-k2")  # C13
    assert comment.attachments == [
        Attachment(
            "proof.png",
            "https://desk.zoho.in/api/v1/tickets/z1/comments/k2/attachments/ca1/content",
            12734,
        )
    ]
    assert run(c.fetch(agent.attachments[0], 1000)) == b"%PDF-label"
    assert seen[-1].headers["Authorization"] == "Zoho-oauthtoken zat-1"
    assert run(c.fetch(agent.attachments[0], 3)) is None


def test_a_comment_with_only_a_file_is_kept() -> None:
    def file_only(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/tickets/z1/comments":
            body = fixture("comments_z1")
            body["data"][1]["content"] = ""
            return httpx.Response(200, json=body)
        return route(request)

    z1 = run(connector(file_only).page("tickets", None)).items[0]
    comment = next(m for m in z1.messages if m.external_id == "comment-k2")
    assert comment.body == "(attachment only)" and len(comment.attachments) == 1


def test_a_thread_gone_mid_read_keeps_its_summary_marked_as_such() -> None:
    def gone(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/tickets/z1/threads/t1":
            return httpx.Response(404, json={"errorCode": "URL_NOT_FOUND"})
        return route(request)

    c = connector(gone)
    z1 = run(c.page("tickets", None)).items[0]
    first = next(m for m in z1.messages if m.external_id == "thread-t1")
    assert first.body.startswith("Where is") and "summary" in first.body
    [noted] = c.failures.take()
    assert (noted.external_id, noted.category) == ("z1", "incomplete")


def test_a_cut_thread_is_read_whole_or_noted() -> None:
    whole = "<div>Where is my order 1042? " + "Please hurry. " * 50 + "</div>"

    def cut(full: httpx.Response) -> Any:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/tickets/z1/threads/t1":
                body = fixture("thread_t1")
                body.update(
                    isContentTruncated=True,
                    fullContentURL="https://desk.zoho.in/api/v1/tickets/z1/threads/t1/fullContent",
                )
                return httpx.Response(200, json=body)
            if request.url.path == "/api/v1/tickets/z1/threads/t1/fullContent":
                return full
            return route(request)

        return handler

    c = connector(cut(httpx.Response(200, json={"content": whole})))
    z1 = run(c.page("tickets", None)).items[0]
    assert z1.messages[0].body.endswith("Please hurry.") and c.failures.take() == []
    c = connector(cut(httpx.Response(404, json={"errorCode": "URL_NOT_FOUND"})))
    z1 = run(c.page("tickets", None)).items[0]
    assert z1.messages[0].body == "Where is my order 1042?"
    [noted] = c.failures.take()
    assert (noted.external_id, noted.category) == ("z1", "incomplete")


def test_a_message_without_a_date_takes_the_tickets_and_a_ticket_its_nearest() -> None:
    """C9: a missing date became the import time, silently."""

    def undated(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/tickets/z1/threads/t1":
            return httpx.Response(200, json={**fixture("thread_t1"), "createdTime": None})
        if request.url.path == "/api/v1/tickets/z2":
            return httpx.Response(200, json={**fixture("ticket_z2"), "createdTime": None})
        if request.url.path == "/api/v1/tickets":
            body = fixture("tickets")
            body["data"][1]["createdTime"] = None
            return httpx.Response(200, json=body)
        return route(request)

    losses = rules.Losses()
    c = connector(undated)
    with rules.collecting(losses):
        z1, z2 = run(c.page("tickets", None)).items
    first = next(m for m in z1.messages if m.external_id == "thread-t1")
    assert first.created_at == z1.created_at == datetime(2025, 3, 1, 10, tzinfo=UTC)
    assert z2.created_at == datetime(2025, 2, 1, 9, tzinfo=UTC)  # its modified time
    assert losses.counts == {"date_replaced": 2}
    assert c.failures.take() == []  # a stand-in date from the source is not a gap


def test_a_ticket_without_any_date_is_noted() -> None:
    def undated(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("/api/v1/tickets", "/api/v1/tickets/z2"):
            found = route(request).json()
            for row in found.get("data", [found]):
                if row["id"] == "z2":
                    row.update(createdTime=None, modifiedTime=None)
            return httpx.Response(200, json=found)
        return route(request)

    c = connector(undated)
    run(c.page("tickets", None))
    [noted] = c.failures.take()
    assert (noted.external_id, noted.category) == ("z2", "incomplete")
    assert "date" in noted.error


def test_a_very_long_history_is_capped_counted_and_noted(monkeypatch: pytest.MonkeyPatch) -> None:
    """C6: past 50 pages the rest of a ticket's history was dropped silently."""
    monkeypatch.setattr(zoho, "DETAIL_PAGES_MAX", 1)
    monkeypatch.setattr(zoho, "LIMIT", 3)  # threads_z1 is then a full page
    losses = rules.Losses()
    c = connector()
    with rules.collecting(losses):
        run(c.page("tickets", None))
    assert losses.counts.get("history_capped") == 1
    [noted] = c.failures.take()
    assert (noted.external_id, noted.category) == ("z1", "incomplete")


def test_archived_tickets_follow_the_live_ones_department_by_department() -> None:
    """C13: archived tickets were never read."""
    seen: list[httpx.Request] = []
    c = connector(seen=seen, include_archived="true")
    live = run(c.page("tickets", None))
    assert [t.external_id for t in live.items] == ["z1", "z2"]
    assert live.cursor == {"archived": "d1", "from": 0}
    old = run(c.page("tickets", live.cursor))
    assert [t.external_id for t in old.items] == ["z9"] and old.cursor == {
        "archived": "d2", "from": 0}  # fmt: skip
    assert old.items[0].created_at == datetime(2019, 5, 1, 8, tzinfo=UTC)  # from its list row
    [noted] = c.failures.take()  # the fake serves no record and no threads for it: kept, noted
    assert (noted.external_id, noted.category) == ("z9", "incomplete")
    asked = next(r for r in seen if r.url.path == "/api/v1/tickets/archivedTickets").url.params
    assert (asked["departmentId"], asked["from"], asked["limit"]) == ("d1", "0", "100")
    last = run(c.page("tickets", old.cursor))
    assert last.items == [] and last.cursor is None
    assert run(connector().page("tickets", None)).cursor is None  # not asked for: not read


def test_archived_paging_stops_at_zohos_5000_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zoho, "LIMIT", 1)

    def full(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/tickets/archivedTickets":
            return httpx.Response(200, json=fixture("archived_d1"))
        return route(request)

    c = connector(full, include_archived="true")
    losses = rules.Losses()
    with rules.collecting(losses):
        page = run(c.page("tickets", {"archived": "d1", "from": 4999}))
    assert page.cursor == {"archived": "d2", "from": 0}
    assert any("5,000 archived" in w for w in c.warnings())
    assert losses.counts == {"archive_capped": 1}  # in the run report too


# Articles


def test_articles_page_from_1_by_50() -> None:
    """B-46: `from=0&limit=100` is refused (422 "from exceeds the range >0")."""
    seen: list[httpx.Request] = []
    c = connector(seen=seen)
    page = run(c.page("articles", None))
    asked = next(r for r in seen if r.url.path == "/api/v1/articles").url.params
    assert (asked["from"], asked["limit"]) == ("1", "50")
    assert page.cursor is None

    def fifty(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/articles":
            return httpx.Response(200, json={"data": [{"id": "art1"}] * 50})
        return route(request)

    assert run(connector(fifty).page("articles", None)).cursor == {"from": 51}


def test_articles_with_their_category_and_replies_said_so() -> None:
    c = connector()
    page = run(c.page("articles", None))
    assert [(a.external_id, a.title, a.body, a.category.name, a.published) for a in page.items] == [
        ("art1", "Refunds", "Steps to get a refund", "Billing", True)]  # fmt: skip
    assert page.items[0].locale == "en"
    [gone] = c.failures.take()  # art2 is listed, then answers 404: deleted since
    assert (gone.kind, gone.external_id, gone.category) == ("article", "art2", "not_found")
    assert run(c.page("replies", None)).items == []
    assert "aren't imported" in c.warnings()[0]


def test_an_article_with_an_empty_body_is_kept() -> None:
    """C13: an article without text was dropped."""

    def empty(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/articles/art2":
            return httpx.Response(200, json=fixture("article_empty"))
        return route(request)

    page = run(connector(empty).page("articles", None))
    art2 = next(a for a in page.items if a.external_id == "art2")
    assert (art2.title, art2.body, art2.published) == ("Empty", "", False)


# Sync


def test_changes_read_each_tickets_modified_time_and_stop_at_an_older_one() -> None:
    """B-49: the list has no `modifiedTime`, so the created time was compared and the sync
    stopped at the first ticket created before `since`, however recently it changed."""
    seen: list[httpx.Request] = []
    since = datetime(2025, 3, 5, tzinfo=UTC)  # z1 was created before, changed after
    page = run(connector(seen=seen).changed_tickets(since, None))
    assert [t.external_id for t in page.items] == ["z1"] and page.cursor is None
    asked = next(r for r in seen if r.url.path == "/api/v1/tickets")
    assert asked.url.params["sortBy"] == "-modifiedTime"
    paths = [r.url.path for r in seen]
    assert "/api/v1/tickets/z2" in paths  # its record was read for its modified time
    assert "/api/v1/tickets/z2/threads" not in paths  # but nothing more


def test_a_full_page_of_changes_goes_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zoho, "LIMIT", 2)
    page = run(connector().changed_tickets(datetime(2025, 1, 1, tzinfo=UTC), None))
    assert [t.external_id for t in page.items] == ["z1", "z2"] and page.cursor == {"from": 2}


# Limits


def test_a_429_waits() -> None:
    def limited(request: httpx.Request) -> httpx.Response:
        if request.url.host == ACCOUNTS:
            return route(request)
        return httpx.Response(429, headers={"Retry-After": "9"})

    with pytest.raises(RateLimited) as waited:
        run(connector(limited).check())
    assert waited.value.retry_after == 9


def test_the_days_credits_used_up_waits_for_zoho() -> None:
    """Zoho answers 429 with Retry-After when the portal's daily credits are used up."""

    def spent(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/tickets":
            return httpx.Response(
                429,
                headers={"Retry-After": "34"},
                json={"errorCode": "THRESHOLD_EXCEEDED", "message": "API credits exceeded."},
            )
        return route(request)

    with pytest.raises(RateLimited) as waited:
        run(connector(spent).page("tickets", None))
    assert waited.value.retry_after == 34


def test_it_only_reads_and_only_the_two_zoho_hosts() -> None:
    seen: list[httpx.Request] = []
    c = connector(seen=seen, include_archived="true")
    run(c.check())
    run(c.discover())
    for kind in ("contacts", "tickets", "articles", "replies"):
        run(c.page(kind, None))
    run(c.changed_tickets(datetime(2025, 1, 1, tzinfo=UTC), None))
    posts = [r for r in seen if r.method != "GET"]
    assert {(r.method, r.url.host, r.url.path) for r in posts} == {
        ("POST", ACCOUNTS, "/oauth/v2/token")}  # fmt: skip
    assert {r.url.host for r in seen} == {DESK, ACCOUNTS}
    assert len(posts) == 1


@pytest.mark.parametrize(
    ("settings", "creds", "said"),
    [
        ({"region": "moon"}, {"client_id": "x", "client_secret": "y", "refresh_token": "z"},
         "data centre"),
        ({}, {"client_id": "x", "client_secret": "y", "refresh_token": "z"}, "region"),
        ({"region": "in"}, {"client_id": "x", "client_secret": "y"}, "refresh_token"),
    ],
)  # fmt: skip
def test_connecting_checks_region_and_credentials(
    settings: dict[str, str], creds: dict[str, str], said: str
) -> None:
    with pytest.raises(SourceError) as err:
        run(build_connector("zoho", settings, json.dumps(creds)))
    assert err.value.permanent and said in str(err.value)
