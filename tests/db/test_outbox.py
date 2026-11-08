"""Outbox events (md 3.2 step 5, PL4, PL5): written in the same transaction as the change,
so a change that fails or is refused leaves no event, and RLS keeps them per workspace."""

import asyncio
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient

from cedesk.db import workspace_session
from cedesk.helpdesk.repo import outbox
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

T = "/api/v1/tickets"


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    slug = await new_workspace(sm)
    token = await sign_in(client, slug, "admin@example.com")
    admin = bearer(token)
    asha = await add(client, token, "asha@example.com", "agent", "Asha")
    bilal = await add(client, token, "bilal@example.com", "agent", "Bilal")
    team = ok(
        await client.post(
            "/api/v1/teams", headers=admin, json={"name": "Billing", "member_ids": [asha]}
        ),
        201,
    )
    me = ok(await client.get("/api/v1/me", headers=admin))["member"]["id"]
    return {"ws": UUID(token.split(".")[0]), "admin": admin, "me": me, "asha": asha,
            "bilal": bilal, "team": team}  # fmt: skip


async def events(sm: Sm, ws: UUID, ticket_id: str) -> list[dict[str, Any]]:
    async with workspace_session(sm, ws) as s:
        rows = await outbox.for_ticket(s, UUID(ticket_id))
    return [dict(r["payload"]) for r in rows]


async def test_each_change_writes_its_events(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    h = w["admin"]
    body = {"subject": "Refund", "contact": {"email": "ana@shop.test", "name": "Ana"},
            "team_id": w["team"]["id"], "description": "My card number is 4111..."}  # fmt: skip
    t = ok(await client.post(T, headers=h, json=body), 201)
    url = f"{T}/{t['id']}"

    created, first, routed = await events(app_sessionmaker, w["ws"], t["id"])
    # The description is the customer's first message, so it is announced too.
    assert first["event"] == "message.created"
    assert first["message"]["kind"] == "customer" and first["message"]["author_member_id"] is None
    assert created["event"] == "ticket.created"
    assert created["ticket"] == {
        "id": t["id"], "number": t["number"], "subject": "Refund", "status": "open",
        "priority": "medium", "team": "Billing", "topic": None,
    }  # fmt: skip
    assert UUID(created["id"]) and created["occurred_at"]
    assert routed["event"] == "assignment.changed"
    assert (routed["from"], routed["to"], routed["by"]) == (None, w["asha"], None)  # routing chose

    ok(await client.patch(url, headers=h, json={"priority": "urgent", "add_tags": ["vip"]}))
    ok(await client.patch(url, headers=h, json={"assignee_id": w["bilal"]}))
    ok(
        await client.post(f"{url}/messages", headers=h, json={"kind": "reply", "body": "On it"}),
        201,
    )
    ok(await client.post(f"{url}/resolve", headers=h))
    ok(await client.post(f"{url}/resolve", headers=h))  # already resolved: no new event
    ok(await client.patch(url, headers=h, json={"status": "open", "priority": "low"}))

    later = (await events(app_sessionmaker, w["ws"], t["id"]))[3:]
    assert [(e["event"], e.get("changes")) for e in later] == [
        ("ticket.updated", ["priority", "tags"]),
        ("ticket.updated", ["assignee_id"]),
        ("assignment.changed", None),
        ("message.created", None),
        ("ticket.resolved", None),
        ("ticket.updated", ["status", "priority"]),
    ]
    moved, reply, resolved = later[2], later[3], later[4]
    assert (moved["from"], moved["to"], moved["by"]) == (w["asha"], w["bilal"], w["me"])
    assert reply["message"]["kind"] == "agent" and reply["message"]["author_member_id"] == w["me"]
    assert resolved["ticket"]["status"] == "resolved"
    # Events carry ids and labels, never message bodies.
    everything = str(await events(app_sessionmaker, w["ws"], t["id"]))
    assert "On it" not in everything and "4111" not in everything


async def test_refused_changes_leave_no_event(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    h = w["admin"]
    t = ok(await client.post(T, headers=h, json={"subject": "Hi", "contact": {
        "email": "a@b.test", "name": "A"}}), 201)  # fmt: skip
    url = f"{T}/{t['id']}"
    before = await events(app_sessionmaker, w["ws"], t["id"])
    assert [e["event"] for e in before] == ["ticket.created"]

    stale = {"If-Match": '"2000-01-01T00:00:00+00:00"'}
    assert (
        await client.patch(url, headers=h | stale, json={"priority": "high"})
    ).status_code == 409
    assert (await client.patch(url, headers=h, json={"priority": "nope"})).status_code == 422
    ok(await client.patch(url, headers=h, json={"subject": "Hi"}))  # no real change
    assert await events(app_sessionmaker, w["ws"], t["id"]) == before


async def test_parallel_edits_each_write_one_event(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    h = w["admin"]
    t = ok(await client.post(T, headers=h, json={"subject": "Hi", "contact": {
        "email": "a@b.test", "name": "A"}}), 201)  # fmt: skip
    url = f"{T}/{t['id']}"
    await asyncio.gather(*(client.patch(url, headers=h, json={"add_tags": [f"t{i}"]})
                           for i in range(5)))  # fmt: skip
    updates = [e for e in await events(app_sessionmaker, w["ws"], t["id"])
               if e["event"] == "ticket.updated"]  # fmt: skip
    assert len(updates) == 5


async def test_events_stay_in_their_workspace(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    t = ok(await client.post(T, headers=w["admin"], json={"subject": "Hi", "contact": {
        "email": "a@b.test", "name": "A"}}), 201)  # fmt: skip
    other = UUID((await sign_in(client, await new_workspace(app_sessionmaker),
                                "admin@example.com")).split(".")[0])  # fmt: skip
    assert await events(app_sessionmaker, other, t["id"]) == []
    assert len(await events(app_sessionmaker, w["ws"], t["id"])) == 1


async def test_resolved_ticket_keeps_its_assignee_on_team_change(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """Moving a resolved ticket to another team keeps the person who resolved it, writes
    no assignment event, and spends nobody's round-robin turn."""
    w = await desk(client, app_sessionmaker)
    h = w["admin"]
    shipping = ok(
        await client.post(
            "/api/v1/teams", headers=h, json={"name": "Shipping", "member_ids": [w["bilal"]]}
        ),
        201,
    )
    body = {"subject": "Refund", "contact": {"email": "ana@shop.test", "name": "Ana"},
            "team_id": w["team"]["id"]}  # fmt: skip
    t = ok(await client.post(T, headers=h, json=body), 201)
    assert t["assignee_id"] == w["asha"]
    url = f"{T}/{t['id']}"
    ok(await client.post(f"{url}/resolve", headers=h))

    moved = ok(await client.patch(url, headers=h, json={"team_id": shipping["id"]}))
    assert moved["status"] == "resolved" and moved["team_name"] == "Shipping"
    assert moved["assignee_id"] == w["asha"]
    assert "Auto-assigned" not in moved["messages"][-1]["body"]
    last = (await events(app_sessionmaker, w["ws"], t["id"]))[-1]
    assert last["event"] == "ticket.updated" and last["changes"] == ["team_id"]

    # Bilal's turn was not spent: the next Shipping ticket goes to him.
    body["team_id"] = shipping["id"]
    assert ok(await client.post(T, headers=h, json=body), 201)["assignee_id"] == w["bilal"]
