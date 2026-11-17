"""The HubSpot importer, driven by recorded-shape JSON and `httpx.MockTransport`; no network."""

import asyncio
import base64
import json
from collections.abc import Callable
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
ASSOCIATIONS_PER_CALL = 1  # HubSpot pages each input's links; one a call makes every ticket page
STAMP = {"startedAt": "2025-03-10T10:00:00.100Z", "completedAt": "2025-03-10T10:00:00.140Z"}
THREADS = "/conversations/v3/conversations/threads"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


async def allow(host: str) -> None:
    return None


def _by_ids(name: str, body: dict[str, Any]) -> httpx.Response:
    wanted = {i["id"] for i in body["inputs"]}
    return httpx.Response(200, json={"status": "COMPLETE", "results": [
        r for r in fixture(name)["results"] if r["id"] in wanted], **STAMP})  # fmt: skip


def _cursor(n: int) -> str:
    return base64.b64encode(str(n).encode()).decode()


def associations(to: str, body: dict[str, Any]) -> httpx.Response:
    """HubSpot's v4 batch read: each input's links, paged on their own (`after` per input)."""
    links = fixture("associations").get(to, {})
    results = []
    for given in body["inputs"]:
        start = int(base64.b64decode(given["after"])) if given.get("after") else 0
        found = links.get(given["id"], [])
        if not found:
            continue
        end = start + ASSOCIATIONS_PER_CALL
        row: dict[str, Any] = {
            "from": {"id": given["id"]},
            "to": [
                {"toObjectId": t, "associationTypes": [
                    {"category": "HUBSPOT_DEFINED", "typeId": 224, "label": None}]}
                for t in found[start:end]
            ],
        }  # fmt: skip
        if end < len(found):
            row["paging"] = {"next": {"after": _cursor(end), "link": None}}
        results.append(row)
    return httpx.Response(200, json={"status": "COMPLETE", "results": results, **STAMP})


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
    if path == "/crm/v3/properties/tickets":
        return httpx.Response(200, json=fixture("properties_tickets"))
    if path == "/crm/v3/objects/contacts":
        return httpx.Response(
            200, json=fixture("contacts_2" if query.get("after") else "contacts_1")
        )
    if path == "/crm/v3/objects/tickets":
        return httpx.Response(200, json=fixture("tickets_2" if query.get("after") else "tickets_1"))
    if path in ("/crm/v3/objects/calls", "/crm/v3/objects/meetings"):
        return httpx.Response(200, json={"results": [], "paging": None})
    if path == "/crm/v3/objects/communications":
        return httpx.Response(200, json={"results": []})
    if path.startswith("/crm/v4/associations/tickets/") and path.endswith("/batch/read"):
        return associations(path.split("/")[5], body)
    if path == "/crm/v3/objects/contacts/batch/read":
        rows = fixture("contacts_1")["results"] + fixture("contacts_2")["results"]
        wanted = {i["id"] for i in body["inputs"]}
        return httpx.Response(200, json={"results": [r for r in rows if r["id"] in wanted]})
    for obj in ("emails", "notes", "calls", "meetings", "communications"):
        if path == f"/crm/v3/objects/{obj}/batch/read":
            return _by_ids(obj, body)
    if path == "/crm/v3/objects/tickets/batch/read":
        return _by_ids("tickets_custom", body)
    if path == "/crm/v3/owners":
        return httpx.Response(
            200, json=fixture("owners_archived" if query.get("archived") == "true" else "owners")
        )
    if path == THREADS:
        if query.get("associatedContactId") == "502" and query.get("threadStatus") == "CLOSED":
            return httpx.Response(200, json=fixture("threads_502"))
        return httpx.Response(200, json={"results": []})
    if path == f"{THREADS}/7001/messages":
        return httpx.Response(200, json=fixture("thread_7001_messages"))
    if path in ("/files/v3/files/9001", "/files/v3/files/9003"):
        return httpx.Response(200, json=fixture(f"file_{path.rsplit('/', 1)[1]}"))
    if path == "/files/v3/files/9001/signed-url":
        return httpx.Response(200, json=fixture("signed_9001"))
    return httpx.Response(404, json={"status": "error", "message": "Not found", "category": ""})


