"""The HubSpot importer, driven by recorded-shape JSON and `httpx.MockTransport`; no network."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from cedesk.helpdesk.domain import imports as rules
from cedesk.helpdesk.domain.imports import Attachment, RateLimited, SourceError
from cedesk.helpdesk.service.imports import hubspot
from cedesk.helpdesk.service.imports.connectors import build_connector
from cedesk.helpdesk.service.imports.http import SourceHttp

FIXTURES = Path(__file__).parent.parent / "fixtures" / "imports" / "hubspot"
TOKEN = "pat-na1-sekret-token-123"
API = "api.hubapi.com"
CDN = "cdn.hubspot.example"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


async def allow(host: str) -> None:
    return None


def _by_ids(name: str, body: dict[str, Any]) -> httpx.Response:
    wanted = {i["id"] for i in body["inputs"]}
    return httpx.Response(200, json={"results": [
        r for r in fixture(name)["results"] if r["id"] in wanted]})  # fmt: skip


def route(request: httpx.Request) -> httpx.Response:
    path, query = request.url.path, request.url.params
    body = json.loads(request.content) if request.content else {}
    if request.url.host == CDN:
        return httpx.Response(200, content=b"%PDF-label")
    if path == "/account-info/v3/details":
        return httpx.Response(200, json=fixture("account"))
    if path == "/crm/v3/objects/contacts/search":
        return httpx.Response(200, json={"total": 3, "results": []})
    if path == "/crm/v3/objects/tickets/search":
        if "filterGroups" in body:
            return httpx.Response(200, json=fixture("changed"))
        return httpx.Response(200, json={"total": 3, "results": []})
    if path == "/crm/v3/pipelines/tickets":
        return httpx.Response(200, json=fixture("pipelines"))
    if path == "/crm/v3/objects/contacts":
        return httpx.Response(
            200, json=fixture("contacts_2" if query.get("after") else "contacts_1")
        )
    if path == "/crm/v3/objects/tickets":
        return httpx.Response(200, json=fixture("tickets_2" if query.get("after") else "tickets_1"))
    if path.startswith("/crm/v4/associations/tickets/") and path.endswith("/batch/read"):
        links = fixture("associations")[path.split("/")[5]]
        return httpx.Response(200, json={"results": [
            {"from": {"id": i["id"]}, "to": [{"toObjectId": t} for t in links.get(i["id"], [])]}
            for i in body["inputs"]]})  # fmt: skip
    if path == "/crm/v3/objects/contacts/batch/read":
        rows = fixture("contacts_1")["results"] + fixture("contacts_2")["results"]
        wanted = {i["id"] for i in body["inputs"]}
        return httpx.Response(200, json={"results": [r for r in rows if r["id"] in wanted]})
    if path == "/crm/v3/objects/emails/batch/read":
        return _by_ids("emails", body)
    if path == "/crm/v3/objects/notes/batch/read":
        return _by_ids("notes", body)
    if path == "/crm/v3/owners":
        return httpx.Response(200, json=fixture("owners"))
    if path == "/files/v3/files/9001":
        return httpx.Response(200, json=fixture("file_9001"))
    if path == "/files/v3/files/9001/signed-url":
        return httpx.Response(200, json=fixture("signed_9001"))
    return httpx.Response(404, json={})


def connector(handler: Any = route, seen: list[httpx.Request] | None = None) -> Any:
    def wrapped(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return handler(request)

    return hubspot.make({}, {"token": TOKEN}, transport=httpx.MockTransport(wrapped), guard=allow)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def tickets() -> dict[str, Any]:
    c = connector()
    first = run(c.page("tickets", None))
    assert first.cursor == {"after": "102"}
    second = run(c.page("tickets", first.cursor))
    assert second.cursor is None
    return {t.external_id: t for t in [*first.items, *second.items]}


def test_check_reads_the_account_with_the_token() -> None:
    seen: list[httpx.Request] = []
    assert run(connector(seen=seen).check()).name == "HubSpot account 1234567"
    assert seen[0].url.host == API and seen[0].headers["Authorization"] == f"Bearer {TOKEN}"


def test_discover_counts_by_search_totals_and_pipelines() -> None:
    seen: list[httpx.Request] = []
    counts = run(connector(seen=seen).discover())
    assert (counts.contacts, counts.tickets, counts.articles, counts.replies, counts.groups) == (
        3, 3, 0, 0, 2)  # fmt: skip
    searches = [json.loads(r.content) for r in seen if r.url.path.endswith("/search")]
    assert searches == [{"limit": 1}, {"limit": 1}]


def test_groups_are_the_pipelines() -> None:
    groups = run(connector().groups())
    assert [(g.external_id, g.name) for g in groups] == [
        ("0", "Support Pipeline"),
        ("77", "Billing"),
    ]


def test_contacts_page_with_after() -> None:
    c = connector()
    first = run(c.page("contacts", None))
    assert [(x.external_id, x.email, x.name) for x in first.items] == [
        ("501", "priya@example.org", "Priya Sharma"),
        ("502", "sam@example.org", "sam"),
    ]
    assert first.cursor == {"after": "502"}
    second = run(c.page("contacts", first.cursor))
    assert [(x.email, x.name) for x in second.items] == [(None, "No Email")]
    assert second.cursor is None


def test_status_comes_from_the_stage() -> None:
    by_id = tickets()
    words = {k: t.status for k, t in by_id.items()}
    assert words == {"101": "waiting", "102": "closed", "103": "open"}
    mapped = {k: rules.status(w, hubspot.STATUS_MAP) for k, w in words.items()}
    assert mapped == {"101": "pending", "102": "resolved", "103": "open"}
    assert hubspot.stage_word({"label": "Waiting on customer"}) == "waiting"
    assert hubspot.stage_word({"label": "Waiting on us"}) == "open"


def test_ticket_fields_people_and_dates() -> None:
    t101, t102, t103 = tickets().values()
    assert (t101.subject, t101.priority, t101.channel, t101.group_id) == (
        "Order 1042 not delivered", "HIGH", "EMAIL", "0")  # fmt: skip
    assert rules.priority(t103.priority) == "urgent" and rules.channel(t103.channel) == "web"
    assert t101.assignee_email == "neha@acme.com" and t102.assignee_email is None  # o9 unknown
    assert t101.fields == {"category": "PRODUCT_ISSUE"} and t102.fields == {}
    assert t101.requester.email == "priya@example.org"
    assert t103.requester.external_id == "502"  # the first contact on the ticket
    assert t102.requester.email is None and t102.requester.external_id == "ticket-102"
    assert t102.resolved_at == datetime(2025, 2, 3, 8, tzinfo=UTC)
    assert t103.subject == "Card charged twice" and t101.tags == ()


def test_messages_emails_notes_and_the_description() -> None:
    t101, t102, t103 = tickets().values()
    assert [(m.kind, m.external_id, m.body) for m in t101.messages] == [
        ("customer", "email-e1", "Hi, my order 1042 hasn't come."),
        ("note", "note-n1", "Courier says Friday"),
        ("agent", "email-e2", "Sorry!\n\nIt ships today."),
    ]  # by time, whatever order HubSpot listed them in
    assert (t101.messages[0].author_name, t101.messages[0].author_email) == (
        "Priya Sharma", "priya@example.org")  # fmt: skip
    # No emails: the ticket's description is the first customer message.
    assert [(m.kind, m.external_id, m.body) for m in t102.messages] == [
        ("customer", "ticket-102", "I want a refund for order 1043")]  # fmt: skip
    # An empty email and an empty note are left out, so the description is used.
    assert [(m.kind, m.body) for m in t103.messages] == [("customer", "Charged twice on 1 March")]


def test_attachments_and_fetch_through_a_signed_link() -> None:
    seen: list[httpx.Request] = []
    c = connector(seen=seen)
    agent = run(c.page("tickets", None)).items[0].messages[-1]
    assert agent.attachments == (
        [Attachment("label.pdf", "hubspot-file:9001", 2048, "application/pdf")]
    )  # 9002 is gone (404): left out
    assert run(c.fetch(agent.attachments[0], 1000)) == b"%PDF-label"
    cdn = [r for r in seen if r.url.host == CDN]
    assert len(cdn) == 1 and "Authorization" not in cdn[0].headers  # the token never goes there
    assert run(c.fetch(agent.attachments[0], 3)) is None  # bigger than the limit
    assert run(c.fetch(Attachment("x", "hubspot-file:9002"), 1000)) is None
    assert run(c.fetch(Attachment("x", "https://elsewhere.example/x"), 1000)) is None


def test_articles_and_replies_are_empty_and_said_so() -> None:
    c = connector()
    assert run(c.page("articles", None)).items == [] and run(c.page("replies", None)).items == []
    assert "aren't imported" in c.warnings()[0]


def test_changes_are_searched_from_a_time_oldest_first() -> None:
    seen: list[httpx.Request] = []
    since = datetime(2025, 3, 8, tzinfo=UTC)
    page = run(connector(seen=seen).changed_tickets(since, None))
    assert [(t.external_id, t.status) for t in page.items] == [("101", "closed")]
    assert page.cursor is None
    body = json.loads(next(r for r in seen if r.url.path.endswith("tickets/search")).content)
    assert body["filterGroups"][0]["filters"] == [{
        "propertyName": "hs_lastmodifieddate", "operator": "GTE",
        "value": str(int(since.timestamp() * 1000))}]  # fmt: skip
    assert body["sorts"] == [{"propertyName": "hs_lastmodifieddate", "direction": "ASCENDING"}]


def test_a_long_look_starts_a_new_search_before_hubspots_limit() -> None:
    def deep(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crm/v3/objects/tickets/search":
            body = json.loads(request.content)
            after = body.get("after", "0")
            found = fixture("changed") | {"paging": {"next": {"after": str(int(after) + 100)}}}
            return httpx.Response(200, json=found)
        return route(request)

    c = connector(deep)
    since = datetime(2025, 3, 1, tzinfo=UTC)
    page = run(c.changed_tickets(since, None))
    assert page.cursor == {"from": int(since.timestamp() * 1000), "after": "100"}
    near_end = run(c.changed_tickets(since, {"from": 1, "after": "9900"}))
    assert near_end.cursor == {"from": int(datetime(2025, 3, 9, 9, tzinfo=UTC).timestamp() * 1000)}


def test_a_429_waits_and_a_401_is_permanent_without_the_token() -> None:
    limited = connector(lambda r: httpx.Response(429, headers={"Retry-After": "7"}))
    with pytest.raises(RateLimited) as waited:
        run(limited.check())
    assert waited.value.retry_after == 7
    refused = connector(lambda r: httpx.Response(401, json={"message": TOKEN}))
    with pytest.raises(SourceError) as err:
        run(refused.check())
    assert err.value.permanent and TOKEN not in str(err.value)


def test_it_only_reads() -> None:
    seen: list[httpx.Request] = []
    c = connector(seen=seen)
    run(c.check())
    run(c.discover())
    for kind in ("contacts", "tickets", "replies", "articles"):
        run(c.page(kind, None))
    run(c.changed_tickets(datetime(2025, 1, 1, tzinfo=UTC), None))
    posts = {r.url.path for r in seen if r.method == "POST"}
    assert {r.method for r in seen} == {"GET", "POST"}
    assert all(p.endswith(("/search", "/batch/read")) for p in posts)
    assert {r.url.host for r in seen} == {API}
    http = SourceHttp(API, headers={}, guard=allow, transport=httpx.MockTransport(route))
    with pytest.raises(ValueError):
        run(http.read("/crm/v3/objects/tickets", {"properties": {"subject": "x"}}))


def test_connecting_needs_a_token() -> None:
    with pytest.raises(SourceError) as err:
        run(build_connector("hubspot", {}, json.dumps({"token": " "})))
    assert "private app" in str(err.value) and err.value.permanent
    assert isinstance(run(build_connector("hubspot", {}, json.dumps({"token": TOKEN}))),
                      hubspot.HubSpotConnector)  # fmt: skip
