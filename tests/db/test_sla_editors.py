"""SLA editors (md W10-W12) with RLS on: an Admin saves the ordered policies, business
hours and holidays; open tickets follow each change at once; agents can't change them."""

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from tests.db.helpers import Sm, ok
from tests.db.test_sla import T, desk, due, minutes_after_creation, new

pytestmark = pytest.mark.db

SLA = "/api/v1/sla"
IST = ZoneInfo("Asia/Kolkata")

DEFAULT = {"name": "Default"}
PHONE = {"name": "Phone calls", "channels": ["phone"], "first_reply_minutes": 5}
VIP = {"name": "VIP customers", "vip_only": True, "first_reply_minutes": 30,
       "resolve_minutes": 240}  # fmt: skip


async def phone_ticket(client: AsyncClient, w: dict[str, Any]) -> dict[str, Any]:
    body = {"subject": "Called", "priority": "medium", "channel": "phone",
            "contact": {"email": "ana@shop.test", "name": "Ana"}}  # fmt: skip
    return ok(await client.post(T, headers=w["admin"], json=body), 201)


async def stored_due(sm: Sm, w: dict[str, Any], ticket_id: str) -> datetime:
    async with workspace_session(sm, w["ws"]) as s:
        found = await s.scalar(
            text("SELECT sla_due_at FROM hd_ticket WHERE id = :id"), {"id": ticket_id}
        )
    assert isinstance(found, datetime)
    return found


