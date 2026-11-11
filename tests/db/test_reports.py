"""The team report (md R1, R2, R6) with RLS on: exact numbers from a known set of
tickets, imported tickets left out of the time metrics (their CSAT counts), merged
tickets left out, the team filter, and who may see it."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.service import sla
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

R = "/api/v1/reports/team"
T = "/api/v1/tickets"


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    """Billing (Neha) and Shipping (Ravi); Sam is a supervisor, Meena a light agent.
    The SLA clock runs 24/7 with a 60-minute first reply for Medium."""
    slug = await new_workspace(sm)
    token = await sign_in(client, slug, "admin@example.com")
    h = bearer(token)
    ids = {n: await add(client, token, f"{n.lower()}@example.com", role, n)
           for n, role in (("Neha", "agent"), ("Ravi", "agent"), ("Sam", "supervisor"),
                           ("Meena", "light_agent"))}  # fmt: skip
    teams = {}
    for name, member in (("Billing", "Neha"), ("Shipping", "Ravi")):
        body = {"name": name, "auto_assign": False, "member_ids": [ids[member]]}
        teams[name] = ok(await client.post("/api/v1/teams", headers=h, json=body), 201)["id"]
    ok(await client.put("/api/v1/sla/targets", headers=h, json={"targets": [
        {"priority": "medium", "first_reply_minutes": 60, "resolve_minutes": 600}]}))  # fmt: skip
    ws = UUID(token.split(".")[0])
    async with workspace_session(sm, ws) as s:
        await s.execute(text(
            "INSERT INTO hd_sla_calendar (workspace_id, timezone, days, opens, closes, "
            "always_open) VALUES (:ws, 'UTC', '{1,2,3,4,5,6,7}', '00:00', '23:59', true)"),
            {"ws": ws})  # fmt: skip
    return {"slug": slug, "h": h, "ws": ws, "ids": ids, "teams": teams}


async def ticket(client: AsyncClient, w: dict[str, Any], name: str, team: str | None,
                 agent: str | None) -> str:  # fmt: skip
    body = {"subject": name, "contact": {"email": f"{name.lower()}@example.org", "name": name},
            "team_id": w["teams"][team] if team else None,
            "assignee_id": w["ids"][agent] if agent else None}  # fmt: skip
    return str(ok(await client.post(T, headers=w["h"], json=body), 201)["id"])


async def setup(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    """Times are set in the database, relative to now:
    A Billing/Neha: created 5 h ago, replied after 30 min (met), resolved after 2 h; CSAT 5.
    B Billing/Neha: created 6 h ago, replied after 2 h (missed), open; CSAT 4.
    C Shipping/Ravi: created 3 h ago, no reply (missed, breaching now).
    D Shipping/Ravi: created 10 min ago (on track).
    E Billing/Neha, imported from Zendesk: created 2 days ago, resolved 1 h ago; CSAT 2.
    G Billing/Neha: merged into A (left out).  H no team: created 20 min ago.
    D also has a rating from 40 days ago (outside the period)."""
    w = await desk(client, sm)
    t = {n: await ticket(client, w, n, team, agent) for n, team, agent in (
        ("A", "Billing", "Neha"), ("B", "Billing", "Neha"), ("C", "Shipping", "Ravi"),
        ("D", "Shipping", "Ravi"), ("E", "Billing", "Neha"), ("G", "Billing", "Neha"),
        ("H", None, None))}  # fmt: skip
    now = datetime.now(UTC)
    h = timedelta(hours=1)
    times = {  # created, first reply, resolved
        "A": (now - 5 * h, now - 4.5 * h, now - 3 * h),
        "B": (now - 6 * h, now - 4 * h, None),
        "C": (now - 3 * h, None, None),
        "D": (now - timedelta(minutes=10), None, None),
        "E": (now - 48 * h, now - 24 * h, now - h),
        "G": (now - h, None, None),
        "H": (now - timedelta(minutes=20), None, None),
    }
    async with workspace_session(sm, w["ws"]) as s:
        for name, (created, replied, resolved) in times.items():
            await s.execute(text(
                "UPDATE hd_ticket SET created_at = :c, first_reply_at = :f, "
                "resolved_at = CAST(:r AS timestamptz), status = CASE WHEN CAST(:r AS "
                "timestamptz) IS NULL THEN 'open' ELSE 'resolved' END WHERE id = :id"),
                {"c": created, "f": replied, "r": resolved, "id": t[name]})  # fmt: skip
        await s.execute(text("UPDATE hd_ticket SET source = 'zendesk', source_id = 'z-1' "
                             "WHERE id = :id"), {"id": t["E"]})  # fmt: skip
        await s.execute(text("UPDATE hd_ticket SET merged_into_id = :a WHERE id = :id"),
                        {"a": t["A"], "id": t["G"]})  # fmt: skip
        for name, score, at in (("A", 5, now - h), ("B", 4, now - h), ("E", 2, now - h),
                                ("D", 1, now - timedelta(days=40))):  # fmt: skip
            await s.execute(text(
                "INSERT INTO hd_csat (workspace_id, ticket_id, contact_id, score, source, "
                "rated_at) SELECT workspace_id, id, contact_id, :score, 'email', :at "
                "FROM hd_ticket WHERE id = :id"),
                {"score": score, "at": at, "id": t[name]})  # fmt: skip
        await sla.refresh(s, all_open=True)
    return w | {"t": t}


async def test_the_team_report_numbers(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await setup(client, app_sessionmaker)
    report = ok(await client.get(R, headers=w["h"]))
    assert report["summary"] == {
        "open": 4,  # B, C, D, H (G is merged)
        "open_breaching": 1,  # C
        "created": 6,  # A B C D E H
        "resolved": 2,  # A, E
        "avg_first_response_minutes": 75.0,  # A 30, B 120 (E is imported)
        "avg_resolution_minutes": 120.0,  # A only (E is imported)
        "first_reply_sla_met_percent": 33.3,  # A met; B and C missed; D, H still running
        "first_reply_sla_measured": 3,
        "csat": {"ratings": 3, "average": 3.67, "happy_percent": 66.7,
                 "by_score": {"5": 1, "4": 1, "3": 0, "2": 1, "1": 0}},  # E's counts, D's is old
    }  # fmt: skip
    from_, to = datetime.fromisoformat(report["from"]), datetime.fromisoformat(report["to"])
    assert to - from_ == timedelta(days=30)
    assert [(r["name"], r["open"], r["created"], r["resolved"], r["avg_first_response_minutes"],
             r["csat_ratings"], r["csat_average"]) for r in report["teams"]] == [
        ("Billing", 1, 3, 2, 75.0, 3, 3.67),
        ("Shipping", 2, 2, 0, None, 0, None),
        ("No team", 1, 1, 0, None, 0, None),
    ]  # fmt: skip
    assert [
        (
            a["name"],
            a["role"],
            a["status"],
            a["open"],
            a["resolved"],
            a["avg_first_response_minutes"],
            a["csat_ratings"],
        )
        for a in report["agents"]
    ] == [
        ("Admin", "admin", "offline", 0, 0, None, 0),
        ("Neha", "agent", "available", 1, 2, 75.0, 3),
        ("Ravi", "agent", "available", 2, 0, None, 0),
        ("Sam", "supervisor", "available", 0, 0, None, 0),
    ]  # fmt: skip  (Meena, a light agent, is not listed)


async def test_one_team_and_a_shorter_period(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await setup(client, app_sessionmaker)
    shipping = ok(await client.get(R, headers=w["h"], params={"team_id": w["teams"]["Shipping"]}))
    got = shipping["summary"]
    assert (got["open"], got["open_breaching"], got["created"], got["csat"]["ratings"]) == (
        2, 1, 2, 0)  # fmt: skip
    assert [r["name"] for r in shipping["teams"]] == ["Shipping"]
    assert [a["name"] for a in shipping["agents"]] == ["Ravi"]

    since = (datetime.now(UTC) - timedelta(minutes=90)).isoformat()
    recent = ok(await client.get(R, headers=w["h"], params={"from": since}))
    assert (recent["summary"]["created"], recent["summary"]["resolved"]) == (2, 1)  # D H; E
    assert recent["summary"]["open"] == 4  # "now", whatever the period


async def test_who_may_see_it_and_bad_periods(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    for who, code in (("neha", 403), ("meena", 403), ("sam", 200)):
        r = await client.get(
            R, headers=bearer(await sign_in(client, w["slug"], f"{who}@example.com"))
        )
        assert r.status_code == code, who
    empty = ok(await client.get(R, headers=w["h"]))["summary"]
    assert (empty["avg_first_response_minutes"], empty["first_reply_sla_met_percent"],
            empty["csat"]["happy_percent"]) == (None, None, None)  # nothing to measure  # fmt: skip
    now = datetime.now(UTC)
    for params in ({"from": now.isoformat(), "to": (now - timedelta(days=1)).isoformat()},
                   {"from": "2026-11-01T00:00:00"},  # no time zone
                   {"from": (now - timedelta(days=400)).isoformat()}):  # fmt: skip
        assert (await client.get(R, headers=w["h"], params=params)).status_code == 422, params
    r = await client.get(R, headers=w["h"], params={"team_id": str(UUID(int=5))})
    assert r.status_code == 404
    # Another workspace's team is unknown here too.
    other = await desk(client, app_sessionmaker)
    r = await client.get(R, headers=w["h"], params={"team_id": other["teams"]["Billing"]})
    assert r.status_code == 404
