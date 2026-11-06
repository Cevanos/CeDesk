"""Sign-in, sessions and members through the real API, as `cedesk_app` with RLS on."""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cedesk.helpdesk.service.auth import SIGN_IN_FAILED, create_workspace
from cedesk.main import create_app
from cedesk.settings import Settings

pytestmark = pytest.mark.db

PASSWORD = "correct-horse-battery"
Sm = async_sessionmaker[AsyncSession]


@pytest.fixture
async def client(app_sessionmaker: Sm) -> AsyncIterator[AsyncClient]:
    app = create_app(Settings())
    app.state.sessionmaker = app_sessionmaker
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def new_workspace(sm: Sm) -> str:
    slug = f"ws-{uuid4().hex[:10]}"
    await create_workspace(
        sm,
        name="Test",
        slug=slug,
        admin_email="Admin@Example.com",
        admin_name="Admin",
        admin_password=PASSWORD,
    )
    return slug


async def sign_in(c: AsyncClient, slug: str, email: str, password: str = PASSWORD) -> str:
    r = await c.post(
        "/api/v1/auth/sign-in", json={"workspace": slug, "email": email, "password": password}
    )
    assert r.status_code == 200, r.text
    token: str = r.json()["token"]
    return token


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def add(c: AsyncClient, admin: str, email: str, role: str) -> str:
    r = await c.post(
        "/api/v1/members",
        headers=bearer(admin),
        json={"email": email, "name": email, "role": role, "password": PASSWORD},
    )
    assert r.status_code == 201, r.text
    member_id: str = r.json()["id"]
    return member_id


async def test_sign_in_and_me(client: AsyncClient, app_sessionmaker: Sm) -> None:
    slug = await new_workspace(app_sessionmaker)
    token = await sign_in(client, slug, " admin@EXAMPLE.com ")
    r = await client.get("/api/v1/me", headers=bearer(token))
    assert r.status_code == 200
    body = r.json()
    assert body["member"]["role"] == "admin"
    assert body["member"]["email"] == "admin@example.com"
    assert body["workspace"]["slug"] == slug
    assert "password_hash" not in r.text


