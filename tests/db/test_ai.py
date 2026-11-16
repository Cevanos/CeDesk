"""Cevanos AI features (md section 16), against a fake Cevanos, with RLS on.

For every AI route (summary, suggested reply, improve, translate, the AI report card):
no connection is a 409 `cevanos_not_connected`; connected, Cevanos's answer comes back;
Cevanos down is a 503; a key without the scope is a 409 `cevanos_scope_missing` naming
it; card numbers never leave CeDesk; nothing is stored. Also: `GET /me` `ai`, API keys
and light agents refused where they should be, team scope, and what each sends."""

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from cedesk.cevanos_client import CevanosError, Connection, make_cevanos
from cedesk.db import workspace_session
from cedesk.helpdesk.domain.redaction import REPLACEMENT
from cedesk.main import create_app
from cedesk.settings import Settings
from tests.db.helpers import PASSWORD, Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

T = "/api/v1/tickets"
C = "/api/v1/cevanos/connection"
SECRET = "whsec_0123456789abcdef"
CARD = "4111 1111 1111 1111"  # a test card number (passes Luhn)
DOWN = CevanosError("Cevanos answered 503", permanent=False, status=503)
NO_SCOPE = CevanosError("Cevanos answered 403 insufficient_scope", permanent=True, status=403)


class FakeAi:
    """One Cevanos with one key. Records what CeDesk sends; `fail[action]` raises (every
    time, until removed)."""

    def __init__(self) -> None:
        self.key = f"cvn_sk_{uuid4().hex}"
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.fail: dict[str, CevanosError] = {}
        self.answers: dict[str, Any] = {
            "summarize": {"data": {"summary": "Priya's parcel is late; a refund was offered."}},
            "rewrite": {"data": {"text": "Thank you for waiting, Priya."}},
            "translate": {"data": {"text": "Where is my order?", "from": "hi"}},
            "draft": {"data": {"text": "Hi Priya, your parcel ships today."}},
            "analytics": {
                "data": {
                    "conversations": 200,
                    "handed_over": 30,
                    "handover_rate": 0.15,
                    "ai_resolved": 170,
                    "ai_resolved_rate": 0.85,
                    "ai_csat": 4.4,
                    "ai_csat_responses": 51,
                }
            },
        }

        # AI5: who Cevanos knows and what its stores hold
        self.by_email: dict[str, list[dict[str, Any]]] = {}
        self.chats: dict[str, dict[str, Any]] = {}
        self.stores: dict[str, dict[str, Any]] = {}

    def connect(self, connection: Connection) -> "FakeAiApi":
        return FakeAiApi(self, connection.api_key)


class FakeAiApi:
    def __init__(self, world: FakeAi, key: str) -> None:
        self.world, self.key = world, key

    def _do(self, action: str, sent: dict[str, Any]) -> Any:
        if self.key != self.world.key:
            raise CevanosError("Cevanos answered 401 unauthorized", permanent=True, status=401)
        if action in self.world.fail:
            raise self.world.fail[action]
        self.world.sent.append((action, sent))
        return self.world.answers.get(action)

    async def me(self) -> Any:
        return {"data": {"scopes": ["conversations:read", "conversations:write", "ai:assist",
                                    "contacts:read", "analytics:read"]}}  # fmt: skip

    async def summarize(self, *, text: str) -> Any:
        return self._do("summarize", {"text": text})

    async def rewrite(self, *, text: str, tone: str) -> Any:
        return self._do("rewrite", {"text": text, "tone": tone})

    async def translate(self, *, text: str, to: str) -> Any:
        return self._do("translate", {"text": text, "to": to})

    async def draft(self, *, text: str, agent_id: str | None) -> Any:
        return self._do("draft", {"text": text, "agent_id": agent_id})

    async def analytics(self, *, start: str, end: str) -> Any:
        return self._do("analytics", {"from": start, "to": end})

    async def conversation(self, conversation_id: str) -> Any:
        self._do("conversation", {"id": conversation_id})
        return {"data": self.world.chats.get(conversation_id, {"id": conversation_id})}

    async def contacts(self, *, email: str) -> Any:
        self._do("contacts", {"email": email})
        return {"data": self.world.by_email.get(email, [])}

    async def orders(self, contact_id: str) -> Any:
        self._do("orders", {"contact": contact_id})
        store = self.world.stores.get(contact_id, {})
        return {"data": {"orders": store.get("orders", []),
                         "unavailable": store.get("unavailable", [])}}  # fmt: skip

    async def payments(self, contact_id: str) -> Any:
        self._do("payments", {"contact": contact_id})
        return {"data": {"payments": self.world.stores.get(contact_id, {}).get("payments", [])}}


