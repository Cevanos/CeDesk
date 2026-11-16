"""CSAT (md P5, T15) with RLS on: the survey email after resolve (once per event, never
for chats, merged tickets or when turned off), rating by the emailed link or in the
portal, one rating per ticket (a new one replaces it), the "Customer rated" line, and
the rating on the agent's ticket view and customer card."""

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.repo import outbox
from cedesk.helpdesk.service.csat import CsatHandler
from cedesk.helpdesk.service.delivery import Event
from cedesk.helpdesk.service.password_reset import Sender
from cedesk.main import create_app
from cedesk.settings import Settings
from tests.db.helpers import Sm, bearer, new_workspace, ok, sign_in
from tests.db.test_portal_tickets import customer

pytestmark = pytest.mark.db

T = "/api/v1/tickets"
PUBLIC = "/api/v1/public/csat"
BASE = "https://help.acme.test"


async def desk(client: AsyncClient, sm: Sm, *, address: bool = True) -> dict[str, Any]:
    slug = await new_workspace(sm)
    admin = await sign_in(client, slug, "admin@example.com")
    if address:
        body = {"address": f"support@{slug}.test", "name": "Acme Support"}
        ok(await client.post("/api/v1/email-addresses", headers=bearer(admin), json=body), 201)
    return {"slug": slug, "admin": bearer(admin), "ws": UUID(admin.split(".")[0])}


async def resolved(
    client: AsyncClient, w: dict[str, Any], email: str = "priya@example.org"
) -> dict[str, Any]:
    body = {"subject": "Mixer arrived broken", "contact": {"email": email, "name": "Priya Shah"}}
    t = ok(await client.post(T, headers=w["admin"], json=body), 201)
    ok(await client.post(f"{T}/{t['id']}/resolve", headers=w["admin"]))
    return dict(t)


async def run_survey(
    sm: Sm, w: dict[str, Any], ticket_id: str, *, fallback: Sender | None = None
) -> None:
    """What the worker does for this ticket's `ticket.resolved` events."""
    handler = CsatHandler(sm, base_url=BASE, fallback=fallback)
    async with workspace_session(sm, w["ws"]) as s:
        rows = await outbox.for_ticket(s, UUID(ticket_id))
    for r in rows:
        if r["event"] == "ticket.resolved":
            event = Event(r["id"], w["ws"], r["event"], r["ticket_id"], dict(r["payload"]),
                          r["occurred_at"])  # fmt: skip
            await handler.handle(event, f"{r['id']}:{handler.name}")


async def surveys(sm: Sm, w: dict[str, Any], ticket_id: str) -> list[dict[str, Any]]:
    async with workspace_session(sm, w["ws"]) as s:
        rows = await s.execute(text(
            "SELECT to_addresses, subject, body_text, sensitive FROM hd_email_out "
            "WHERE ticket_id = :t AND subject LIKE 'How did we do?%' ORDER BY created_at"),
            {"t": ticket_id})  # fmt: skip
    return [dict(r) for r in rows.mappings()]


def token_in(body: str) -> str:
    found = re.search(r"#token=(\S+)", body)
    assert found
    return found[1]


