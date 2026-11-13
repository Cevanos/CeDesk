"""Side conversations in Slack (md X5, the Slack half), with RLS on and a fake Slack: the
app's install (bot token and signing secret, checked, never shown or audited), starting
one in a channel, writing again in the thread, a signed reply coming back to the right
side conversation (once), the signature rules, and tenancy."""

import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs
from uuid import UUID

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.service import side_conversations as sides
from cedesk.helpdesk.service.app_post import AppPost
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

A = "/api/v1/apps/slack"
T = "/api/v1/tickets"
TOKEN = "xoxb-1111-2222-abcdefgh"
SIGNING = "8f742231b10e8888abcd99yyyzzz85a5"
CHANNEL = "C0WAREHOUSE"


class FakeSlack:
    """slack.com's Web API: auth.test, chat.postMessage, users.info."""

    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if request.url.host != "slack.com":
            return httpx.Response(404)
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return httpx.Response(200, json={"ok": False, "error": "invalid_auth"})
        path = request.url.path
        if path == "/api/auth.test":
            return httpx.Response(200, json={"ok": True, "team": "Acme"})
        if path == "/api/chat.postMessage":
            body = json.loads(request.content)
            if body["channel"] != "#warehouse" and body["channel"] != CHANNEL:
                return httpx.Response(200, json={"ok": False, "error": "not_in_channel"})
            self.posts.append(body)
            ts = f"1700000000.{len(self.posts):06d}"
            return httpx.Response(200, json={"ok": True, "channel": CHANNEL, "ts": ts})
        if path == "/api/users.info":
            user = parse_qs(request.url.query.decode())["user"][0]
            if user == "U0RAVI":
                return httpx.Response(200, json={"ok": True, "user": {
                    "name": "ravi", "profile": {"display_name": "Ravi (warehouse)"}}})  # fmt: skip
            return httpx.Response(200, json={"ok": False, "error": "user_not_found"})
        return httpx.Response(404)


def install_fake(client: AsyncClient) -> FakeSlack:
    fake = FakeSlack()

    def post(host: str, headers: Mapping[str, str], label: str) -> AppPost:
        async def public(name: str) -> None:
            return None

        return AppPost(host, headers=headers, label=label, guard=public,
                       transport=httpx.MockTransport(fake))  # fmt: skip

    client._transport.app.state.app_post = post  # type: ignore[attr-defined]
    return fake


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    slug = await new_workspace(sm)
    token = await sign_in(client, slug, "admin@example.com")
    h = bearer(token)
    neha = await add(client, token, "neha@example.com", "agent", "Neha")
    meena = await add(client, token, "meena@example.com", "light_agent", "Meena")
    me = ok(await client.get("/api/v1/me", headers=h))
    return {"slug": slug, "h": h, "ws": UUID(me["workspace"]["id"]), "neha_id": neha,
            "meena_id": meena,
            "neha": bearer(await sign_in(client, slug, "neha@example.com")),
            "meena": bearer(await sign_in(client, slug, "meena@example.com"))}  # fmt: skip


async def install(client: AsyncClient, w: dict[str, Any], signing: str = SIGNING) -> Any:
    body = {"secret": TOKEN, "signing_secret": signing}
    return ok(await client.put(A, headers=w["h"], json=body))


async def ticket(client: AsyncClient, w: dict[str, Any]) -> str:
    body = {"subject": "Parcel stuck", "description": "My card was charged twice",
            "contact": {"email": "priya@example.org", "name": "Priya"},
            "assignee_id": w["neha_id"]}  # fmt: skip
    return str(ok(await client.post(T, headers=w["h"], json=body), 201)["id"])


def signed(body: bytes, secret: str = SIGNING, at: int | None = None) -> dict[str, str]:
    stamp = str(at if at is not None else int(time.time()))
    mac = hmac.new(secret.encode(), b"v0:" + stamp.encode() + b":" + body, hashlib.sha256)
    return {"X-Slack-Request-Timestamp": stamp, "X-Slack-Signature": f"v0={mac.hexdigest()}",
            "Content-Type": "application/json"}  # fmt: skip


def reply_event(thread_ts: str, ts: str, text: str = "Shipped today &amp; tracking sent",
                **extra: Any) -> bytes:  # fmt: skip
    event = {"type": "message", "channel": CHANNEL, "thread_ts": thread_ts, "ts": ts,
             "user": "U0RAVI", "text": text, **extra}  # fmt: skip
    return json.dumps({"type": "event_callback", "event": event}).encode()