async def test_every_sign_in_failure_looks_the_same(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    slug = await new_workspace(app_sessionmaker)
    attempts = [
        ("no-such-workspace", "admin@example.com", PASSWORD),
        (slug, "nobody@example.com", PASSWORD),
        (slug, "admin@example.com", "wrong-password-123"),
    ]
    for workspace, email, password in attempts:
        r = await client.post(
            "/api/v1/auth/sign-in",
            json={"workspace": workspace, "email": email, "password": password},
        )
        assert r.status_code == 401
        assert r.json()["error"]["message"] == SIGN_IN_FAILED


async def test_bad_or_missing_tokens_are_refused(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/me")).status_code == 401
    for token in ["junk", f"{uuid4()}.junk"]:
        r = await client.get("/api/v1/me", headers=bearer(token))
        assert r.status_code == 401
        assert r.headers["www-authenticate"] == "Bearer"


async def test_a_token_cannot_be_moved_to_another_workspace(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    slug_a = await new_workspace(app_sessionmaker)
    slug_b = await new_workspace(app_sessionmaker)
    token_a = await sign_in(client, slug_a, "admin@example.com")
    token_b = await sign_in(client, slug_b, "admin@example.com")
    ws_b = token_b.split(".", 1)[0]
    forged = f"{ws_b}.{token_a.split('.', 1)[1]}"
    assert (await client.get("/api/v1/me", headers=bearer(forged))).status_code == 401


async def test_sign_out_ends_the_session(client: AsyncClient, app_sessionmaker: Sm) -> None:
    slug = await new_workspace(app_sessionmaker)
    token = await sign_in(client, slug, "admin@example.com")
    assert (await client.post("/api/v1/auth/sign-out", headers=bearer(token))).status_code == 204
    assert (await client.get("/api/v1/me", headers=bearer(token))).status_code == 401


async def test_roles_on_member_routes(client: AsyncClient, app_sessionmaker: Sm) -> None:
    slug = await new_workspace(app_sessionmaker)
    admin = await sign_in(client, slug, "admin@example.com")
    await add(client, admin, "agent@example.com", "agent")
    await add(client, admin, "light@example.com", "light_agent")
    agent = await sign_in(client, slug, "agent@example.com")
    light = await sign_in(client, slug, "light@example.com")

    for token in (agent, light):
        r = await client.post(
            "/api/v1/members",
            headers=bearer(token),
            json={"email": "x@example.com", "name": "X", "role": "admin", "password": PASSWORD},
        )
        assert r.status_code == 403
        listed = await client.get("/api/v1/members", headers=bearer(token))
        assert listed.status_code == 200
        assert len(listed.json()["members"]) == 3

    dup = await client.post(
        "/api/v1/members",
        headers=bearer(admin),
        json={"email": "AGENT@example.com", "name": "A", "role": "agent", "password": PASSWORD},
    )
    assert dup.status_code == 409


async def test_admin_cannot_change_own_role(client: AsyncClient, app_sessionmaker: Sm) -> None:
    slug = await new_workspace(app_sessionmaker)
    admin = await sign_in(client, slug, "admin@example.com")
    me = (await client.get("/api/v1/me", headers=bearer(admin))).json()["member"]["id"]
    r = await client.patch(f"/api/v1/members/{me}", headers=bearer(admin), json={"role": "agent"})
    assert r.status_code == 403


async def test_deactivating_a_member_signs_them_out(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    slug = await new_workspace(app_sessionmaker)
    admin = await sign_in(client, slug, "admin@example.com")
    agent_id = await add(client, admin, "agent@example.com", "agent")
    agent = await sign_in(client, slug, "agent@example.com")
    r = await client.patch(
        f"/api/v1/members/{agent_id}", headers=bearer(admin), json={"is_active": False}
    )
    assert r.status_code == 200
    assert (await client.get("/api/v1/me", headers=bearer(agent))).status_code == 401
    r = await client.post(
        "/api/v1/auth/sign-in",
        json={"workspace": slug, "email": "agent@example.com", "password": PASSWORD},
    )
    assert r.status_code == 401


async def test_one_workspace_cannot_touch_another(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    slug_a = await new_workspace(app_sessionmaker)
    slug_b = await new_workspace(app_sessionmaker)
    admin_a = await sign_in(client, slug_a, "admin@example.com")
    admin_b = await sign_in(client, slug_b, "admin@example.com")
    b_agent = await add(client, admin_b, "agent@example.com", "agent")

    listed = (await client.get("/api/v1/members", headers=bearer(admin_a))).json()["members"]
    assert [m["email"] for m in listed] == ["admin@example.com"]
    r = await client.patch(
        f"/api/v1/members/{b_agent}", headers=bearer(admin_a), json={"is_active": False}
    )
    assert r.status_code == 404


async def test_ten_wrong_passwords_lock_the_account(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    slug = await new_workspace(app_sessionmaker)
    for _ in range(10):
        r = await client.post(
            "/api/v1/auth/sign-in",
            json={"workspace": slug, "email": "admin@example.com", "password": "wrong-pass-123"},
        )
        assert r.status_code == 401
    r = await client.post(
        "/api/v1/auth/sign-in",
        json={"workspace": slug, "email": "admin@example.com", "password": PASSWORD},
    )
    assert r.status_code == 401


async def test_paging_members(client: AsyncClient, app_sessionmaker: Sm) -> None:
    slug = await new_workspace(app_sessionmaker)
    admin = await sign_in(client, slug, "admin@example.com")
    for i in range(3):
        await add(client, admin, f"a{i}@example.com", "agent")
    first = (await client.get("/api/v1/members?limit=2", headers=bearer(admin))).json()
    assert len(first["members"]) == 2
    second = (
        await client.get(
            f"/api/v1/members?limit=2&after={first['next_after']}", headers=bearer(admin)
        )
    ).json()
    assert len(second["members"]) == 2
    assert second["next_after"] is None
    ids = {m["id"] for m in first["members"] + second["members"]}
    assert len(ids) == 4
