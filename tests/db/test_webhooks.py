"""Outgoing webhooks (md PL3-PL6), with RLS on: Admins add, change and delete them; ticket
events reach the URL signed, each URL on its own clock; failures are retried, then given
up and listed, and can be sent again; the test ping; turned-off and deleted webhooks; one
delivery per event; another workspace's events never reach a URL."""

import json
from typing import Any
from uuid import UUID

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import text

from cedesk.crypto import Sealer
from cedesk.db import workspace_session
from cedesk.egress import BadAddress
from cedesk.helpdesk.domain import webhooks as rules
from cedesk.helpdesk.service.delivery import deliver_due
from cedesk.helpdesk.service.webhooks import WebhookFanout, prune_everywhere, send_due
from cedesk.settings import Settings
from cedesk.webhook_client import WebhookClient
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

W = "/api/v1/webhooks"
D = "/api/v1/webhooks/deliveries"
T = "/api/v1/tickets"
URL = "https://hooks.example.com/cedesk"
SEALER = Sealer(Settings().secret_key.get_secret_value())  # the app's, in tests


class Receiver:
    """Stands in for the internet: records what arrives; answers `status[host]`."""

    def __init__(self) -> None:
        self.got: list[httpx.Request] = []
        self.status: dict[str, int] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.got.append(request)
        return httpx.Response(self.status.get(request.url.host, 200), text="thanks")

    def bodies(self, host: str = "hooks.example.com") -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.got if r.url.host == host]


async def guard(host: str) -> None:
    if host.startswith("intranet."):
        raise BadAddress(f"'{host}' isn't a public address.")


def install(client: AsyncClient) -> tuple[Receiver, WebhookClient]:
    receiver = Receiver()
    fake = WebhookClient(transport=httpx.MockTransport(receiver), guard=guard)
    client._transport.app.state.webhook_client = fake  # type: ignore[attr-defined]
    return receiver, fake


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    slug = await new_workspace(sm)
    token = await sign_in(client, slug, "admin@example.com")
    h = bearer(token)
    neha = await add(client, token, "neha@example.com", "agent", "Neha")
    me = ok(await client.get("/api/v1/me", headers=h))
    return {"slug": slug, "h": h, "token": token, "neha": neha,
            "ws": UUID(me["workspace"]["id"])}  # fmt: skip


async def hook(client: AsyncClient, w: dict[str, Any], events: list[str], url: str = URL) -> Any:
    return ok(await client.post(W, headers=w["h"], json={"url": url, "events": events}), 201)


async def ticket(
    client: AsyncClient, w: dict[str, Any], subject: str = "Order late",
    email: str = "priya@example.org",
) -> Any:  # fmt: skip
    body = {"subject": subject, "contact": {"email": email, "name": "Priya"}}
    return ok(await client.post(T, headers=w["h"], json=body), 201)


async def run(sm: Sm, fake: WebhookClient) -> Any:
    """One worker pass: the outbox hands events out, then due deliveries are sent."""
    await deliver_due(sm, [WebhookFanout(sm)])
    return await send_due(sm, SEALER, fake)


async def make_due(sm: Sm, ws: UUID) -> None:
    async with workspace_session(sm, ws) as s:
        await s.execute(text("UPDATE hd_webhook_delivery SET next_try_at = now() "
                             "WHERE state = 'pending'"))  # fmt: skip


