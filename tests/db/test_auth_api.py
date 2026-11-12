"""Sign-in, sessions and members through the real API, as `cedesk_app` with RLS on."""

import asyncio
import threading
import time
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.service import passwords
from cedesk.helpdesk.service.auth import SIGN_IN_FAILED
from tests.db.helpers import PASSWORD, Sm, add, bearer, new_workspace, sign_in

pytestmark = pytest.mark.db


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


async def test_wrong_passwords_in_parallel_all_count(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    slug = await new_workspace(app_sessionmaker)
    wrong = {"workspace": slug, "email": "admin@example.com", "password": "wrong-pass-123"}
    tries = await asyncio.gather(
        *(client.post("/api/v1/auth/sign-in", json=wrong) for _ in range(10))
    )
    assert {r.status_code for r in tries} == {401}
    right = wrong | {"password": PASSWORD}
    assert (await client.post("/api/v1/auth/sign-in", json=right)).status_code == 401  # locked


async def test_password_check_holds_no_lock_and_does_not_block(
    client: AsyncClient, app_sessionmaker: Sm, monkeypatch: pytest.MonkeyPatch
) -> None:
    """While a (slow) password check runs, the member's row can be written and other
    requests are answered."""
    slug = await new_workspace(app_sessionmaker)
    token = await sign_in(client, slug, "admin@example.com")
    workspace_id = UUID(token.split(".")[0])
    me = (await client.get("/api/v1/me", headers=bearer(token))).json()["member"]["id"]
    # The app's first /healthz pays a one-time setup cost; keep it out of the timing below.
    assert (await client.get("/healthz")).status_code == 200

    real_verify = passwords.verify
    started = threading.Event()

    def slow_verify(password_hash: str | None, password: str) -> tuple[bool, str | None]:
        started.set()
        time.sleep(1.5)
        return real_verify(password_hash, password)

    monkeypatch.setattr(passwords, "verify", slow_verify)
    body = {"workspace": slug, "email": "admin@example.com", "password": "wrong-pass-123"}
    attempt = asyncio.create_task(client.post("/api/v1/auth/sign-in", json=body))
    assert await asyncio.to_thread(started.wait, 5)

    t0 = time.perf_counter()
    assert (await client.get("/healthz")).status_code == 200
    async with workspace_session(app_sessionmaker, workspace_id) as s:
        await s.execute(text("SET LOCAL lock_timeout = '200ms'"))
        await s.execute(text("SELECT 1 FROM member WHERE id = :id FOR UPDATE"), {"id": me})
    assert time.perf_counter() - t0 < 1.0  # neither waited for the 1.5 s password check
    assert not attempt.done()  # ... which is still running
    assert (await attempt).status_code == 401