@pytest.fixture
async def world() -> FakeAi:
    return FakeAi()


@pytest.fixture
async def cv(app_sessionmaker: Sm, world: FakeAi) -> AsyncIterator[AsyncClient]:
    settings = Settings(sign_in_limit=100_000)
    app = create_app(settings)
    app.state.sessionmaker = app_sessionmaker
    app.state.cevanos = make_cevanos(settings, connect=world.connect)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def desk(c: AsyncClient, sm: Sm, world: FakeAi, *, connect: bool = True) -> dict[str, Any]:
    slug = await new_workspace(sm)
    token = await sign_in(c, slug, "admin@example.com")
    admin = bearer(token)
    await add(c, token, "lee@example.com", "light_agent", "Lee")
    await add(c, token, "asha@example.com", "agent", "Asha")
    w = {"slug": slug, "admin": admin, "ws": UUID(token.split(".")[0]),
         "lee": bearer(await sign_in(c, slug, "lee@example.com")),
         "asha": bearer(await sign_in(c, slug, "asha@example.com"))}  # fmt: skip
    if connect:
        ok(await c.put(C, headers=admin, json={"api_key": world.key, "webhook_secret": SECRET}))
    made = ok(await c.post(T, headers=admin, json={
        "subject": "Parcel late", "description": "Where is my parcel? Order 1042.",
        "contact": {"email": "priya@example.org", "name": "Priya"}}), 201)  # fmt: skip
    w["ticket"] = made["id"]
    ok(await c.post(f"{T}/{made['id']}/messages", headers=admin,
                    json={"kind": "note", "body": "Courier says Tuesday"}), 201)  # fmt: skip
    ok(await c.post(f"{T}/{made['id']}/messages", headers=admin,
                    json={"kind": "reply", "body": "Checking with the courier."}), 201)  # fmt: skip
    return w


def calls(w: dict[str, Any]) -> list[tuple[str, str, dict[str, Any] | None, str]]:
    """Every AI route: (method, path, body, the fake's action)."""
    t = w["ticket"]
    return [
        ("POST", f"{T}/{t}/summary", None, "summarize"),
        ("POST", f"{T}/{t}/draft", None, "draft"),
        ("POST", "/api/v1/assist/improve", {"text": "thx 4 waiting", "tone": "friendly"},
         "rewrite"),
        ("POST", "/api/v1/assist/translate", {"text": "Mera order kahan hai?", "to": "en"},
         "translate"),
        ("GET", "/api/v1/reports/ai", None, "analytics"),
    ]  # fmt: skip


async def call(c: AsyncClient, method: str, path: str, body: Any, headers: Any) -> Any:
    return await c.request(method, path, json=body, headers=headers)


def error(r: Any, status: int) -> dict[str, Any]:
    assert r.status_code == status, (r.status_code, r.text)
    return r.json()["error"]  # type: ignore[no-any-return]


async def test_without_a_connection_every_ai_route_is_a_409(cv: AsyncClient, app_sessionmaker: Sm,
                                                            world: FakeAi) -> None:  # fmt: skip
    w = await desk(cv, app_sessionmaker, world, connect=False)
    assert ok(await cv.get("/api/v1/me", headers=w["admin"]))["ai"] is False
    for method, path, body, _ in calls(w):
        found = error(await call(cv, method, path, body, w["admin"]), 409)
        assert found == {"code": "cevanos_not_connected",
                         "message": "Connect Cevanos in Settings to use this."}  # fmt: skip
    assert world.sent == []


