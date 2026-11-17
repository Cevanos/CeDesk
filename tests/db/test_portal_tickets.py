"""The customer's tickets in the portal (md P2-P4) with RLS on: a customer sees and
touches only their own tickets (another's is 404), sees only customer, agent and AI
messages, opens tickets that run rules and routing like any other, and a reply on a
resolved ticket reopens it."""

import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.repo import outbox
from cedesk.helpdesk.service.delivery import Event
from cedesk.helpdesk.service.portal_notice import PortalNoticeHandler
from cedesk.main import create_app
from cedesk.settings import Settings
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

P = "/api/v1/portal"
T = "/api/v1/tickets"
REFUND = [
    {"key": "order_id", "label": "Order ID", "kind": "text", "required": True},
    {"key": "reason", "label": "Reason", "kind": "select", "options": ["Damaged", "Late"]},
]


@pytest.fixture
async def client(app_sessionmaker: Sm, tmp_path: Path) -> AsyncIterator[AsyncClient]:
    settings = Settings(sign_in_limit=100_000, storage_local_path=str(tmp_path / "files"))
    app = create_app(settings)
    app.state.sessionmaker = app_sessionmaker
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    slug = await new_workspace(sm)
    admin = await sign_in(client, slug, "admin@example.com")
    body = {"address": f"support@{slug}.test", "name": "Acme Support"}
    ok(await client.post("/api/v1/email-addresses", headers=bearer(admin), json=body), 201)
    neha_id = await add(client, admin, "neha@example.com", "agent", "Neha")
    billing = {"name": "Billing", "member_ids": [neha_id]}
    team = ok(await client.post("/api/v1/teams", headers=bearer(admin), json=billing), 201)
    refund = ok(await client.post("/api/v1/ticket-types", headers=bearer(admin),
                                  json={"name": "Refund", "fields": REFUND}), 201)  # fmt: skip
    return {
        "slug": slug, "admin": bearer(admin), "ws": UUID(admin.split(".")[0]),
        "neha": bearer(await sign_in(client, slug, "neha@example.com")),
        "team": team["id"], "refund": refund["id"],
    }  # fmt: skip


async def customer(client: AsyncClient, sm: Sm, w: dict[str, Any], email: str) -> dict[str, str]:
    r = await client.post(f"{P}/{w['slug']}/code", json={"email": email})
    assert r.status_code == 202, r.text
    async with workspace_session(sm, w["ws"]) as s:
        body = (await s.execute(text(
            "SELECT body_text FROM hd_email_out WHERE to_addresses = ARRAY[:to] "
            "ORDER BY created_at DESC LIMIT 1"), {"to": email})).scalar_one()  # fmt: skip
    code = re.search(r"\b(\d{6})\b", body)
    assert code
    r = await client.post(f"{P}/{w['slug']}/session", json={"email": email, "code": code[1]})
    return bearer(ok(r)["token"])


