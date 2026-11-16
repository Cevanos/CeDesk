"""SLA on tickets (md W9, W13, T9) with RLS on: the pill on every ticket, the `sla` list
sort, targets an Admin changes, VIP moving a customer's tickets, and `contact.updated`.

Most tests set the workspace to 24/7 so the clocks don't depend on when the tests run."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.service.sla import refresh_everywhere
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

T = "/api/v1/tickets"


async def desk(client: AsyncClient, sm: Sm, *, always_open: bool = True) -> dict[str, Any]:
    slug = await new_workspace(sm)
    token = await sign_in(client, slug, "admin@example.com")
    ws = UUID(token.split(".")[0])
    await add(client, token, "neha@example.com", "agent", "Neha")
    if always_open:
        await calendar(sm, ws, days=[1, 2, 3, 4, 5, 6, 7], always_open=True)
    return {
        "admin": bearer(token),
        "neha": bearer(await sign_in(client, slug, "neha@example.com")),
        "ws": ws,
    }


async def calendar(sm: Sm, ws: UUID, *, days: list[int], always_open: bool = False) -> None:
    """Written straight to the table (the editor, PUT /sla/calendar, has its own tests)."""
    async with workspace_session(sm, ws) as s:
        await s.execute(
            text(
                "INSERT INTO hd_sla_calendar (workspace_id, timezone, days, opens, closes, "
                "always_open) VALUES (:ws, 'Asia/Kolkata', :days, '09:00', '19:00', :open)"
            ),
            {"ws": ws, "days": days, "open": always_open},
        )


async def new(
    client: AsyncClient, w: dict[str, Any], priority: str, email: str = "ana@shop.test"
) -> dict[str, Any]:
    body = {"subject": f"{priority} one", "priority": priority,
            "contact": {"email": email, "name": "Ana"}}  # fmt: skip
    return ok(await client.post(T, headers=w["admin"], json=body), 201)


def due(t: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(t["sla"][t["sla"]["current"]]["due_at"])


def minutes_after_creation(t: dict[str, Any]) -> float:
    return (due(t) - datetime.fromisoformat(t["created_at"])).total_seconds() / 60


async def say(
    client: AsyncClient, w: dict[str, Any], t: dict[str, Any], kind: str, body: str
) -> dict[str, Any]:
    """Adds a reply or note and returns the ticket as it is now."""
    url = f"{T}/{t['id']}"
    ok(await client.post(f"{url}/messages", headers=w["admin"],
                         json={"kind": kind, "body": body}), 201)  # fmt: skip
    return ok(await client.get(url, headers=w["admin"]))


async def listed(client: AsyncClient, w: dict[str, Any], **params: Any) -> list[dict[str, Any]]:
    return ok(await client.get(T, headers=w["admin"], params={"sort": "sla", **params}))["tickets"]


async def test_every_ticket_shows_its_sla(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    t = await new(client, w, "urgent")
    sla = t["sla"]
    assert (sla["policy"], sla["current"], sla["state"], sla["paused"]) == (
        "Default", "first_reply", "on_track", False,
    )  # fmt: skip
    assert sla["first_reply"]["target_minutes"] == 15
    assert sla["resolve"]["target_minutes"] == 240
    assert minutes_after_creation(t) == pytest.approx(15, abs=0.1)
    assert 14 < sla["first_reply"]["minutes_left"] <= 15

    [row] = await listed(client, w)
    assert row["sla"] == ok(await client.get(f"{T}/{t['id']}", headers=w["admin"]))["sla"] | {
        "first_reply": row["sla"]["first_reply"] | {"minutes_left": pytest.approx(
            row["sla"]["first_reply"]["minutes_left"], abs=0.2)},
        "resolve": row["sla"]["resolve"] | {"minutes_left": pytest.approx(
            row["sla"]["resolve"]["minutes_left"], abs=0.2)},
    }  # fmt: skip


async def test_the_first_reply_stops_the_first_clock(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await new(client, w, "high")
    after_note = await say(client, w, t, "note", "Checking with the warehouse")
    assert after_note["sla"]["current"] == "first_reply"  # a note is not a reply

    r = await say(client, w, t, "reply", "On its way!")
    sla = r["sla"]
    assert sla["current"] == "resolve" and sla["first_reply"]["state"] == "met"
    assert sla["first_reply"]["stopped_at"] is not None
    assert minutes_after_creation(r) == pytest.approx(480, abs=0.1)

    resolved = ok(await client.post(f"{T}/{t['id']}/resolve", headers=w["admin"]))
    assert resolved["sla"]["resolve"]["state"] == "met"


async def test_the_sla_sort_puts_the_soonest_due_first(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    low = await new(client, w, "low")  # 8 h
    urgent = await new(client, w, "urgent")  # 15 min
    medium = await new(client, w, "medium")  # 4 h
    done = await new(client, w, "urgent")
    ok(await client.post(f"{T}/{done['id']}/resolve", headers=w["admin"]))
    assert [t["id"] for t in await listed(client, w)] == [
        urgent["id"], medium["id"], low["id"], done["id"],
    ]  # fmt: skip

    # Raising the priority moves the ticket up at once.
    ok(await client.patch(f"{T}/{low['id']}", headers=w["admin"], json={"priority": "urgent"}))
    order = [t["id"] for t in await listed(client, w)]
    # Both urgent now; `low` was created first, so its 15 minutes run out first.
    assert order == [low["id"], urgent["id"], medium["id"], done["id"]]

    # Pages follow the same order.
    first = ok(await client.get(T, headers=w["admin"], params={"sort": "sla", "limit": 2}))
    rest = ok(await client.get(T, headers=w["admin"], params={
        "sort": "sla", "limit": 2, "cursor": first["next_cursor"]}))  # fmt: skip
    assert [t["id"] for t in first["tickets"] + rest["tickets"]] == order


async def test_vip_customers_get_their_policy_and_an_event(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await new(client, w, "medium", "vip@shop.test")
    other = await new(client, w, "medium", "someone@shop.test")
    before = await client.get(f"{T}/{t['id']}", headers=w["admin"])
    assert minutes_after_creation(t) == pytest.approx(240, abs=0.1)

    contact = t["contact"]["id"]
    ok(await client.patch(f"/api/v1/contacts/{contact}/profile", headers=w["admin"],
                          json={"vip": True}))  # fmt: skip
    after = await client.get(f"{T}/{t['id']}", headers=w["admin"])
    vip = ok(after)
    assert vip["sla"]["policy"] == "VIP customers"
    assert minutes_after_creation(vip) == pytest.approx(30, abs=0.1)
    assert after.headers["ETag"] == before.headers["ETag"]  # the ticket itself didn't change
    assert [x["id"] for x in await listed(client, w)] == [t["id"], other["id"]]

    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        sql = "SELECT ticket_id, payload FROM hd_outbox WHERE event = 'contact.updated'"
        result = await s.execute(text(sql))
        [(ticket_id, payload)] = result.all()
    assert ticket_id is None
    assert payload["contact"]["id"] == contact and payload["contact"]["vip"] is True
    assert payload["changes"] == ["vip"]

    # The same value again changes nothing and sends nothing.
    ok(await client.patch(f"/api/v1/contacts/{contact}/profile", headers=w["admin"],
                          json={"vip": True}))  # fmt: skip
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        count = await s.scalar(
            text("SELECT count(*) FROM hd_outbox WHERE event = 'contact.updated'")
        )
    assert count == 1


def vip_lines(t: dict[str, Any]) -> list[str]:
    return [m["body"] for m in t["messages"] if m["kind"] == "system" and "VIP" in m["body"]]


async def test_a_vip_change_leaves_a_line_on_every_open_ticket(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """Plan 3.7.3 (bug B-10): the VIP policy applied but the timeline said nothing."""
    w = await desk(client, app_sessionmaker)
    one = await new(client, w, "medium", "vip@shop.test")
    two = await new(client, w, "high", "vip@shop.test")  # the same customer
    done = await new(client, w, "low", "vip@shop.test")
    ok(await client.post(f"{T}/{done['id']}/resolve", headers=w["admin"]))
    stranger = await new(client, w, "medium", "someone@shop.test")  # another customer
    profile = f"/api/v1/contacts/{one['contact']['id']}/profile"

    async def lines(t: dict[str, Any]) -> list[str]:
        return vip_lines(ok(await client.get(f"{T}/{t['id']}", headers=w["admin"])))

    ok(await client.patch(profile, headers=w["admin"], json={"vip": True}))
    assert await lines(one) == ["Customer marked VIP by Admin"]
    assert await lines(two) == ["Customer marked VIP by Admin"]
    assert await lines(done) == []  # resolved: the new policy does not reach it
    assert await lines(stranger) == []

    ok(await client.patch(profile, headers=w["admin"], json={"vip": False}))
    assert await lines(one) == ["Customer marked VIP by Admin", "VIP removed by Admin"]
    assert await lines(done) == []


async def test_changing_a_name_or_repeating_the_vip_value_writes_no_line(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await new(client, w, "medium", "vip@shop.test")
    profile = f"/api/v1/contacts/{t['contact']['id']}/profile"
    ok(await client.patch(profile, headers=w["admin"], json={"name": "Ana Rao"}))
    ok(await client.patch(profile, headers=w["admin"], json={"vip": False}))  # already false
    assert vip_lines(ok(await client.get(f"{T}/{t['id']}", headers=w["admin"]))) == []
    ok(await client.patch(profile, headers=w["admin"], json={"vip": True}))
    ok(await client.patch(profile, headers=w["admin"], json={"vip": True}))  # the same again
    assert vip_lines(ok(await client.get(f"{T}/{t['id']}", headers=w["admin"]))) == [
        "Customer marked VIP by Admin"
    ]


async def test_admins_change_the_targets(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    settings = ok(await client.get("/api/v1/sla", headers=w["neha"]))  # everyone can read
    urgent = settings["targets"][0]
    assert urgent == {
        "priority": "urgent", "first_reply_minutes": 15, "resolve_minutes": 240,
        "reads_as": "Urgent tickets get a first reply within 15 min and are resolved "
                    "within 4 h of business time.",
        "is_default": True,
    }  # fmt: skip
    assert [p["name"] for p in settings["policies"]] == [
        "VIP customers", "Live chat & messaging", "Default",
    ]  # fmt: skip
    assert settings["calendar"]["always_open"] is True

    t = await new(client, w, "urgent")
    body = {"targets": [{"priority": "urgent", "first_reply_minutes": 5, "resolve_minutes": 90}]}
    assert (
        await client.put("/api/v1/sla/targets", headers=w["neha"], json=body)
    ).status_code == 403
    changed = ok(await client.put("/api/v1/sla/targets", headers=w["admin"], json=body))
    assert changed["targets"][0]["is_default"] is False
    assert changed["targets"][0]["reads_as"].endswith("within 5 min and are resolved within "
                                                      "1 h 30 min of business time.")  # fmt: skip
    assert changed["targets"][1]["is_default"] is True  # high untouched

    # The open ticket follows the new target, in the pill and in the sort.
    now = ok(await client.get(f"{T}/{t['id']}", headers=w["admin"]))
    assert minutes_after_creation(now) == pytest.approx(5, abs=0.1)
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        stored = await s.scalar(text("SELECT sla_due_at FROM hd_ticket WHERE id = :id"),
                                {"id": t["id"]})  # fmt: skip
    assert stored == due(now)

    for bad in (
        [{"priority": "urgent", "first_reply_minutes": 60, "resolve_minutes": 30}],
        [{"priority": "urgent", "first_reply_minutes": 0, "resolve_minutes": 30}],
        [{"priority": "urgent", "first_reply_minutes": 5, "resolve_minutes": 30}] * 2,
        [{"priority": "someday", "first_reply_minutes": 5, "resolve_minutes": 30}],
    ):
        r = await client.put("/api/v1/sla/targets", headers=w["admin"], json={"targets": bad})
        assert r.status_code == 422, bad


async def test_the_clock_pauses_when_closed(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker, always_open=False)
    today = datetime.now(UTC).astimezone(ZoneInfo("Asia/Kolkata")).isoweekday()
    soon = {today % 7 + 1, (today + 1) % 7 + 1}  # open only the next two days
    await calendar(app_sessionmaker, w["ws"], days=sorted(soon))
    t = await new(client, w, "urgent")
    assert t["sla"]["paused"] is True and t["sla"]["state"] == "on_track"
    assert t["sla"]["first_reply"]["minutes_left"] == 15  # not a minute used yet
    # Due 15 minutes after 9:00 tomorrow, local time.
    local = due(t).astimezone(ZoneInfo("Asia/Kolkata"))
    assert (local.hour, local.minute) == (9, 15)
    assert local.date() - datetime.now(ZoneInfo("Asia/Kolkata")).date() == timedelta(days=1)


async def test_a_breach_shows_and_stays(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    t = await new(client, w, "urgent")
    async with workspace_session(app_sessionmaker, w["ws"]) as s:  # created 20 minutes ago
        await s.execute(text("UPDATE hd_ticket SET created_at = now() - interval '20 min'"))
    late = ok(await client.get(f"{T}/{t['id']}", headers=w["admin"]))
    assert late["sla"]["state"] == "breached"
    assert late["sla"]["first_reply"]["minutes_left"] < -4
    r = await say(client, w, t, "reply", "Sorry for the wait")
    assert r["sla"]["first_reply"]["state"] == "breached"  # a late reply doesn't undo it


async def test_sla_refresh_fills_tickets_from_before_the_upgrade(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await new(client, w, "urgent")
    async with workspace_session(app_sessionmaker, w["ws"]) as s:  # as before migration 0019
        await s.execute(text("UPDATE hd_ticket SET sla_due_at = NULL"))
    assert await refresh_everywhere(app_sessionmaker) >= 1
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        stored = await s.scalar(text("SELECT sla_due_at FROM hd_ticket"))
    assert stored == due(t)