async def test_connected_each_route_gives_cevanos_answer(cv: AsyncClient, app_sessionmaker: Sm,
                                                         world: FakeAi) -> None:  # fmt: skip
    w = await desk(cv, app_sessionmaker, world)
    h, t = w["admin"], w["ticket"]
    assert ok(await cv.get("/api/v1/me", headers=h))["ai"] is True
    assert ok(await cv.post(f"{T}/{t}/summary", headers=h)) == {
        "summary": "Priya's parcel is late; a refund was offered."}  # fmt: skip
    assert ok(await cv.post(f"{T}/{t}/draft", headers=h)) == {
        "text": "Hi Priya, your parcel ships today."}  # fmt: skip
    improved = await cv.post("/api/v1/assist/improve", headers=h,
                             json={"text": "thx 4 waiting", "tone": "friendly"})  # fmt: skip
    assert ok(improved) == {"text": "Thank you for waiting, Priya."}
    translated = await cv.post("/api/v1/assist/translate", headers=h,
                               json={"text": "Mera order kahan hai?", "to": "en"})  # fmt: skip
    assert ok(translated) == {"text": "Where is my order?", "from": "hi"}
    report = ok(await cv.get("/api/v1/reports/ai", headers=h))
    assert report["ai"] == {"conversations": 200, "handed_over": 30, "handover_rate": 0.15,
                            "ai_resolved": 170, "ai_resolved_rate": 0.85, "ai_csat": 4.4,
                            "ai_csat_responses": 51}  # fmt: skip
    sent = dict(world.sent)
    # The summary is for agents: the note goes; the draft is for the customer: it doesn't.
    assert "Internal note (Admin): Courier says Tuesday" in sent["summarize"]["text"]
    assert "Customer (Priya): Where is my parcel? Order 1042." in sent["summarize"]["text"]
    assert "Courier says Tuesday" not in sent["draft"]["text"]
    assert "Agent (Admin): Checking with the courier." in sent["draft"]["text"]
    assert sent["draft"]["agent_id"] is None  # not a Cevanos chat: Cevanos's default agent
    assert sent["rewrite"] == {"text": "thx 4 waiting", "tone": "friendly"}
    assert sent["translate"] == {"text": "Mera order kahan hai?", "to": "en"}
    assert set(sent["analytics"]) == {"from", "to"}  # the period, as ISO times


async def test_cevanos_down_is_a_503(cv: AsyncClient, app_sessionmaker: Sm, world: FakeAi) -> None:
    w = await desk(cv, app_sessionmaker, world)
    for method, path, body, action in calls(w):
        world.fail[action] = DOWN
        found = error(await call(cv, method, path, body, w["admin"]), 503)
        assert found["code"] == "Unavailable" and "Try again" in found["message"]


async def test_a_key_without_the_scope_says_which(cv: AsyncClient, app_sessionmaker: Sm,
                                                  world: FakeAi) -> None:  # fmt: skip
    w = await desk(cv, app_sessionmaker, world)
    for method, path, body, action in calls(w):
        world.fail[action] = NO_SCOPE
        found = error(await call(cv, method, path, body, w["admin"]), 409)
        scope = "analytics:read" if action == "analytics" else "ai:assist"
        assert found["code"] == "cevanos_scope_missing"
        assert f"lacks the {scope} scope" in found["message"]


async def test_card_numbers_never_leave_cedesk(cv: AsyncClient, app_sessionmaker: Sm,
                                               world: FakeAi) -> None:  # fmt: skip
    w = await desk(cv, app_sessionmaker, world)
    async with workspace_session(app_sessionmaker, w["ws"]) as s:  # as if it got past intake
        await s.execute(text(
            "INSERT INTO hd_message (workspace_id, ticket_id, kind, author_name, body) "
            "VALUES (:ws, :t, 'customer', 'Priya', :body)"),
            {"ws": w["ws"], "t": UUID(w["ticket"]), "body": f"My card {CARD} was charged"},
        )  # fmt: skip
    h, t = w["admin"], w["ticket"]
    ok(await cv.post(f"{T}/{t}/summary", headers=h))
    ok(await cv.post(f"{T}/{t}/draft", headers=h))
    ok(await cv.post("/api/v1/assist/improve", headers=h, json={"text": f"Card {CARD} refunded"}))
    ok(await cv.post("/api/v1/assist/translate", headers=h,
                     json={"text": f"Card {CARD} refunded", "to": "hi"}))  # fmt: skip
    sent = json.dumps(world.sent)
    assert "4111" not in sent and sent.count(REPLACEMENT) == 4


async def counts(sm: Sm, ws: UUID) -> dict[str, int]:
    tables = ("hd_message", "hd_outbox", "hd_audit", "hd_notification", "hd_event_log",
              "hd_ticket", "hd_contact", "hd_idempotency")  # fmt: skip
    async with workspace_session(sm, ws) as s:
        return {
            t: int((await s.execute(text(f"SELECT count(*) FROM {t}"))).scalar_one())  # noqa: S608
            for t in tables
        }


