"""Sandbox (md S10) through the real API with RLS on: copy the live rules, macros and
saved replies, edit the copy without touching live, compare, try the rules on samples,
deploy all-or-nothing with every change audited, delete."""

from typing import Any

import pytest
from httpx import AsyncClient

from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

S = "/api/v1/sandbox"
REFUND = {"kind": "text_contains_any", "words": ["refund"]}
HIGH = {"kind": "set_priority", "priority": "high"}
URGENT = {"kind": "set_priority", "priority": "urgent"}
CLOSE = [{"kind": "set_status", "status": "resolved"}]
CHAT = [{"kind": "channel_in", "channels": ["chat"]}]


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    """A rule, a macro and a saved reply, live."""
    slug = await new_workspace(sm)
    token = await sign_in(client, slug, "admin@example.com")
    h = bearer(token)
    await add(client, token, "neha@example.com", "agent", "Neha")
    rule = ok(await client.post("/api/v1/rules", headers=h, json={
        "name": "Refunds", "enabled": True, "event": "created",
        "conditions": [REFUND], "actions": [HIGH]}), 201)  # fmt: skip
    macro = ok(await client.post("/api/v1/macros", headers=h, json={
        "name": "Close", "steps": CLOSE}), 201)  # fmt: skip
    reply = ok(await client.post("/api/v1/saved-replies", headers=h,
                                 json={"shortcut": "hi", "body": "Hello!"}), 201)  # fmt: skip
    return {"h": h, "slug": slug, "rule": rule, "macro": macro, "reply": reply,
            "neha": bearer(await sign_in(client, slug, "neha@example.com"))}  # fmt: skip


async def live(client: AsyncClient, w: dict[str, Any]) -> dict[str, Any]:
    return {
        "rules": ok(await client.get("/api/v1/rules", headers=w["h"])),
        "macros": ok(await client.get("/api/v1/macros", headers=w["h"])),
        "replies": ok(await client.get("/api/v1/saved-replies", headers=w["h"])),
    }


