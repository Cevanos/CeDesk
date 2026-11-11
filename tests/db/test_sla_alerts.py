"""SLA breach alerts (md W14) with RLS on: when an open ticket's clock runs out, its
assignee and the Supervisors of its team are told once per clock (first reply, then
resolution); unassigned tickets without a team Supervisor, resolved and merged tickets,
and inactive members get nothing."""

from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.service import customer_replies, sla
from cedesk.helpdesk.service.sla_alerts import alert_everywhere, alert_workspace
from tests.db.helpers import Sm, bearer, ok, sign_in
from tests.db.test_reports import T, desk, ticket

pytestmark = pytest.mark.db


async def made_ago(sm: Sm, w: dict[str, Any], ticket_id: str, minutes: int) -> None:
    """As if the ticket was made `minutes` ago and that time passed (medium: first reply
    60, resolve 600). Moving `created_at` is a change, which would count its breach as
    alerted; time passing doesn't, so the alert state is kept as it was."""
    async with workspace_session(sm, w["ws"]) as s:
        before = await s.scalar(text("SELECT sla_alerted_due FROM hd_ticket WHERE id = :id"),
                                {"id": ticket_id})  # fmt: skip
        await s.execute(
            text("UPDATE hd_ticket SET created_at = now() - make_interval(mins => :m) "
                 "WHERE id = :id"), {"m": minutes, "id": ticket_id})  # fmt: skip
        await sla.refresh(s, ticket_ids=[UUID(ticket_id)])
        await s.execute(text("UPDATE hd_ticket SET sla_alerted_due = :b WHERE id = :id"),
                        {"b": before, "id": ticket_id})  # fmt: skip


async def inbox(client: AsyncClient, w: dict[str, Any], who: str) -> list[str]:
    h = bearer(await sign_in(client, w["slug"], f"{who.lower()}@example.com"))
    found = ok(await client.get("/api/v1/notifications", headers=h))["notifications"]
    return [n["text"] for n in found if n["kind"] == "sla_breach"]