async def test_admins_add_change_and_delete_webhooks(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    install(client)
    w = await desk(client, app_sessionmaker)
    made = await hook(client, w, ["ticket.resolved", "ticket.created"])
    secret = made["secret"]
    assert secret.startswith("whsec_") and made["secret_hint"] == "whsec_…" + secret[-4:]
    assert made["events"] == ["ticket.created", "ticket.resolved"] and made["enabled"] is True
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        sealed = (await s.execute(text("SELECT secret_sealed FROM hd_webhook"))).scalar_one()
    assert secret.encode() not in bytes(sealed)  # sealed, not kept as it is
    listed = ok(await client.get(W, headers=w["h"]))
    assert [h["id"] for h in listed] == [made["id"]] and "secret" not in listed[0]

    for body in (
        {"url": "http://hooks.example.com", "events": ["ticket.created"]},
        {"url": "https://10.0.0.5/x", "events": ["ticket.created"]},
        {"url": "https://intranet.example.com/x", "events": ["ticket.created"]},  # not public
        {"url": URL, "events": ["ticket.deleted"]},
        {"url": URL, "events": []},
    ):
        assert (await client.post(W, headers=w["h"], json=body)).status_code == 422, body
    neha = bearer(await sign_in(client, w["slug"], "neha@example.com"))
    assert (await client.get(W, headers=neha)).status_code == 403
    key = ok(await client.post("/api/v1/api-keys", headers=w["h"],
                               json={"name": "k", "access": "admin"}), 201)["key"]  # fmt: skip
    assert (await client.get(W, headers=bearer(key))).status_code == 403

    url = f"{W}/{made['id']}"
    patch = {"enabled": False, "events": ["ticket.updated"]}
    changed = ok(await client.patch(url, headers=w["h"], json=patch))
    assert (changed["enabled"], changed["events"]) == (False, ["ticket.updated"])
    rolled = ok(await client.post(f"{url}/secret", headers=w["h"]))
    assert rolled["secret"] != secret and rolled["secret_hint"] != made["secret_hint"]
    assert (await client.delete(url, headers=w["h"])).status_code == 204
    assert (await client.delete(url, headers=w["h"])).status_code == 404

    page = ok(await client.get("/api/v1/audit", headers=w["h"], params={"area": "webhooks"}))
    entries = page["entries"]
    assert [e["action"] for e in entries] == ["deleted", "secret_changed", "changed", "created"]
    assert secret not in json.dumps(entries) and rolled["secret"] not in json.dumps(entries)

    for _ in range(rules.MAX_WEBHOOKS):
        await hook(client, w, ["ticket.created"])
    r = await client.post(W, headers=w["h"], json={"url": URL, "events": ["ticket.created"]})
    assert r.status_code == 409


async def test_ticket_events_reach_the_url_signed(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    receiver, fake = install(client)
    w = await desk(client, app_sessionmaker)
    every = await hook(client, w, list(rules.EVENTS))
    resolved_only = await hook(client, w, ["ticket.resolved"], "https://other.example.com/in")

    t = await ticket(client, w)
    await run(app_sessionmaker, fake)
    got = receiver.bodies()
    assert [b["event"] for b in got] == ["ticket.created"]
    assert got[0]["ticket"]["id"] == t["id"] and got[0]["ticket"]["subject"] == "Order late"
    assert set(got[0]) >= {"event", "ticket", "id", "occurred_at"}  # PL5
    sent = receiver.got[0]
    stamp = int(sent.headers["X-CeDesk-Timestamp"])
    assert sent.headers["X-CeDesk-Signature"] == rules.signature(every["secret"], stamp,
                                                                  sent.content)  # fmt: skip
    assert sent.headers["X-CeDesk-Event"] == "ticket.created"

    ok(await client.patch(f"{T}/{t['id']}", headers=w["h"], json={"priority": "high"}))
    ok(await client.post(f"{T}/{t['id']}/messages", headers=w["h"],
                         json={"kind": "note", "body": "internal"}), 201)  # fmt: skip
    reply = ok(await client.post(f"{T}/{t['id']}/messages", headers=w["h"],
                                 json={"kind": "reply", "body": "On its way"}), 201)  # fmt: skip
    ok(await client.post(f"{T}/{t['id']}/resolve", headers=w["h"]))
    await run(app_sessionmaker, fake)
    events = [b["event"] for b in receiver.bodies()]
    assert events == ["ticket.created", "ticket.updated", "message.created", "ticket.resolved"]
    message = receiver.bodies()[2]["message"]
    assert (message["id"], message["kind"]) == (reply["id"], "agent")
    assert "On its way" not in json.dumps(receiver.bodies())  # ids, never the text
    assert [b["event"] for b in receiver.bodies("other.example.com")] == ["ticket.resolved"]

    page = ok(await client.get(D, headers=w["h"], params={"webhook_id": every["id"]}))
    first = page["deliveries"][-1]
    assert (first["state"], first["tries"], first["event"]) == ("delivered", 1, "ticket.created")
    assert first["attempts"][0]["status"] == 200 and first["attempts"][0]["response"] == "thanks"
    assert first["id"] == receiver.got[0].headers["X-CeDesk-Delivery"]
    assert ok(await client.get(D, headers=w["h"], params={"webhook_id": resolved_only["id"]}))[
        "deliveries"][0]["event"] == "ticket.resolved"  # fmt: skip


async def test_a_failing_url_is_retried_then_given_up(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    receiver, fake = install(client)
    w = await desk(client, app_sessionmaker)
    down = await hook(client, w, ["ticket.created"], "https://down.example.com/x")
    await hook(client, w, ["ticket.created"])  # a healthy one next to it
    receiver.status["down.example.com"] = 500
    await ticket(client, w)
    first = await run(app_sessionmaker, fake)
    assert (first.delivered, first.retrying, first.failed) == (1, 1, 0)  # the healthy one went
    again = await send_due(app_sessionmaker, SEALER, fake)
    assert (again.delivered, again.retrying) == (0, 0)  # not time yet (1 minute)
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        wait = (await s.execute(text(
            "SELECT extract(epoch FROM next_try_at - now()) FROM hd_webhook_delivery "
            "WHERE state = 'pending'"))).scalar_one()  # fmt: skip
    assert 50 < wait <= 60
    for _ in range(rules.MAX_TRIES - 1):
        await make_due(app_sessionmaker, w["ws"])
        await send_due(app_sessionmaker, SEALER, fake)
    failed = ok(await client.get(D, headers=w["h"], params={"state": "failed"}))["deliveries"]
    assert len(failed) == 1 and failed[0]["webhook_id"] == down["id"]
    assert failed[0]["tries"] == rules.MAX_TRIES and len(failed[0]["attempts"]) == rules.MAX_TRIES
    assert failed[0]["last_error"] == "HTTP 500." and failed[0]["next_try_at"] is None
    assert len(receiver.bodies("down.example.com")) == rules.MAX_TRIES
    await make_due(app_sessionmaker, w["ws"])
    await send_due(app_sessionmaker, SEALER, fake)
    assert len(receiver.bodies("down.example.com")) == rules.MAX_TRIES  # given up: no more

    receiver.status["down.example.com"] = 204
    r = await client.post(f"{D}/{failed[0]['id']}/retry", headers=w["h"])
    assert r.status_code == 204
    done = await send_due(app_sessionmaker, SEALER, fake)
    assert done.delivered == 1
    delivery_ids = {r.headers["X-CeDesk-Delivery"] for r in receiver.got
                    if r.url.host == "down.example.com"}  # fmt: skip
    assert delivery_ids == {failed[0]["id"]}  # the same id on every try
    again_r = await client.post(f"{D}/{failed[0]['id']}/retry", headers=w["h"])
    assert again_r.status_code == 404  # delivered: nothing to send again


async def test_the_test_ping(client: AsyncClient, app_sessionmaker: Sm) -> None:
    receiver, _ = install(client)
    w = await desk(client, app_sessionmaker)
    made = await hook(client, w, ["ticket.created"])
    sent = ok(await client.post(f"{W}/{made['id']}/test", headers=w["h"]))
    assert (sent["event"], sent["state"], sent["tries"]) == ("ping", "delivered", 1)
    assert receiver.bodies()[0]["event"] == "ping"
    assert receiver.bodies()[0]["webhook"] == {"id": made["id"]}
    stamp = int(receiver.got[0].headers["X-CeDesk-Timestamp"])
    assert receiver.got[0].headers["X-CeDesk-Signature"] == rules.signature(
        made["secret"], stamp, receiver.got[0].content)  # fmt: skip
    receiver.status["hooks.example.com"] = 404
    failed = ok(await client.post(f"{W}/{made['id']}/test", headers=w["h"]))
    assert (failed["state"], failed["last_status"]) == ("failed", 404)  # one try, no retry
    r = await client.post(f"{W}/{UUID(int=4)}/test", headers=w["h"])
    assert r.status_code == 404


async def test_turned_off_and_deleted_webhooks(client: AsyncClient, app_sessionmaker: Sm) -> None:
    receiver, fake = install(client)
    w = await desk(client, app_sessionmaker)
    made = await hook(client, w, ["ticket.created", "ticket.updated"])
    url = f"{W}/{made['id']}"
    ok(await client.patch(url, headers=w["h"], json={"enabled": False}))
    t = await ticket(client, w)
    await run(app_sessionmaker, fake)
    assert ok(await client.get(D, headers=w["h"]))["deliveries"] == []  # off: nothing queued
    assert t["id"] not in json.dumps(receiver.bodies())

    ok(await client.patch(url, headers=w["h"], json={"enabled": True}))
    ok(await client.patch(f"{T}/{t['id']}", headers=w["h"], json={"priority": "low"}))
    await deliver_due(app_sessionmaker, [WebhookFanout(app_sessionmaker)])  # queued...
    ok(await client.patch(url, headers=w["h"], json={"enabled": False}))  # ...then turned off
    await send_due(app_sessionmaker, SEALER, fake)
    assert t["id"] not in json.dumps(receiver.bodies())
    log = ok(await client.get(D, headers=w["h"]))["deliveries"]
    assert (log[0]["state"], log[0]["last_error"]) == ("failed", "The webhook is turned off.")

    assert (await client.delete(url, headers=w["h"])).status_code == 204
    assert ok(await client.get(D, headers=w["h"]))["deliveries"] == []  # the log went with it


async def test_one_delivery_per_event_when_handed_out_twice(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """The outbox may hand an event out again (a worker died after the hand-out, before
    marking it done): no second delivery, and no error that would keep it retrying."""
    install(client)
    w = await desk(client, app_sessionmaker)
    await hook(client, w, ["ticket.created"])
    await ticket(client, w)
    await deliver_due(app_sessionmaker, [WebhookFanout(app_sessionmaker)])
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(text("DELETE FROM hd_outbox_done"))
        await s.execute(text("UPDATE hd_outbox SET published_at = NULL, next_attempt_at = now()"))
    again = await deliver_due(app_sessionmaker, [WebhookFanout(app_sessionmaker)])
    assert again.delivered >= 1 and (again.retrying, again.failed) == (0, 0)
    assert len(ok(await client.get(D, headers=w["h"]))["deliveries"]) == 1


async def test_another_workspace_events_never_reach_a_url(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    receiver, fake = install(client)
    mine = await desk(client, app_sessionmaker)
    theirs = await desk(client, app_sessionmaker)
    await hook(client, mine, ["ticket.created"])
    their_ticket = await ticket(client, theirs)
    await run(app_sessionmaker, fake)
    # Other tests' workspaces may have deliveries of their own; none is about this ticket.
    assert their_ticket["id"] not in json.dumps(receiver.bodies())
    assert ok(await client.get(D, headers=mine["h"]))["deliveries"] == []
    assert ok(await client.get(D, headers=theirs["h"]))["deliveries"] == []


async def test_erasing_a_customer_clears_their_deliveries(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """md S11 with PL5: a delivery body has the ticket's subject (the customer's words).
    Erasing the customer clears it; one not sent yet is stopped, never sent."""
    receiver, fake = install(client)
    w = await desk(client, app_sessionmaker)
    await hook(client, w, ["ticket.created", "ticket.updated"], "https://down.example.com/x")
    receiver.status["down.example.com"] = 500
    t = await ticket(client, w, "Refund for Priya, order 1042")
    await ticket(client, w, "Someone else", "sam@example.org")
    await run(app_sessionmaker, fake)  # tried once each, failed: waiting to try again
    contact = t["contact"]["id"]
    erase = {"contact_id": contact, "confirm_email": "priya@example.org"}
    done = ok(await client.post("/api/v1/privacy/erase", headers=w["h"], json=erase), 201)
    assert done["counts"]["webhook_deliveries"] == 1

    rows = ok(await client.get(D, headers=w["h"]))["deliveries"]
    mine = [d for d in rows if d["body"] == {"erased": True}]
    assert len(mine) == 1 and mine[0]["state"] == "failed"
    assert mine[0]["last_error"] == "Stopped: the customer was erased."
    assert all(a["response"] is None for a in mine[0]["attempts"])
    assert "Refund for Priya" not in json.dumps(rows)
    assert any(d["body"].get("ticket", {}).get("subject") == "Someone else" for d in rows)

    sent = len(receiver.bodies("down.example.com"))
    await make_due(app_sessionmaker, w["ws"])
    await send_due(app_sessionmaker, SEALER, fake)
    later = receiver.bodies("down.example.com")[sent:]
    assert later and all("Refund for Priya" not in json.dumps(b) for b in later)


async def test_the_log_keeps_thirty_days(client: AsyncClient, app_sessionmaker: Sm) -> None:
    """md PL6: delivered and failed deliveries (and their tries) go after 30 days; one
    still being tried stays, however old."""
    receiver, fake = install(client)
    w = await desk(client, app_sessionmaker)
    await hook(client, w, ["ticket.created"])
    await hook(client, w, ["ticket.created"], "https://down.example.com/x")
    receiver.status["down.example.com"] = 500
    await ticket(client, w)
    await ticket(client, w, "Newer")
    await run(app_sessionmaker, fake)
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(
            text(
                "UPDATE hd_webhook_delivery SET created_at = now() - interval "
                "'31 days' WHERE body->'ticket'->>'subject' = 'Order late'"
            )
        )
    await prune_everywhere(app_sessionmaker)
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        left = (
            await s.execute(
                text("SELECT body->'ticket'->>'subject', state FROM hd_webhook_delivery")
            )
        ).all()
        tries = (await s.execute(text(
            "SELECT count(*) FROM hd_webhook_attempt a LEFT JOIN hd_webhook_delivery d "
            "ON d.id = a.delivery_id WHERE d.id IS NULL"))).scalar_one()  # fmt: skip
    assert sorted(left) == [("Newer", "delivered"), ("Newer", "pending"),
                            ("Order late", "pending")]  # fmt: skip
    assert tries == 0