async def test_a_customer_opens_a_ticket_like_any_other_channel(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    rule = {
        "name": "Refunds to Billing", "event": "created",
        "conditions": [{"kind": "text_contains_any", "words": ["refund"]}],
        "actions": [{"kind": "set_team", "team_id": w["team"]}],
    }  # fmt: skip
    ok(await client.post("/api/v1/rules", headers=w["admin"], json=rule), 201)
    priya = await customer(client, app_sessionmaker, w, "priya@example.org")

    types = ok(await client.get(f"{P}/ticket-types", headers=priya))
    assert [t["name"] for t in types] == ["Refund"]
    assert types[0]["fields"][0]["required"] is True

    message = "I want a refund for my order, the parcel arrived completely crushed and wet"
    new = {"message": message, "type_id": w["refund"], "fields": {"order_id": "A-77"}}
    t = ok(await client.post(f"{P}/tickets", headers=priya, json=new), 201)
    assert t["subject"] == "I want a refund for my order, the parcel arrived…"
    assert len(t["subject"]) <= 60
    assert t["status_label"] == "Open" and t["type_name"] == "Refund"
    assert t["fields"] == {"order_id": "A-77"}
    assert [(m["author"], m["body"]) for m in t["messages"]] == [("You", message)]

    # The agents see it as a portal ticket, routed by the rule to Neha.
    seen = ok(await client.get(f"{T}/{t['id']}", headers=w["admin"]))
    assert seen["channel"] == "portal" and seen["contact"]["email"] == "priya@example.org"
    assert seen["team_name"] == "Billing" and seen["assignee_name"] == "Neha"

    bad = {"message": "Refund please", "type_id": w["refund"], "fields": {}}
    assert (await client.post(f"{P}/tickets", headers=priya, json=bad)).status_code == 422
    bad = {"message": "Hi", "type_id": str(UUID(int=1))}
    assert (await client.post(f"{P}/tickets", headers=priya, json=bad)).status_code == 422
    assert (await client.post(f"{P}/tickets", headers=priya,
                              json={"message": "   "})).status_code == 422  # fmt: skip


async def test_customers_only_ever_see_their_own_tickets(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    priya = await customer(client, app_sessionmaker, w, "priya@example.org")
    ravi = await customer(client, app_sessionmaker, w, "ravi@example.org")
    mine = ok(await client.post(f"{P}/tickets", headers=priya, json={"message": "Mine"}), 201)
    # An agent-made ticket for Priya (another channel) is hers too.
    body = {"subject": "By phone", "contact": {"email": "priya@example.org", "name": "Priya"},
            "channel": "phone"}  # fmt: skip
    phone = ok(await client.post(T, headers=w["admin"], json=body), 201)

    listed = ok(await client.get(f"{P}/tickets", headers=priya))["tickets"]
    assert [x["id"] for x in listed] == [phone["id"], mine["id"]]  # newest first
    assert listed[1]["label"].startswith("T-")
    assert ok(await client.get(f"{P}/tickets", headers=ravi))["tickets"] == []

    for path in (f"{P}/tickets/{mine['id']}", f"{P}/tickets/{UUID(int=5)}"):
        r = await client.get(path, headers=ravi)
        assert r.status_code == 404 and r.json()["error"]["message"] == "Ticket not found."
    r = await client.post(f"{P}/tickets/{mine['id']}/messages", headers=ravi, json={"body": "x"})
    assert r.status_code == 404

    page = ok(await client.get(f"{P}/tickets", headers=priya, params={"limit": 1}))
    assert len(page["tickets"]) == 1 and page["next_cursor"]
    rest = ok(await client.get(f"{P}/tickets", headers=priya,
                               params={"limit": 1, "cursor": page["next_cursor"]}))  # fmt: skip
    assert [x["id"] for x in rest["tickets"]] == [mine["id"]]

    # A customer of another workspace finds nothing either.
    other = await desk(client, app_sessionmaker)
    stranger = await customer(client, app_sessionmaker, other, "priya@example.org")
    assert (await client.get(f"{P}/tickets/{mine['id']}", headers=stranger)).status_code == 404


async def test_the_customer_sees_replies_but_never_notes_or_system_lines(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    priya = await customer(client, app_sessionmaker, w, "priya@example.org")
    t = ok(await client.post(f"{P}/tickets", headers=priya, json={"message": "Where is it?"}), 201)
    url = f"{T}/{t['id']}/messages"
    ok(await client.post(url, headers=w["neha"], json={"kind": "note", "body": "Secret"}), 201)
    ok(await client.post(url, headers=w["neha"], json={"kind": "reply", "body": "On its way"}),
       201)  # fmt: skip

    seen = ok(await client.get(f"{P}/tickets/{t['id']}", headers=priya))
    assert [(m["author"], m["body"], m["from_customer"]) for m in seen["messages"]] == [
        ("You", "Where is it?", True),
        ("Neha · Test Support", "On its way", False),
    ]
    assert "Secret" not in str(seen) and "Ticket created" not in str(seen)


async def test_a_reply_on_a_resolved_ticket_reopens_it_but_thanks_does_not(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    priya = await customer(client, app_sessionmaker, w, "priya@example.org")
    t = ok(await client.post(f"{P}/tickets", headers=priya, json={"message": "Broken"}), 201)
    ok(await client.patch(f"{T}/{t['id']}", headers=w["admin"], json={"status": "pending"}))
    assert ok(await client.get(f"{P}/tickets/{t['id']}", headers=priya))["status_label"] == (
        "Waiting on you"
    )
    ok(await client.post(f"{T}/{t['id']}/resolve", headers=w["admin"]))

    thanks = ok(await client.post(f"{P}/tickets/{t['id']}/messages", headers=priya,
                                  json={"body": "Thank you so much!"}), 201)  # fmt: skip
    assert thanks["status"] == "resolved"
    again = ok(await client.post(f"{P}/tickets/{t['id']}/messages", headers=priya,
                                 json={"body": "It broke again"}), 201)  # fmt: skip
    assert again["status"] == "open" and again["status_label"] == "Open"
    assert [m["author"] for m in again["messages"]] == ["You", "You", "You"]
    assert ok(await client.get(f"{T}/{t['id']}", headers=w["admin"]))["reopen_count"] == 1


async def test_files_on_replies_can_be_downloaded_but_not_files_on_notes(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    priya = await customer(client, app_sessionmaker, w, "priya@example.org")
    ravi = await customer(client, app_sessionmaker, w, "ravi@example.org")
    t = ok(await client.post(f"{P}/tickets", headers=priya, json={"message": "Invoice?"}), 201)
    ids = []
    for name in ("invoice.pdf", "internal.pdf"):
        r = await client.post(f"{T}/{t['id']}/attachments", params={"name": name},
                              content=b"%PDF-1.4", headers={**w["neha"],
                              "Content-Type": "application/pdf"})  # fmt: skip
        ids.append(ok(r, 201)["id"])
    url = f"{T}/{t['id']}/messages"
    for kind, body, file_id in (("reply", "Here", ids[0]), ("note", "Ours", ids[1])):
        sent = {"kind": kind, "body": body, "attachment_ids": [file_id]}
        ok(await client.post(url, headers=w["neha"], json=sent), 201)

    seen = ok(await client.get(f"{P}/tickets/{t['id']}", headers=priya))
    assert [f["name"] for f in seen["messages"][1]["attachments"]] == ["invoice.pdf"]
    link = ok(await client.get(f"{P}/attachments/{ids[0]}", headers=priya))
    assert link["file"]["name"] == "invoice.pdf"
    got = await client.get(link["url"])
    assert got.status_code == 200 and got.content == b"%PDF-1.4"
    for who, file_id in ((priya, ids[1]), (ravi, ids[0])):
        assert (await client.get(f"{P}/attachments/{file_id}", headers=who)).status_code == 404
        r = await client.get(f"{P}/attachments/{file_id}/content", headers=who)
        assert r.status_code == 404

    # S5: the workspace wants sign-in for files, so the portal hands out its own path.
    ok(
        await client.put(
            "/api/v1/security-settings", headers=w["admin"], json={"attachments_need_sign_in": True}
        )
    )
    link = ok(await client.get(f"{P}/attachments/{ids[0]}", headers=priya))
    assert link["needs_sign_in"] is True
    assert link["url"].endswith(f"/api/v1/portal/attachments/{ids[0]}/content")
    assert (await client.get(link["url"])).status_code == 401
    assert (await client.get(link["url"], headers=w["neha"])).status_code == 401  # agent token
    got = await client.get(link["url"], headers=priya)
    assert (got.status_code, got.content) == (200, b"%PDF-1.4")


async def test_at_most_ten_new_tickets_an_hour(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    priya = await customer(client, app_sessionmaker, w, "priya@example.org")
    for n in range(10):
        ok(await client.post(f"{P}/tickets", headers=priya, json={"message": f"#{n}"}), 201)
    r = await client.post(f"{P}/tickets", headers=priya, json={"message": "Eleventh"})
    assert r.status_code == 429 and r.headers["retry-after"] == "3600"


async def test_types_hidden_from_customers_are_not_offered_or_accepted(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    inside = {"name": "Internal escalation", "for_customers": False}
    hidden = ok(await client.post("/api/v1/ticket-types", headers=w["admin"], json=inside), 201)
    assert hidden["for_customers"] is False
    priya = await customer(client, app_sessionmaker, w, "priya@example.org")
    assert [t["name"] for t in ok(await client.get(f"{P}/ticket-types", headers=priya))] == [
        "Refund"
    ]
    r = await client.post(f"{P}/tickets", headers=priya,
                          json={"message": "Hi", "type_id": hidden["id"]})  # fmt: skip
    assert r.status_code == 422
    assert r.json()["error"]["message"] == "That ticket type does not exist."
    # Agents still see and use it; showing it again puts it back in the portal.
    agents = ok(await client.get("/api/v1/ticket-types", headers=w["neha"]))
    assert {t["name"] for t in agents} == {"Refund", "Internal escalation"}
    ok(await client.patch(f"/api/v1/ticket-types/{hidden['id']}", headers=w["admin"],
                          json={"for_customers": True}))  # fmt: skip
    assert len(ok(await client.get(f"{P}/ticket-types", headers=priya))) == 2


async def notices(sm: Sm, w: dict[str, Any], ticket_id: str) -> list[dict[str, Any]]:
    """Runs the worker's portal handler on the ticket's events; the emails it queued."""
    handler = PortalNoticeHandler(sm, base_url="https://help.acme.test", fallback=None)
    async with workspace_session(sm, w["ws"]) as s:
        rows = await outbox.for_ticket(s, UUID(ticket_id))
    for r in rows:
        if r["event"] == "message.created":
            event = Event(r["id"], w["ws"], r["event"], r["ticket_id"], dict(r["payload"]),
                          r["occurred_at"])  # fmt: skip
            await handler.handle(event, f"{r['id']}:{handler.name}")
            await handler.handle(event, f"{r['id']}:{handler.name}")  # a retry
    async with workspace_session(sm, w["ws"]) as s:
        found = await s.execute(text(
            "SELECT to_addresses, subject, body_text FROM hd_email_out "
            "WHERE ticket_id = :t AND header_message_id LIKE '<portal-reply-%'"),
            {"t": ticket_id})  # fmt: skip
    return [dict(m) for m in found.mappings()]


async def test_the_customer_is_emailed_when_an_agent_replies(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    priya = await customer(client, app_sessionmaker, w, "priya@example.org")
    t = ok(await client.post(f"{P}/tickets", headers=priya, json={"message": "Broken"}), 201)
    url = f"{T}/{t['id']}/messages"
    ok(await client.post(url, headers=w["neha"], json={"kind": "note", "body": "Hmm"}), 201)
    assert await notices(app_sessionmaker, w, t["id"]) == []  # a note: nothing

    for body in ("Secret fix: restart it", "Also try this"):  # two replies in a row
        ok(await client.post(url, headers=w["neha"], json={"kind": "reply", "body": body}), 201)
    [mail] = await notices(app_sessionmaker, w, t["id"])  # one email, retries included
    assert mail["to_addresses"] == ["priya@example.org"]
    assert mail["subject"] == f"New reply on your request [{t['label']}] Broken"
    assert "Neha from Test support replied" in mail["body_text"]
    assert f"https://help.acme.test/{w['slug']}/tickets/{t['id']}" in mail["body_text"]
    assert "Secret fix" not in mail["body_text"]  # no message text in the email

    # One reply, and its event is handled again after the quiet time (the worker was
    # down and retried it): still one email.
    late = ok(await client.post(f"{P}/tickets", headers=priya, json={"message": "Late"}), 201)
    ok(await client.post(f"{T}/{late['id']}/messages", headers=w["neha"],
                         json={"kind": "reply", "body": "Done"}), 201)  # fmt: skip
    assert len(await notices(app_sessionmaker, w, late["id"])) == 1
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(text("UPDATE hd_email_out SET created_at = now() - interval '1 hour' "
                             "WHERE ticket_id = :t"), {"t": late["id"]})  # fmt: skip
    assert len(await notices(app_sessionmaker, w, late["id"])) == 1

    # No sender anywhere: skipped quietly (a warning in the log), not a failed event.
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(text("DELETE FROM hd_email_out WHERE ticket_id = :t"), {"t": t["id"]})
        await s.execute(text("DELETE FROM hd_email_address"))
    assert await notices(app_sessionmaker, w, t["id"]) == []

    # Tickets from other channels get their own handling, not this email.
    body = {"subject": "By phone", "contact": {"email": "ravi@example.org", "name": "Ravi"},
            "channel": "phone"}  # fmt: skip
    phone = ok(await client.post(T, headers=w["admin"], json=body), 201)
    ok(await client.post(f"{T}/{phone['id']}/messages", headers=w["neha"],
                         json={"kind": "reply", "body": "Hi"}), 201)  # fmt: skip
    assert await notices(app_sessionmaker, w, phone["id"]) == []


async def test_an_imported_ticket_shows_the_customer_only_its_types_fields(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """Before, the portal showed every field of an imported ticket: the old help desk's
    custom fields (agents' own, e.g. a fraud score), extra tags, who was copied, the link
    to its agent view. Now only the fields of the ticket's type; agents still see all."""
    sm = app_sessionmaker
    w = await desk(client, sm)
    priya = await customer(client, sm, w, "priya@example.org")
    mine = ok(await client.post(f"{P}/tickets", headers=priya, json={"message": "Hi"}), 201)
    old = {"source_url": "https://acme.zendesk.com/agent/tickets/9", "fraud_score": 92,
           "more_tags": "vip, churn-risk", "source_cc": "ops@globex.test"}  # fmt: skip
    async with workspace_session(sm, w["ws"]) as s:  # what an import writes
        await s.execute(
            text("UPDATE hd_ticket SET source = 'zendesk', source_id = '9', "
                 "fields = CAST(:f AS jsonb) WHERE id = :id"),
            {"f": json.dumps(old), "id": mine["id"]},
        )  # fmt: skip
    assert ok(await client.get(f"{P}/tickets/{mine['id']}", headers=priya))["fields"] == {}
    listed = ok(await client.get(f"{P}/tickets", headers=priya))["tickets"]
    assert "fraud_score" not in str(listed) and "zendesk" not in str(listed)
    agent = ok(await client.get(f"{T}/{mine['id']}", headers=w["admin"]))
    assert agent["fields"] == old  # agents still see everything

    # An agent gives it the Refund type and its field: that one the customer sees. (A new
    # type drops values it doesn't define, each with a line: CeDesk's existing rule.)
    change = {"type_id": w["refund"], "fields": {"order_id": "A-9"}}
    ok(await client.patch(f"{T}/{mine['id']}", headers=w["admin"], json=change))
    seen = ok(await client.get(f"{P}/tickets/{mine['id']}", headers=priya))
    assert seen["fields"] == {"order_id": "A-9"}


async def test_a_tickets_own_fields_are_shown_as_before(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """Tickets not imported are unchanged by that rule: a type later hidden from customers
    keeps showing the values the customer gave, and so does a value whose field was later
    removed from the type."""
    sm = app_sessionmaker
    w = await desk(client, sm)
    priya = await customer(client, sm, w, "priya@example.org")
    new = {"message": "Refund please", "type_id": w["refund"],
           "fields": {"order_id": "A-77", "reason": "Late"}}  # fmt: skip
    typed = ok(await client.post(f"{P}/tickets", headers=priya, json=new), 201)
    ok(await client.patch(f"/api/v1/ticket-types/{w['refund']}", headers=w["admin"],
                          json={"for_customers": False}))  # fmt: skip
    got = ok(await client.get(f"{P}/tickets/{typed['id']}", headers=priya))
    assert got["fields"] == {"order_id": "A-77", "reason": "Late"}
    ok(await client.patch(f"/api/v1/ticket-types/{w['refund']}", headers=w["admin"],
                          json={"fields": REFUND[:1]}))  # "reason" removed  # fmt: skip
    got = ok(await client.get(f"{P}/tickets/{typed['id']}", headers=priya))
    assert got["fields"] == {"order_id": "A-77", "reason": "Late"}
