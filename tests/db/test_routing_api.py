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


async def test_workspace_default_capacity(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker, names=("Asha", "Bilal"))
    h = w["admin"]
    asha, bilal = w["ids"]["Asha"], w["ids"]["Bilal"]
    agent = bearer(await sign_in(client, w["slug"], "asha@example.com"))
    caps = ("cap_email", "cap_chat", "cap_messaging")

    def by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {r["id"]: r for r in rows}

    # The md defaults until an Admin changes them; everyone can read them.
    assert ok(await client.get("/api/v1/routing-defaults", headers=agent)) == {
        "cap_email": 5,
        "cap_chat": 2,
        "cap_messaging": 3,
    }
    r = await client.patch("/api/v1/routing-defaults", headers=agent, json={"cap_email": 1})
    assert r.status_code == 403
    for bad in ({"cap_email": -1}, {"cap_chat": 101}, {"cap_email": 1, "other": 2}):
        r = await client.patch("/api/v1/routing-defaults", headers=h, json=bad)
        assert r.status_code == 422, bad

    # Bilal keeps his own Email limit; Asha follows the workspace.
    ok(await client.patch(f"/api/v1/agents/{bilal}/routing", headers=h, json={"cap_email": 4}))
    new = ok(await client.patch("/api/v1/routing-defaults", headers=h, json={"cap_email": 1}))
    assert new == {"cap_email": 1, "cap_chat": 2, "cap_messaging": 3}
    agents = by_id(ok(await client.get("/api/v1/agents", headers=h)))
    assert [agents[asha][c] for c in caps] == [1, 2, 3]
    assert agents[asha]["cap_overrides"] == []
    assert (agents[bilal]["cap_email"], agents[bilal]["cap_overrides"]) == (4, ["email"])

    # Routing uses the workspace default: Asha is full after one Email ticket.
    ok(
        await client.put(
            "/api/v1/me/status",
            headers=bearer(await sign_in(client, w["slug"], "bilal@example.com")),
            json={"status": "offline"},
        )
    )
    first = await new_ticket(client, w, channel="email")
    second = await new_ticket(client, w, channel="email")
    assert first["assignee_name"] == "Asha"
    assert second["assignee_id"] is None
    assert "Asha is full on Email (1)" in last_line(second)

    # null puts Bilal back on the workspace default.
    reset = ok(
        await client.patch(f"/api/v1/agents/{bilal}/routing", headers=h, json={"cap_email": None})
    )
    assert (reset["cap_email"], reset["cap_overrides"]) == (1, [])
    assert reset["status"] == "offline"

    # Another workspace keeps the md defaults.
    other = await desk(client, app_sessionmaker, names=("Zed",))
    assert (
        ok(await client.get("/api/v1/routing-defaults", headers=other["admin"]))["cap_email"] == 5
    )


async def test_team_limit_is_1_to_50(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker, names=("Asha",))
    url = f"/api/v1/teams/{w['team']['id']}"
    for bad in (0, 51, 500):
        r = await client.patch(url, headers=w["admin"], json={"max_open_per_agent": bad})
        assert r.status_code == 422, bad
        r = await client.post(
            "/api/v1/teams", headers=w["admin"], json={"name": f"T{bad}", "max_open_per_agent": bad}
        )
        assert r.status_code == 422, bad
    assert (
        ok(await client.patch(url, headers=w["admin"], json={"max_open_per_agent": 50}))[
            "max_open_per_agent"
        ]
        == 50
    )