def connector(
    handler: Callable[[httpx.Request], httpx.Response] = route,
    seen: list[httpx.Request] | None = None,
) -> Any:
    def wrapped(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return handler(request)

    return hubspot.make({}, {"token": TOKEN}, transport=httpx.MockTransport(wrapped), guard=allow)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def tickets(c: Any = None) -> dict[str, Any]:
    c = c or connector()
    first = run(c.page("tickets", None))
    assert first.cursor == {"after": "102"}
    second = run(c.page("tickets", first.cursor))
    assert second.cursor is None
    return {t.external_id: t for t in [*first.items, *second.items]}


def but(change: Callable[[httpx.Request], httpx.Response | None]) -> Any:
    """`route`, except where `change` answers."""

    def handler(request: httpx.Request) -> httpx.Response:
        return change(request) or route(request)

    return handler


def test_check_reads_the_account_with_the_token() -> None:
    seen: list[httpx.Request] = []
    assert run(connector(seen=seen).check()).name == "HubSpot account 1234567"
    assert seen[0].url.host == API and seen[0].headers["Authorization"] == f"Bearer {TOKEN}"


def test_discover_counts_by_search_totals_and_pipelines() -> None:
    seen: list[httpx.Request] = []
    c = connector(seen=seen)
    counts = run(c.discover())
    assert (counts.contacts, counts.tickets, counts.articles, counts.replies, counts.groups) == (
        3, 3, 0, 0, 2)  # fmt: skip
    searches = [json.loads(r.content) for r in seen if r.url.path.endswith("/search")]
    assert searches == [{"limit": 1}, {"limit": 1}]
    assert len(c.warnings()) == 1  # every optional read works: only the knowledge base note


def test_groups_are_the_pipelines() -> None:
    groups = run(connector().groups())
    assert [(g.external_id, g.name) for g in groups] == [
        ("0", "Support Pipeline"),
        ("77", "Billing"),
    ]


def test_contacts_page_with_after_phone_and_company() -> None:
    c = connector()
    first = run(c.page("contacts", None))
    assert [(x.external_id, x.email, x.name) for x in first.items] == [
        ("501", "priya@example.org", "Priya Sharma"),
        ("502", "sam@example.org", "sam"),
    ]
    assert [(x.phone, x.organization) for x in first.items] == [
        ("+91 98200 00000", "Sharma Traders"),
        ("+1 415 555 0100", None),  # mobile when there is no phone
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
    assert t101.assignee_email == "neha@acme.com"
    assert t101.requester.email == "priya@example.org"
    assert t103.requester.external_id == "502"  # the first contact on the ticket
    assert t102.requester.email is None and t102.requester.external_id == "ticket-102"
    assert t102.resolved_at == datetime(2025, 2, 3, 8, tzinfo=UTC)
    assert t103.subject == "Card charged twice" and t101.tags == ()
    assert t101.ticket_type == "Product issue" and t102.ticket_type is None
    assert t101.source_url == "https://app.hubspot.com/contacts/1234567/ticket/101"


def test_an_archived_owner_still_resolves_the_assignee() -> None:
    """C6: a former agent (deactivated user) is only listed with `archived=true`."""
    seen: list[httpx.Request] = []
    by_id = tickets(connector(seen=seen))
    assert by_id["102"].assignee_email == "arjun@acme.com"
    owners = [r.url.params.get("archived") for r in seen if r.url.path == "/crm/v3/owners"]
    assert owners == [None, "true"]  # once each, cached for the next page


def test_owners_are_paged_to_the_end() -> None:
    """C6: no cap of 50 pages; every page is read until HubSpot gives no `after`."""
    pages = 75

    def many(request: httpx.Request) -> httpx.Response | None:
        if request.url.path != "/crm/v3/owners":
            return None
        if request.url.params.get("archived") == "true":
            return httpx.Response(200, json={"results": []})
        n = int(request.url.params.get("after") or 0)
        found: dict[str, Any] = {"results": [
            {"id": f"o{n}", "email": f"agent{n}@acme.com", "archived": False}]}  # fmt: skip
        if n + 1 < pages:
            found["paging"] = {"next": {"after": str(n + 1)}}
        return httpx.Response(200, json=found)

    owners = run(connector(but(many))._owner_map())
    assert len(owners) == pages and owners["o74"].email == "agent74@acme.com"


def test_custom_properties_are_kept_with_their_labels() -> None:
    """C7: the account's own properties (not HubSpot's, not archived), an option as its label."""
    seen: list[httpx.Request] = []
    t101, t102, t103 = tickets(connector(seen=seen)).values()
    assert t101.fields == {
        "category": "PRODUCT_ISSUE",
        "Order number": "A-1042",
        "Order channel": "Web shop, In store",
        "hs_time_to_close_sla_due_date": "2025-03-03T10:00:00Z",
    }
    assert t102.fields == {} and t103.fields == {"Order number": "0"}
    asked = [json.loads(r.content) for r in seen if r.url.path.endswith("tickets/batch/read")]
    assert asked and set(asked[0]["properties"]) == {
        "order_number", "order_channel", "hs_time_to_close_sla_due_date"}  # fmt: skip
    listed = [r for r in seen if r.url.path == "/crm/v3/properties/tickets"]
    assert len(listed) == 1  # read once, for both pages


def test_every_association_page_is_read() -> None:
    """C7: HubSpot pages each ticket's links (`paging.next.after` per input); all are read."""
    seen: list[httpx.Request] = []
    t101 = tickets(connector(seen=seen))["101"]
    assert [m.external_id for m in t101.messages if m.kind != "note"] == ["email-e1", "email-e2"]
    again = [
        json.loads(r.content)["inputs"]
        for r in seen
        if r.url.path == "/crm/v4/associations/tickets/emails/batch/read"
    ]
    assert again[1] == [{"id": "101", "after": _cursor(1)}]  # only the input with more


def test_messages_emails_notes_and_the_description() -> None:
    t101, t102, _ = tickets().values()
    assert [(m.kind, m.external_id, m.body) for m in t101.messages] == [
        ("customer", "email-e1", "Hi, my order 1042 hasn't come."),
        ("note", "note-n1", "Courier says Friday"),
        ("agent", "email-e2", "Sorry!\n\nIt ships today."),
    ]  # by time; the description repeats the first email, so it isn't added again
    first, note, agent = t101.messages
    assert (first.author_name, first.author_email) == ("Priya Sharma", "priya@example.org")
    assert (first.to, first.cc) == (
        ["support@acme.com"],
        ["ravi@example.org", "priya.work@example.org"],
    )
    assert (agent.to, agent.bcc) == (["priya@example.org"], ["audit@acme.com"])
    assert (note.author_name, note.author_email) == ("Neha Rao", "neha@acme.com")  # its owner
    assert [(m.kind, m.external_id) for m in t102.messages] == [
        ("customer", "ticket-102"), ("note", "call-31001")]  # fmt: skip


def test_the_description_is_kept_when_the_ticket_has_emails() -> None:
    """C7: the old code dropped `content` whenever the ticket had an email."""

    def other(request: httpx.Request) -> httpx.Response | None:
        if request.url.path == "/crm/v3/objects/tickets" and not request.url.params.get("after"):
            found = fixture("tickets_1")
            found["results"][0]["properties"]["content"] = "Where is my order?"
            return httpx.Response(200, json=found)
        return None

    t101 = run(connector(but(other)).page("tickets", None)).items[0]
    assert [(m.kind, m.external_id, m.body) for m in t101.messages[:2]] == [
        ("customer", "ticket-101", "Where is my order?"),
        ("customer", "email-e1", "Hi, my order 1042 hasn't come."),
    ]


def test_calls_meetings_and_logged_messages_are_notes_by_their_owner() -> None:
    t102, t103 = tickets()["102"], tickets()["103"]
    call = t102.messages[1]
    assert call.body == (
        "Call: Refund call (incoming, completed, 3:05)\n\n"
        "Customer wants the refund to the card.\n\n"
        "Recording: https://api.twilio.com/2010-04-01/Accounts/AC0/Recordings/RE0.mp3"
    )
    assert call.created_at == datetime(2025, 2, 2, 9, 30, tzinfo=UTC)
    assert call.author_email == "arjun@acme.com"  # an archived owner
    by_id = {m.external_id: m for m in t103.messages}
    meeting = by_id["meeting-32001"]
    assert meeting.kind == "note" and meeting.body == (
        "Meeting: Billing review (2025-03-06 09:00 UTC to 2025-03-06 09:30 UTC, completed)\n\n"
        "Walk through both charges.\n\nInternal notes:\nRefund one charge."
    )
    assert [a.name for a in meeting.attachments] == ["invoice.pdf"]
    whatsapp = by_id["communication-33001"]
    assert whatsapp.body == "WhatsApp logged in HubSpot\n\nSent the refund reference on WhatsApp."
    assert whatsapp.created_at == datetime(2025, 3, 7, 11, tzinfo=UTC)  # no hs_timestamp


def test_inbox_threads_of_a_chat_ticket_are_its_conversation() -> None:
    seen: list[httpx.Request] = []
    t103 = tickets(connector(seen=seen))["103"]
    assert [(m.kind, m.external_id, m.body) for m in t103.messages] == [
        ("customer", "ticket-103", "Charged twice on 1 March"),
        ("agent", "thread-7001-m2", "Sorry about that, checking now."),
        ("note", "thread-7001-m3", "Looks like a gateway retry."),
        ("note", "meeting-32001", t103.messages[3].body),
        ("note", "communication-33001", t103.messages[4].body),
    ]  # the welcome line is HubSpot's; the first chat line repeats the description
    agent = t103.messages[1]
    assert agent.author_name == "Neha Rao" and [a.name for a in agent.attachments] == [
        "invoice.pdf"]  # fmt: skip
    looked = [dict(r.url.params) for r in seen if r.url.path == THREADS]
    assert {(q["associatedContactId"], q["threadStatus"], q["association"]) for q in looked} == {
        ("502", "OPEN", "TICKET"), ("502", "CLOSED", "TICKET")}  # fmt: skip
    asked = [r.url.path for r in seen if r.url.path.startswith(f"{THREADS}/")]
    assert asked == [f"{THREADS}/7001/messages"]  # 7002 belongs to another ticket
    # An email ticket's inbox messages are its emails: no thread lookup for it.
    assert all(q["associatedContactId"] != "501" for q in looked)


def test_scopes_the_key_lacks_leave_their_part_out_and_say_so() -> None:
    def refused(request: httpx.Request) -> httpx.Response | None:
        path = request.url.path
        if (
            any(o in path for o in ("calls", "meetings", "communications"))
            or path.startswith(THREADS)
            or path == "/crm/v3/properties/tickets"
        ):
            return httpx.Response(403, json={"status": "error", "category": "MISSING_SCOPES"})
        return None

    c = connector(but(refused))
    run(c.discover())
    by_id = tickets(c)
    assert [m.external_id for m in by_id["102"].messages] == ["ticket-102"]
    assert [m.external_id for m in by_id["103"].messages] == ["ticket-103"]
    assert by_id["101"].fields == {"category": "PRODUCT_ISSUE"}
    said = " ".join(c.warnings())
    for part in ("calls", "meetings", "WhatsApp", "inbox threads", "ticket properties"):
        assert part in said
    assert c.failures.take() == []


def test_a_thread_gone_mid_import_keeps_the_ticket_and_notes_it() -> None:
    def gone(request: httpx.Request) -> httpx.Response | None:
        if request.url.path == f"{THREADS}/7001/messages":
            return httpx.Response(404, json={"status": "error", "message": "Thread not found"})
        return None

    c = connector(but(gone))
    t103 = tickets(c)["103"]
    assert not any((m.external_id or "").startswith("thread-") for m in t103.messages)
    noted = c.failures.take()
    assert [(f.kind, f.external_id, f.category) for f in noted] == [("ticket", "103", "incomplete")]


def test_a_ticket_that_keeps_failing_is_set_aside_when_the_run_isolates() -> None:
    def broken(request: httpx.Request) -> httpx.Response | None:
        if request.url.path == f"{THREADS}/7001/messages":
            return httpx.Response(503)
        return None

    c = connector(but(broken))
    with pytest.raises(SourceError) as err:
        run(c.page("tickets", {"after": "102"}))
    assert not err.value.permanent  # retried with backoff first
    c.failures.isolate = True
    page = run(c.page("tickets", {"after": "102"}))
    assert page.items == []
    assert [(f.external_id, f.category) for f in c.failures.take()] == [("103", "temporary")]


def test_a_missing_date_is_never_silently_now() -> None:
    """C9: the nearest source date is used; with none, the ticket is noted incomplete."""

    def undated(request: httpx.Request) -> httpx.Response | None:
        if request.url.path == "/crm/v3/objects/tickets" and request.url.params.get("after"):
            found = fixture("tickets_2")
            found["results"][0]["properties"]["createdate"] = None
            return httpx.Response(200, json=found)
        if request.url.path == "/crm/v3/objects/communications/batch/read":
            found = fixture("communications")
            found["results"][0]["properties"]["hs_createdate"] = None
            return httpx.Response(200, json=found)
        return None

    c = connector(but(undated))
    t103 = run(c.page("tickets", {"after": "102"})).items[0]
    # No createdate: the earliest message stands in, not the import time. The logged
    # WhatsApp line has no date of its own either: it takes the ticket's update time.
    whatsapp = next(m for m in t103.messages if m.external_id == "communication-33001")
    assert whatsapp.created_at == datetime(2025, 3, 5, 12, tzinfo=UTC)
    assert t103.created_at == whatsapp.created_at
    assert t103.messages[0].external_id == "ticket-103"  # the description stays first
    assert c.failures.take() == []

    def nothing(request: httpx.Request) -> httpx.Response | None:
        if request.url.path == "/crm/v3/objects/tickets" and request.url.params.get("after"):
            found = fixture("tickets_2")
            found["results"][0]["properties"].update(
                {"createdate": None, "hs_lastmodifieddate": None}
            )
            return httpx.Response(200, json=found)
        if request.url.path in (f"{THREADS}/7001/messages",):
            return httpx.Response(200, json={"results": []})
        if request.url.path.endswith(("meetings/batch/read", "communications/batch/read")):
            return httpx.Response(200, json={"results": []})
        return None

    c = connector(but(nothing))
    run(c.page("tickets", {"after": "102"}))
    noted = c.failures.take()
    assert [(f.external_id, f.category) for f in noted] == [("103", "incomplete")]
    assert "creation date" in noted[0].error


def test_attachments_and_fetch_through_a_signed_link() -> None:
    seen: list[httpx.Request] = []
    c = connector(seen=seen)
    agent = run(c.page("tickets", None)).items[0].messages[-1]
    assert agent.attachments == [
        Attachment("label.pdf", "label.pdf in HubSpot (file id 9001)", 2048, "application/pdf"),
        Attachment("attachment-9002", "attachment-9002 in HubSpot (file id 9002)"),
    ]  # 9001 is private: named, not linked; 9002 is gone (404) and is kept as a link line
    assert run(c.fetch(agent.attachments[0], 1000)) == b"%PDF-label"
    cdn = [r for r in seen if r.url.host == CDN]
    assert len(cdn) == 1 and "Authorization" not in cdn[0].headers  # the token never goes there
    assert run(c.fetch(agent.attachments[0], 3)) is None  # bigger than the limit
    assert run(c.fetch(agent.attachments[1], 1000)) is None  # gone: None, never an error
    assert run(c.fetch(Attachment("x", "hubspot-file:9001"), 1000)) == b"%PDF-label"  # old form
    assert run(c.fetch(Attachment("x", "https://elsewhere.example/x"), 1000)) is None


def test_a_public_file_is_referenced_by_its_own_address() -> None:
    """B-34: a file kept as a link shows something a person can open."""
    t103 = tickets()["103"]
    meeting = next(m for m in t103.messages if m.external_id == "meeting-32001")
    assert meeting.attachments[0].url == (
        "https://1234567.fs1.hubspotusercontent-na1.net/hubfs/1234567/invoice.pdf "
        "(HubSpot file id 9003)"
    )
    assert hubspot.file_id_of(meeting.attachments[0].url) == "9003"
    line = rules.link_line("invoice.pdf", 4096, meeting.attachments[0].url, "gone")
    assert "hubspot-file:" not in line and "invoice.pdf (HubSpot file id 9003)" in line


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


def test_connecting_needs_a_token_or_service_key() -> None:
    with pytest.raises(SourceError) as err:
        run(build_connector("hubspot", {}, json.dumps({"token": " "})))
    said = str(err.value)
    assert "private app" in said and "service key" in said and err.value.permanent
    assert "crm.objects.tickets.read" in said and "files.read" in said
    assert isinstance(run(build_connector("hubspot", {}, json.dumps({"token": TOKEN}))),
                      hubspot.HubSpotConnector)  # fmt: skip


def test_a_contact_gone_from_the_inbox_is_not_a_missing_scope() -> None:
    def gone(request: httpx.Request) -> httpx.Response | None:
        if request.url.path == THREADS and request.url.params.get("threadStatus") == "OPEN":
            return httpx.Response(404, json={"status": "error", "message": "Contact not found"})
        return None

    c = connector(but(gone))
    t103 = tickets(c)["103"]
    assert not any((m.external_id or "").startswith("thread-") for m in t103.messages)
    assert len(c.warnings()) == 1 and c.failures.take() == []