async def test_nothing_is_stored(cv: AsyncClient, app_sessionmaker: Sm, world: FakeAi) -> None:
    w = await desk(cv, app_sessionmaker, world)
    before = await counts(app_sessionmaker, w["ws"])
    for method, path, body, _ in calls(w):
        found = await call(cv, method, path, body, w["admin"])
        assert found.status_code == 200, found.text
    assert await counts(app_sessionmaker, w["ws"]) == before
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        bodies = (await s.execute(text("SELECT string_agg(body, ' ') FROM hd_message"))).scalar()
    assert "refund was offered" not in bodies and "ships today" not in bodies


async def test_who_may_use_them(cv: AsyncClient, app_sessionmaker: Sm, world: FakeAi) -> None:
    w = await desk(cv, app_sessionmaker, world)
    t = w["ticket"]
    # A light agent reads and writes notes: summary, improve and translate, not a draft.
    ok(await cv.post(f"{T}/{t}/summary", headers=w["lee"]))
    ok(await cv.post("/api/v1/assist/improve", headers=w["lee"], json={"text": "ok"}))
    assert (await cv.post(f"{T}/{t}/draft", headers=w["lee"])).status_code == 403
    # Reports need the `reports` right: an agent has none.
    assert (await cv.get("/api/v1/reports/ai", headers=w["asha"])).status_code == 403
    # An API key never spends the workspace's AI credits.
    key = ok(await cv.post("/api/v1/api-keys", headers=w["admin"],
                           json={"name": "CRM", "access": "read_write"}), 201)["key"]  # fmt: skip
    found = error(await cv.post(f"{T}/{t}/summary", headers=bearer(key)), 403)
    assert found["message"] == "AI help is for people signed in, not API keys."
    # Bad input never reaches Cevanos.
    for path, body in (("/api/v1/assist/improve", {"text": "hi", "tone": "angry"}),
                       ("/api/v1/assist/translate", {"text": "hi", "to": "English!"}),
                       ("/api/v1/assist/improve", {"text": "   "})):  # fmt: skip
        assert (await cv.post(path, headers=w["admin"], json=body)).status_code == 422
    assert (await cv.get("/api/v1/reports/ai", headers=w["admin"],
                         params={"from": "2026-11-10T00:00:00Z",
                                 "to": "2026-11-01T00:00:00Z"})).status_code == 422  # fmt: skip
    assert len(world.sent) == 2  # the light agent's summary and improve


async def test_team_scope_and_a_missing_ticket(cv: AsyncClient, app_sessionmaker: Sm,
                                               world: FakeAi) -> None:  # fmt: skip
    w = await desk(cv, app_sessionmaker, world)
    h = w["admin"]
    teams = {}
    for name in ("Billing", "Shipping"):
        made = ok(await cv.post("/api/v1/teams", headers=h,
                                json={"name": name, "auto_assign": False}), 201)  # fmt: skip
        teams[name] = made["id"]
    ok(await cv.patch(f"{T}/{w['ticket']}", headers=h, json={"team_id": teams["Billing"]}))
    only = {"name": "Shipping only", "scope": "team_only", "team_id": teams["Shipping"]}
    role = ok(await cv.post("/api/v1/roles", headers=h, json=only), 201)
    ok(await cv.post("/api/v1/members", headers=h, json={
        "email": "sam@example.com", "name": "Sam", "role": "custom", "role_id": role["id"],
        "password": PASSWORD}), 201)  # fmt: skip
    sam_h = bearer(await sign_in(cv, w["slug"], "sam@example.com"))
    for action in ("summary", "draft"):
        assert (await cv.post(f"{T}/{w['ticket']}/{action}", headers=sam_h)).status_code == 404
        assert (await cv.post(f"{T}/{uuid4()}/{action}", headers=h)).status_code == 404
    assert world.sent == []


async def test_a_chat_tickets_draft_is_written_by_its_cevanos_agent(
    cv: AsyncClient, app_sessionmaker: Sm, world: FakeAi
) -> None:
    w = await desk(cv, app_sessionmaker, world)
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(text(
            "INSERT INTO hd_cevanos_chat (workspace_id, ticket_id, conversation_id, agent_id) "
            "VALUES (:ws, :t, :conv, 'agent-77')"),
            {"ws": w["ws"], "t": UUID(w["ticket"]), "conv": f"conv-{uuid4().hex[:8]}"},
        )  # fmt: skip
    ok(await cv.post(f"{T}/{w['ticket']}/draft", headers=w["admin"]))
    assert dict(world.sent)["draft"]["agent_id"] == "agent-77"


