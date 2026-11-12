"""API keys (md PL1, PL2) and the inbound API channel (C3), with RLS on: keys are shown
once and kept as a hash, act as their creator within their level, reach only the listed
routes, stop when revoked or when their creator goes, keep to their workspace and its IP
list, share a request budget per workspace, and let another system open tickets and add
its customer's messages."""

import hashlib
from dataclasses import replace
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.domain.api_keys import KeyAccess
from cedesk.helpdesk.domain.permissions import Action, Role, built_in
from cedesk.helpdesk.service import api_keys
from cedesk.helpdesk.service.errors import Forbidden
from cedesk.ratelimit import SlidingWindowLimiter
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in
from tests.db.test_security_settings import from_ip

pytestmark = pytest.mark.db

K = "/api/v1/api-keys"
T = "/api/v1/tickets"
IN = "/api/v1/inbound/tickets"


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    """Admin; Neha, an agent; Sam, a supervisor; the Billing team."""
    slug = await new_workspace(sm)
    token = await sign_in(client, slug, "admin@example.com")
    h = bearer(token)
    ids = {"neha": await add(client, token, "neha@example.com", "agent", "Neha"),
           "sam": await add(client, token, "sam@example.com", "supervisor", "Sam")}  # fmt: skip
    body = {"name": "Billing", "auto_assign": False, "member_ids": [ids["neha"]]}
    team = ok(await client.post("/api/v1/teams", headers=h, json=body), 201)["id"]
    me = ok(await client.get("/api/v1/me", headers=h))
    return {"slug": slug, "h": h, "token": token, "ids": ids, "team": team,
            "ws": UUID(me["workspace"]["id"]), "admin": me["member"]["id"]}  # fmt: skip


async def make_key(client: AsyncClient, w: dict[str, Any], access: str, name: str = "Shop") -> Any:
    return ok(await client.post(K, headers=w["h"], json={"name": name, "access": access}), 201)


async def ticket(client: AsyncClient, h: dict[str, str], subject: str = "Hello") -> Any:
    body = {"subject": subject, "contact": {"email": "priya@example.org", "name": "Priya"}}
    return ok(await client.post(T, headers=h, json=body), 201)


async def audit(client: AsyncClient, w: dict[str, Any]) -> list[Any]:
    page = ok(await client.get("/api/v1/audit", headers=w["h"], params={"area": "api_keys"}))
    return list(page["entries"])


