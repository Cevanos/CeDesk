"""Tickets, ticket types and teams through the real API, as `cedesk_app` with RLS on."""

import asyncio
from typing import Any

import pytest
from httpx import AsyncClient

from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

T = "/api/v1/tickets"
REFUND_FIELDS = [
    {"key": "order_id", "label": "Order ID", "kind": "text", "required": True},
    {"key": "amount", "label": "Amount", "kind": "number"},
    {"key": "reason", "label": "Reason", "kind": "select", "options": ["Damaged", "Late"]},
]


def ticket(
    subject: str = "Where is my order?", email: str = "ana@shop.test", **extra: Any
) -> dict[str, Any]:
    return {"subject": subject, "contact": {"email": email, "name": "Ana Silva"}, **extra}


async def admin_of_new_workspace(client: AsyncClient, sm: Sm) -> tuple[str, str]:
    slug = await new_workspace(sm)
    return slug, await sign_in(client, slug, "admin@example.com")


def system_lines(t: dict[str, Any]) -> list[str]:
    return [m["body"] for m in t["messages"] if m["kind"] == "system"]


async def test_numbers_count_up_per_workspace(client: AsyncClient, app_sessionmaker: Sm) -> None:
    _, a = await admin_of_new_workspace(client, app_sessionmaker)
    _, b = await admin_of_new_workspace(client, app_sessionmaker)
    labels = [
        ok(await client.post(T, headers=bearer(a), json=ticket()), 201)["label"] for _ in range(3)
    ]
    assert labels == ["T-1001", "T-1002", "T-1003"]
    assert ok(await client.post(T, headers=bearer(b), json=ticket()), 201)["label"] == "T-1001"