async def test_copy_edit_compare_try_and_deploy(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    box = ok(await client.post(S, headers=w["h"]), 201)
    assert (await client.post(S, headers=w["h"])).status_code == 409
    assert [r["name"] for r in box["rules"]] == ["Refunds"]
    assert box["rules"][0]["id"] == w["rule"]["id"]
    assert box["saved_replies"] == [{"id": w["reply"]["id"], "shortcut": "hi", "body": "Hello!"}]
    before = await live(client, w)

    refunds = {**box["rules"][0], "actions": [URGENT]}
    chat = {"name": "Chats are urgent", "event": "created",
            "conditions": CHAT, "actions": [URGENT]}  # fmt: skip
    ok(await client.put(f"{S}/rules", headers=w["h"], json={"items": [chat, refunds]}))
    ok(await client.put(f"{S}/macros", headers=w["h"], json={"items": []}))
    replies = [{"id": w["reply"]["id"], "shortcut": "hi", "body": "Hello there!"},
               {"shortcut": "/Bye", "body": "Bye!"}]  # fmt: skip
    got = ok(await client.put(f"{S}/saved_replies", headers=w["h"], json={"items": replies}))
    assert got["saved_replies"][1] == {"id": None, "shortcut": "bye", "body": "Bye!"}
    assert await live(client, w) == before  # nothing live changed

    diff = ok(await client.get(f"{S}/compare", headers=w["h"]))
    assert diff["same"] is False
    assert diff["rules"]["added"] == ["Chats are urgent"] and diff["rules"]["removed"] == []
    assert [(c["label"], list(c["changes"])) for c in diff["rules"]["changed"]] == [
        ("Refunds", ["actions"])
    ]
    assert diff["rules"]["order_changed"] is True
    assert diff["macros"]["removed"] == ["Close"]
    assert diff["saved_replies"]["added"] == ["/bye"]
    assert diff["saved_replies"]["changed"][0]["changes"] == {"body": ["Hello!", "Hello there!"]}

    tried = {t["sample"]: t for t in ok(await client.post(f"{S}/try", headers=w["h"]))}
    refund = tried["Refund request by email"]
    assert (refund["live"]["priority"], refund["sandbox"]["priority"]) == ("high", "urgent")
    assert refund["sandbox"]["lines"] == ["Rule applied: Refunds → Priority Urgent"]
    assert tried["Thanks from the portal"]["same"] is True
    assert tried["Angry chat: checkout down"]["sandbox"]["lines"] == [
        "Rule applied: Chats are urgent → Priority Urgent"
    ]

    done = ok(await client.post(f"{S}/deploy", headers=w["h"]))
    assert done["rules"]["added"] == ["Chats are urgent"]
    now = await live(client, w)
    assert [r["name"] for r in now["rules"]] == ["Chats are urgent", "Refunds"]
    assert now["rules"][1]["actions"] == [{**URGENT, "keep_urgent": False}]
    assert now["rules"][1]["id"] == w["rule"]["id"]
    assert now["macros"] == []
    assert {(r["shortcut"], r["body"]) for r in now["replies"]} == {
        ("hi", "Hello there!"), ("bye", "Bye!")
    }  # fmt: skip

    # Each change has its own audit entry, then the deploy itself.
    audit = ok(await client.get("/api/v1/audit", headers=w["h"], params={"limit": 9}))["entries"]
    assert [(e["area"], e["action"], e["target"]) for e in audit[:8]] == [
        ("sandbox", "deployed", "Sandbox"),
        ("saved_replies", "created", "Saved reply /bye"),
        ("saved_replies", "changed", "Saved reply /hi"),
        ("macros", "deleted", "Macro Close"),
        ("rules", "reordered", "Rule order"),
        ("rules", "changed", "Rule Refunds"),
        ("rules", "created", "Rule Chats are urgent"),
        ("sandbox", "changed", "Sandbox saved replies"),
    ]
    # The sandbox is now a fresh copy: nothing more to deploy.
    assert ok(await client.get(f"{S}/compare", headers=w["h"]))["same"] is True
    assert ok(await client.get(S, headers=w["h"]))["rules"][0]["id"] is not None
    assert (await client.post(f"{S}/deploy", headers=w["h"])).status_code == 422


async def test_bad_items_rollback_and_rights(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    for method, path in (("POST", S), ("GET", S), ("GET", f"{S}/compare"),
                         ("POST", f"{S}/deploy"), ("DELETE", S)):  # fmt: skip
        assert (await client.request(method, path, headers=w["neha"])).status_code == 403
    assert (await client.get(S, headers=w["h"])).status_code == 404
    assert (await client.put(f"{S}/rules", headers=w["h"], json={"items": []})).status_code == 404
    box = ok(await client.post(S, headers=w["h"]), 201)

    wrong = {"name": "X", "event": "nope", "conditions": [], "actions": [HIGH]}
    bad = await client.put(f"{S}/rules", headers=w["h"],
                           json={"items": [box["rules"][0], wrong]})  # fmt: skip
    assert bad.status_code == 422 and bad.json()["error"]["message"].startswith("Item 2:")
    twice = [{"shortcut": "hi", "body": "a"}, {"shortcut": "HI", "body": "b"}]
    r = await client.put(f"{S}/saved_replies", headers=w["h"], json={"items": twice})
    assert r.status_code == 422 and "/hi" in r.json()["error"]["message"]

    # A macro points at a team that is deleted before Deploy: nothing is deployed, not
    # even the rule change that comes first.
    team = ok(await client.post("/api/v1/teams", headers=w["h"], json={"name": "VIP"}), 201)
    ok(await client.put(f"{S}/rules", headers=w["h"],
                        json={"items": [{**box["rules"][0], "name": "Refunds 2"}]}))  # fmt: skip
    to_vip = [{"kind": "set_team", "team_id": team["id"]}]
    ok(await client.put(f"{S}/macros", headers=w["h"],
                        json={"items": [{**box["macros"][0], "steps": to_vip}]}))  # fmt: skip
    assert (await client.delete(f"/api/v1/teams/{team['id']}", headers=w["h"])).status_code == 204
    before = await live(client, w)
    audit_before = ok(await client.get("/api/v1/audit", headers=w["h"]))["entries"][0]["seq"]
    r = await client.post(f"{S}/deploy", headers=w["h"])
    assert r.status_code == 422
    assert r.json()["error"]["message"].startswith("Nothing was deployed. Macros:")
    assert await live(client, w) == before
    assert (
        ok(await client.get("/api/v1/audit", headers=w["h"]))["entries"][0]["seq"] == audit_before
    )

    assert (await client.delete(S, headers=w["h"])).status_code == 204
    assert (await client.get(S, headers=w["h"])).status_code == 404
    [entry] = ok(await client.get("/api/v1/audit", headers=w["h"], params={"limit": 1}))["entries"]
    assert (entry["area"], entry["action"]) == ("sandbox", "deleted")