async def test_admins_save_the_ordered_policies(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    t = await phone_ticket(client, w)
    assert minutes_after_creation(t) == pytest.approx(240, abs=0.1)  # medium target

    body = {"policies": [VIP, PHONE, DEFAULT]}
    assert (await client.put(f"{SLA}/policies", headers=w["neha"], json=body)).status_code == 403
    saved = ok(await client.put(f"{SLA}/policies", headers=w["admin"], json=body))
    assert saved["policies_are_default"] is False
    assert [(p["position"], p["name"], p["channels"]) for p in saved["policies"]] == [
        (1, "VIP customers", []), (2, "Phone calls", ["phone"]), (3, "Default", []),
    ]  # fmt: skip
    # The open ticket follows the new policy at once: pill and stored due time.
    now = ok(await client.get(f"{T}/{t['id']}", headers=w["admin"]))
    assert now["sla"]["policy"] == "Phone calls"
    assert minutes_after_creation(now) == pytest.approx(5, abs=0.1)
    assert await stored_due(app_sessionmaker, w, t["id"]) == due(now)

    # Switched off: the next match (Default) applies.
    off = {"policies": [VIP, {**PHONE, "enabled": False}, DEFAULT]}
    ok(await client.put(f"{SLA}/policies", headers=w["admin"], json=off))
    now = ok(await client.get(f"{T}/{t['id']}", headers=w["admin"]))
    assert now["sla"]["policy"] == "Default"
    assert minutes_after_creation(now) == pytest.approx(240, abs=0.1)

    for bad in (
        [PHONE],  # nothing catches the rest
        [DEFAULT, PHONE],  # Default first hides Phone calls
        [PHONE, {**DEFAULT, "enabled": False}],
        [PHONE, PHONE, DEFAULT],
        [{**PHONE, "channels": ["fax"]}, DEFAULT],
        [{**PHONE, "channels": ["phone", "phone"]}, DEFAULT],
        [{**PHONE, "first_reply_minutes": 600, "resolve_minutes": 60}, DEFAULT],
        [],
    ):
        r = await client.put(f"{SLA}/policies", headers=w["admin"], json={"policies": bad})
        assert r.status_code == 422, bad
    # Back to the built-in three.
    reset = ok(await client.put(f"{SLA}/policies", headers=w["admin"], json={"policies": None}))
    assert reset["policies_are_default"] is True
    assert [p["name"] for p in reset["policies"]] == [
        "VIP customers", "Live chat & messaging", "Default",
    ]  # fmt: skip


async def test_admins_set_business_hours(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker, always_open=False)
    t = await new(client, w, "urgent")
    default = ok(await client.get(SLA, headers=w["neha"]))["calendar"]
    assert (default["days"], default["opens"], default["closes"], default["is_default"]) == (
        [1, 2, 3, 4, 5, 6], "09:00:00", "19:00:00", True,
    )  # fmt: skip

    open_all_day = {"timezone": "Europe/London", "days": [1, 2, 3, 4, 5, 6, 7],
                    "opens": "00:00", "closes": "23:59", "always_open": True}  # fmt: skip
    r = await client.put(f"{SLA}/calendar", headers=w["neha"], json=open_all_day)
    assert r.status_code == 403
    saved = ok(await client.put(f"{SLA}/calendar", headers=w["admin"], json=open_all_day))
    assert saved["calendar"] == {**saved["calendar"], "timezone": "Europe/London",
                                 "always_open": True, "is_default": False}  # fmt: skip
    # 24/7: the open ticket is due 15 minutes after it was made, wherever "now" falls.
    now = ok(await client.get(f"{T}/{t['id']}", headers=w["admin"]))
    assert minutes_after_creation(now) == pytest.approx(15, abs=0.1)
    assert now["sla"]["paused"] is False
    assert await stored_due(app_sessionmaker, w, t["id"]) == due(now)

    for bad in (
        {**open_all_day, "timezone": "Mars/Base"},
        {**open_all_day, "opens": "18:00", "closes": "09:00"},
        {**open_all_day, "days": [1, 1]},
        {**open_all_day, "days": [0]},
        {**open_all_day, "days": []},
    ):
        r = await client.put(f"{SLA}/calendar", headers=w["admin"], json=bad)
        assert r.status_code == 422, bad


async def test_admins_add_move_and_remove_holidays(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker, always_open=False)
    every_day = {"timezone": "Asia/Kolkata", "days": [1, 2, 3, 4, 5, 6, 7],
                 "opens": "00:00", "closes": "23:59"}  # fmt: skip
    ok(await client.put(f"{SLA}/calendar", headers=w["admin"], json=every_day))
    t = await new(client, w, "urgent")
    today = datetime.now(IST).date()

    holiday = {"day": today.isoformat(), "name": "Founders' Day"}
    assert (
        await client.post("/api/v1/holidays", headers=w["neha"], json=holiday)
    ).status_code == 403
    saved = ok(await client.post("/api/v1/holidays", headers=w["admin"], json=holiday), 201)
    [h] = saved["calendar"]["holidays"]
    assert (h["day"], h["name"]) == (today.isoformat(), "Founders' Day")
    # The clock doesn't run today: the open ticket is due 15 minutes into tomorrow.
    now = ok(await client.get(f"{T}/{t['id']}", headers=w["admin"]))
    local = due(now).astimezone(IST)
    assert (local.date(), local.hour, local.minute) == (today + timedelta(days=1), 0, 15)
    assert await stored_due(app_sessionmaker, w, t["id"]) == due(now)

    taken = await client.post("/api/v1/holidays", headers=w["admin"],
                              json={"day": today.isoformat(), "name": "Again"})  # fmt: skip
    assert taken.status_code == 409
    url = f"/api/v1/holidays/{h['id']}"
    moved = ok(await client.patch(url, headers=w["admin"],
                                  json={"day": "2026-12-25", "name": "Christmas"}))  # fmt: skip
    assert [(x["day"], x["name"]) for x in moved["calendar"]["holidays"]] == [
        ("2026-12-25", "Christmas")
    ]
    # Moved away from today: the ticket is due 15 minutes after it was made again (or
    # in the first minutes of tomorrow, if the test ran in the last minute of the day).
    now = ok(await client.get(f"{T}/{t['id']}", headers=w["admin"]))
    assert due(now).astimezone(IST).date() in (today, today + timedelta(days=1))
    assert minutes_after_creation(now) < 17

    assert (
        await client.patch(url, headers=w["admin"], json={"day": "1999-01-01"})
    ).status_code == 422
    assert (await client.delete(url, headers=w["neha"])).status_code == 403
    assert (await client.delete(url, headers=w["admin"])).status_code == 204
    assert ok(await client.get(SLA, headers=w["admin"]))["calendar"]["holidays"] == []
    assert (await client.delete(url, headers=w["admin"])).status_code == 404
    assert (await client.patch(url, headers=w["admin"], json={"name": "X"})).status_code == 404
    assert date.fromisoformat(h["day"]) == today
