"""Audit log (md S9) through the real API with RLS on: settings changes are recorded with
who, when, IP and area; the chain verifies and shows tampering; only the `settings` right
reads it; the app role can't change it."""

import asyncio
import csv
import io
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from cedesk.db import workspace_session
from cedesk.tenancy import WORKSPACE_SETTING
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

A = "/api/v1/audit"
M = "/api/v1/macros"
STEP = [{"kind": "set_priority", "priority": "high"}]


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    slug = await new_workspace(sm)
    token = await sign_in(client, slug, "admin@example.com")
    h = bearer(token)
    await add(client, token, "neha@example.com", "agent", "Neha")
    me = ok(await client.get("/api/v1/me", headers=h))
    return {"slug": slug, "h": h, "ws": UUID(me["workspace"]["id"])}


async def entries(client: AsyncClient, w: dict[str, Any], **params: Any) -> list[Any]:
    return list(ok(await client.get(A, headers=w["h"], params=params))["entries"])


async def test_a_settings_change_is_recorded(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)  # adding Neha: created, then made Available
    macro = ok(await client.post(M, headers=w["h"], json={"name": "Refund", "steps": STEP}), 201)
    ok(await client.patch(f"{M}/{macro['id']}", headers=w["h"], json={"name": "Refund ok"}))
    # A change refused (name taken) records nothing.
    other = ok(await client.post(M, headers=w["h"], json={"name": "Other", "steps": STEP}), 201)
    r = await client.patch(f"{M}/{other['id']}", headers=w["h"], json={"name": "Refund ok"})
    assert r.status_code == 409
    assert (await client.delete(f"{M}/{macro['id']}", headers=w["h"])).status_code == 204

    found = await entries(client, w, area="macros")
    assert [(e["action"], e["target"]) for e in found] == [
        ("deleted", "Macro Refund ok"),
        ("created", "Macro Other"),
        ("changed", "Macro Refund ok"),
        ("created", "Macro Refund"),
    ]
    changed = found[2]
    assert changed["detail"] == {"changes": {"name": ["Refund", "Refund ok"]}}
    assert (changed["actor_name"], changed["ip"], changed["area"]) == (
        "Admin",
        "127.0.0.1",
        "macros",
    )
    assert changed["target_id"] == macro["id"]

    every = await entries(client, w)
    assert [e["seq"] for e in every] == list(range(len(every), 0, -1))
    assert every[-1]["target"] == "Member Neha <neha@example.com>"
    assert (every[-2]["area"], every[-2]["target"]) == ("routing", "Routing for Neha")
    assert every[-2]["detail"] == {"changes": {"status": ["offline", "available"]}}
    assert len(every) == 6


async def test_secrets_never_reach_the_log(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    neha = next(
        m["id"]
        for m in ok(await client.get("/api/v1/members", headers=w["h"]))["members"]
        if m["email"] == "neha@example.com"
    )
    r = await client.post(
        f"/api/v1/members/{neha}/password",
        headers=w["h"],
        json={"new_password": "a-brand-new-password-9"},
    )
    assert r.status_code == 204, r.text
    ok(await client.put("/api/v1/security-settings", headers=w["h"], json={"password_min": 12}))
    found = await entries(client, w)
    assert found[0]["detail"] == {"changes": {"password_min": [10, 12]}}
    assert found[1]["action"] == "password_reset"
    raw = (await client.get(f"{A}/export.csv", headers=w["h"])).text
    assert "a-brand-new-password-9" not in raw
    assert "a-brand-new-password-9" not in str(found)


async def test_only_the_settings_right_reads_it(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    neha = bearer(await sign_in(client, w["slug"], "neha@example.com"))
    for method, path in (("GET", A), ("POST", f"{A}/verify"), ("GET", f"{A}/export.csv")):
        assert (await client.request(method, path, headers=neha)).status_code == 403
    role = ok(
        await client.post(
            "/api/v1/roles", headers=w["h"], json={"name": "Settings desk", "settings": True}
        ),
        201,
    )
    ok(
        await client.patch(
            f"/api/v1/members/{(await entries(client, w))[-1]['target_id']}",
            headers=w["h"],
            json={"role": "custom", "role_id": role["id"]},
        )
    )
    assert len(await entries(client, {**w, "h": neha})) >= 3


async def test_paging_and_csv(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    for name in ("=SUM(A1)", "Two", "Three"):
        ok(await client.post(M, headers=w["h"], json={"name": name, "steps": STEP}), 201)
    first = ok(await client.get(A, headers=w["h"], params={"limit": 3}))
    assert [e["seq"] for e in first["entries"]] == [5, 4, 3] and first["next_before"] == 3
    rest = ok(await client.get(A, headers=w["h"], params={"limit": 3, "before": 3}))
    assert [e["seq"] for e in rest["entries"]] == [2, 1] and rest["next_before"] is None

    r = await client.get(f"{A}/export.csv", headers=w["h"], params={"area": "macros"})
    assert r.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0][:7] == ["seq", "when (UTC)", "who", "ip", "area", "action", "what"]
    assert [row[6] for row in rows[1:]] == ["Macro =SUM(A1)", "Macro Two", "Macro Three"]
    # The target starts with "Macro", so it's safe; a value starting with "=" is quoted.
    assert all(not cell.startswith("=") for row in rows for cell in row)
    assert (await client.get(A, headers=w["h"], params={"area": "nope"})).status_code == 422


async def test_the_chain_verifies_and_shows_tampering(
    client: AsyncClient, app_sessionmaker: Sm, owner_engine: AsyncEngine
) -> None:
    w = await desk(client, app_sessionmaker)
    # Two saves at the same moment still get their own places in the chain.
    await asyncio.gather(
        *(client.post(M, headers=w["h"], json={"name": f"M{i}", "steps": STEP}) for i in range(4))
    )
    good = ok(await client.post(f"{A}/verify", headers=w["h"]))
    assert (good["ok"], good["checked"], good["broken_at"]) == (True, 6, None)

    # The app's own database role can't change or remove an entry.
    for sql in ("UPDATE hd_audit SET target = 'x'", "DELETE FROM hd_audit"):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with workspace_session(app_sessionmaker, w["ws"]) as s:
                await s.execute(text(sql))

    # Someone with the database owner's password edits entry 3: Verify points at it.
    async with owner_engine.begin() as c:
        await c.execute(
            text("SELECT set_config(:k, :v, true)"), {"k": WORKSPACE_SETTING, "v": str(w["ws"])}
        )
        await c.execute(text("UPDATE hd_audit SET target = 'Macro hidden' WHERE seq = 3"))
    bad = ok(await client.post(f"{A}/verify", headers=w["h"]))
    assert (bad["ok"], bad["checked"], bad["broken_at"]) == (False, 2, 3)
    assert bad["message"].startswith("Entry 3 doesn't match")
