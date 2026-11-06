"""Auto-assignment and agent routing state through the real API, with RLS on."""

import asyncio
from typing import Any

import pytest
from httpx import AsyncClient

from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

T = "/api/v1/tickets"


async def desk(
    client: AsyncClient, sm: Sm, names: tuple[str, ...] = ("Asha", "Bilal", "Chen")
) -> dict[str, Any]:
    slug = await new_workspace(sm)
    admin = bearer(await sign_in(client, slug, "admin@example.com"))
    ids = {
        n: await add(
            client, admin["Authorization"].split()[1], f"{n.lower()}@example.com", "agent", n
        )
        for n in names
    }
    team = ok(
        await client.post(
            "/api/v1/teams",
            headers=admin,
            json={"name": "Billing", "member_ids": list(ids.values())},
        ),
        201,
    )
    return {"slug": slug, "admin": admin, "ids": ids, "team": team}


async def new_ticket(client: AsyncClient, w: dict[str, Any], **extra: Any) -> dict[str, Any]:
    body = {
        "subject": "Help",
        "contact": {"email": "ana@shop.test", "name": "Ana"},
        "team_id": w["team"]["id"],
        **extra,
    }
    return ok(await client.post(T, headers=w["admin"], json=body), 201)  # type: ignore[no-any-return]


def last_line(t: dict[str, Any]) -> str:
    return [m["body"] for m in t["messages"] if m["kind"] == "system"][-1]  # type: ignore[no-any-return]


async def test_round_robin_with_reason_and_notification(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    names = [(await new_ticket(client, w))["assignee_name"] for _ in range(4)]
    assert names == ["Asha", "Bilal", "Chen", "Asha"]

    t = await new_ticket(client, w)
    assert t["assignee_name"] == "Bilal"
    assert last_line(t) == "Auto-assigned to Bilal (Billing · Email 2 of 5)"
    bilal = bearer(await sign_in(client, w["slug"], "bilal@example.com"))
    box = ok(await client.get("/api/v1/notifications", headers=bilal))
    assert box["unread"] == 2
    assert box["notifications"][0]["text"] == f"You were auto-assigned {t['label']}: Help"


async def test_status_capacity_and_team_limit(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    asha = bearer(await sign_in(client, w["slug"], "asha@example.com"))
    ok(await client.put("/api/v1/me/status", headers=asha, json={"status": "on_break"}))
    ok(
        await client.patch(
            f"/api/v1/agents/{w['ids']['Bilal']}/routing", headers=w["admin"], json={"cap_email": 0}
        )
    )
    ok(
        await client.patch(
            f"/api/v1/teams/{w['team']['id']}", headers=w["admin"], json={"max_open_per_agent": 1}
        )
    )

    t = await new_ticket(client, w)
    assert t["assignee_name"] == "Chen"
    assert last_line(t) == (
        "Auto-assigned to Chen (Billing · Email 1 of 5)"
        " · skipped: Asha is On a break; Bilal is full on Email (0)"
    )
    t2 = await new_ticket(client, w)
    assert t2["assignee_id"] is None
    assert last_line(t2).endswith("Chen has 1 open")

    ok(await client.patch(f"{T}/{t['id']}", headers=w["admin"], json={"status": "resolved"}))
    assert (await new_ticket(client, w))["assignee_name"] == "Chen"


async def test_skill_and_channel(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    refund = ok(
        await client.post(
            "/api/v1/ticket-types",
            headers=w["admin"],
            json={"name": "Refund", "required_skill": "Refunds"},
        ),
        201,
    )
    ok(
        await client.patch(
            f"/api/v1/agents/{w['ids']['Chen']}/routing",
            headers=w["admin"],
            json={"skills": ["Refunds", " refunds "]},
        )
    )
    ok(
        await client.patch(
            f"/api/v1/agents/{w['ids']['Asha']}/routing",
            headers=w["admin"],
            json={"channels": ["email"]},
        )
    )

    t = await new_ticket(client, w, type_id=refund["id"], channel="chat")
    assert t["assignee_name"] == "Chen"
    assert last_line(t) == (
        "Auto-assigned to Chen (Billing · skill Refunds · Website chat 1 of 2)"
        " · skipped: Asha has Website chat off"
    )
    agents = {a["name"]: a for a in ok(await client.get("/api/v1/agents", headers=w["admin"]))}
    assert agents["Chen"]["skills"] == ["refunds"]
    assert agents["Chen"]["open_by_channel"] == {"chat": 1}


async def test_when_routing_runs(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    chosen = await new_ticket(client, w, assignee_id=w["ids"]["Chen"])
    assert chosen["assignee_name"] == "Chen"
    assert not any(m["body"].startswith("Auto-assigned") for m in chosen["messages"])

    no_team = ok(
        await client.post(
            T,
            headers=w["admin"],
            json={"subject": "x", "contact": {"email": "a@b.co", "name": "A"}},
        ),
        201,
    )
    assert no_team["assignee_id"] is None
    moved = ok(
        await client.patch(
            f"{T}/{no_team['id']}", headers=w["admin"], json={"team_id": w["team"]["id"]}
        )
    )
    assert moved["assignee_name"] == "Asha"
    assert last_line(moved).startswith("Auto-assigned to Asha (Billing")

    ok(
        await client.patch(
            f"/api/v1/teams/{w['team']['id']}", headers=w["admin"], json={"auto_assign": False}
        )
    )
    off = await new_ticket(client, w)
    assert off["assignee_id"] is None
    assert last_line(off) == "Left unassigned: auto-assign is off for Billing"


async def test_parallel_tickets_spread_evenly(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    made = await asyncio.gather(*[new_ticket(client, w) for _ in range(6)])
    names = sorted(t["assignee_name"] for t in made)
    assert names == ["Asha", "Asha", "Bilal", "Bilal", "Chen", "Chen"]


async def test_status_and_routing_permissions(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker, names=("Asha",))
    lee = await add(
        client, w["admin"]["Authorization"].split()[1], "lee@example.com", "light_agent", "Lee"
    )
    light = bearer(await sign_in(client, w["slug"], "lee@example.com"))
    asha = bearer(await sign_in(client, w["slug"], "asha@example.com"))

    assert (
        await client.put("/api/v1/me/status", headers=light, json={"status": "available"})
    ).status_code == 403
    r = await client.patch(
        f"/api/v1/agents/{lee}/routing", headers=w["admin"], json={"status": "available"}
    )
    assert r.status_code == 422
    assert (
        await client.patch(
            f"/api/v1/agents/{w['ids']['Asha']}/routing", headers=asha, json={"cap_chat": 9}
        )
    ).status_code == 403
    agents = {a["name"]: a for a in ok(await client.get("/api/v1/agents", headers=asha))}
    assert agents["Lee"]["status"] is None
    assert agents["Asha"]["status"] == "available"
    mine = ok(await client.put("/api/v1/me/status", headers=asha, json={"status": "offline"}))
    assert mine["status"] == "offline"