async def test_the_assignee_and_team_supervisors_hear_once_per_clock(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    body = {"name": "Billing", "member_ids": [w["ids"]["Neha"], w["ids"]["Sam"]]}
    ok(await client.patch(f"/api/v1/teams/{w['teams']['Billing']}", headers=w["h"], json=body))
    late = await ticket(client, w, "Refund", "Billing", "Neha")
    on_time = await ticket(client, w, "Fresh", "Billing", "Neha")
    await made_ago(app_sessionmaker, w, late, 90)
    number = ok(await client.get(f"{T}/{late}", headers=w["h"]))["number"]

    assert await alert_workspace(app_sessionmaker, w["ws"]) == 1
    note = f"SLA breached on T-{number} (first reply overdue): Refund"
    assert await inbox(client, w, "Neha") == [note]
    assert await inbox(client, w, "Sam") == [note]  # Billing's Supervisor
    assert await inbox(client, w, "Ravi") == []  # not his ticket, not his team
    # The next minute: nothing new.
    assert await alert_workspace(app_sessionmaker, w["ws"]) == 0

    # Replied (late): the resolve clock runs now, due 600 minutes after creation.
    ok(await client.post(f"{T}/{late}/messages", headers=w["h"],
                         json={"kind": "reply", "body": "On it"}), 201)  # fmt: skip
    assert await alert_workspace(app_sessionmaker, w["ws"]) == 0
    await made_ago(app_sessionmaker, w, late, 700)
    assert await alert_everywhere(app_sessionmaker) >= 1
    resolution = f"SLA breached on T-{number} (resolution overdue): Refund"
    assert await inbox(client, w, "Neha") == [resolution, note]  # newest first
    assert await alert_workspace(app_sessionmaker, w["ws"]) == 0
    assert on_time  # never late, never alerted
    assert len(await inbox(client, w, "Neha")) == 2


async def test_who_is_left_out(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    nobody = await ticket(client, w, "Nobody", None, None)  # no team, no assignee
    resolved = await ticket(client, w, "Done", "Shipping", "Ravi")
    gone = await ticket(client, w, "Gone", "Shipping", "Ravi")  # Ravi deactivated
    for t in (nobody, resolved, gone):
        await made_ago(app_sessionmaker, w, t, 90)
    ok(await client.post(f"{T}/{resolved}/resolve", headers=w["h"]))
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(text("UPDATE member SET is_active = false WHERE id = :id"),
                        {"id": w["ids"]["Ravi"]})  # fmt: skip

    # The unassigned one and Ravi's open one are marked; no one is told.
    assert await alert_workspace(app_sessionmaker, w["ws"]) == 2
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        found = await s.scalar(text("SELECT count(*) FROM hd_notification "
                                    "WHERE kind = 'sla_breach'"))  # fmt: skip
        alerted = await s.scalar(text("SELECT count(*) FROM hd_ticket "
                                      "WHERE sla_alerted_due IS NOT NULL"))  # fmt: skip
    assert (found, alerted) == (0, 2)
    assert await alert_workspace(app_sessionmaker, w["ws"]) == 0


async def test_a_batch_leaves_the_rest_for_the_next_minute(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    for n in range(3):
        await made_ago(app_sessionmaker, w, await ticket(client, w, f"T{n}", None, "Neha"), 90)
    assert await alert_workspace(app_sessionmaker, w["ws"], batch=2) == 2
    assert await alert_workspace(app_sessionmaker, w["ws"], batch=2) == 1
    assert len(await inbox(client, w, "Neha")) == 3


async def test_a_breach_made_by_a_change_is_not_alerted(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """Alerts are for a clock running out. Shorter targets, or a reopened old ticket, put
    the due time in the past at once: the pill shows it, nobody is woken up for it."""
    w = await desk(client, app_sessionmaker)
    old = await ticket(client, w, "Old", "Billing", "Neha")
    await made_ago(app_sessionmaker, w, old, 30)  # 30 of 60 minutes used: on time
    late = await ticket(client, w, "Late", "Billing", "Neha")
    await made_ago(app_sessionmaker, w, late, 90)  # time ran out: still to be alerted

    # The Admin halves the Medium first-reply target: "Old" is now overdue too.
    ok(await client.put("/api/v1/sla/targets", headers=w["h"], json={"targets": [
        {"priority": "medium", "first_reply_minutes": 20, "resolve_minutes": 600}]}))  # fmt: skip
    assert ok(await client.get(f"{T}/{old}", headers=w["h"]))["sla"]["state"] == "breached"
    assert await alert_workspace(app_sessionmaker, w["ws"]) == 1  # only "Late"
    assert [n.split(":")[1].strip() for n in await inbox(client, w, "Neha")] == ["Late"]
    # Shorter again: both due times move, still in the past. "Late" was alerted already.
    ok(await client.put("/api/v1/sla/targets", headers=w["h"], json={"targets": [
        {"priority": "medium", "first_reply_minutes": 15, "resolve_minutes": 600}]}))  # fmt: skip
    assert await alert_workspace(app_sessionmaker, w["ws"]) == 0

    # Resolved in time, reopened by the customer 11 hours later: the resolution clock
    # (600 minutes) is already past. No alert for it.
    fresh = await ticket(client, w, "Fresh", "Billing", "Neha")
    ok(await client.post(f"{T}/{fresh}/resolve", headers=w["h"]))
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(text("UPDATE hd_ticket SET created_at = now() - interval '11 hours' "
                             "WHERE id = :id"), {"id": fresh})  # fmt: skip
    await customer_replies.customer_reply(app_sessionmaker, w["ws"], UUID(fresh), "Still broken")
    assert ok(await client.get(f"{T}/{fresh}", headers=w["h"]))["status"] == "open"
    assert await alert_workspace(app_sessionmaker, w["ws"]) == 0
    assert len(await inbox(client, w, "Neha")) == 1
    # The same for "Late", which was alerted (first reply) before it was resolved.
    ok(await client.post(f"{T}/{late}/resolve", headers=w["h"]))
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(text("UPDATE hd_ticket SET created_at = now() - interval '11 hours' "
                             "WHERE id = :id"), {"id": late})  # fmt: skip
    await customer_replies.customer_reply(app_sessionmaker, w["ws"], UUID(late), "Again")
    assert await alert_workspace(app_sessionmaker, w["ws"]) == 0
    assert len(await inbox(client, w, "Neha")) == 1
