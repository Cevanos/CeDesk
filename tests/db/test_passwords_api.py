"""Changing my own password and an Admin resetting a member's, through the real API."""

import asyncio

import pytest
from httpx import AsyncClient

from cedesk.helpdesk.service.members import TOO_MANY_TRIES, WRONG_CURRENT
from tests.db.helpers import PASSWORD, Sm, add, bearer, new_workspace, sign_in

pytestmark = pytest.mark.db

NEW = "a-brand-new-password"


async def change(c: AsyncClient, token: str, current: str, new: str) -> tuple[int, str]:
    r = await c.post(
        "/api/v1/me/password",
        headers=bearer(token),
        json={"current_password": current, "new_password": new},
    )
    return r.status_code, r.json()["error"]["message"] if r.content else ""


async def try_sign_in(c: AsyncClient, slug: str, email: str, password: str) -> int:
    r = await c.post(
        "/api/v1/auth/sign-in", json={"workspace": slug, "email": email, "password": password}
    )
    return r.status_code


async def test_change_my_password(client: AsyncClient, app_sessionmaker: Sm) -> None:
    slug = await new_workspace(app_sessionmaker)
    admin = await sign_in(client, slug, "admin@example.com")
    await add(client, admin, "neha@example.com", "agent")
    here = await sign_in(client, slug, "neha@example.com")
    phone = await sign_in(client, slug, "neha@example.com")

    assert await change(client, here, PASSWORD, NEW) == (204, "")
    # This session stays; the other one ends.
    assert (await client.get("/api/v1/me", headers=bearer(here))).status_code == 200
    assert (await client.get("/api/v1/me", headers=bearer(phone))).status_code == 401
    # Only the new password works now.
    assert await try_sign_in(client, slug, "neha@example.com", PASSWORD) == 401
    assert await try_sign_in(client, slug, "neha@example.com", NEW) == 200


async def test_change_refuses_bad_input(client: AsyncClient, app_sessionmaker: Sm) -> None:
    slug = await new_workspace(app_sessionmaker)
    token = await sign_in(client, slug, "admin@example.com")
    assert await change(client, token, "wrong-password-1", NEW) == (422, WRONG_CURRENT)
    assert (await change(client, token, PASSWORD, "short"))[0] == 422
    assert (await change(client, token, PASSWORD, "x" * 129))[0] == 422
    assert (await change(client, token, PASSWORD, PASSWORD))[0] == 422
    assert (await client.post("/api/v1/me/password", json={})).status_code == 401
    # Nothing changed.
    assert await try_sign_in(client, slug, "admin@example.com", PASSWORD) == 200


async def test_wrong_current_passwords_lock_like_sign_in(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    slug = await new_workspace(app_sessionmaker)
    token = await sign_in(client, slug, "admin@example.com")
    for _ in range(10):
        assert (await change(client, token, "wrong-password-1", NEW))[0] == 422
    # Locked: even the right current password is refused, and sign-in is locked too.
    assert await change(client, token, PASSWORD, NEW) == (403, TOO_MANY_TRIES)
    assert await try_sign_in(client, slug, "admin@example.com", PASSWORD) == 401


async def test_parallel_changes_only_one_wins(client: AsyncClient, app_sessionmaker: Sm) -> None:
    slug = await new_workspace(app_sessionmaker)
    token = await sign_in(client, slug, "admin@example.com")
    results = await asyncio.gather(
        change(client, token, PASSWORD, "first-new-password"),
        change(client, token, PASSWORD, "second-new-password"),
    )
    codes = sorted(code for code, _ in results)
    # The loser sees 409 if it checked the old password before the winner saved, else 422
    # (its "current" password is no longer right). Never two winners.
    assert codes in ([204, 409], [204, 422])
    winner = "first-new-password" if results[0][0] == 204 else "second-new-password"
    assert await try_sign_in(client, slug, "admin@example.com", winner) == 200


async def test_admin_resets_a_members_password(client: AsyncClient, app_sessionmaker: Sm) -> None:
    slug = await new_workspace(app_sessionmaker)
    admin = await sign_in(client, slug, "admin@example.com")
    neha = await add(client, admin, "neha@example.com", "agent")
    neha_token = await sign_in(client, slug, "neha@example.com")
    # Neha locks herself out.
    for _ in range(10):
        await try_sign_in(client, slug, "neha@example.com", "wrong-password-1")
    assert await try_sign_in(client, slug, "neha@example.com", PASSWORD) == 401

    r = await client.post(
        f"/api/v1/members/{neha}/password", headers=bearer(admin), json={"new_password": NEW}
    )
    assert r.status_code == 204, r.text
    # Signed out everywhere, unlocked, and only the new password works.
    assert (await client.get("/api/v1/me", headers=bearer(neha_token))).status_code == 401
    assert await try_sign_in(client, slug, "neha@example.com", PASSWORD) == 401
    assert await try_sign_in(client, slug, "neha@example.com", NEW) == 200
    # The Admin stays signed in.
    assert (await client.get("/api/v1/me", headers=bearer(admin))).status_code == 200


async def test_reset_rules(client: AsyncClient, app_sessionmaker: Sm) -> None:
    slug = await new_workspace(app_sessionmaker)
    admin = await sign_in(client, slug, "admin@example.com")
    me = (await client.get("/api/v1/me", headers=bearer(admin))).json()["member"]["id"]
    neha = await add(client, admin, "neha@example.com", "agent")
    sup = await add(client, admin, "sup@example.com", "supervisor")
    sup_token = await sign_in(client, slug, "sup@example.com")

    def reset(token: str, member: str, password: str = NEW):  # type: ignore[no-untyped-def]
        return client.post(
            f"/api/v1/members/{member}/password",
            headers=bearer(token),
            json={"new_password": password},
        )

    # Only Admins.
    assert (await reset(sup_token, neha)).status_code == 403
    # Not your own: that goes through /me/password with the current one.
    assert (await reset(admin, me)).status_code == 403
    assert (await reset(admin, neha, "short")).status_code == 422
    assert (await reset(admin, "00000000-0000-0000-0000-000000000000")).status_code == 404
    # A member of another workspace is not found.
    other = await new_workspace(app_sessionmaker)
    other_admin = await sign_in(client, other, "admin@example.com")
    assert (await reset(other_admin, sup)).status_code == 404
    assert await try_sign_in(client, slug, "sup@example.com", PASSWORD) == 200
