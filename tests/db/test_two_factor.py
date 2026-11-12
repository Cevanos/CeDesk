"""Two-step sign-in (md S2) through the real API with RLS on."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import two_factor as totp
from tests.db.helpers import PASSWORD, Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    slug = await new_workspace(sm)
    token = await sign_in(client, slug, "admin@example.com")
    h = bearer(token)
    neha = await add(client, token, "neha@example.com", "agent", "Neha")
    me = ok(await client.get("/api/v1/me", headers=h))
    return {
        "slug": slug,
        "h": h,
        "neha": neha,
        "ws": UUID(me["workspace"]["id"]),
        "admin": me["member"]["id"],
    }


def code(secret: str, ahead: int = 0) -> str:
    return totp.code_at(secret, totp.step_of(datetime.now(UTC)) + ahead)


async def sql(sm: Sm, w: dict[str, Any], query: str, **params: Any) -> Any:
    async with workspace_session(sm, w["ws"]) as s:
        result = await s.execute(text(query), params)
        return result.mappings().all() if result.returns_rows else None


async def forget_steps(sm: Sm, w: dict[str, Any]) -> None:
    """Lets the current step's code be used again (tests run faster than 30 seconds)."""
    await sql(sm, w, "UPDATE hd_member_2fa SET last_step = 0")


async def password_step(client: AsyncClient, w: dict[str, Any], email: str) -> Any:
    return ok(
        await client.post(
            "/api/v1/auth/sign-in",
            json={"workspace": w["slug"], "email": email, "password": PASSWORD},
        )
    )


async def turn_on(client: AsyncClient, h: dict[str, str]) -> tuple[str, list[str]]:
    setup = ok(await client.post("/api/v1/me/2fa/setup", headers=h))
    codes = ok(
        await client.post("/api/v1/me/2fa/confirm", headers=h, json={"code": code(setup["secret"])})
    )
    return setup["secret"], codes["recovery_codes"]