async def side_rows(sm: Sm, ws: UUID) -> list[Any]:
    async with workspace_session(sm, ws) as s:
        got = await s.execute(text(
            "SELECT m.direction, m.body, m.author_name FROM hd_side_message m "
            "ORDER BY m.created_at, m.id"))  # fmt: skip
        return list(got.mappings().all())


async def test_installing_the_slack_app(client: AsyncClient, app_sessionmaker: Sm) -> None:
    install_fake(client)
    w = await desk(client, app_sessionmaker)
    for body in ({"secret": "not-a-token", "signing_secret": SIGNING},
                 {"secret": TOKEN},
                 {"secret": TOKEN, "signing_secret": "short"},
                 {"secret": "xoxb-9999-wrong-token", "signing_secret": SIGNING}):  # fmt: skip
        assert (await client.put(A, headers=w["h"], json=body)).status_code == 422
    assert (await client.put(A, headers=w["neha"], json={"secret": TOKEN,
            "signing_secret": SIGNING})).status_code == 403  # fmt: skip

    got = await install(client, w)
    assert (got["installed"], got["secret_hint"], got["card"]) == (True, "…efgh", "side")
    assert got["second_credential"] == "signing_secret"
    shown = json.dumps(ok(await client.get("/api/v1/apps", headers=w["h"])))
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        audited = json.dumps([dict(r) for r in (await s.execute(text(
            "SELECT detail FROM hd_audit WHERE area = 'apps'"))).mappings().all()])  # fmt: skip
    for secret in (TOKEN, SIGNING):
        assert secret not in shown and secret not in audited  # never shown, never audited


