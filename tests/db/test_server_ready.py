"""CORS, the sign-in attempt limit and the demo workspace, through the real app."""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from cedesk.demo import seed_demo
from cedesk.main import create_app
from cedesk.settings import Settings
from tests.db.helpers import PASSWORD, Sm, bearer, new_workspace, sign_in

pytestmark = pytest.mark.db
FRONTEND = "http://localhost:3000"


async def make_client(sm: Sm, **settings: object) -> AsyncIterator[AsyncClient]:
    app = create_app(Settings(**settings))  # type: ignore[arg-type]
    app.state.sessionmaker = sm
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


@pytest.fixture
async def cors_client(app_sessionmaker: Sm) -> AsyncIterator[AsyncClient]:
    async for c in make_client(app_sessionmaker, cors_origins=FRONTEND):
        yield c


async def test_cors_lets_the_frontend_call_the_api(
    cors_client: AsyncClient, app_sessionmaker: Sm
) -> None:
    preflight = await cors_client.options(
        "/api/v1/tickets/x",
        headers={
            "Origin": FRONTEND,
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "authorization,if-match,content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == FRONTEND
    # Creates send an Idempotency-Key (md 2B); a browser asks first, and a refusal
    # here would block every such call from the frontend.
    preflight = await cors_client.options(
        "/api/v1/tickets",
        headers={
            "Origin": FRONTEND,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,idempotency-key",
        },
    )
    assert preflight.status_code == 200, preflight.text
    slug = await new_workspace(app_sessionmaker)
    token = await sign_in(cors_client, slug, "admin@example.com")
    r = await cors_client.get("/api/v1/me", headers={**bearer(token), "Origin": FRONTEND})
    assert r.headers["access-control-allow-origin"] == FRONTEND
    exposed = r.headers["access-control-expose-headers"].lower()
    assert "etag" in exposed and "idempotent-replayed" in exposed
    # Errors carry the header too, so the frontend can read the message.
    r = await cors_client.get("/api/v1/me", headers={"Origin": FRONTEND})
    assert r.status_code == 401 and r.headers["access-control-allow-origin"] == FRONTEND


async def test_cors_refuses_other_sites(cors_client: AsyncClient) -> None:
    r = await cors_client.options(
        "/api/v1/me",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in r.headers
    r = await cors_client.get("/healthz", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


async def test_no_cors_by_default(client: AsyncClient) -> None:
    r = await client.get("/healthz", headers={"Origin": FRONTEND})
    assert "access-control-allow-origin" not in r.headers


async def test_sign_in_attempts_are_limited_per_address(app_sessionmaker: Sm) -> None:
    slug = await new_workspace(app_sessionmaker)
    async for c in make_client(app_sessionmaker, sign_in_limit=3):
        body = {"workspace": slug, "email": "admin@example.com", "password": "wrong-password-1"}
        for _ in range(3):
            assert (await c.post("/api/v1/auth/sign-in", json=body)).status_code == 401
        r = await c.post("/api/v1/auth/sign-in", json={**body, "password": PASSWORD})
        assert r.status_code == 429
        assert r.json()["error"]["code"] == "TooManyAttempts"
        assert int(r.headers["retry-after"]) > 0
        # Other routes are not limited.
        assert (await c.get("/healthz")).status_code == 200


async def test_demo_workspace(client: AsyncClient, app_sessionmaker: Sm) -> None:
    slug = f"demo-{uuid4().hex[:8]}"
    accounts = await seed_demo(app_sessionmaker, slug=slug, name="Acme Store", password=PASSWORD)
    assert accounts[0] == ("Alex", "alex@acme-store.in", "admin")
    assert len(accounts) == 7
    admin = await sign_in(client, slug, "alex@acme-store.in")
    page = (await client.get("/api/v1/tickets", headers=bearer(admin))).json()
    by_subject = {t["subject"]: t for t in page["tickets"]}
    assert len(by_subject) == 6
    rows = (await client.get("/api/v1/agents", headers=bearer(admin))).json()
    agents = {a["id"]: a for a in rows}
    refund = by_subject["Refund for damaged T-shirt (order #1042)"]
    assert agents[refund["assignee_id"]]["name"] == "Sam Rao"  # skill refunds
    statuses = {a["name"]: a["status"] for a in agents.values()}
    assert statuses["Sam Rao"] == "on_break" and statuses["Morgan"] == "offline"
    # Jordan's @mention reached Sam.
    sam = await sign_in(client, slug, "sam@acme-store.in")
    inbox = (await client.get("/api/v1/notifications", headers=bearer(sam))).json()
    assert any(n["kind"] == "mention" for n in inbox["notifications"])
    # Running it again for the same address fails cleanly.
    with pytest.raises(Exception, match="taken"):
        await seed_demo(app_sessionmaker, slug=slug, name="Acme Store", password=PASSWORD)
