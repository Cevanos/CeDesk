"""Auto-close inactive tickets (feature-gap plan 1.4) with RLS on: the setting (Admin,
off by default, audited), and the daily pass that resolves Pending tickets with no
customer reply for N days, sends the notice first, skips what is held, audits each close
and never touches another workspace."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient

from cedesk.helpdesk.domain import auto_close as rules
from cedesk.helpdesk.service.auto_close import close_workspace, run_auto_close
from tests.db.helpers import Sm, ok
from tests.db.inbox_helpers import T, as_, audit, bell, desk, events, lines, sql, ticket

pytestmark = pytest.mark.db

S = "/api/v1/auto-close"


async def pending(client: AsyncClient, w: dict[str, Any], name: str, days_idle: float,
                  **extra: Any) -> str:  # fmt: skip
    t = await ticket(client, w, name, "Billing", "Neha")
    ok(await client.patch(f"{T}/{t}", headers=w["h"], json={"status": "pending", **extra}))
    await idle(client, w, t, days_idle)
    return t


async def idle(client: AsyncClient, w: dict[str, Any], t: str, days: float) -> None:
    await sql(w["sm"], w["ws"],
              "UPDATE hd_ticket SET updated_at = now() - make_interval(secs => :s) "
              "WHERE id = :t", s=days * 86400, t=t)  # fmt: skip


async def turn_on(client: AsyncClient, w: dict[str, Any], **body: Any) -> Any:
    return ok(await client.put(S, headers=w["h"],
                               json={"enabled": True, "after_days": 3, **body}))  # fmt: skip


async def test_the_setting(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    them = await desk(client, app_sessionmaker)
    got = ok(await client.get(S, headers=w["h"]))
    assert (got["enabled"], got["after_days"], got["notice"]) == (False, 7, None)
    neha = await as_(client, w, "Neha")
    assert (await client.get(S, headers=neha)).status_code == 403
    r = await client.put(S, headers=neha, json={"enabled": True, "after_days": 3})
    assert r.status_code == 403
    for bad in (0, 366):
        r = await client.put(S, headers=w["h"], json={"enabled": True, "after_days": bad})
        assert r.status_code == 422
    saved = await turn_on(client, w, notice="  Closing this, {{customer.first_name}}.  ")
    assert (saved["enabled"], saved["after_days"]) == (True, 3)
    assert saved["notice"] == "Closing this, {{customer.first_name}}."
    assert saved["updated_by_name"] == "Admin"
    entry = (await audit(client, w, "auto_close"))[0]
    assert entry["detail"]["changes"]["enabled"] == [False, True]
    # Saving the same again writes no entry; another workspace keeps its own setting.
    await turn_on(client, w, notice="Closing this, {{customer.first_name}}.")
    assert len(await audit(client, w, "auto_close")) == 1
    assert ok(await client.get(S, headers=them["h"]))["enabled"] is False


async def test_the_pass_closes_inactive_tickets_and_skips_the_rest(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    w["sm"] = app_sessionmaker
    them = await desk(client, app_sessionmaker)
    them["sm"] = app_sessionmaker
    await turn_on(client, w)
    sam = await as_(client, w, "Sam")

    old = await pending(client, w, "Old", 5)
    ok(await client.post(f"{T}/{old}/followers", headers=sam, json={"member_id": w["ids"]["Sam"]}))
    await idle(client, w, old, 5)  # following touched nothing, but be sure
    fresh = await pending(client, w, "Fresh", 1)
    replied = await pending(client, w, "Replied", 5)
    await sql(app_sessionmaker, w["ws"],
              "INSERT INTO hd_message (workspace_id, ticket_id, kind, body, author_name, "
              "created_at) VALUES (:w, :t, 'customer', 'Still broken', 'Replied', "
              "now() - interval '1 day')", w=w["ws"], t=replied)  # fmt: skip
    await idle(client, w, replied, 5)  # as if the reply had not touched the ticket
    snoozed = await ticket(client, w, "Snoozed", "Billing", "Neha")
    until = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    ok(await client.post(f"{T}/{snoozed}/snooze", headers=w["h"], json={"until": until}))
    await idle(client, w, snoozed, 5)
    open_old = await ticket(client, w, "Open", "Billing", "Neha")
    await idle(client, w, open_old, 5)
    imported = await pending(client, w, "Imported", 5)
    await sql(app_sessionmaker, w["ws"], "UPDATE hd_ticket SET source = 'zendesk', "
              "source_id = 'z-9' WHERE id = :t", t=imported)  # fmt: skip
    await idle(client, w, imported, 5)
    parent = await pending(client, w, "Parent", 5)
    ok(await client.post(f"{T}/{parent}/children", headers=w["h"],
                         json={"team_id": w["teams"]["Shipping"]}), 201)  # fmt: skip
    await idle(client, w, parent, 5)
    theirs = await pending(client, them, "Theirs", 30)  # their setting is off

    assert await run_auto_close(app_sessionmaker) >= 1
    status = {name: ok(await client.get(f"{T}/{t}", headers=w["h"]))["status"]
              for name, t in (("old", old), ("fresh", fresh), ("replied", replied),
                              ("snoozed", snoozed), ("open", open_old),
                              ("imported", imported), ("parent", parent))}  # fmt: skip
    assert status == {"old": "resolved", "fresh": "pending", "replied": "pending",
                      "snoozed": "pending", "open": "open", "imported": "pending",
                      "parent": "pending"}  # fmt: skip
    assert ok(await client.get(f"{T}/{theirs}", headers=them["h"]))["status"] == "pending"

    assert rules.closed_line(3) in await lines(client, w, old)
    kinds = [e["event"] for e in await events(app_sessionmaker, w["ws"], old)]
    assert kinds[-1] == "ticket.resolved"
    closes = [e for e in await audit(client, w, "tickets") if e["action"] == "auto_closed"]
    assert len(closes) == 1 and closes[0]["actor_name"] == "Auto-close"
    assert closes[0]["detail"]["ticket_id"] == old
    number = ok(await client.get(f"{T}/{old}", headers=w["h"]))["number"]
    assert await bell(client, w, "Sam", "following") == [
        f"T-{number} was resolved automatically (no reply): Old"]  # fmt: skip
    # Once only.
    assert await close_workspace(app_sessionmaker, w["ws"], datetime.now(UTC)) == 0


async def test_the_notice_goes_out_like_a_reply(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    w["sm"] = app_sessionmaker
    domain = f"{w['slug']}.test"
    ok(await client.post("/api/v1/email-addresses", headers=w["h"],
                         json={"address": f"help@{domain}", "name": "Acme help"}), 201)  # fmt: skip
    await turn_on(client, w, notice="Hi {{customer.first_name}}, closing this for now.")
    body = {"subject": "Torn shirt", "channel": "email", "assignee_id": w["ids"]["Neha"],
            "contact": {"email": "priya@example.com", "name": "Priya Sharma"},
            "description": "It arrived torn."}  # fmt: skip
    t = ok(await client.post(T, headers=w["h"], json=body), 201)["id"]
    ok(await client.patch(f"{T}/{t}", headers=w["h"], json={"status": "pending"}))
    # The customer wrote 4 days ago and nothing happened since.
    await sql(app_sessionmaker, w["ws"], "UPDATE hd_message SET created_at = now() - "
              "interval '4 days' WHERE ticket_id = :t", t=t)  # fmt: skip
    await idle(client, w, t, 4)

    assert await close_workspace(app_sessionmaker, w["ws"], datetime.now(UTC)) == 1
    detail = ok(await client.get(f"{T}/{t}", headers=w["h"]))
    notice = [m for m in detail["messages"] if m["kind"] == "agent"]
    assert [m["body"] for m in notice] == ["Hi Priya, closing this for now."]
    assert notice[0]["author_name"] == "Test" and notice[0]["author_member_id"] is None
    rows = await sql(app_sessionmaker, w["ws"],
                     "SELECT to_addresses, body_text FROM hd_email_out WHERE ticket_id = :t "
                     "AND message_id IS NOT NULL", t=t)  # fmt: skip
    assert len(rows) == 1 and rows[0][0] == ["priya@example.com"]
    assert rows[0][1].startswith("Hi Priya, closing this for now.")
    assert detail["status"] == "resolved"
    assert UUID(t)
