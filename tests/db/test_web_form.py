"""The public web form (md C2) with RLS on: off until the Admin turns it on, makes a `web`
ticket like any other channel, replies go out by email, and the spam guards (honeypot,
per IP, per email) hold."""

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.main import create_app
from cedesk.settings import Settings
from tests.db.helpers import Sm, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

T = "/api/v1/tickets"
REFUND = [{"key": "order_id", "label": "Order ID", "kind": "text", "required": True}]


def app_client(sm: Sm, **settings: Any) -> AsyncClient:
    app = create_app(Settings(sign_in_limit=100_000, **settings))
    app.state.sessionmaker = sm
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.fixture
async def client(app_sessionmaker: Sm) -> AsyncIterator[AsyncClient]:
    async with app_client(app_sessionmaker, form_limit=1000) as c:
        yield c


async def desk(client: AsyncClient, sm: Sm, *, form: bool = True) -> dict[str, Any]:
    slug = await new_workspace(sm)
    token = await sign_in(client, slug, "admin@example.com")
    admin = bearer(token)
    body = {"address": f"support@{slug}.test", "name": "Acme Support"}
    ok(await client.post("/api/v1/email-addresses", headers=admin, json=body), 201)
    refund = ok(await client.post("/api/v1/ticket-types", headers=admin,
                                  json={"name": "Refund", "fields": REFUND}), 201)  # fmt: skip
    if form:
        ok(await client.put("/api/v1/portal-settings", headers=admin,
                            json={"allowed_domains": [], "web_form": True}))  # fmt: skip
    return {"slug": slug, "admin": admin, "ws": UUID(token.split(".")[0]),
            "refund": refund["id"], "url": f"/api/v1/public/{slug}/form"}  # fmt: skip


async def web_tickets(sm: Sm, w: dict[str, Any]) -> list[dict[str, Any]]:
    async with workspace_session(sm, w["ws"]) as s:
        rows = await s.execute(text(
            "SELECT t.id, t.subject, c.email, c.name FROM hd_ticket t "
            "JOIN hd_contact c ON c.id = t.contact_id WHERE t.channel = 'web' "
            "ORDER BY t.created_at"))  # fmt: skip
    return [dict(r) for r in rows.mappings()]


def post(name: str = "Priya Shah", email: str = "priya@example.org", **more: Any) -> Any:
    return {"name": name, "email": email, "message": "My order never arrived", **more}


async def test_the_form_is_off_until_the_admin_turns_it_on(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker, form=False)
    assert ok(await client.get("/api/v1/portal-settings", headers=w["admin"]))["web_form"] is False
    for url in (w["url"], "/api/v1/public/no-such-workspace/form"):
        assert (await client.get(url)).status_code == 404
        r = await client.post(url, json=post())
        assert r.status_code == 404 and r.json()["error"]["message"] == (
            "This contact form is not available."
        )
    assert await web_tickets(app_sessionmaker, w) == []

    ok(await client.put("/api/v1/portal-settings", headers=w["admin"],
                        json={"allowed_domains": [], "web_form": True}))  # fmt: skip
    form = ok(await client.get(w["url"]))
    assert form["workspace_name"] == "Test"
    assert [(t["name"], t["fields"][0]["key"]) for t in form["types"]] == [("Refund", "order_id")]


