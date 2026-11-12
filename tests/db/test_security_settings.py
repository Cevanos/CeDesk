"""Security settings (md S3, S4, S6, S8) through the real API with RLS on. Files needing
sign-in (S5) are tested with the other file tests in test_attachments_api.py."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.service import security
from cedesk.helpdesk.service.delivery import Event
from cedesk.helpdesk.service.errors import NotFound
from cedesk.helpdesk.service.password_reset import Sender
from cedesk.helpdesk.service.security_alerts import SecurityAlertHandler
from tests.db.helpers import PASSWORD, Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

S = "/api/v1/security-settings"
HERE = "127.0.0.1"  # the test client's address


@asynccontextmanager
async def from_ip(client: AsyncClient, ip: str) -> AsyncIterator[AsyncClient]:
    """The same app, called from another address."""
    app = client._transport.app  # type: ignore[attr-defined]
    transport = ASGITransport(app=app, client=(ip, 5000))
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    slug = await new_workspace(sm)
    token = await sign_in(client, slug, "admin@example.com")
    h = bearer(token)
    neha = await add(client, token, "neha@example.com", "agent", "Neha")
    me = ok(await client.get("/api/v1/me", headers=h))
    return {"slug": slug, "h": h, "token": token, "neha": neha, "ws": UUID(me["workspace"]["id"])}


async def save(client: AsyncClient, w: dict[str, Any], **body: Any) -> Any:
    return await client.put(S, headers=w["h"], json=body)


async def run_sql(sm: Sm, w: dict[str, Any], sql: str, **params: Any) -> Any:
    async with workspace_session(sm, w["ws"]) as s:
        result = await s.execute(text(sql), params)
        return result.mappings().all() if result.returns_rows else None


async def test_defaults_score_and_admin_only(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    got = ok(await client.get(S, headers=w["h"]))
    assert {
        k: got[k]
        for k in (
            "password_min",
            "password_expiry_days",
            "session_length",
            "ip_allowlist",
            "attachments_need_sign_in",
            "admin_alerts",
        )
    } == {
        "password_min": 10,
        "password_expiry_days": None,
        "session_length": "12h",
        "ip_allowlist": [],
        "attachments_need_sign_in": False,
        "admin_alerts": True,
    }
    score = ok(await client.get("/api/v1/security/score", headers=w["h"]))
    on = {i["key"]: (i["on"], i["status"]) for i in score["items"]}
    # Out of the box: redaction (10), session not 7 days (5), alerts (5).
    assert (score["score"], score["level"]) == (20, "critical")
    assert on["two_factor"] == (False, "critical")
    assert on["passwords"] == (False, "recommended")
    assert on["redaction"] == (True, "healthy")

    ok(await save(client, w, password_min=12, ip_allowlist=[HERE], attachments_need_sign_in=True))
    ok(
        await client.put(
            "/api/v1/portal-settings", headers=w["h"], json={"allowed_domains": ["example.com"]}
        )
    )
    score = ok(await client.get("/api/v1/security/score", headers=w["h"]))
    assert (score["score"], score["level"]) == (55, "recommended")

    # A custom role with the settings right still can't see or change security.
    role = ok(
        await client.post("/api/v1/roles", headers=w["h"], json={"name": "Ops", "settings": True}),
        201,
    )
    ok(
        await client.patch(
            f"/api/v1/members/{w['neha']}",
            headers=w["h"],
            json={"role": "custom", "role_id": role["id"]},
        )
    )
    neha = bearer(await sign_in(client, w["slug"], "neha@example.com"))
    for method, path in [
        ("GET", S),
        ("PUT", S),
        ("GET", "/api/v1/security/score"),
        ("POST", "/api/v1/security/test-my-ip"),
    ]:
        r = await client.request(method, path, headers=neha, json={})
        assert r.status_code == 403, path


async def test_password_minimum_applies_to_new_passwords(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    for bad in (7, 65):
        assert (await save(client, w, password_min=bad)).status_code == 422
    assert (await save(client, w, password_expiry_days=30)).status_code == 422
    ok(await save(client, w, password_min=14))
    body = {"email": "ravi@example.com", "name": "Ravi", "role": "agent"}
    r = await client.post("/api/v1/members", headers=w["h"], json=body | {"password": "x" * 13})
    assert (r.status_code, r.json()["error"]["message"]) == (422, "Use at least 14 characters.")
    ok(
        await client.post("/api/v1/members", headers=w["h"], json=body | {"password": "y" * 14}),
        201,
    )
    r = await client.post(
        f"/api/v1/members/{w['neha']}/password", headers=w["h"], json={"new_password": "z" * 12}
    )
    assert r.status_code == 422
    # The old, shorter password still signs in.
    await sign_in(client, w["slug"], "neha@example.com")


async def test_ip_allowlist_is_enforced_on_sign_in_and_every_request(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    # Not a network; a list without my own address.
    assert (await save(client, w, ip_allowlist=["10.0.0.300"])).status_code == 422
    assert (await save(client, w, ip_allowlist=["0.0.0.0/0"])).status_code == 422
    r = await save(client, w, ip_allowlist=["10.0.0.0/8"])
    assert (r.status_code, "Your own address (127.0.0.1)" in r.json()["error"]["message"]) == (
        422,
        True,
    )
    test = "/api/v1/security/test-my-ip"
    assert ok(await client.post(test, headers=w["h"], json={"ip_allowlist": ["10.0.0.0/8"]})) == {
        "ip": HERE,
        "allowed": False,
    }
    assert ok(await client.post(test, headers=w["h"], json={}))["allowed"] is True

    saved = ok(await save(client, w, ip_allowlist=["127.0.0.1", "10.9.8.7/8", " 10.0.0.0/8"]))
    assert saved["ip_allowlist"] == ["127.0.0.1/32", "10.0.0.0/8"]
    async with from_ip(client, "10.1.2.3") as office:
        ok(await office.get("/api/v1/me", headers=w["h"]))
        await sign_in(office, w["slug"], "neha@example.com")
    async with from_ip(client, "203.0.113.9") as cafe:
        r = await cafe.get("/api/v1/me", headers=w["h"])
        assert (r.status_code, r.json()["error"]["code"]) == (403, "IpNotAllowed")
        r = await cafe.post(
            "/api/v1/auth/sign-in",
            json={"workspace": w["slug"], "email": "neha@example.com", "password": PASSWORD},
        )
        assert (r.status_code, r.json()["error"]["code"]) == (403, "IpNotAllowed")
    ok(await save(client, w, ip_allowlist=[]))
    async with from_ip(client, "203.0.113.9") as cafe:
        ok(await cafe.get("/api/v1/me", headers=w["h"]))


async def test_session_length_and_idle_sign_out(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    left = (
        "SELECT extract(epoch FROM expires_at - now()) / 3600 AS h FROM auth_session "
        "WHERE revoked_at IS NULL ORDER BY created_at"
    )
    hours = [r["h"] for r in await run_sql(app_sessionmaker, w, left)]
    assert all(11.9 < h <= 12 for h in hours)

    ok(await save(client, w, session_length="8h"))  # open sessions get shorter too
    hours = [r["h"] for r in await run_sql(app_sessionmaker, w, left)]
    assert all(7.9 < h <= 8 for h in hours)
    signed = ok(
        await client.post(
            "/api/v1/auth/sign-in",
            json={"workspace": w["slug"], "email": "neha@example.com", "password": PASSWORD},
        )
    )
    neha = bearer(signed["token"])

    ok(await save(client, w, session_length="1h_idle"))
    hours = [r["h"] for r in await run_sql(app_sessionmaker, w, left)]
    assert all(0.9 < h <= 1 for h in hours)
    # Used again after 30 minutes: another hour from now.
    await run_sql(
        app_sessionmaker,
        w,
        "UPDATE auth_session SET last_used_at = now() - interval '30 minutes', "
        "expires_at = now() + interval '30 minutes'",
    )
    ok(await client.get("/api/v1/me", headers=neha))
    hours = [r["h"] for r in await run_sql(app_sessionmaker, w, left)]
    assert sorted(round(h, 1) for h in hours) == [0.5, 1.0]
    # Idle past the hour: signed out.
    await run_sql(
        app_sessionmaker,
        w,
        "UPDATE auth_session SET last_used_at = now() - interval '61 minutes', "
        "expires_at = now() - interval '1 minute'",
    )
    assert (await client.get("/api/v1/me", headers=neha)).status_code == 401


async def test_expired_password_must_be_changed_first(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    ok(await save(client, w, password_expiry_days=90))
    await run_sql(
        app_sessionmaker,
        w,
        "UPDATE member SET password_changed_at = now() - interval '91 days' WHERE id = :id",
        id=w["neha"],
    )
    signed = ok(
        await client.post(
            "/api/v1/auth/sign-in",
            json={"workspace": w["slug"], "email": "neha@example.com", "password": PASSWORD},
        )
    )
    assert signed["password_expired"] is True
    neha = bearer(signed["token"])
    assert ok(await client.get("/api/v1/me", headers=neha))["password_expired"] is True
    r = await client.get("/api/v1/tickets", headers=neha)
    assert (r.status_code, r.json()["error"]["code"]) == (403, "PasswordExpired")
    changed = await client.post(
        "/api/v1/me/password",
        headers=neha,
        json={"current_password": PASSWORD, "new_password": "a fresh password"},
    )
    assert changed.status_code == 204
    ok(await client.get("/api/v1/tickets", headers=neha))
    assert ok(await client.get("/api/v1/me", headers=neha))["password_expired"] is False


async def test_admins_are_emailed_about_security_changes(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)  # adding Neha queued the first alert
    await add(client, w["token"], "boss@example.com", "admin", "Boss")
    ok(
        await client.patch(
            f"/api/v1/members/{w['neha']}", headers=w["h"], json={"is_active": False}
        )
    )
    ok(await client.patch(f"/api/v1/members/{w['neha']}", headers=w["h"], json={"name": "Neha S"}))
    ok(await client.patch(f"/api/v1/members/{w['neha']}", headers=w["h"], json={"role": "admin"}))
    ok(await client.patch(f"/api/v1/members/{w['neha']}", headers=w["h"], json={"role": "admin"}))
    ok(await save(client, w, ip_allowlist=["127.0.0.1"]))
    ok(await save(client, w, ip_allowlist=["127.0.0.1"], password_min=12))  # list unchanged
    ok(await save(client, w, ip_allowlist=["127.0.0.1"], admin_alerts=False))
    ok(
        await client.patch(f"/api/v1/members/{w['neha']}", headers=w["h"], json={"is_active": True})
    )  # alerts are off now

    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        rows = (
            (
                await s.execute(
                    text(
                        "SELECT * FROM hd_outbox WHERE event = 'security.alert' "
                        "ORDER BY occurred_at"
                    )
                )
            )
            .mappings()
            .all()
        )
    assert [r["payload"]["what"] for r in rows] == [
        "Member added: Neha <neha@example.com>, role Agent.",
        "Member added: Boss <boss@example.com>, role Admin.",
        "Member deactivated: Neha <neha@example.com>.",
        "Role changed for Neha S <neha@example.com>: Agent to Admin.",
        "IP allowlist changed from empty (every address allowed) to 127.0.0.1/32.",
        "Admin security alerts were turned off.",
    ]

    handler = SecurityAlertHandler(app_sessionmaker, fallback=Sender("help@acme.test", "Acme"))
    for _ in range(2):  # a retried event sends nothing new
        for r in rows:
            event = Event(r["id"], w["ws"], r["event"], None, dict(r["payload"]), r["occurred_at"])
            await handler.handle(event, f"{r['id']}:{handler.name}")
    sent = await run_sql(
        app_sessionmaker,
        w,
        "SELECT to_addresses, subject, body_text FROM hd_email_out "
        "WHERE subject LIKE 'Security alert%' ORDER BY created_at",
    )
    assert len(sent) == 18  # 6 alerts x 3 Admins (Neha is one now)
    assert {tuple(m["to_addresses"]) for m in sent} == {
        ("admin@example.com",),
        ("boss@example.com",),
        ("neha@example.com",),
    }
    assert "Done by: Admin" in sent[0]["body_text"]


async def test_a_save_changes_only_the_fields_sent(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    full = {
        "password_min": 14,
        "session_length": "8h",
        "attachments_need_sign_in": True,
        "ip_allowlist": ["10.0.0.0/8", "127.0.0.1"],
    }
    ok(await client.put(S, headers=w["h"], json=full))
    after = ok(await client.put(S, headers=w["h"], json={"admin_alerts": True}))
    assert {k: after[k] for k in full} == full | {"ip_allowlist": ["10.0.0.0/8", "127.0.0.1/32"]}


async def test_the_server_can_clear_an_ip_list_that_locks_everyone_out(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    ok(await client.put(S, headers=w["h"], json={"ip_allowlist": [HERE]}))
    async with from_ip(client, "203.0.113.9") as away:
        assert (await away.get("/api/v1/me", headers=w["h"])).status_code == 403
        assert await security.clear_ip_allowlist(app_sessionmaker, w["slug"].upper()) is True
        assert await security.clear_ip_allowlist(app_sessionmaker, w["slug"]) is False
        ok(await away.get("/api/v1/me", headers=w["h"]))
    with pytest.raises(NotFound):
        await security.clear_ip_allowlist(app_sessionmaker, "no-such-place")
    alerts = await run_sql(
        app_sessionmaker,
        w,
        "SELECT payload->>'what' AS what, payload->>'by' AS by FROM hd_outbox "
        "WHERE event = 'security.alert' ORDER BY occurred_at",
    )
    assert (alerts[-1]["what"], alerts[-1]["by"]) == (
        "IP allowlist cleared from the server (every address can sign in again).",
        None,
    )
    # The audit log shows it came from the server, with no member and no address.
    audit = await run_sql(
        app_sessionmaker,
        w,
        "SELECT actor_id, actor_name, ip, area, target FROM hd_audit ORDER BY seq DESC LIMIT 1",
    )
    assert dict(audit[0]) == {
        "actor_id": None,
        "actor_name": "Server (command line)",
        "ip": None,
        "area": "security",
        "target": "IP allowlist cleared from the server",
    }