async def test_parallel_creates_never_share_a_number(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    _, a = await admin_of_new_workspace(client, app_sessionmaker)
    responses = await asyncio.gather(
        *[client.post(T, headers=bearer(a), json=ticket(f"Order {i}")) for i in range(10)]
    )
    numbers = sorted(ok(r, 201)["number"] for r in responses)
    assert numbers == list(range(1001, 1011))


async def test_create_and_open(client: AsyncClient, app_sessionmaker: Sm) -> None:
    _, a = await admin_of_new_workspace(client, app_sessionmaker)
    created = ok(
        await client.post(
            T,
            headers=bearer(a),
            json=ticket(description="It never arrived.", priority="high", tags=["Late Delivery"]),
        ),
        201,
    )
    t = ok(await client.get(f"{T}/{created['id']}", headers=bearer(a)))
    assert t["status"] == "open"
    assert t["priority"] == "high"
    assert t["tags"] == ["late-delivery"]
    assert t["contact"]["name"] == "Ana Silva"
    assert t["contact"]["other_tickets"] == 0
    assert t["messages"][0] == {**t["messages"][0], "kind": "customer", "body": "It never arrived."}
    assert system_lines(t) == ["Ticket created by Admin"]

    again = ok(
        await client.post(T, headers=bearer(a), json=ticket("Second", email="ANA@shop.test")), 201
    )
    assert again["contact"]["id"] == t["contact"]["id"]
    assert again["contact"]["other_tickets"] == 1


async def test_ticket_types_and_field_checks(client: AsyncClient, app_sessionmaker: Sm) -> None:
    slug, a = await admin_of_new_workspace(client, app_sessionmaker)
    refund = ok(
        await client.post(
            "/api/v1/ticket-types",
            headers=bearer(a),
            json={"name": "Refund", "fields": REFUND_FIELDS},
        ),
        201,
    )
    good = ticket(
        type_id=refund["id"], fields={"order_id": "A-77", "amount": 19.99, "reason": "Late"}
    )
    t = ok(await client.post(T, headers=bearer(a), json=good), 201)
    assert t["fields"] == {"order_id": "A-77", "amount": 19.99, "reason": "Late"}
    assert t["type_name"] == "Refund"

    for fields in (
        {"amount": 5},
        {"order_id": "A", "reason": "Lost"},
        {"order_id": "A", "amount": "5"},
        {"order_id": "A", "colour": "red"},
    ):
        r = await client.post(
            T, headers=bearer(a), json=ticket(type_id=refund["id"], fields=fields)
        )
        assert r.status_code == 422, fields

    dup = await client.post("/api/v1/ticket-types", headers=bearer(a), json={"name": "Refund"})
    assert dup.status_code == 409
    await add(client, a, "agent@example.com", "agent")
    agent = await sign_in(client, slug, "agent@example.com")
    r = await client.post("/api/v1/ticket-types", headers=bearer(agent), json={"name": "Bug"})
    assert r.status_code == 403
    assert len(ok(await client.get("/api/v1/ticket-types", headers=bearer(agent)))) == 1


async def test_ticket_type_rename_reorder_delete(client: AsyncClient, app_sessionmaker: Sm) -> None:
    _, a = await admin_of_new_workspace(client, app_sessionmaker)
    h = bearer(a)
    refund = ok(
        await client.post(
            "/api/v1/ticket-types", headers=h, json={"name": "Refund", "fields": REFUND_FIELDS}
        ),
        201,
    )
    bug = ok(await client.post("/api/v1/ticket-types", headers=h, json={"name": "Bug"}), 201)
    assert [refund["position"], bug["position"]] == [0, 1]

    reordered = list(reversed(REFUND_FIELDS))
    changed = ok(
        await client.patch(
            f"/api/v1/ticket-types/{refund['id']}",
            headers=h,
            json={"name": "Refund request", "fields": reordered, "required_skill": "billing"},
        )
    )
    assert changed["name"] == "Refund request"
    assert [f["key"] for f in changed["fields"]] == ["reason", "amount", "order_id"]
    assert changed["required_skill"] == "billing"
    cleared = ok(
        await client.patch(
            f"/api/v1/ticket-types/{refund['id']}", headers=h, json={"required_skill": None}
        )
    )
    assert cleared["required_skill"] is None

    order = ok(
        await client.put(
            "/api/v1/ticket-types/order", headers=h, json={"ids": [bug["id"], refund["id"]]}
        )
    )
    assert [x["name"] for x in order] == ["Bug", "Refund request"]
    bad = await client.put("/api/v1/ticket-types/order", headers=h, json={"ids": [bug["id"]]})
    assert bad.status_code == 422

    t = ok(
        await client.post(
            T, headers=h, json=ticket(type_id=refund["id"], fields={"order_id": "A-1"})
        ),
        201,
    )
    assert (
        await client.delete(f"/api/v1/ticket-types/{refund['id']}", headers=h)
    ).status_code == 204
    after = ok(await client.get(f"{T}/{t['id']}", headers=h))
    assert after["type_id"] is None
    assert after["subject"] == t["subject"]


async def test_changes_write_system_lines(client: AsyncClient, app_sessionmaker: Sm) -> None:
    _, a = await admin_of_new_workspace(client, app_sessionmaker)
    h = bearer(a)
    team = ok(await client.post("/api/v1/teams", headers=h, json={"name": "Billing"}), 201)
    t = ok(await client.post(T, headers=h, json=ticket(tags=["vip"])), 201)
    url = f"{T}/{t['id']}"

    changed = ok(
        await client.patch(
            url,
            headers=h,
            json={
                "priority": "urgent",
                "team_id": team["id"],
                "add_tags": ["refund"],
                "remove_tags": ["vip"],
            },
        )
    )
    assert changed["tags"] == ["refund"]
    assert system_lines(changed)[1:] == [
        "Priority set to Urgent by Admin",
        "Team set to Billing by Admin",
        "Left unassigned: Billing has no members",
        "Tag refund added by Admin",
        "Tag vip removed by Admin",
    ]

    resolved = ok(await client.patch(url, headers=h, json={"status": "resolved"}))
    assert resolved["resolved_at"] is not None
    reopened = ok(await client.patch(url, headers=h, json={"status": "open"}))
    assert reopened["resolved_at"] is None
    assert reopened["reopen_count"] == 1
    assert system_lines(reopened)[-2:] == [
        "Status set to Resolved by Admin",
        "Status set to Open by Admin",
    ]

    same = ok(await client.patch(url, headers=h, json={"priority": "urgent"}))
    assert len(same["messages"]) == len(reopened["messages"])
    assert (await client.patch(url, headers=h, json={"subject": None})).status_code == 422


async def test_assignment_rules(client: AsyncClient, app_sessionmaker: Sm) -> None:
    slug, a = await admin_of_new_workspace(client, app_sessionmaker)
    agent_id = await add(client, a, "neha@example.com", "agent", "Neha")
    ravi_id = await add(client, a, "ravi@example.com", "agent", "Ravi")
    light_id = await add(client, a, "light@example.com", "light_agent")
    await add(client, a, "sup@example.com", "supervisor")
    agent = bearer(await sign_in(client, slug, "neha@example.com"))
    light = bearer(await sign_in(client, slug, "light@example.com"))
    sup = bearer(await sign_in(client, slug, "sup@example.com"))
    url = f"{T}/{ok(await client.post(T, headers=bearer(a), json=ticket()), 201)['id']}"

    assert (
        await client.patch(url, headers=agent, json={"assignee_id": ravi_id})
    ).status_code == 403
    taken = ok(await client.patch(url, headers=agent, json={"assignee_id": agent_id}))
    assert taken["assignee_name"] == "Neha"
    assert system_lines(taken)[-1] == "Assigned to Neha by Neha"
    moved = ok(await client.patch(url, headers=sup, json={"assignee_id": ravi_id}))
    assert moved["assignee_name"] == "Ravi"
    assert (
        await client.patch(url, headers=agent, json={"assignee_id": agent_id})
    ).status_code == 403
    assert (await client.patch(url, headers=sup, json={"assignee_id": light_id})).status_code == 422

    assert (await client.get(url, headers=light)).status_code == 200
    assert (await client.patch(url, headers=light, json={"priority": "low"})).status_code == 403
    assert (await client.post(T, headers=light, json=ticket())).status_code == 403


async def test_list_tabs_filters_search_and_order(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    _, a = await admin_of_new_workspace(client, app_sessionmaker)
    h = bearer(a)
    me = ok(await client.get("/api/v1/me", headers=h))["member"]["id"]
    team = ok(await client.post("/api/v1/teams", headers=h, json={"name": "Billing"}), 201)
    refund = ok(
        await client.post(
            "/api/v1/ticket-types", headers=h, json={"name": "Refund", "fields": REFUND_FIELDS}
        ),
        201,
    )

    low = ok(await client.post(T, headers=h, json=ticket("Low one", priority="low")), 201)
    urgent = ok(
        await client.post(
            T, headers=h, json=ticket("Urgent one", priority="urgent", team_id=team["id"])
        ),
        201,
    )
    mine = ok(
        await client.post(
            T, headers=h, json=ticket("Mine", email="bo@x.test", assignee_id=me, tags=["gift"])
        ),
        201,
    )
    fielded = ok(
        await client.post(
            T,
            headers=h,
            json=ticket("Money back", type_id=refund["id"], fields={"order_id": "ZX-9"}),
        ),
        201,
    )
    done1 = ok(await client.post(T, headers=h, json=ticket("Done first")), 201)
    done2 = ok(await client.post(T, headers=h, json=ticket("Done second")), 201)
    ok(await client.patch(f"{T}/{done1['id']}", headers=h, json={"status": "resolved"}))
    ok(await client.patch(f"{T}/{done2['id']}", headers=h, json={"status": "resolved"}))
    ok(await client.patch(f"{T}/{fielded['id']}", headers=h, json={"status": "pending"}))

    async def subjects(query: str) -> list[str]:
        page = ok(await client.get(f"{T}?{query}", headers=h))
        assert page["counts"] == {"open": 3, "pending": 1, "resolved": 2}
        return [t["subject"] for t in page["tickets"]]

    assert await subjects("status=open") == ["Urgent one", "Mine", "Low one"]
    assert await subjects("status=resolved") == ["Done second", "Done first"]
    assert await subjects("status=open&scope=mine") == ["Mine"]
    assert await subjects("status=open&scope=unassigned") == ["Urgent one", "Low one"]
    assert await subjects("priority=low") == ["Low one"]
    assert await subjects(f"team={team['id']}") == ["Urgent one"]
    assert await subjects(f"q=T-{low['number']}") == ["Low one"]
    assert await subjects("q=URGENT") == ["Urgent one"]
    assert await subjects("q=bo@x") == ["Mine"]
    assert await subjects("q=zx-9") == ["Money back"]
    assert await subjects("q=GIFT") == ["Mine"]
    assert await subjects("q=%25") == []
    assert await subjects("q=_") == []
    assert urgent["number"] < mine["number"]

    page = ok(await client.get(f"{T}?limit=2", headers=h))
    assert page["next_cursor"]
    rest = ok(await client.get(f"{T}?limit=10&cursor={page['next_cursor']}", headers=h))
    assert len(page["tickets"]) + len(rest["tickets"]) == 6
    assert rest["next_cursor"] is None


async def test_teams(client: AsyncClient, app_sessionmaker: Sm) -> None:
    slug, a = await admin_of_new_workspace(client, app_sessionmaker)
    h = bearer(a)
    neha = await add(client, a, "neha@example.com", "agent")
    team = ok(
        await client.post(
            "/api/v1/teams", headers=h, json={"name": "Billing", "member_ids": [neha]}
        ),
        201,
    )
    assert team["member_ids"] == [neha]
    assert team["max_open_per_agent"] == 5
    changed = ok(
        await client.patch(
            f"/api/v1/teams/{team['id']}",
            headers=h,
            json={"max_open_per_agent": 8, "member_ids": []},
        )
    )
    assert changed["member_ids"] == []
    assert changed["max_open_per_agent"] == 8
    assert (
        await client.post("/api/v1/teams", headers=h, json={"name": "Billing"})
    ).status_code == 409

    t = ok(await client.post(T, headers=h, json=ticket(team_id=team["id"])), 201)
    assert (await client.delete(f"/api/v1/teams/{team['id']}", headers=h)).status_code == 204
    assert ok(await client.get(f"{T}/{t['id']}", headers=h))["team_id"] is None

    agent = bearer(await sign_in(client, slug, "neha@example.com"))
    assert (
        await client.post("/api/v1/teams", headers=agent, json={"name": "X"})
    ).status_code == 403


async def test_one_workspace_cannot_use_anothers_records(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    _, a = await admin_of_new_workspace(client, app_sessionmaker)
    _, b = await admin_of_new_workspace(client, app_sessionmaker)
    hb = bearer(b)
    b_member = await add(client, b, "agent@example.com", "agent")
    b_team = ok(await client.post("/api/v1/teams", headers=hb, json={"name": "B team"}), 201)
    b_type = ok(await client.post("/api/v1/ticket-types", headers=hb, json={"name": "B type"}), 201)
    b_ticket = ok(await client.post(T, headers=hb, json=ticket()), 201)

    ha = bearer(a)
    assert (await client.get(f"{T}/{b_ticket['id']}", headers=ha)).status_code == 404
    assert (
        await client.patch(f"{T}/{b_ticket['id']}", headers=ha, json={"priority": "low"})
    ).status_code == 404
    for extra in ({"team_id": b_team["id"]}, {"type_id": b_type["id"]}, {"assignee_id": b_member}):
        assert (await client.post(T, headers=ha, json=ticket(**extra))).status_code == 422, extra
    r = await client.post("/api/v1/teams", headers=ha, json={"name": "A", "member_ids": [b_member]})
    assert r.status_code == 422
    assert ok(await client.get(T, headers=ha))["tickets"] == []
    assert ok(await client.get("/api/v1/teams", headers=ha)) == []
