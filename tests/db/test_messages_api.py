"""Replies, notes, @mentions and notifications through the real API, with RLS on."""

from typing import Any

import pytest
from httpx import AsyncClient

from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

T = "/api/v1/tickets"


async def setup(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    slug = await new_workspace(sm)
    admin = await sign_in(client, slug, "admin@example.com")
    neha_id = await add(client, admin, "neha@example.com", "agent", "Neha")
    light_id = await add(client, admin, "light@example.com", "light_agent", "Lee")
    refund = ok(
        await client.post(
            "/api/v1/ticket-types",
            headers=bearer(admin),
            json={
                "name": "Refund",
                "fields": [{"key": "order_id", "label": "Order ID", "kind": "text"}],
            },
        ),
        201,
    )
    t = ok(
        await client.post(
            T,
            headers=bearer(admin),
            json={
                "subject": "Broken parcel",
                "contact": {"email": "ana@shop.test", "name": "Ana Silva"},
                "type_id": refund["id"],
                "fields": {"order_id": "A-77"},
            },
        ),
        201,
    )
    return {
        "slug": slug,
        "admin": bearer(admin),
        "neha": bearer(await sign_in(client, slug, "neha@example.com")),
        "neha_id": neha_id,
        "light": bearer(await sign_in(client, slug, "light@example.com")),
        "light_id": light_id,
        "ticket": t,
        "url": f"{T}/{t['id']}/messages",
    }


async def test_reply_fills_placeholders_and_keeps_status(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await setup(client, app_sessionmaker)
    ticket_url = f"{T}/{w['ticket']['id']}"
    ok(await client.patch(ticket_url, headers=w["admin"], json={"status": "pending"}))

    msg = ok(
        await client.post(
            w["url"],
            headers=w["neha"],
            json={
                "kind": "reply",
                "body": "Hi {{customer.first_name}}, refund for {{fields.order_id}} "
                "is on its way. {{agent.name}}",
            },
        ),
        201,
    )
    assert msg["body"] == "Hi Ana, refund for A-77 is on its way. Neha"
    assert msg["kind"] == "agent"
    assert msg["author_name"] == "Neha"

    t = ok(await client.get(ticket_url, headers=w["admin"]))
    assert t["status"] == "pending"
    first = t["first_reply_at"]
    assert first is not None
    ok(
        await client.post(
            w["url"], headers=w["neha"], json={"kind": "reply", "body": "Any update?"}
        ),
        201,
    )
    assert ok(await client.get(ticket_url, headers=w["admin"]))["first_reply_at"] == first


async def test_empty_messages_are_refused(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await setup(client, app_sessionmaker)
    for body in ("", "   \n "):
        r = await client.post(w["url"], headers=w["admin"], json={"kind": "note", "body": body})
        assert r.status_code == 422


async def test_note_mentions_notify_the_mentioned_member(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await setup(client, app_sessionmaker)
    admin_id = ok(await client.get("/api/v1/me", headers=w["admin"]))["member"]["id"]
    note = ok(
        await client.post(
            w["url"],
            headers=w["admin"],
            json={
                "kind": "note",
                "body": "@Neha can you check {{fields.order_id}}?",
                "mention_ids": [w["neha_id"], admin_id, w["neha_id"]],
            },
        ),
        201,
    )
    assert note["kind"] == "note"
    assert note["body"] == "@Neha can you check {{fields.order_id}}?"
    assert sorted(note["mention_ids"]) == sorted([w["neha_id"], admin_id])

    box = ok(await client.get("/api/v1/notifications", headers=w["neha"]))
    assert box["unread"] == 1
    [n] = box["notifications"]
    assert n["text"] == f"Admin mentioned you on {w['ticket']['label']}: Broken parcel"
    assert n["ticket_label"] == w["ticket"]["label"]
    assert ok(await client.get("/api/v1/notifications", headers=w["admin"]))["unread"] == 0

    assert (
        await client.post(f"/api/v1/notifications/{n['id']}/read", headers=w["admin"])
    ).status_code == 404
    assert (
        await client.post(f"/api/v1/notifications/{n['id']}/read", headers=w["neha"])
    ).status_code == 204
    assert ok(await client.get("/api/v1/notifications?unread=true", headers=w["neha"])) == {
        "notifications": [],
        "unread": 0,
        "next_cursor": None,
    }

    ok(
        await client.post(
            w["url"],
            headers=w["admin"],
            json={"kind": "note", "body": "again", "mention_ids": [w["neha_id"]]},
        ),
        201,
    )
    assert (
        await client.post("/api/v1/notifications/read-all", headers=w["neha"])
    ).status_code == 204
    assert ok(await client.get("/api/v1/notifications", headers=w["neha"]))["unread"] == 0

    t = ok(await client.get(f"{T}/{w['ticket']['id']}", headers=w["admin"]))
    assert [m["kind"] for m in t["messages"]][-2:] == ["note", "note"]


async def test_mention_rules(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await setup(client, app_sessionmaker)
    other = await setup(client, app_sessionmaker)
    r = await client.post(
        w["url"],
        headers=w["admin"],
        json={"kind": "reply", "body": "Hi", "mention_ids": [w["neha_id"]]},
    )
    assert r.status_code == 422
    r = await client.post(
        w["url"],
        headers=w["admin"],
        json={"kind": "note", "body": "Hi", "mention_ids": [other["neha_id"]]},
    )
    assert r.status_code == 422
    ok(
        await client.patch(
            f"/api/v1/members/{w['neha_id']}", headers=w["admin"], json={"is_active": False}
        )
    )
    r = await client.post(
        w["url"],
        headers=w["admin"],
        json={"kind": "note", "body": "Hi", "mention_ids": [w["neha_id"]]},
    )
    assert r.status_code == 422


async def test_light_agent_can_note_but_not_reply(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await setup(client, app_sessionmaker)
    ok(
        await client.post(
            w["url"],
            headers=w["light"],
            json={"kind": "note", "body": "Looks like a courier issue"},
        ),
        201,
    )
    r = await client.post(w["url"], headers=w["light"], json={"kind": "reply", "body": "Hello"})
    assert r.status_code == 403


async def test_another_workspaces_ticket_is_not_found(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await setup(client, app_sessionmaker)
    other = await setup(client, app_sessionmaker)
    r = await client.post(w["url"], headers=other["admin"], json={"kind": "note", "body": "hi"})
    assert r.status_code == 404