async def test_the_survey_link_rates_the_ticket(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    t = await resolved(client, w)
    await run_survey(app_sessionmaker, w, t["id"])
    await run_survey(app_sessionmaker, w, t["id"])  # a retried event sends nothing more
    [mail] = await surveys(app_sessionmaker, w, t["id"])
    label = f"T-{t['number']}"
    assert mail["to_addresses"] == ["priya@example.org"] and mail["sensitive"] is True
    assert mail["subject"] == f"How did we do? [{label}]"
    assert mail["body_text"].startswith("Hi Priya,") and "Mixer arrived broken" in mail["body_text"]
    token = token_in(mail["body_text"])
    assert f"{BASE}/rate?score=5#token={token}" in mail["body_text"]

    page = ok(await client.get(f"{PUBLIC}/{token}"))
    assert page == {"workspace_name": "Test", "ticket_label": label,
                    "subject": "Mixer arrived broken", "rating": None}  # fmt: skip
    rated = ok(await client.post(f"{PUBLIC}/{token}", json={"score": 4, "comment": " Fast! "}))
    assert (rated["score"], rated["comment"], rated["source"]) == (4, "Fast!", "email")

    seen = ok(await client.get(f"{T}/{t['id']}", headers=w["admin"]))
    assert seen["csat"]["score"] == 4 and seen["csat"]["comment"] == "Fast!"
    assert seen["contact"]["csat_average"] == 4.0 and seen["contact"]["csat_count"] == 1
    assert seen["messages"][-1]["body"] == 'Customer rated 4/5: "Fast!"'

    # Rating again changes it (one rating per ticket); the page shows the current one.
    ok(await client.post(f"{PUBLIC}/{token}", json={"score": 2}))
    assert ok(await client.get(f"{PUBLIC}/{token}"))["rating"]["score"] == 2
    seen = ok(await client.get(f"{T}/{t['id']}", headers=w["admin"]))
    assert seen["csat"] == {**seen["csat"], "score": 2, "comment": None}
    assert seen["contact"]["csat_count"] == 1
    assert seen["messages"][-1]["body"] == "Customer changed the rating to 2/5"
    # The same rating again adds no line.
    ok(await client.post(f"{PUBLIC}/{token}", json={"score": 2}))
    again = ok(await client.get(f"{T}/{t['id']}", headers=w["admin"]))
    assert [m["body"] for m in again["messages"]].count("Customer changed the rating to 2/5") == 1
    assert len(again["messages"]) == len(seen["messages"])
    # A card number in the comment is removed before saving, as in any customer message.
    card = {"score": 1, "comment": "Refund it to 4111 1111 1111 1111"}
    assert ok(await client.post(f"{PUBLIC}/{token}", json=card))["comment"] == (
        "Refund it to [card number removed]"
    )
    last = ok(await client.get(f"{T}/{t['id']}", headers=w["admin"]))["messages"][-1]["body"]
    assert last == 'Customer changed the rating to 1/5: "Refund it to [card number removed]"'

    for bad in ({"score": 0}, {"score": 6}, {"score": 3, "comment": "x" * 2001}):
        assert (await client.post(f"{PUBLIC}/{token}", json=bad)).status_code == 422


async def test_the_customer_profile_shows_the_same_rating_as_the_ticket(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """Bug B-12 (plan 3.7.2): the ticket's customer card showed 4.0 while the customer's own
    profile (`GET /contacts/{id}/tickets`) had no rating field and read 'No ratings'."""
    w = await desk(client, app_sessionmaker)
    t = await resolved(client, w)
    contact = t["contact"]["id"]
    profile = f"/api/v1/contacts/{contact}/tickets"

    unrated = ok(await client.get(profile, headers=w["admin"]))["contact"]
    assert (unrated["csat_average"], unrated["csat_count"]) == (None, 0)

    await run_survey(app_sessionmaker, w, t["id"])
    [mail] = await surveys(app_sessionmaker, w, t["id"])
    ok(await client.post(f"{PUBLIC}/{token_in(mail['body_text'])}", json={"score": 4}))

    rated = ok(await client.get(profile, headers=w["admin"]))["contact"]
    assert (rated["csat_average"], rated["csat_count"]) == (4.0, 1)
    ticket_card = ok(await client.get(f"{T}/{t['id']}", headers=w["admin"]))["contact"]
    assert (ticket_card["csat_average"], ticket_card["csat_count"]) == (4.0, 1)  # the same

    # Editing the customer returns the same numbers.
    edited = ok(await client.patch(f"/api/v1/contacts/{contact}/profile", headers=w["admin"],
                                   json={"name": "Priya S"}))  # fmt: skip
    assert (edited["csat_average"], edited["csat_count"]) == (4.0, 1)


async def test_bad_expired_and_reopened_links(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    t = await resolved(client, w)
    await run_survey(app_sessionmaker, w, t["id"])
    token = token_in((await surveys(app_sessionmaker, w, t["id"]))[0]["body_text"])

    for wrong in (token[:-2] + "xx", "not-a-token-at-all", f"{UUID(int=3)}.abcdefgh"):
        r = await client.post(f"{PUBLIC}/{wrong}", json={"score": 5})
        assert r.status_code == 410, wrong
        assert r.json()["error"]["message"] == "This survey link is invalid or has expired."

    # The customer wrote back and the ticket reopened: no rating until it is resolved again.
    ok(await client.patch(f"{T}/{t['id']}", headers=w["admin"], json={"status": "open"}))
    assert (await client.post(f"{PUBLIC}/{token}", json={"score": 5})).status_code == 409

    ok(await client.post(f"{T}/{t['id']}/resolve", headers=w["admin"]))
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(text("UPDATE hd_csat_link SET expires_at = :t"), {"t": datetime.now(UTC)})
    assert (await client.get(f"{PUBLIC}/{token}")).status_code == 410

    # A link from another workspace never works here (the workspace is in the token).
    other = await desk(client, app_sessionmaker)
    assert (await client.get(f"{PUBLIC}/{other['ws']}.{token.split('.', 1)[1]}")).status_code == 410


async def test_when_no_survey_is_sent(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    # Turned off by the Admin.
    ok(await client.put("/api/v1/portal-settings", headers=w["admin"],
                        json={"allowed_domains": [], "csat_survey": False}))  # fmt: skip
    assert (
        ok(await client.get("/api/v1/portal-settings", headers=w["admin"]))["csat_survey"] is False
    )
    off = await resolved(client, w)
    await run_survey(app_sessionmaker, w, off["id"])
    assert await surveys(app_sessionmaker, w, off["id"]) == []
    ok(
        await client.put(
            "/api/v1/portal-settings", headers=w["admin"], json={"allowed_domains": []}
        )
    )

    # A Cevanos chat customer (the widget asks them), and a ticket merged into another.
    chat = await resolved(client, w, "chat-c1@cevanos.invalid")
    merged = await resolved(client, w, "ravi@example.org")
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(text("UPDATE hd_ticket SET merged_into_id = :into WHERE id = :id"),
                        {"into": chat["id"], "id": merged["id"]})  # fmt: skip
    # Reopened before the worker got to it.
    reopened = await resolved(client, w, "asha@example.org")
    ok(await client.patch(f"{T}/{reopened['id']}", headers=w["admin"], json={"status": "open"}))
    for t in (chat, merged, reopened):
        await run_survey(app_sessionmaker, w, t["id"])
        assert await surveys(app_sessionmaker, w, t["id"]) == [], t["contact"]["email"]


async def test_no_sender_skips_the_survey_unless_the_server_has_one(
    client: AsyncClient, app_sessionmaker: Sm, caplog: pytest.LogCaptureFixture
) -> None:
    w = await desk(client, app_sessionmaker, address=False)
    t = await resolved(client, w)
    await run_survey(app_sessionmaker, w, t["id"])  # no error: skipped, with a warning
    assert await surveys(app_sessionmaker, w, t["id"]) == []
    assert "csat survey not sent: no support address" in caplog.text
    await run_survey(
        app_sessionmaker, w, t["id"], fallback=Sender("no-reply@cedesk.test", "CeDesk")
    )
    assert len(await surveys(app_sessionmaker, w, t["id"])) == 1


async def test_rating_in_the_portal(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    priya = await customer(client, app_sessionmaker, w, "priya@example.org")
    ravi = await customer(client, app_sessionmaker, w, "ravi@example.org")
    t = ok(await client.post("/api/v1/portal/tickets", headers=priya,
                             json={"message": "Broken mixer"}), 201)  # fmt: skip
    url = f"/api/v1/portal/tickets/{t['id']}/csat"
    assert t["csat"] is None
    r = await client.post(url, headers=priya, json={"score": 5})
    assert r.status_code == 409 and r.json()["error"]["message"] == (
        "You can rate a ticket once it is resolved."
    )
    ok(await client.post(f"{T}/{t['id']}/resolve", headers=w["admin"]))
    assert (await client.post(url, headers=ravi, json={"score": 1})).status_code == 404
    rated = ok(await client.post(url, headers=priya, json={"score": 5, "comment": "Great"}))
    assert rated["source"] == "portal"

    mine = ok(await client.get(f"/api/v1/portal/tickets/{t['id']}", headers=priya))
    assert mine["csat"]["score"] == 5
    assert "Customer rated" not in str(mine["messages"])  # agents' line only
    seen = ok(await client.get(f"{T}/{t['id']}", headers=w["admin"]))
    assert seen["messages"][-1]["body"] == 'Customer rated 5/5: "Great"'


async def test_the_rating_page_has_its_own_limit_not_the_sign_in_one(
    app_sessionmaker: Sm,
) -> None:
    app = create_app(Settings(sign_in_limit=3, public_limit=4))
    app.state.sessionmaker = app_sessionmaker
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        w = await desk(c, app_sessionmaker)  # one sign-in
        t = await resolved(c, w)
        await run_survey(app_sessionmaker, w, t["id"])
        token = token_in((await surveys(app_sessionmaker, w, t["id"]))[0]["body_text"])
        for _ in range(4):
            ok(await c.get(f"{PUBLIC}/{token}"))
        r = await c.post(f"{PUBLIC}/{token}", json={"score": 5})
        assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
        # Sign-ins from the same address still work (2 left of 3).
        for _ in range(2):
            assert await sign_in(c, w["slug"], "admin@example.com")