# AI5: orders and payments from Cevanos's stores

ORDER = {"id": "o-1", "number": "#1042", "created_at": "2026-11-10T09:00:00Z",
         "payment": "paid", "fulfillment": None, "total": 1299, "currency": "inr",
         "items": 2, "url": "https://acme.myshopify.com/admin/orders/1"}  # fmt: skip
OLDER = ORDER | {"id": "o-0", "number": "#1001", "created_at": "2026-10-01T09:00:00Z"}
PAYMENT = {"id": "ch_1", "amount": "1299.00", "currency": "INR", "status": "succeeded",
           "refunded": False, "created_at": "2026-11-10T09:01:00Z",
           "description": "Order #1042", "url": None}  # fmt: skip


async def test_the_sidebar_reads_cevanos_stores(cv: AsyncClient, app_sessionmaker: Sm,
                                                world: FakeAi) -> None:  # fmt: skip
    w = await desk(cv, app_sessionmaker, world)
    h, t = w["admin"], w["ticket"]
    world.by_email["priya@example.org"] = [{"id": "c-7", "email": "priya@example.org"}]
    world.stores["c-7"] = {
        "orders": [OLDER, ORDER], "payments": [PAYMENT],
        "unavailable": [{"source": "woocommerce", "message": "timed out"}],
    }  # fmt: skip
    card = ok(await cv.get(f"{T}/{t}/orders", headers=h))
    assert (card["source"], card["available"]) == ("cevanos", True)
    assert card["message"] == "Not read from woocommerce just now."
    assert [o["number"] for o in card["orders"]] == ["#1042", "#1001"]  # newest first
    assert card["orders"][0] | {"created_at": None} == {
        "id": "o-1", "number": "#1042", "created_at": None, "payment": "paid",
        "fulfillment": None, "total": "1299.00", "currency": "INR", "items": 2,
        "url": "https://acme.myshopify.com/admin/orders/1"}  # fmt: skip
    pay = ok(await cv.get(f"{T}/{t}/payments", headers=h))
    assert (pay["source"], pay["available"], pay["message"]) == ("cevanos", True, None)
    assert [(p["id"], p["amount"], p["url"]) for p in pay["payments"]] == [
        ("ch_1", "1299.00", None)]  # fmt: skip
    assert ("contacts", {"email": "priya@example.org"}) in world.sent

    # A ticket from a Cevanos chat: that chat's contact, not a lookup by email.
    world.sent.clear()
    conversation = f"conv-{uuid4().hex[:8]}"
    world.chats[conversation] = {"id": conversation, "contact": {"id": "c-9", "name": "Ravi"}}
    world.stores["c-9"] = {"orders": [ORDER]}
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(text(
            "INSERT INTO hd_cevanos_chat (workspace_id, ticket_id, conversation_id) "
            "VALUES (:ws, :t, :conv)"),
            {"ws": w["ws"], "t": UUID(t), "conv": conversation},
        )  # fmt: skip
    card = ok(await cv.get(f"{T}/{t}/orders", headers=h))
    assert [o["id"] for o in card["orders"]] == ["o-1"]
    assert [a for a, _ in world.sent] == ["conversation", "orders"]


async def test_a_cevanos_problem_shows_on_the_card(cv: AsyncClient, app_sessionmaker: Sm,
                                                   world: FakeAi) -> None:  # fmt: skip
    w = await desk(cv, app_sessionmaker, world)
    url = f"{T}/{w['ticket']}/orders"
    card = ok(await cv.get(url, headers=w["admin"]))  # Cevanos doesn't know her
    assert (card["available"], card["message"]) == (
        False, "Cevanos has no customer with this email, so there is nothing to show.")  # fmt: skip
    world.by_email["priya@example.org"] = [{"id": "c-7"}]
    world.fail["orders"] = DOWN
    card = ok(await cv.get(url, headers=w["admin"]))
    assert card["available"] is False and "Try again" in card["message"]
    world.fail["orders"] = NO_SCOPE
    card = ok(await cv.get(url, headers=w["admin"]))
    assert card["available"] is False and "lacks the contacts:read scope" in card["message"]
    ok(await cv.get(f"{T}/{w['ticket']}", headers=w["admin"]))  # the ticket itself is fine