async def test_a_form_post_is_a_web_ticket_answered_by_email(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    message = "Refund please, the parcel arrived completely crushed and the mixer is broken"
    form = post(type_id=w["refund"], fields={"order_id": "A-77"}, message=message)
    r = await client.post(w["url"], json=form)
    assert r.status_code == 202 and r.json() == {
        "message": "Thanks, we got your message. We'll reply by email."
    }
    [t] = await web_tickets(app_sessionmaker, w)
    assert t["email"] == "priya@example.org" and t["name"] == "Priya Shah"
    assert t["subject"] == "Refund please, the parcel arrived completely crushed and…"

    seen = ok(await client.get(f"{T}/{t['id']}", headers=w["admin"]))
    assert seen["channel"] == "web" and seen["source"] == "web_form"
    assert seen["type_name"] == "Refund"
    assert seen["fields"] == {"order_id": "A-77"} and seen["sla"] is not None

    # The agent's reply goes to the customer by email, threaded for their answer.
    ok(await client.post(f"{T}/{t['id']}/messages", headers=w["admin"],
                         json={"kind": "reply", "body": "Refund sent"}), 201)  # fmt: skip
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        mail = (await s.execute(text(
            "SELECT to_addresses, subject, header_message_id FROM hd_email_out "
            "WHERE ticket_id = :t"), {"t": t["id"]})).mappings().one()  # fmt: skip
    assert mail["to_addresses"] == ["priya@example.org"] and mail["header_message_id"]
    assert seen["label"] in mail["subject"]

    # No name: the part before the @.
    ok(await client.post(w["url"], json=post(name="  ", email="Ravi.K@Example.org")), 202)
    assert (await web_tickets(app_sessionmaker, w))[-1]["name"] == "ravi.k"


async def test_bad_posts_are_refused(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    for bad in (
        post(email="not-an-email"),
        post(message="   "),
        post(type_id=w["refund"]),  # the required order id is missing
        post(type_id=str(UUID(int=7))),
        post(extra="field"),
    ):
        assert (await client.post(w["url"], json=bad)).status_code == 422, bad
    assert await web_tickets(app_sessionmaker, w) == []


async def test_a_filled_honeypot_makes_nothing_but_looks_the_same(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    person = await client.post(w["url"], json=post())
    bot = await client.post(w["url"], json=post(website="http://spam.example"))
    assert (bot.status_code, bot.json()) == (person.status_code, person.json())
    assert len(await web_tickets(app_sessionmaker, w)) == 1


async def test_at_most_five_an_hour_per_email(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    for _ in range(5):
        ok(await client.post(w["url"], json=post(email="PRIYA@example.org")), 202)
    r = await client.post(w["url"], json=post())
    assert r.status_code == 429 and r.headers["retry-after"] == "3600"
    ok(await client.post(w["url"], json=post(email="ravi@example.org")), 202)
    assert len(await web_tickets(app_sessionmaker, w)) == 6


async def test_posts_per_ip_have_their_own_limit(app_sessionmaker: Sm) -> None:
    async with app_client(app_sessionmaker, form_limit=3) as c:
        w = await desk(c, app_sessionmaker)
        for n in range(3):
            ok(await c.post(w["url"], json=post(email=f"p{n}@example.org")), 202)
        r = await c.post(w["url"], json=post(email="p9@example.org"))
        assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
        # Sign-in has its own budget: still works.
        assert await sign_in(c, w["slug"], "admin@example.com")
    assert len(await web_tickets(app_sessionmaker, w)) == 3


async def test_types_hidden_from_customers_stay_off_the_form(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    inside = {"name": "Internal escalation", "for_customers": False}
    hidden = ok(await client.post("/api/v1/ticket-types", headers=w["admin"], json=inside), 201)
    assert [t["name"] for t in ok(await client.get(w["url"]))["types"]] == ["Refund"]
    assert (await client.post(w["url"], json=post(type_id=hidden["id"]))).status_code == 422
    assert await web_tickets(app_sessionmaker, w) == []


async def test_a_card_number_cut_by_the_subject_is_still_removed(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """The subject is the message's first 60 characters; it is made after redaction, so
    a cut through the number can't keep part of it."""
    w = await desk(client, app_sessionmaker)
    message = "Please refund the double charge on my card 4111 1111 1111 1111 today"
    assert len("Please refund the double charge on my card 4111 1111") < 60  # the cut
    # The number typed into a text field too.
    fields = {"order_id": "4111-1111-1111-1111"}
    ok(await client.post(w["url"], json=post(message=message, type_id=w["refund"],
                                             fields=fields)), 202)  # fmt: skip
    [t] = await web_tickets(app_sessionmaker, w)
    assert t["subject"] == "Please refund the double charge on my card [card number…"
    seen = ok(await client.get(f"{T}/{t['id']}", headers=w["admin"]))
    assert seen["messages"][0]["body"] == (
        "Please refund the double charge on my card [card number removed] today"
    )
    assert seen["fields"] == {"order_id": "[card number removed]"}
    # Once in the subject and once in the message: one number.
    assert "1 card number removed from the customer's message" in [
        m["body"] for m in seen["messages"]
    ]