async def test_setting_up_the_app(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    neha = bearer(await sign_in(client, w["slug"], "neha@example.com"))
    other = bearer(await sign_in(client, w["slug"], "neha@example.com"))
    assert ok(await client.get("/api/v1/me/2fa", headers=neha)) == {
        "enabled": False,
        "required": False,
        "recovery_codes_left": 0,
    }

    setup = ok(await client.post("/api/v1/me/2fa/setup", headers=neha))
    assert setup["otpauth_uri"].startswith("otpauth://totp/CeDesk%3Aneha%40example.com")
    assert f"secret={setup['secret']}" in setup["otpauth_uri"]
    wrong = await client.post("/api/v1/me/2fa/confirm", headers=neha, json={"code": "000000"})
    assert wrong.status_code == 422
    codes = ok(
        await client.post(
            "/api/v1/me/2fa/confirm", headers=neha, json={"code": code(setup["secret"])}
        )
    )["recovery_codes"]
    assert len(set(codes)) == 10 and all(len(c) == 11 and c[5] == "-" for c in codes)
    assert ok(await client.get("/api/v1/me/2fa", headers=neha))["recovery_codes_left"] == 10
    assert (await client.post("/api/v1/me/2fa/setup", headers=neha)).status_code == 409
    # Signed in elsewhere without the second step: that session ends.
    assert (await client.get("/api/v1/me", headers=other)).status_code == 401
    members = ok(await client.get("/api/v1/members", headers=w["h"]))["members"]
    assert {m["email"]: m["two_factor"] for m in members} == {
        "admin@example.com": False,
        "neha@example.com": True,
    }
    # Kept sealed, never as the plain secret.
    stored = await sql(app_sessionmaker, w, "SELECT secret_sealed FROM hd_member_2fa")
    assert setup["secret"].encode() not in bytes(stored[0]["secret_sealed"])


async def test_signing_in_needs_the_code(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    secret, recovery = await turn_on(
        client, bearer(await sign_in(client, w["slug"], "neha@example.com"))
    )
    first = await password_step(client, w, "neha@example.com")
    assert first["two_factor_required"] is True
    left = datetime.fromisoformat(first["expires_at"]) - datetime.now(UTC)
    assert left.total_seconds() <= 600
    pending = bearer(first["token"])
    for path in ("/api/v1/tickets", "/api/v1/me"):
        r = await client.get(path, headers=pending)
        assert (r.status_code, r.json()["error"]["code"]) == (403, "TwoFactorRequired")
    used = (await sql(app_sessionmaker, w, "SELECT last_step FROM hd_member_2fa"))[0]
    replay = totp.code_at(secret, int(used["last_step"]))
    r = await client.post("/api/v1/auth/2fa", headers=pending, json={"code": replay})
    assert r.status_code == 422  # this step was used to confirm: no replay
    done = ok(
        await client.post("/api/v1/auth/2fa", headers=pending, json={"code": code(secret, 1)})
    )
    left = datetime.fromisoformat(done["expires_at"]) - datetime.now(UTC)
    assert 11.9 * 3600 < left.total_seconds() <= 12 * 3600
    ok(await client.get("/api/v1/tickets", headers=pending))
    assert (
        await client.post("/api/v1/auth/2fa", headers=pending, json={"code": code(secret)})
    ).status_code == 409

    # A recovery code works once.
    for expected in (200, 422):
        token = bearer((await password_step(client, w, "neha@example.com"))["token"])
        r = await client.post(
            "/api/v1/auth/2fa", headers=token, json={"code": recovery[0].lower().replace("-", " ")}
        )
        assert r.status_code == expected
    neha = bearer((await password_step(client, w, "neha@example.com"))["token"])
    await forget_steps(app_sessionmaker, w)
    ok(await client.post("/api/v1/auth/2fa", headers=neha, json={"code": code(secret)}))
    assert ok(await client.get("/api/v1/me/2fa", headers=neha))["recovery_codes_left"] == 9


async def test_wrong_codes_lock_the_member_out(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    secret, _ = await turn_on(client, bearer(await sign_in(client, w["slug"], "neha@example.com")))
    pending = bearer((await password_step(client, w, "neha@example.com"))["token"])
    for _ in range(10):
        r = await client.post("/api/v1/auth/2fa", headers=pending, json={"code": "123456"})
        assert r.status_code == 422
    r = await client.post("/api/v1/auth/2fa", headers=pending, json={"code": code(secret, 1)})
    assert (r.status_code, "Too many" in r.json()["error"]["message"]) == (403, True)


async def test_the_workspace_can_require_it(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    neha = bearer(await sign_in(client, w["slug"], "neha@example.com"))
    r = await client.put("/api/v1/security-settings", headers=w["h"], json={"require_2fa": True})
    assert (r.status_code, "for yourself first" in r.json()["error"]["message"]) == (422, True)
    await turn_on(client, w["h"])  # this session stays signed in
    ok(await client.put("/api/v1/security-settings", headers=w["h"], json={"require_2fa": True}))

    # Neha's open session can now only set up 2FA.
    r = await client.get("/api/v1/tickets", headers=neha)
    assert (r.status_code, r.json()["error"]["code"]) == (403, "TwoFactorSetupRequired")
    assert ok(await client.get("/api/v1/me", headers=neha))["two_factor_setup_required"] is True
    secret, _ = await turn_on(client, neha)
    ok(await client.get("/api/v1/tickets", headers=neha))
    r = await client.post("/api/v1/me/2fa/disable", headers=neha, json={"code": code(secret, 1)})
    assert r.status_code == 403  # required, so it stays on

    score = ok(await client.get("/api/v1/security/score", headers=w["h"]))
    assert next(i for i in score["items"] if i["key"] == "two_factor")["on"] is True
    alerts = await sql(
        app_sessionmaker,
        w,
        "SELECT payload->>'what' AS what FROM hd_outbox "
        "WHERE event = 'security.alert' ORDER BY occurred_at",
    )
    assert alerts[-1]["what"] == "Two-step sign-in is now required for every member."


async def test_admin_reset_and_turning_it_off(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    neha = bearer(await sign_in(client, w["slug"], "neha@example.com"))
    reset = f"/api/v1/members/{w['neha']}/2fa/reset"
    assert (await client.post(reset, headers=w["h"])).status_code == 409  # not on
    secret, _ = await turn_on(client, neha)
    assert (await client.post(reset, headers=neha)).status_code == 403  # not an Admin
    own = f"/api/v1/members/{w['admin']}/2fa/reset"
    assert (await client.post(own, headers=w["h"])).status_code == 403
    assert (await client.post(reset, headers=w["h"])).status_code == 204
    assert (await client.get("/api/v1/me", headers=neha)).status_code == 401  # signed out
    again = await password_step(client, w, "neha@example.com")
    assert again["two_factor_required"] is False
    alerts = await sql(
        app_sessionmaker,
        w,
        "SELECT payload->>'what' AS what FROM hd_outbox "
        "WHERE event = 'security.alert' ORDER BY occurred_at",
    )
    assert alerts[-1]["what"] == "Two-step sign-in reset for Neha <neha@example.com>."

    # Turning it off yourself needs a code.
    neha = bearer(again["token"])
    secret, _ = await turn_on(client, neha)
    off = "/api/v1/me/2fa/disable"
    assert (await client.post(off, headers=neha, json={"code": "999999"})).status_code == 422
    assert (await client.post(off, headers=neha, json={"code": code(secret, 1)})).status_code == 204
    assert ok(await client.get("/api/v1/me/2fa", headers=neha))["enabled"] is False