async def test_admins_make_list_and_revoke_keys(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    made = await make_key(client, w, "read_write", "Shopify sync")
    key = made["key"]
    assert key.startswith("cdk_") and made["hint"] == "cdk_…" + key[-4:]
    assert (made["name"], made["access"], made["created_by_name"]) == (
        "Shopify sync",
        "read_write",
        "Admin",
    )
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        row = (await s.execute(text("SELECT * FROM hd_api_key"))).mappings().one()
    assert bytes(row["key_hash"]) == hashlib.sha256(key.encode()).digest()
    assert key not in {str(v) for v in row.values()}  # only the hash and last 4 are kept

    listed = ok(await client.get(K, headers=w["h"]))
    assert [(k["name"], k["hint"]) for k in listed] == [("Shopify sync", made["hint"])]
    assert "key" not in listed[0]  # never shown again

    for who in ("neha", "sam"):
        h = bearer(await sign_in(client, w["slug"], f"{who}@example.com"))
        assert (await client.get(K, headers=h)).status_code == 403
        r = await client.post(K, headers=h, json={"name": "x", "access": "read"})
        assert r.status_code == 403
    for body in ({"name": "", "access": "read"}, {"name": "x", "access": "root"},
                 {"name": "x" * 81, "access": "read"}):  # fmt: skip
        assert (await client.post(K, headers=w["h"], json=body)).status_code == 422, body

    assert (await client.get(T, headers=bearer(key))).status_code == 200
    r = await client.delete(f"{K}/{made['id']}", headers=w["h"])
    assert r.status_code == 204
    r = await client.get(T, headers=bearer(key))
    assert r.status_code == 401
    assert (await client.delete(f"{K}/{made['id']}", headers=w["h"])).status_code == 204
    assert (await client.delete(f"{K}/{UUID(int=3)}", headers=w["h"])).status_code == 404
    gone = ok(await client.get(K, headers=w["h"]))[0]
    assert gone["revoked_at"] is not None and gone["revoked_by_name"] == "Admin"

    entries = await audit(client, w)
    assert [(e["action"], e["target"]) for e in entries] == [
        ("revoked", "Shopify sync"),
        ("created", "Shopify sync"),
    ]  # fmt: skip  (once each)
    assert entries[1]["detail"] == {"access": "read_write", "hint": made["hint"]}
    assert key not in str(entries)


async def test_a_key_acts_as_its_creator_within_its_level(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    read = bearer((await make_key(client, w, "read", "Reader"))["key"])
    rw = bearer((await make_key(client, w, "read_write", "Shop"))["key"])
    admin = bearer((await make_key(client, w, "admin", "Setup"))["key"])

    me = ok(await client.get("/api/v1/me", headers=read))
    assert me["member"]["id"] == w["admin"]  # who the key acts as
    assert (await client.get(T, headers=read)).status_code == 200
    assert (await client.post(T, headers=read, json={})).status_code == 403
    assert (await client.get("/api/v1/reports/team", headers=read)).status_code == 200

    made = await ticket(client, rw)
    lines = [m["body"] for m in made["messages"] if m["kind"] == "system"]
    assert 'Ticket created by Admin (API key "Shop")' in lines
    note = {"kind": "note", "body": "Checked the order"}
    ok(await client.post(f"{T}/{made['id']}/messages", headers=rw, json=note), 201)
    team = f"/api/v1/teams/{w['team']}"
    assert (await client.patch(team, headers=rw, json={"name": "Money"})).status_code == 403
    ok(await client.patch(team, headers=admin, json={"name": "Money"}))
    teams = ok(await client.get("/api/v1/audit", headers=w["h"], params={"area": "teams"}))
    assert teams["entries"][0]["actor_name"] == 'Admin (API key "Setup")'

    for method, path, body in (
        ("POST", K, {"name": "more", "access": "admin"}), ("GET", K, None),
        ("GET", "/api/v1/security-settings", None), ("POST", "/api/v1/auth/sign-out", None),
        ("PUT", "/api/v1/me/status", {"status": "offline"}),
        ("POST", "/api/v1/members", {"email": "x@example.com", "name": "X", "role": "admin",
                                     "password": "correct-horse-battery"}),
    ):  # fmt: skip
        r = await client.request(method, path, headers=admin, json=body)
        assert r.status_code == 403, (method, path, r.text)

    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        sql = "SELECT name, last_used_at IS NOT NULL FROM hd_api_key ORDER BY name"
        used = (await s.execute(text(sql))).all()
    assert [tuple(u) for u in used] == [("Reader", True), ("Setup", True), ("Shop", True)]


async def test_behind_the_route_list_the_caller_itself_is_limited(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """Two layers under the route list: a key's caller carries only its level's rights,
    and the key service refuses a key even when it has an Admin's rights."""
    w = await desk(client, app_sessionmaker)
    never = {Action.manage_access, Action.approve}
    for access, has, lacks in (
        ("read", {Action.read_ticket, Action.reports}, {Action.reply, Action.create_ticket}),
        ("read_write", {Action.reply, Action.assign_others}, {Action.settings}),
        ("admin", {Action.settings, Action.reply}, set()),
    ):
        key = (await make_key(client, w, access))["key"]
        caller = await api_keys.resolve(app_sessionmaker, key, "127.0.0.1")
        assert caller.api_key_access == access
        assert has <= caller.access.actions, access
        assert not (lacks | never) & caller.access.actions, access
    as_admin = replace(caller, access=built_in(Role.admin))
    with pytest.raises(Forbidden):
        await api_keys.create_key(app_sessionmaker, as_admin, name="x", access=KeyAccess.read)
    with pytest.raises(Forbidden):
        await api_keys.all_keys(app_sessionmaker, as_admin)


async def test_a_key_stops_with_its_creator(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    other = await add(client, w["token"], "ana@example.com", "admin", "Ana")
    ana = bearer(await sign_in(client, w["slug"], "ana@example.com"))
    key = ok(await client.post(K, headers=ana, json={"name": "Ana's", "access": "read"}), 201)
    assert (await client.get(T, headers=bearer(key["key"]))).status_code == 200
    ok(await client.patch(f"/api/v1/members/{other}", headers=w["h"], json={"is_active": False}))
    assert (await client.get(T, headers=bearer(key["key"]))).status_code == 401


async def test_a_key_keeps_to_its_workspace(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    other = await desk(client, app_sessionmaker)
    theirs = await ticket(client, other["h"])
    key = (await make_key(client, w, "admin"))["key"]
    assert (await client.get(f"{T}/{theirs['id']}", headers=bearer(key))).status_code == 404
    # Naming the other workspace in the key gains nothing: the rest must match there.
    forged = f"cdk_{other['ws'].hex}_{key.split('_', 2)[2]}"
    assert (await client.get(T, headers=bearer(forged))).status_code == 401
    for junk in ("cdk_nothing", "cdk_" + "0" * 32 + "_x"):
        assert (await client.get(T, headers=bearer(junk))).status_code == 401


async def test_the_ip_list_applies_to_keys(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    key = bearer((await make_key(client, w, "read"))["key"])
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(text(
            "INSERT INTO hd_security_settings (workspace_id, ip_allowlist) "
            "VALUES (:ws, '{10.0.0.0/8}') ON CONFLICT (workspace_id) "
            "DO UPDATE SET ip_allowlist = EXCLUDED.ip_allowlist"), {"ws": w["ws"]})  # fmt: skip
    assert (await client.get(T, headers=key)).status_code == 403  # from 127.0.0.1
    async with from_ip(client, "10.1.2.3") as office:
        assert (await office.get(T, headers=key)).status_code == 200


async def test_keys_share_a_budget_per_workspace(client: AsyncClient, app_sessionmaker: Sm) -> None:
    client._transport.app.state.api_key_requests = SlidingWindowLimiter(3, 60)  # type: ignore[attr-defined]
    w = await desk(client, app_sessionmaker)
    one = bearer((await make_key(client, w, "read", "One"))["key"])
    two = bearer((await make_key(client, w, "read", "Two"))["key"])
    for h in (one, two, one):
        assert (await client.get(T, headers=h)).status_code == 200
    r = await client.get(T, headers=two)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert (await client.get(T, headers=w["h"])).status_code == 200  # people aren't counted
    other = await desk(client, app_sessionmaker)  # another workspace has its own budget
    theirs = bearer((await make_key(client, other, "read"))["key"])
    assert (await client.get(T, headers=theirs)).status_code == 200


async def test_another_system_opens_a_ticket_for_its_customer(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    rw = bearer((await make_key(client, w, "read_write", "Shop"))["key"])
    body = {"contact": {"email": "Priya@Example.org", "name": "Priya"},
            "message": "My order #1234 never arrived. Card 4111 1111 1111 1111.",
            "external_id": "shop-77", "tags": ["shop"]}  # fmt: skip
    got = ok(await client.post(IN, headers=rw, json=body), 201)
    assert got["created"] is True
    t = ok(await client.get(f"{T}/{got['ticket_id']}", headers=w["h"]))
    assert (t["channel"], t["number"], t["contact"]["email"]) == (
        "api",
        got["number"],
        "priya@example.org",
    )
    assert "4111" not in t["subject"] and t["subject"].startswith("My order #1234")
    first = next(m for m in t["messages"] if m["kind"] == "customer")
    assert "4111" not in first["body"]
    assert "Ticket created by Priya (api)" in [m["body"] for m in t["messages"]]

    again = await client.post(IN, headers=rw, json=body)
    assert again.status_code == 200 and again.json() == got | {"created": False}

    read = bearer((await make_key(client, w, "read", "Reader"))["key"])
    assert (await client.post(IN, headers=read, json=body)).status_code == 403
    assert (await client.post(IN, headers=w["h"], json=body)).status_code == 403  # a person
    bad = body | {"assignee_id": w["ids"]["neha"]}
    assert (await client.post(IN, headers=rw, json=bad)).status_code == 422  # not offered


async def test_the_customer_writes_again(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    rw = bearer((await make_key(client, w, "read_write", "Shop"))["key"])
    first = {"contact": {"email": "priya@example.org", "name": "Priya"}, "message": "Help"}
    tid = ok(await client.post(IN, headers=rw, json=first), 201)["ticket_id"]
    url = f"{IN}/{tid}/messages"

    said = ok(await client.post(url, headers=rw, json={"message": "Any news?",
                                                       "external_id": "m-1"}), 201)  # fmt: skip
    assert said["outcome"] == "added"
    again = await client.post(url, headers=rw, json={"message": "Any news?", "external_id": "m-1"})
    assert again.status_code == 200 and again.json() == {**said, "outcome": "duplicate"}

    ok(await client.post(f"{T}/{tid}/resolve", headers=w["h"]))
    back = ok(await client.post(url, headers=rw, json={"message": "Still broken"}), 201)
    assert back["outcome"] == "reopened"
    ok(await client.post(f"{T}/{tid}/resolve", headers=w["h"]))
    thanks = ok(await client.post(url, headers=rw, json={"message": "Thanks!"}), 201)
    assert thanks["outcome"] == "thanked"
    t = ok(await client.get(f"{T}/{tid}", headers=w["h"]))
    assert t["status"] == "resolved"
    assert [m["body"] for m in t["messages"] if m["kind"] == "customer"] == [
        "Help", "Any news?", "Still broken", "Thanks!"]  # fmt: skip

    web = await ticket(client, w["h"])  # made by an agent, channel web
    r = await client.post(f"{IN}/{web['id']}/messages", headers=rw, json={"message": "Hi"})
    assert r.status_code == 409
    r = await client.post(f"{IN}/{UUID(int=9)}/messages", headers=rw, json={"message": "Hi"})
    assert r.status_code == 404
    other = await desk(client, app_sessionmaker)
    theirs = bearer((await make_key(client, other, "read_write"))["key"])
    r = await client.post(url, headers=theirs, json={"message": "Hi"})
    assert r.status_code == 404  # another workspace's ticket doesn't exist for this key

    into = ok(await client.post(IN, headers=rw, json=first | {"message": "Second"}), 201)
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(text("UPDATE hd_ticket SET merged_into_id = :into WHERE id = :id"),
                        {"into": UUID(into["ticket_id"]), "id": UUID(tid)})  # fmt: skip
    r = await client.post(url, headers=rw, json={"message": "Hello?"})
    assert r.status_code == 409 and into["ticket_id"] in r.json()["error"]["message"]
