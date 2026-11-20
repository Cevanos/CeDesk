"""Snooze (feature-gap plan 1.1) with RLS on: a ticket waits in Pending until a time, the
worker's pass reopens it and tells the assignee and followers, a customer reply wakes it
early, any other status change ends it, and another workspace can't touch it."""

from typing import Any

import pytest
from httpx import AsyncClient

from cedesk.helpdesk.domain import snooze as rules
from cedesk.helpdesk.service import customer_replies
from cedesk.helpdesk.service.snooze import wake_everywhere, wake_workspace
from tests.db.helpers import Sm, ok
from tests.db.inbox_helpers import T, as_, bell, desk, events, lines, soon, sql, ticket

pytestmark = pytest.mark.db


async def snooze(client: AsyncClient, h: dict[str, str], ticket_id: str, **delta: float) -> Any:
    return await client.post(f"{T}/{ticket_id}/snooze", headers=h, json={"until": soon(**delta)})


async def test_snooze_puts_it_in_pending_until_then_and_the_list_shows_it(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await ticket(client, w, "Refund", "Billing", "Neha")
    other = await ticket(client, w, "Other", "Billing", "Neha")
    neha = await as_(client, w, "Neha")

    got = ok(await snooze(client, neha, t, hours=2))
    assert got["status"] == "pending" and got["snoozed_until"] is not None
    assert any(line.startswith("Snoozed until ") and line.endswith(" by Neha")
               for line in await lines(client, w, t))  # fmt: skip

    page = ok(await client.get(f"{T}?snoozed=true", headers=neha))
    assert [r["id"] for r in page["tickets"]] == [t]
    assert page["counts"]["snoozed"] == 1 and page["counts"]["pending"] == 1
    page = ok(await client.get(f"{T}?snoozed=false", headers=neha))
    assert [r["id"] for r in page["tickets"]] == [other]
    assert len(ok(await client.get(T, headers=neha))["tickets"]) == 2

    updated = [e["payload"]["changes"] for e in await events(app_sessionmaker, w["ws"], t)
               if e["event"] == "ticket.updated"]  # fmt: skip
    assert updated[-1] == ["status", "snoozed_until"]

    # Snoozing again moves the time; the status stays Pending.
    later = ok(await snooze(client, neha, t, days=3))
    assert later["snoozed_until"] > got["snoozed_until"] and later["status"] == "pending"


async def test_what_is_refused(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    t = await ticket(client, w, "Refund", "Billing", "Neha")
    neha = await as_(client, w, "Neha")
    assert (await snooze(client, neha, t, minutes=-5)).status_code == 422
    assert (await snooze(client, neha, t, seconds=10)).status_code == 422
    assert (await snooze(client, neha, t, days=400)).status_code == 422
    naive = await client.post(f"{T}/{t}/snooze", headers=neha, json={"until": "2030-01-01T09:00"})
    assert naive.status_code == 422
    meena = await as_(client, w, "Meena")  # light agent: reads and notes only
    assert (await snooze(client, meena, t, hours=1)).status_code == 403
    ok(await client.post(f"{T}/{t}/resolve", headers=neha))
    r = await snooze(client, neha, t, hours=1)
    assert r.status_code == 409 and "Reopen it first" in r.json()["error"]["message"]
    # If-Match: a stale ETag is refused.
    t2 = await ticket(client, w, "Two", "Billing", "Neha")
    etag = (await client.get(f"{T}/{t2}", headers=neha)).headers["ETag"]
    ok(await client.patch(f"{T}/{t2}", headers=neha, json={"priority": "high"}))
    r = await client.post(f"{T}/{t2}/snooze", headers={**neha, "If-Match": etag},
                          json={"until": soon(hours=1)})  # fmt: skip
    assert r.status_code == 409


async def test_the_worker_wakes_it_and_tells_the_assignee_and_followers(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await ticket(client, w, "Refund", "Billing", "Neha")
    later = await ticket(client, w, "Later", "Billing", "Neha")
    sam = await as_(client, w, "Sam")
    ok(await client.post(f"{T}/{t}/followers", headers=sam, json={"member_id": w["ids"]["Sam"]}))
    ok(await snooze(client, w["h"], t, hours=1))
    ok(await snooze(client, w["h"], later, hours=5))
    number = ok(await client.get(f"{T}/{t}", headers=w["h"]))["number"]
    assert await wake_workspace(app_sessionmaker, w["ws"]) == 0  # not yet

    await sql(app_sessionmaker, w["ws"],
              "UPDATE hd_ticket SET snoozed_until = now() - interval '1 minute' WHERE id = :t",
              t=t)  # fmt: skip
    assert await wake_everywhere(app_sessionmaker) >= 1
    got = ok(await client.get(f"{T}/{t}", headers=w["h"]))
    assert (got["status"], got["snoozed_until"]) == ("open", None)
    assert rules.WOKE_LINE in await lines(client, w, t)
    note = f"T-{number} is back from snooze: Refund"
    assert await bell(client, w, "Neha", "snooze_ended") == [note]
    # Newest first: the wake-up, then the snooze itself (a status change).
    assert await bell(client, w, "Sam", "following") == [
        note, f"T-{number} is now Pending (Admin): Refund"]  # fmt: skip
    still = ok(await client.get(f"{T}/{later}", headers=w["h"]))
    assert still["status"] == "pending" and still["snoozed_until"] is not None
    assert await wake_workspace(app_sessionmaker, w["ws"]) == 0  # once only
    resolved = [e for e in await events(app_sessionmaker, w["ws"], t)
                if e["event"] == "ticket.updated"]  # fmt: skip
    assert resolved[-1]["payload"]["changes"] == ["status", "snoozed_until"]


async def test_a_customer_reply_wakes_it_early(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    t = await ticket(client, w, "Refund", "Billing", "Neha")
    ok(await snooze(client, w["h"], t, days=2))
    from uuid import UUID

    done = await customer_replies.customer_reply(app_sessionmaker, w["ws"], UUID(t), "Any news?")
    assert done.outcome.value in ("added", "rules_ran")
    got = ok(await client.get(f"{T}/{t}", headers=w["h"]))
    assert (got["status"], got["snoozed_until"]) == ("open", None)
    assert rules.CUSTOMER_WOKE_LINE in await lines(client, w, t)


async def test_another_status_or_unsnooze_ends_it(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    a = await ticket(client, w, "A", "Billing", "Neha")
    b = await ticket(client, w, "B", "Billing", "Neha")
    c = await ticket(client, w, "C", "Billing", "Neha")
    for t in (a, b, c):
        ok(await snooze(client, w["h"], t, hours=3))

    got = ok(await client.patch(f"{T}/{a}", headers=w["h"], json={"status": "open"}))
    assert (got["status"], got["snoozed_until"]) == ("open", None)
    assert rules.CLEARED_LINE in await lines(client, w, a)

    got = ok(await client.delete(f"{T}/{b}/snooze", headers=w["h"]))
    assert (got["status"], got["snoozed_until"]) == ("open", None)
    assert rules.unsnoozed_line("Admin") in await lines(client, w, b)
    # Not snoozed: nothing changes.
    again = ok(await client.delete(f"{T}/{b}/snooze", headers=w["h"]))
    assert again["updated_at"] == got["updated_at"]

    # Any path that changes the status clears it in the database (here: plain SQL).
    await sql(app_sessionmaker, w["ws"], "UPDATE hd_ticket SET status = 'open' WHERE id = :t", t=c)
    got = ok(await client.get(f"{T}/{c}", headers=w["h"]))
    assert got["snoozed_until"] is None


async def test_another_workspace_cant_snooze_or_see_it(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    them = await desk(client, app_sessionmaker)
    t = await ticket(client, w, "Refund", "Billing", "Neha")
    ok(await snooze(client, w["h"], t, hours=1))
    assert (await snooze(client, them["h"], t, hours=2)).status_code == 404
    assert (await client.delete(f"{T}/{t}/snooze", headers=them["h"])).status_code == 404
    page = ok(await client.get(f"{T}?snoozed=true", headers=them["h"]))
    assert page["tickets"] == [] and page["counts"]["snoozed"] == 0
    # Their worker pass never wakes ours.
    await sql(app_sessionmaker, w["ws"],
              "UPDATE hd_ticket SET snoozed_until = now() - interval '1 minute' WHERE id = :t",
              t=t)  # fmt: skip
    assert await wake_workspace(app_sessionmaker, them["ws"]) == 0
    assert ok(await client.get(f"{T}/{t}", headers=w["h"]))["status"] == "pending"