async def test_a_side_conversation_in_slack(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    fake = install_fake(client)
    w = await desk(client, sm)
    t = await ticket(client, w)
    url = f"{T}/{t}/side-conversations/slack"
    start = {"channel": "#Warehouse", "body": "Can you check where parcel #1077 is?"}
    assert (await client.post(url, headers=w["neha"], json=start)).status_code == 409  # no app
    await install(client, w)
    assert (await client.post(url, headers=w["meena"], json=start)).status_code == 403
    r = await client.post(url, headers=w["neha"], json=start | {"channel": "#nobot"})
    assert r.status_code == 422 and "isn't in #nobot" in r.json()["error"]["message"]

    side = ok(await client.post(url, headers=w["neha"], json=start), 201)
    assert (side["via"], side["to"], side["from_address"]) == ("slack", ["#warehouse"], "")
    posted = fake.posts[-1]
    assert (
        posted["channel"] == "#warehouse" and "Can you check where parcel #1077" in posted["text"]
    )
    assert "Parcel stuck" in posted["text"] and "charged twice" not in posted["text"]
    first_ts = "1700000000.000001"

    # Ravi answers in the thread: signed, matched, saved once, Neha is told.
    event = reply_event(first_ts, "1700000050.000200")
    events = f"/api/v1/slack/events/{w['ws']}"
    for _ in range(2):  # Slack sends it again: saved once
        assert ok(await client.post(events, content=event, headers=signed(event))) == {}
    rows = await side_rows(sm, w["ws"])
    assert [(r["direction"], r["author_name"]) for r in rows] == [
        ("out", "Neha"), ("in", "Ravi (warehouse)")]  # fmt: skip
    assert rows[1]["body"] == "Shipped today & tracking sent"
    detail = ok(await client.get(f"{T}/{t}", headers=w["h"]))
    lines = [m["body"] for m in detail["messages"] if m["kind"] == "system"]
    assert "Side conversation in Slack #warehouse started by Neha" in lines
    assert "Ravi (warehouse) replied in the side conversation in Slack #warehouse" in lines
    assert not any("Shipped today" in m["body"] for m in detail["messages"])  # not the ticket's
    notes = ok(await client.get("/api/v1/notifications", headers=w["neha"]))
    assert [n["kind"] for n in notes["notifications"]].count("side_reply") == 1

    # Ignored: a bot's message (ours too), an edit, a top-level message, another thread.
    for ignored in (reply_event(first_ts, "1700000060.1", bot_id="B01"),
                    reply_event(first_ts, "1700000061.1", subtype="message_changed"),
                    reply_event("1700000062.1", "1700000062.1"),
                    reply_event("1699999999.000001", "1700000063.1")):  # fmt: skip
        assert ok(await client.post(events, content=ignored, headers=signed(ignored))) == {}
    assert len(await side_rows(sm, w["ws"])) == 2

    # Slack's URL check gets its challenge back.
    check = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
    assert ok(await client.post(events, content=check, headers=signed(check))) == {
        "challenge": "abc123"
    }

    # Writing again goes in the same thread.
    more = f"/api/v1/side-conversations/{side['id']}/messages"
    again = ok(await client.post(more, headers=w["neha"], json={"body": "Thanks Ravi!"}), 201)
    assert fake.posts[-1]["thread_ts"] == first_ts and "Thanks Ravi!" in fake.posts[-1]["text"]
    assert [m["direction"] for m in again["messages"]] == ["out", "in", "out"]


async def test_the_signature_is_checked_first(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    install_fake(client)
    w = await desk(client, sm)
    t = await ticket(client, w)
    events = f"/api/v1/slack/events/{w['ws']}"
    event = reply_event("1700000000.000001", "1700000050.000200")
    assert (await client.post(events, content=event, headers=signed(event))).status_code == 401
    await install(client, w)
    start = {"channel": "#warehouse", "body": "Where is it?"}
    ok(await client.post(f"{T}/{t}/side-conversations/slack", headers=w["neha"], json=start), 201)

    other = event.replace(b"Shipped", b"Stolen")
    bad = [
        {},  # no signature at all
        signed(event, secret="0" * 32),  # another secret
        signed(other),  # signed for another body
        signed(event, at=int(time.time()) - 301),  # older than 5 minutes: a replay
        {**signed(event), "X-Slack-Signature": "v0=" + "0" * 64},
    ]
    for headers in bad:
        r = await client.post(events, content=event, headers=headers)
        assert r.status_code == 401, headers
    assert len(await side_rows(sm, w["ws"])) == 1  # only our first message
    assert ok(await client.post(events, content=event, headers=signed(event))) == {}
    assert len(await side_rows(sm, w["ws"])) == 2


async def test_another_workspace_cant_reach_it(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    install_fake(client)
    a = await desk(client, sm)
    b = await desk(client, sm)
    await install(client, a)
    await install(client, b, signing="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
    t = await ticket(client, a)
    side = ok(await client.post(f"{T}/{t}/side-conversations/slack", headers=a["neha"],
                                json={"channel": "#warehouse", "body": "Where?"}), 201)  # fmt: skip
    event = reply_event("1700000000.000001", "1700000050.000200")
    b_signed = signed(event, secret="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
    # B's signature on A's URL: refused. On B's own URL: A's thread isn't found there.
    r = await client.post(f"/api/v1/slack/events/{a['ws']}", content=event, headers=b_signed)
    assert r.status_code == 401
    r = await client.post(f"/api/v1/slack/events/{b['ws']}", content=event, headers=b_signed)
    assert r.status_code == 200
    assert len(await side_rows(sm, a["ws"])) == 1 and await side_rows(sm, b["ws"]) == []
    # B's people can't see or write in A's side conversation.
    r = await client.post(f"/api/v1/side-conversations/{side['id']}/messages", headers=b["neha"],
                          json={"body": "hi"})  # fmt: skip
    assert r.status_code == 404


async def test_a_team_only_role_and_email_headers(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    fake = install_fake(client)
    w = await desk(client, sm)
    await install(client, w)
    t = await ticket(client, w)
    url = f"{T}/{t}/side-conversations/slack"
    start = {"channel": "#warehouse", "body": "Where?"}
    first = ok(await client.post(url, headers=w["neha"], json=start), 201)
    assert first["via"] == "slack" and len(fake.posts) == 1

    # A team-only role can't start one on another team's ticket: nothing reaches Slack.
    billing = {"name": "Billing", "auto_assign": False}
    team = ok(await client.post("/api/v1/teams", headers=w["h"], json=billing), 201)["id"]
    role = ok(await client.post("/api/v1/roles", headers=w["h"], json={
        "name": "Billing only", "scope": "team_only", "team_id": team}), 201)  # fmt: skip
    ok(await client.patch(f"/api/v1/members/{w['meena_id']}", headers=w["h"],
                          json={"role": "custom", "role_id": role["id"]}))  # fmt: skip
    meena = bearer(await sign_in(client, w["slug"], "meena@example.com"))
    r = await client.post(url, headers=meena, json=start)
    assert r.status_code in (403, 404) and len(fake.posts) == 1

    # An email can never attach itself to a Slack thread, whatever its headers say.
    key = "slack:C0WAREHOUSE:1700000000.000001"
    for header in (key, f"<{key}>"):
        got = await sides.receive_reply(
            sm, w["ws"], thread_ids=[header.strip("<>"), header], message_id="<x@evil.test>",
            from_address="x@evil.test", from_name="X", text="Hijack",
        )  # fmt: skip
        assert got is None
    assert len(await side_rows(sm, w["ws"])) == 1
