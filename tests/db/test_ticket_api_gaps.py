"""Phase 1 ticket API gaps: seed data, cursor pages and sort, If-Match, resolve, and the
customer card (VIP edit, the customer's tickets). Through the real API with RLS on."""

import asyncio
from typing import Any

import pytest
from httpx import AsyncClient

from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

T = "/api/v1/tickets"
C = "/api/v1/contacts"


def ticket(subject: str, email: str = "ana@shop.test", **extra: Any) -> dict[str, Any]:
    return {"subject": subject, "contact": {"email": email, "name": "Ana Silva"}, **extra}


def system_lines(t: dict[str, Any]) -> list[str]:
    return [m["body"] for m in t["messages"] if m["kind"] == "system"]


async def admin(client: AsyncClient, sm: Sm, *, seed: bool = False) -> str:
    return await sign_in(client, await new_workspace(sm, seed=seed), "admin@example.com")


# 1. Seed data


async def test_a_new_workspace_gets_the_mocks_teams_and_types(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    h = bearer(await admin(client, app_sessionmaker, seed=True))
    teams = ok(await client.get("/api/v1/teams", headers=h))
    assert sorted(t["name"] for t in teams) == ["Billing", "Shipping", "Tech"]
    assert all(t["auto_assign"] and t["max_open_per_agent"] == 5 for t in teams)
    types = ok(await client.get("/api/v1/ticket-types", headers=h))
    assert [t["name"] for t in types] == [
        "Refund",
        "Shipping issue",
        "Technical problem",
        "Billing question",
        "General",
    ]
    refund = types[0]
    assert [f["key"] for f in refund["fields"]] == ["order_id", "amount", "reason"]
    assert refund["fields"][2]["options"] == [
        "Damaged",
        "Wrong item",
        "Late delivery",
        "Changed mind",
    ]

    # The seeded type works on a ticket, and seeded data is editable like any other.
    created = ok(
        await client.post(
            T,
            headers=h,
            json=ticket(
                "Torn shirt",
                type_id=refund["id"],
                fields={"order_id": "#1042", "amount": 1500, "reason": "Damaged"},
            ),
        ),
        201,
    )
    assert created["fields"] == {"order_id": "#1042", "amount": 1500, "reason": "Damaged"}
    billing = next(t for t in teams if t["name"] == "Billing")
    renamed = ok(
        await client.patch(f"/api/v1/teams/{billing['id']}", headers=h, json={"name": "Money"})
    )
    assert renamed["name"] == "Money"

    # Each workspace gets its own copy.
    other = bearer(await admin(client, app_sessionmaker, seed=True))
    assert len(ok(await client.get("/api/v1/teams", headers=other))) == 3


async def test_a_workspace_can_start_empty(client: AsyncClient, app_sessionmaker: Sm) -> None:
    h = bearer(await admin(client, app_sessionmaker, seed=False))
    assert ok(await client.get("/api/v1/teams", headers=h)) == []
    assert ok(await client.get("/api/v1/ticket-types", headers=h)) == []


# 2 and 7. Cursor pages and sort


async def _mixed_tickets(client: AsyncClient, h: dict[str, str]) -> list[dict[str, Any]]:
    made = []
    for i, prio in enumerate(["low", "urgent", "medium", "high", "urgent", "low", "medium"]):
        made.append(ok(await client.post(T, headers=h, json=ticket(f"t{i}", priority=prio)), 201))
    for t in (made[0], made[3]):
        ok(await client.post(f"{T}/{t['id']}/resolve", headers=h))
    return made


async def _walk(client: AsyncClient, h: dict[str, str], query: str, limit: int) -> list[str]:
    seen: list[str] = []
    cursor = None
    for _ in range(20):
        url = f"{T}?{query}&limit={limit}" + (f"&cursor={cursor}" if cursor else "")
        page = ok(await client.get(url, headers=h))
        seen += [t["subject"] for t in page["tickets"]]
        cursor = page["next_cursor"]
        if cursor is None:
            return seen
    raise AssertionError("paging never ended")


async def test_cursor_pages_cover_every_ticket_once_in_every_sort(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    h = bearer(await admin(client, app_sessionmaker))
    await _mixed_tickets(client, h)
    for sort in ("priority", "newest", "oldest", "updated"):
        whole = await _walk(client, h, f"sort={sort}", 200)
        assert len(whole) == 7 and len(set(whole)) == 7
        for size in (1, 2, 3):
            assert await _walk(client, h, f"sort={sort}", size) == whole, (sort, size)


async def test_sort_orders(client: AsyncClient, app_sessionmaker: Sm) -> None:
    h = bearer(await admin(client, app_sessionmaker))
    made = await _mixed_tickets(client, h)

    # Default: open most urgent first, then oldest; resolved last, newest resolved first.
    assert await _walk(client, h, "sort=priority", 50) == [
        "t1", "t4", "t2", "t6", "t5", "t3", "t0"
    ]  # fmt: skip
    assert await _walk(client, h, "sort=newest", 50) == [f"t{i}" for i in range(6, -1, -1)]
    assert await _walk(client, h, "sort=oldest", 50) == [f"t{i}" for i in range(7)]
    ok(await client.patch(f"{T}/{made[2]['id']}", headers=h, json={"subject": "t2!"}))
    assert (await _walk(client, h, "sort=updated", 50))[0] == "t2!"
    # Filters still apply with a sort.
    assert await _walk(client, h, "sort=oldest&priority=urgent", 50) == ["t1", "t4"]


async def test_a_new_ticket_during_paging_causes_no_repeats(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    h = bearer(await admin(client, app_sessionmaker))
    for i in range(4):
        ok(await client.post(T, headers=h, json=ticket(f"t{i}")), 201)
    first = ok(await client.get(f"{T}?sort=newest&limit=2", headers=h))
    ok(await client.post(T, headers=h, json=ticket("late arrival")), 201)
    rest = ok(
        await client.get(f"{T}?sort=newest&limit=10&cursor={first['next_cursor']}", headers=h)
    )
    subjects = [t["subject"] for t in first["tickets"] + rest["tickets"]]
    assert subjects == ["t3", "t2", "t1", "t0"]


async def test_bad_cursors_and_limits_are_refused(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    h = bearer(await admin(client, app_sessionmaker))
    for i in range(3):
        ok(await client.post(T, headers=h, json=ticket(f"t{i}")), 201)
    cursor = ok(await client.get(f"{T}?sort=newest&limit=1", headers=h))["next_cursor"]
    for url in (
        f"{T}?sort=oldest&cursor={cursor}",  # a cursor from another sort
        f"{T}?cursor=not-a-cursor",
        f"{T}?cursor=WyJwcmlvcml0eSIsMSwxLCJ4IiwxXQ",  # right shape, k3 not a number
        f"{T}?limit=201",
        f"{T}?limit=0",
        f"{T}?sort=sideways",
    ):
        r = await client.get(url, headers=h)
        assert r.status_code == 422, (url, r.text)
        assert "error" in r.json()
    assert len(ok(await client.get(f"{T}?limit=200", headers=h))["tickets"]) == 3


# 3. If-Match


async def test_if_match_refuses_a_stale_change(client: AsyncClient, app_sessionmaker: Sm) -> None:
    h = bearer(await admin(client, app_sessionmaker))
    created = await client.post(T, headers=h, json=ticket("Where is it?"))
    tid = ok(created, 201)["id"]
    first_etag = created.headers["ETag"]
    read = await client.get(f"{T}/{tid}", headers=h)
    assert read.headers["ETag"] == first_etag

    changed = await client.patch(
        f"{T}/{tid}", headers={**h, "If-Match": first_etag}, json={"priority": "high"}
    )
    ok(changed)
    assert changed.headers["ETag"] != first_etag

    stale = await client.patch(
        f"{T}/{tid}", headers={**h, "If-Match": first_etag}, json={"priority": "low"}
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "Conflict"
    assert ok(await client.get(f"{T}/{tid}", headers=h))["priority"] == "high"

    # The current ETag, a weak or unquoted one, "*", or no header at all are accepted.
    current = changed.headers["ETag"]
    for value in (current, f"W/{current}", current.strip('"')):
        ok(await client.patch(f"{T}/{tid}", headers={**h, "If-Match": value}, json={}))
    ok(await client.patch(f"{T}/{tid}", headers={**h, "If-Match": "*"}, json={"priority": "low"}))
    ok(await client.patch(f"{T}/{tid}", headers=h, json={"priority": "medium"}))

    for bad in ("yesterday", '"2026-11-08T10:00:00"'):  # not a date; no time zone
        r = await client.patch(f"{T}/{tid}", headers={**h, "If-Match": bad}, json={})
        assert r.status_code == 422, bad


async def test_two_agents_saving_the_same_version_one_wins(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    h = bearer(await admin(client, app_sessionmaker))
    created = await client.post(T, headers=h, json=ticket("Race"))
    tid, tag = ok(created, 201)["id"], created.headers["ETag"]
    results = await asyncio.gather(
        *[
            client.patch(f"{T}/{tid}", headers={**h, "If-Match": tag}, json={"subject": f"v{i}"})
            for i in range(4)
        ]
    )
    assert sorted(r.status_code for r in results) == [200, 409, 409, 409]
    winner = next(r.json()["subject"] for r in results if r.status_code == 200)
    assert ok(await client.get(f"{T}/{tid}", headers=h))["subject"] == winner


# 4. Resolve


async def test_resolve(client: AsyncClient, app_sessionmaker: Sm) -> None:
    slug = await new_workspace(app_sessionmaker)
    a = await sign_in(client, slug, "admin@example.com")
    await add(client, a, "neha@example.com", "agent", "Neha")
    await add(client, a, "light@example.com", "light_agent")
    neha = bearer(await sign_in(client, slug, "neha@example.com"))
    light = bearer(await sign_in(client, slug, "light@example.com"))
    created = await client.post(T, headers=neha, json=ticket("Done soon"))
    tid, tag = ok(created, 201)["id"], created.headers["ETag"]

    assert (await client.post(f"{T}/{tid}/resolve", headers=light)).status_code == 403
    resolved = ok(await client.post(f"{T}/{tid}/resolve", headers={**neha, "If-Match": tag}))
    assert resolved["status"] == "resolved" and resolved["resolved_at"]
    assert system_lines(resolved)[-1] == "Status set to Resolved by Neha"

    # Again: nothing changes, no second line; a stale ETag is still refused.
    again = ok(await client.post(f"{T}/{tid}/resolve", headers=neha))
    assert system_lines(again) == system_lines(resolved)
    stale = await client.post(f"{T}/{tid}/resolve", headers={**neha, "If-Match": tag})
    assert stale.status_code == 409

    # Reopening still counts, as with PATCH.
    reopened = ok(await client.patch(f"{T}/{tid}", headers=neha, json={"status": "open"}))
    assert reopened["reopen_count"] == 1
    missing = "00000000-0000-0000-0000-000000000000"
    assert (await client.post(f"{T}/{missing}/resolve", headers=neha)).status_code == 404


# 5 and 6. Customer card


async def test_customer_card_vip_and_tickets(client: AsyncClient, app_sessionmaker: Sm) -> None:
    slug = await new_workspace(app_sessionmaker)
    a = await sign_in(client, slug, "admin@example.com")
    await add(client, a, "light@example.com", "light_agent")
    h, light = bearer(a), bearer(await sign_in(client, slug, "light@example.com"))
    ana = [ok(await client.post(T, headers=h, json=ticket(f"ana {i}")), 201) for i in range(3)]
    ok(await client.post(T, headers=h, json=ticket("bob", email="bob@shop.test")), 201)
    cid = ana[0]["contact"]["id"]

    card = ok(await client.get(f"{C}/{cid}/tickets", headers=h))
    assert card["contact"]["email"] == "ana@shop.test"
    assert card["contact"]["ticket_count"] == 3 and card["contact"]["vip"] is False
    assert [t["subject"] for t in card["tickets"]] == ["ana 2", "ana 1", "ana 0"]
    assert card["next_cursor"] is None
    first = ok(await client.get(f"{C}/{cid}/tickets?limit=2", headers=light))
    rest = ok(await client.get(f"{C}/{cid}/tickets?cursor={first['next_cursor']}", headers=h))
    assert [t["subject"] for t in first["tickets"] + rest["tickets"]] == ["ana 2", "ana 1", "ana 0"]

    vip = ok(await client.patch(f"{C}/{cid}/profile", headers=h, json={"vip": True}))
    assert vip["vip"] is True and vip["name"] == "Ana Silva"
    assert ok(await client.get(f"{T}/{ana[0]['id']}", headers=h))["contact"]["vip"] is True
    listed = ok(await client.get(f"{T}?q=ana@shop", headers=h))["tickets"]
    assert all(t["contact"]["vip"] for t in listed)
    renamed = ok(await client.patch(f"{C}/{cid}/profile", headers=h, json={"name": " Ana S. "}))
    assert renamed["name"] == "Ana S." and renamed["vip"] is True

    assert (
        await client.patch(f"{C}/{cid}/profile", headers=light, json={"vip": False})
    ).status_code == 403
    for body in ({"vip": None}, {"vip": True, "email": "x@y.z"}, {"name": ""}):
        r = await client.patch(f"{C}/{cid}/profile", headers=h, json=body)
        assert r.status_code == 422, body

    # Another workspace can neither see nor change this customer.
    other = bearer(await admin(client, app_sessionmaker))
    assert (await client.get(f"{C}/{cid}/tickets", headers=other)).status_code == 404
    assert (
        await client.patch(f"{C}/{cid}/profile", headers=other, json={"vip": False})
    ).status_code == 404
    assert ok(await client.get(f"{C}/{cid}/tickets", headers=h))["contact"]["vip"] is True


# Cursor pages on messages and notifications (md 2B: every list)


async def test_a_long_ticket_shows_its_latest_messages_and_pages_back(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    h = bearer(await admin(client, app_sessionmaker))
    tid = ok(await client.post(T, headers=h, json=ticket("Long one")), 201)["id"]
    for i in range(60):
        ok(
            await client.post(
                f"{T}/{tid}/messages", headers=h, json={"kind": "note", "body": f"n{i}"}
            ),
            201,
        )

    # 61 system/notes in all: the creation line plus 60 notes. The ticket shows the latest
    # 50, oldest to newest, so the newest note is last.
    t = ok(await client.get(f"{T}/{tid}", headers=h))
    bodies = [m["body"] for m in t["messages"]]
    assert len(bodies) == 50 and bodies[-1] == "n59" and bodies[0] == "n10"
    assert t["older_messages_cursor"]

    older = ok(
        await client.get(f"{T}/{tid}/messages?cursor={t['older_messages_cursor']}", headers=h)
    )
    assert [m["body"] for m in older["messages"]] == [f"n{i}" for i in range(9, -1, -1)] + [
        "Ticket created by Admin"
    ]
    assert older["next_cursor"] is None

    # Paging the whole thread newest first, in pages of 7, sees every message once.
    seen: list[str] = []
    cursor = None
    while True:
        url = f"{T}/{tid}/messages?limit=7" + (f"&cursor={cursor}" if cursor else "")
        page = ok(await client.get(url, headers=h))
        seen += [m["body"] for m in page["messages"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [f"n{i}" for i in range(59, -1, -1)] + ["Ticket created by Admin"]

    short = ok(await client.post(T, headers=h, json=ticket("Short")), 201)
    assert short["older_messages_cursor"] is None

    for url in (
        f"{T}/{tid}/messages?cursor=bad",
        f"{T}/{tid}/messages?limit=201",
        f"{T}?cursor={t['older_messages_cursor']}",  # a message cursor on the ticket list
    ):
        assert (await client.get(url, headers=h)).status_code == 422, url
    missing = "00000000-0000-0000-0000-000000000000"
    assert (await client.get(f"{T}/{missing}/messages", headers=h)).status_code == 404
    other = bearer(await admin(client, app_sessionmaker))
    assert (await client.get(f"{T}/{tid}/messages", headers=other)).status_code == 404


async def test_notifications_page_newest_first(client: AsyncClient, app_sessionmaker: Sm) -> None:
    slug = await new_workspace(app_sessionmaker)
    a = await sign_in(client, slug, "admin@example.com")
    neha_id = await add(client, a, "neha@example.com", "agent", "Neha")
    neha = bearer(await sign_in(client, slug, "neha@example.com"))
    tid = ok(await client.post(T, headers=bearer(a), json=ticket("Ping")), 201)["id"]
    for i in range(5):
        ok(
            await client.post(
                f"{T}/{tid}/messages",
                headers=bearer(a),
                json={"kind": "note", "body": f"@Neha look {i}", "mention_ids": [neha_id]},
            ),
            201,
        )

    whole = ok(await client.get("/api/v1/notifications", headers=neha))
    assert whole["unread"] == 5 and whole["next_cursor"] is None
    ids = [n["id"] for n in whole["notifications"]]
    seen: list[str] = []
    cursor = None
    while True:
        url = "/api/v1/notifications?limit=2" + (f"&cursor={cursor}" if cursor else "")
        page = ok(await client.get(url, headers=neha))
        assert page["unread"] == 5
        seen += [n["id"] for n in page["notifications"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == ids and len(ids) == 5
    times = [n["created_at"] for n in whole["notifications"]]
    assert times == sorted(times, reverse=True)
    assert (await client.get("/api/v1/notifications?cursor=nope", headers=neha)).status_code == 422
    assert (await client.get("/api/v1/notifications?limit=201", headers=neha)).status_code == 422
