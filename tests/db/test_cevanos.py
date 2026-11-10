"""Cevanos connector (md C4-C7, section 7.1), against a fake Cevanos.

Settings are Admin-only and the secrets are sealed; the webhook is refused without a good
signature; a handover becomes a ticket with the chat copied in, routed (W8) and waiting
for Accept; replies wait for Accept; Accept takes the chat over in Cevanos; replies go into
the chat; new customer messages are read by polling; close, reopen and a second handover
are synced; an agent can turn a chat into a ticket.

The test database is shared: the fake answers only this test's key, the poller reads only
this test's workspace, and the outbox pass ignores other workspaces' chats."""

import json
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from cedesk.cevanos_client import CevanosError, Connection, make_cevanos
from cedesk.db import workspace_session
from cedesk.helpdesk.domain.cevanos import SIGNATURE_HEADER, sign
from cedesk.helpdesk.service.cevanos import CevanosHandler, ChatPoller
from cedesk.helpdesk.service.delivery import deliver_due
from cedesk.main import create_app
from cedesk.settings import Settings
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

T = "/api/v1/tickets"
C = "/api/v1/cevanos/connection"
SECRET = "whsec_0123456789abcdef"


class FakeCevanos:
    """One Cevanos with one key. Records every write; `fail[action]` raises once."""

    def __init__(self) -> None:
        self.key = f"cvn_sk_{uuid4().hex}"
        self.chats: dict[str, list[dict[str, Any]]] = {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.fail: dict[str, CevanosError] = {}
        self.scopes = ["conversations:read", "conversations:write", "chat"]
        self.reads = 0

    def connect(self, connection: Connection) -> "FakeApi":
        return FakeApi(self, connection.api_key)

    def chat(self, *lines: tuple[str, str]) -> str:
        conversation = str(uuid4())
        self.chats[conversation] = []
        for role, body in lines:
            self.say(conversation, role, body)
        return conversation

    def say(self, conversation: str, role: str, body: str) -> None:
        self.chats[conversation].append(
            {"id": str(uuid4()), "role": role, "text": body, "created_at": "2026-11-10T10:00:00Z"}
        )


class FakeApi:
    def __init__(self, world: FakeCevanos, key: str) -> None:
        self.world, self.key = world, key

    def _check(self, action: str) -> None:
        if self.key != self.world.key:
            raise CevanosError("Cevanos answered 401 unauthorized", permanent=True, status=401)
        error = self.world.fail.pop(action, None)
        if error is not None:
            raise error

    async def me(self) -> Any:
        self._check("me")
        return {"data": {"workspace_id": "w", "scopes": self.world.scopes}}

    async def messages(self, conversation_id: str) -> Any:
        self._check("messages")
        self.world.reads += 1
        if conversation_id not in self.world.chats:
            raise CevanosError("Cevanos answered 404 conversation_not_found", permanent=True,
                               status=404)  # fmt: skip
        return {"data": list(self.world.chats[conversation_id])}

    async def take_over(self, conversation_id: str, *, operator_name: str, key: str) -> None:
        self._check("take_over")
        self.world.calls.append(("take_over", conversation_id, {"name": operator_name, "key": key}))

    async def reply(self, conversation_id: str, *, body: str, operator_name: str, key: str) -> None:
        self._check("reply")
        self.world.calls.append(
            ("reply", conversation_id, {"body": body, "name": operator_name, "key": key})
        )
        self.world.say(conversation_id, "operator", body)  # Cevanos shows it in the chat

    async def close(self, conversation_id: str, *, key: str) -> None:
        self._check("close")
        self.world.calls.append(("close", conversation_id, {"key": key}))


@pytest.fixture
async def world() -> FakeCevanos:
    return FakeCevanos()


@pytest.fixture
async def cv(app_sessionmaker: Sm, world: FakeCevanos) -> AsyncIterator[AsyncClient]:
    settings = Settings(sign_in_limit=100_000)
    app = create_app(settings)
    app.state.sessionmaker = app_sessionmaker
    app.state.cevanos = make_cevanos(settings, connect=world.connect)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        c.cevanos = app.state.cevanos  # type: ignore[attr-defined]
        yield c


async def desk(c: AsyncClient, sm: Sm, world: FakeCevanos, *, connect: bool = True,
               **settings: Any) -> dict[str, Any]:  # fmt: skip
    slug = await new_workspace(sm)
    token = await sign_in(c, slug, "admin@example.com")
    admin = bearer(token)
    asha = await add(c, token, "asha@example.com", "agent", "Asha")
    await add(c, token, "lee@example.com", "light_agent", "Lee")
    team = ok(await c.post("/api/v1/teams", headers=admin,
                           json={"name": "Chat", "member_ids": [asha]}), 201)  # fmt: skip
    w = {
        "slug": slug, "admin": admin, "ws": UUID(token.split(".")[0]), "team": team["id"],
        "asha_id": asha, "asha": bearer(await sign_in(c, slug, "asha@example.com")),
        "lee": bearer(await sign_in(c, slug, "lee@example.com")),
    }  # fmt: skip
    if connect:
        body = {"api_key": world.key, "webhook_secret": SECRET, "team_id": team["id"], **settings}
        ok(await c.put(C, headers=admin, json=body))
    return w


async def event(c: AsyncClient, w: dict[str, Any], name: str, data: dict[str, Any], *,
                event_id: str | None = None, secret: str = SECRET,
                at: int | None = None) -> Any:  # fmt: skip
    body = json.dumps({"event": name, "event_id": event_id or str(uuid4()),
                       "delivered_at": "2026-11-10T10:00:00Z", "data": data}).encode()  # fmt: skip
    headers = {SIGNATURE_HEADER: sign(secret, body, at or int(time.time())),
               "Content-Type": "application/json"}  # fmt: skip
    return await c.post(f"/api/v1/cevanos/events/{w['ws']}", content=body, headers=headers)


def escalated(conversation: str, reason: str = "could_not_answer") -> dict[str, Any]:
    return {"conversation_id": conversation, "agent_id": str(uuid4()),
            "channel_kind": "widget", "reason": reason, "handed_to_user_id": None,
            "seconds_since_start": 42}  # fmt: skip


async def hand_over(c: AsyncClient, w: dict[str, Any], conversation: str) -> dict[str, Any]:
    got = ok(await event(c, w, "conversation.escalated", escalated(conversation)))
    assert got["outcome"] == "created"
    return ok(await c.get(f"{T}/{got['ticket_id']}", headers=w["admin"]))  # type: ignore[no-any-return]


async def deliver(c: AsyncClient, sm: Sm) -> None:
    await deliver_due(sm, [CevanosHandler(sm, c.cevanos)])  # type: ignore[attr-defined]


def kinds(t: dict[str, Any]) -> list[tuple[str, str]]:
    return [(m["kind"], m["body"]) for m in t["messages"]]


def lines(t: dict[str, Any]) -> list[str]:
    return [m["body"] for m in t["messages"] if m["kind"] == "system"]


# Settings


async def test_connection_settings(
    cv: AsyncClient, app_sessionmaker: Sm, world: FakeCevanos
) -> None:
    w = await desk(cv, app_sessionmaker, world, connect=False)
    got = ok(await cv.get(C, headers=w["admin"]))
    assert got["connected"] is False and got["webhook_path"] == f"/api/v1/cevanos/events/{w['ws']}"
    assert (await cv.get(C, headers=w["asha"])).status_code == 403
    assert (await cv.put(C, headers=w["asha"], json={"api_key": world.key})).status_code == 403

    r = await cv.put(C, headers=w["admin"], json={"api_key": world.key})
    assert r.status_code == 422 and "webhook secret" in r.json()["error"]["message"]
    r = await cv.put(C, headers=w["admin"], json={"api_key": "sk_live_x", "webhook_secret": SECRET})
    assert r.status_code == 422
    r = await cv.put(C, headers=w["admin"], json={"api_key": world.key, "webhook_secret": "short"})
    assert r.status_code == 422
    r = await cv.put(C, headers=w["admin"], json={"api_key": world.key, "webhook_secret": SECRET,
                                                  "team_id": str(uuid4())})  # fmt: skip
    assert r.status_code == 422

    body = {"api_key": world.key, "webhook_secret": SECRET, "team_id": w["team"]}
    got = ok(await cv.put(C, headers=w["admin"], json=body))
    assert got["connected"] and got["readable"] and got["api_key_hint"] == world.key[-4:]
    assert got["team_name"] == "Chat" and got["close_on_resolve"] is False
    assert world.key not in json.dumps(got) and SECRET not in json.dumps(got)

    # Sealed in the database: neither secret is there in readable form.
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        row = (await s.execute(text("SELECT * FROM hd_cevanos_connection"))).mappings().one()
    assert world.key.encode() not in bytes(row["api_key_sealed"])
    assert SECRET.encode() not in bytes(row["webhook_secret_sealed"])

    # Later saves keep a secret that is left out.
    got = ok(await cv.put(C, headers=w["admin"], json={"close_on_resolve": True}))
    assert (
        got["close_on_resolve"] and got["api_key_hint"] == world.key[-4:] and got["team_id"] is None
    )
    assert ok(await cv.post(f"{C}/check", headers=w["admin"]))["ok"] is True
    world.scopes = ["conversations:read"]
    check = ok(await cv.post(f"{C}/check", headers=w["admin"]))
    assert check == {"ok": False, "missing_scopes": ["conversations:write"],
                     "message": "The key works but lacks conversations:write."}  # fmt: skip

    assert (await cv.delete(C, headers=w["admin"])).status_code == 204
    assert ok(await cv.get(C, headers=w["admin"]))["connected"] is False
    assert (await cv.delete(C, headers=w["admin"])).status_code == 404


async def test_a_changed_server_key_makes_the_secrets_unreadable(
    cv: AsyncClient, app_sessionmaker: Sm, world: FakeCevanos
) -> None:
    w = await desk(cv, app_sessionmaker, world)
    other = make_cevanos(Settings(secret_key="another-secret-key-of-32-characters!!"),
                         connect=world.connect)  # fmt: skip
    cv.cevanos = other  # type: ignore[attr-defined]
    app = cv._transport.app  # type: ignore[attr-defined]
    app.state.cevanos = other
    assert ok(await cv.get(C, headers=w["admin"]))["readable"] is False
    r = await event(cv, w, "conversation.escalated", escalated(world.chat(("customer", "hi"))))
    assert r.status_code == 401


# The webhook


async def test_the_webhook_needs_a_good_signature(
    cv: AsyncClient, app_sessionmaker: Sm, world: FakeCevanos
) -> None:
    w = await desk(cv, app_sessionmaker, world)
    conversation = world.chat(("customer", "Where is my order?"))
    data = escalated(conversation)
    assert (await event(cv, w, "conversation.escalated", data, secret="x" * 20)).status_code == 401
    old = int(time.time()) - 3600
    assert (await event(cv, w, "conversation.escalated", data, at=old)).status_code == 401
    r = await cv.post(f"/api/v1/cevanos/events/{w['ws']}", content=b"{}")
    assert r.status_code == 401
    r = await cv.post(f"/api/v1/cevanos/events/{uuid4()}", content=b"{}")
    assert r.status_code == 404  # no such workspace, or not connected
    big = b"x" * (256 * 1024 + 1)
    assert (await cv.post(f"/api/v1/cevanos/events/{w['ws']}", content=big)).status_code == 413
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        assert (await s.execute(text("SELECT count(*) FROM hd_ticket"))).scalar_one() == 0
    got = ok(await event(cv, w, "message.answered", {"conversation_id": conversation}))
    assert got == {"outcome": "ignored", "ticket_id": None}


async def test_a_handover_becomes_a_routed_ticket_waiting_for_accept(
    cv: AsyncClient, app_sessionmaker: Sm, world: FakeCevanos
) -> None:
    w = await desk(cv, app_sessionmaker, world)
    conversation = world.chat(
        ("customer", "Hi, my order #48213 has not arrived"),
        ("agent", "Sorry! Let me check that for you."),
        ("system", "Handed over"),
        ("customer", "I need it by Friday"),
    )
    t = await hand_over(cv, w, conversation)
    assert t["subject"] == "Hi, my order #48213 has not arrived"
    assert t["channel"] == "chat" and t["accepted"] is False
    assert t["conversation_id"] == conversation and t["source"] == "cevanos"
    assert t["handover"]["reason"] == "could_not_answer" and t["handover"]["order_id"] == "48213"
    assert t["contact"]["name"] == "Chat customer"
    assert t["contact"]["email"].endswith("@cevanos.invalid")
    assert kinds(t)[:3] == [
        ("customer", "Hi, my order #48213 has not arrived"),
        ("ai", "Sorry! Let me check that for you."),
        ("customer", "I need it by Friday"),
    ]
    # W8: the connection's team, then routing picked Asha and told her.
    assert t["team_name"] == "Chat" and t["assignee_name"] == "Asha"
    assert lines(t)[-1].startswith("Auto-assigned to Asha (Chat")
    box = ok(await cv.get("/api/v1/notifications", headers=w["asha"]))
    assert box["notifications"][0]["text"].startswith("You were auto-assigned")

    # The same event again, or a take-over inside Cevanos: no second ticket.
    again = ok(await event(cv, w, "conversation.escalated", escalated(conversation)))
    assert again == {"outcome": "duplicate", "ticket_id": t["id"]}
    echo = ok(await event(cv, w, "conversation.escalated",
                          escalated(conversation, "manual_takeover")))  # fmt: skip
    assert echo["outcome"] == "ignored"

    # The mood arrives in its own event.
    data = {"conversation_id": conversation, "sentiment": "frustrated", "topic_id": None}
    assert ok(await event(cv, w, "conversation.classified", data))["outcome"] == "updated"
    assert ok(await cv.get(f"{T}/{t['id']}", headers=w["admin"]))["sentiment"] == "negative"


async def test_reply_waits_for_accept_and_accept_takes_the_chat_over(
    cv: AsyncClient, app_sessionmaker: Sm, world: FakeCevanos
) -> None:
    w = await desk(cv, app_sessionmaker, world)
    conversation = world.chat(("customer", "Refund please"))
    t = await hand_over(cv, w, conversation)
    url = f"{T}/{t['id']}"

    r = await cv.post(f"{url}/messages", headers=w["asha"], json={"kind": "reply", "body": "Hi"})
    assert r.status_code == 409 and "Accept" in r.json()["error"]["message"]
    ok(await cv.post(f"{url}/messages", headers=w["asha"],
                     json={"kind": "note", "body": "Looking"}), 201)  # fmt: skip
    assert (await cv.post(f"{url}/accept", headers=w["lee"])).status_code == 403

    key = {"Idempotency-Key": str(uuid4())}
    t = ok(await cv.post(f"{url}/accept", headers=w["asha"] | key))
    assert t["accepted"] is True and t["assignee_name"] == "Asha"
    assert lines(t)[-1] == "Chat accepted by Asha"
    replay = await cv.post(f"{url}/accept", headers=w["asha"] | key)
    assert replay.status_code == 200 and replay.headers["Idempotent-Replayed"] == "true"
    assert (await cv.post(f"{url}/accept", headers=w["asha"])).status_code == 409

    reply = {"kind": "reply", "body": "Hi {{customer.name}}, done!"}
    ok(await cv.post(f"{url}/messages", headers=w["asha"], json=reply), 201)
    await deliver(cv, app_sessionmaker)
    mine = [x for x in world.calls if x[1] == conversation]
    assert [(a, d.get("name")) for a, _, d in mine] == [("take_over", "Asha"), ("reply", "Asha")]
    assert mine[1][2]["body"] == "Hi Chat customer, done!"
    assert mine[0][2]["key"] != mine[1][2]["key"]  # one idempotency key per effect
    await deliver(cv, app_sessionmaker)
    assert len([x for x in world.calls if x[1] == conversation]) == 2  # sent once
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        taken = await s.execute(text("SELECT taken_over_at FROM hd_cevanos_chat"))
        assert taken.scalar_one() is not None

    # Files can't go into a chat.
    with_file = {"kind": "reply", "body": "See file", "attachment_ids": [str(uuid4())]}
    r = await cv.post(f"{url}/messages", headers=w["asha"], json=with_file)
    assert r.status_code == 422


async def test_accepting_someone_elses_chat_needs_the_right_to_reassign(
    cv: AsyncClient, app_sessionmaker: Sm, world: FakeCevanos
) -> None:
    w = await desk(cv, app_sessionmaker, world)
    token = w["admin"]["Authorization"].split()[1]
    await add(cv, token, "bo@example.com", "agent", "Bo")
    bo = bearer(await sign_in(cv, w["slug"], "bo@example.com"))
    t = await hand_over(cv, w, world.chat(("customer", "Hello")))
    assert t["assignee_name"] == "Asha"
    assert (await cv.post(f"{T}/{t['id']}/accept", headers=bo)).status_code == 403
    t = ok(await cv.post(f"{T}/{t['id']}/accept", headers=w["admin"]))
    assert t["assignee_name"] == "Admin" and "Assigned to Admin by Admin" in lines(t)


async def test_customer_messages_are_read_by_polling(
    cv: AsyncClient, app_sessionmaker: Sm, world: FakeCevanos
) -> None:
    w = await desk(cv, app_sessionmaker, world)
    conversation = world.chat(("customer", "Hello"))
    t = await hand_over(cv, w, conversation)
    poller = ChatPoller(app_sessionmaker, cv.cevanos)  # type: ignore[attr-defined]

    async def poll_now() -> Any:
        async with workspace_session(app_sessionmaker, w["ws"]) as s:
            await s.execute(text("UPDATE hd_cevanos_chat SET next_poll_at = now()"))
        return await poller.poll_workspace(w["ws"])

    assert (await poll_now()).added == 0
    world.say(conversation, "customer", "Are you there?")
    world.say(conversation, "operator", "Someone in Cevanos wrote this")
    result = await poll_now()
    assert (result.read, result.added) == (1, 1)
    got = ok(await cv.get(f"{T}/{t['id']}", headers=w["admin"]))
    assert ("customer", "Are you there?") in kinds(got)
    assert ("agent", "Someone in Cevanos wrote this") not in kinds(got)
    assert (await poll_now()).added == 0  # nothing twice

    # Not due yet: not read.
    reads = world.reads
    assert (await poller.poll_workspace(w["ws"])).read == 0 and world.reads == reads

    # 429: this workspace pauses; a temporary error waits longer next time.
    world.fail["messages"] = CevanosError("Cevanos answered 429", permanent=False, status=429,
                                          retry_after=30)  # fmt: skip
    await poll_now()
    assert (await poll_now()).read == 0  # paused
    poller._paused_until.clear()
    world.fail["messages"] = CevanosError("Cevanos answered 503", permanent=False, status=503)
    assert (await poll_now()).failed == 1
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        row = (await s.execute(text("SELECT poll_failures, next_poll_at > now() AS later "
                                    "FROM hd_cevanos_chat"))).mappings().one()  # fmt: skip
    assert row["poll_failures"] == 1 and row["later"]

    # The chat is gone in Cevanos: reading stops, with a line on the ticket.
    del world.chats[conversation]
    await poll_now()
    got = ok(await cv.get(f"{T}/{t['id']}", headers=w["admin"]))
    assert lines(got)[-1].startswith("Stopped reading this chat from Cevanos: Cevanos answered 404")
    assert (await poll_now()).read == 0


async def test_polling_keeps_within_the_key_limit(
    cv: AsyncClient, app_sessionmaker: Sm, world: FakeCevanos
) -> None:
    w = await desk(cv, app_sessionmaker, world)
    for i in range(5):
        await hand_over(cv, w, world.chat(("customer", f"Chat {i}")))
    poller = ChatPoller(app_sessionmaker, cv.cevanos, batch=2, per_minute=3)  # type: ignore[attr-defined]
    assert (await poller.poll_workspace(w["ws"])).read == 2
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(text("UPDATE hd_cevanos_chat SET next_poll_at = now()"))
    assert (await poller.poll_workspace(w["ws"])).read == 1
    assert (await poller.poll_workspace(w["ws"])).read == 0  # 3 reads this minute


async def test_close_reopen_and_a_second_handover(
    cv: AsyncClient, app_sessionmaker: Sm, world: FakeCevanos
) -> None:
    w = await desk(cv, app_sessionmaker, world, close_on_resolve=True)
    conversation = world.chat(("customer", "Broken"))
    t = await hand_over(cv, w, conversation)
    url = f"{T}/{t['id']}"
    ok(await cv.post(f"{url}/accept", headers=w["asha"]))
    ok(await cv.post(f"{url}/resolve", headers=w["asha"]))
    await deliver(cv, app_sessionmaker)
    assert [a for a, c, _ in world.calls if c == conversation] == ["take_over", "close"]
    got = ok(await cv.get(url, headers=w["admin"]))
    assert lines(got)[-1] == "The chat was closed in Cevanos"
    # Cevanos then reports the close: already known.
    closed = {"conversation_id": conversation, "ended_by": "person"}
    assert ok(await event(cv, w, "conversation.closed", closed))["outcome"] == "duplicate"

    # A person reopens it in Cevanos: the ticket opens again.
    data = {"conversation_id": conversation, "reopened_by": "person", "ended_by": "person"}
    assert ok(await event(cv, w, "conversation.reopened", data))["outcome"] == "updated"
    got = ok(await cv.get(url, headers=w["admin"]))
    assert got["status"] == "open" and got["reopen_count"] == 1
    assert lines(got)[-2:] == [
        "The chat was reopened in Cevanos",
        "Status set to Open (chat reopened)",
    ]

    # It ends quietly, the customer comes back, and the AI hands it over again.
    closed = {"conversation_id": conversation, "ended_by": "quiet"}
    ok(await event(cv, w, "conversation.closed", closed))
    ok(await cv.post(f"{url}/resolve", headers=w["asha"]))
    back = {"conversation_id": conversation, "reopened_by": "customer", "ended_by": "quiet"}
    ok(await event(cv, w, "conversation.reopened", back))
    assert ok(await cv.get(url, headers=w["admin"]))["status"] == "resolved"
    again_id = str(uuid4())
    got = ok(
        await event(cv, w, "conversation.escalated", escalated(conversation), event_id=again_id)
    )
    assert got == {"outcome": "handed_over_again", "ticket_id": t["id"]}
    got = ok(await cv.get(url, headers=w["admin"]))
    assert got["status"] == "open" and got["accepted"] is False
    assert lines(got)[-1] == "Handed over again by Cevanos (could_not_answer). Accept it to reply."
    resent = await event(
        cv, w, "conversation.escalated", escalated(conversation), event_id=again_id
    )
    assert ok(resent)["outcome"] == "duplicate"
    # Accepted again: a late resend of that same event must not undo the Accept.
    ok(await cv.post(f"{url}/accept", headers=w["asha"]))
    resent = await event(
        cv, w, "conversation.escalated", escalated(conversation), event_id=again_id
    )
    assert ok(resent)["outcome"] == "duplicate"
    assert ok(await cv.get(url, headers=w["admin"]))["accepted"] is True


async def test_cevanos_down_or_refusing(
    cv: AsyncClient, app_sessionmaker: Sm, world: FakeCevanos
) -> None:
    w = await desk(cv, app_sessionmaker, world)
    conversation = world.chat(("customer", "Help"))
    world.fail["messages"] = CevanosError("Cevanos can't be reached", permanent=False)
    r = await event(cv, w, "conversation.escalated", escalated(conversation))
    assert r.status_code == 503  # Cevanos sends the event again
    # A chat the key can't read still becomes a ticket, with a line saying why.
    got = ok(await event(cv, w, "conversation.escalated", escalated("not-on-this-key")))
    t = ok(await cv.get(f"{T}/{got['ticket_id']}", headers=w["admin"]))
    assert t["subject"] == "Chat handed over from Cevanos (widget)"
    assert lines(t)[-1].startswith("The chat couldn't be read from Cevanos: Cevanos answered 404")

    # A reply Cevanos refuses for good: a line on the ticket and the Admin failed list.
    t = await hand_over(cv, w, conversation)
    ok(await cv.post(f"{T}/{t['id']}/accept", headers=w["asha"]))
    await deliver(cv, app_sessionmaker)
    world.fail["reply"] = CevanosError("Cevanos answered 409 conversation_not_ongoing",
                                       permanent=True, status=409)  # fmt: skip
    ok(await cv.post(f"{T}/{t['id']}/messages", headers=w["asha"],
                     json={"kind": "reply", "body": "Hello?"}), 201)  # fmt: skip
    await deliver(cv, app_sessionmaker)
    got = ok(await cv.get(f"{T}/{t['id']}", headers=w["admin"]))
    assert lines(got)[-1] == "Not sent to the chat: Cevanos answered 409 conversation_not_ongoing"
    failed = ok(await cv.get("/api/v1/outbox/failed", headers=w["admin"]))["events"]
    assert failed[0]["event"] == "message.created"
    assert failed[0]["last_error"].startswith("cevanos: Cevanos answered 409")


async def test_long_replies_go_in_parts(
    cv: AsyncClient, app_sessionmaker: Sm, world: FakeCevanos
) -> None:
    w = await desk(cv, app_sessionmaker, world)
    conversation = world.chat(("customer", "Explain"))
    t = await hand_over(cv, w, conversation)
    ok(await cv.post(f"{T}/{t['id']}/accept", headers=w["asha"]))
    ok(await cv.post(f"{T}/{t['id']}/messages", headers=w["asha"],
                     json={"kind": "reply", "body": "a" * 8000 + "b" * 10}), 201)  # fmt: skip
    await deliver(cv, app_sessionmaker)
    replies = [d for a, c, d in world.calls if c == conversation and a == "reply"]
    assert [len(r["body"]) for r in replies] == [8000, 10]
    assert replies[0]["key"].endswith(".1") and replies[1]["key"].endswith(".2")


async def test_an_agent_turns_a_chat_into_a_ticket(
    cv: AsyncClient, app_sessionmaker: Sm, world: FakeCevanos
) -> None:
    w = await desk(cv, app_sessionmaker, world)
    conversation = world.chat(
        ("customer", "Can I change my address?"),
        ("operator", "Yes, which one?"),
    )
    url = f"{T}/from-conversation/{conversation}"
    t = ok(await cv.post(url, headers=w["asha"]), 201)
    assert t["accepted"] is False and t["handover"]["reason"] == "converted_by_agent"
    assert ("agent", "Yes, which one?") in kinds(t) and t["first_reply_at"] is not None
    r = await cv.post(url, headers=w["asha"])
    assert r.status_code == 409 and t["label"] in r.json()["error"]["message"]
    assert (await cv.post(f"{T}/from-conversation/{uuid4()}", headers=w["asha"])).status_code == 404
    assert (await cv.post(f"{T}/from-conversation/bad%20id", headers=w["asha"])).status_code == 422

    other = await desk(cv, app_sessionmaker, world, connect=False)
    r = await cv.post(
        f"{T}/from-conversation/{world.chat(('customer', 'x'))}", headers=other["asha"]
    )
    assert r.status_code == 404 and "isn't connected" in r.json()["error"]["message"]


async def test_not_connected_changes_nothing(
    cv: AsyncClient, app_sessionmaker: Sm, world: FakeCevanos
) -> None:
    """A workspace without Cevanos: its events pass through the handler untouched."""
    w = await desk(cv, app_sessionmaker, world, connect=False)
    body = {"subject": "Email ticket", "contact": {"email": "ana@shop.test", "name": "Ana"}}
    t = ok(await cv.post(T, headers=w["admin"], json=body), 201)
    ok(await cv.post(f"{T}/{t['id']}/messages", headers=w["admin"],
                     json={"kind": "reply", "body": "Hi"}), 201)  # fmt: skip
    await deliver(cv, app_sessionmaker)
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        result = await s.execute(text("SELECT count(*) FROM hd_outbox WHERE published_at IS NULL"))
        assert result.scalar_one() == 0
    assert (await cv.post(f"{T}/{t['id']}/accept", headers=w["admin"])).status_code == 409
    poller = ChatPoller(app_sessionmaker, cv.cevanos)  # type: ignore[attr-defined]
    assert (await poller.poll_workspace(w["ws"])).read == 0
