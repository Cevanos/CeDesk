"""Demo and sample data (md D1-D3), with RLS on: what the seed makes (through the services,
so rules run), the past tickets' dates and ratings, the samples, the reset (only this
workspace), Admin only, audited, never an email, and off on a production server."""

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import seeds
from cedesk.settings import Env, Settings
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

D = "/api/v1/demo"


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    slug = await new_workspace(sm, seed=True)  # the default teams, types and rules
    token = await sign_in(client, slug, "admin@example.com")
    neha = await add(client, token, "neha@example.com", "agent", "Neha")
    me = ok(await client.get("/api/v1/me", headers=bearer(token)))
    return {"slug": slug, "h": bearer(token), "neha": neha, "me": me["member"]["id"],
            "ws": UUID(me["workspace"]["id"])}  # fmt: skip


async def rows(sm: Sm, ws: UUID, sql: str) -> list[Any]:
    async with workspace_session(sm, ws) as s:
        return list((await s.execute(text(sql))).mappings().all())


async def count(sm: Sm, ws: UUID, table: str) -> int:
    sql = f"SELECT count(*) AS n FROM {table}"  # noqa: S608 (a table name from the test)
    return int((await rows(sm, ws, sql))[0]["n"])


_TICKETS = """
SELECT t.subject, t.status, t.accepted, t.assignee_id, t.priority, t.source, t.handover,
       t.created_at, t.first_reply_at, t.resolved_at, now() AS now, tm.name AS team,
       (SELECT count(*) FROM hd_message m WHERE m.ticket_id = t.id AND m.kind = 'agent')
           AS replies,
       (SELECT count(*) FROM hd_message m WHERE m.ticket_id = t.id AND m.kind = 'note') AS notes,
       (SELECT r.score FROM hd_csat r WHERE r.ticket_id = t.id) AS score
FROM hd_ticket t LEFT JOIN hd_team tm ON tm.id = t.team_id
"""


async def audit(sm: Sm, ws: UUID) -> list[tuple[str, dict[str, Any]]]:
    got = await rows(sm, ws, "SELECT action, detail FROM hd_audit WHERE area = 'demo' ORDER BY seq")
    return [(r["action"], dict(r["detail"])) for r in got]


async def test_the_demo_seed(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    support = {"address": f"support@{w['slug']}.test", "name": "Acme support"}  # can email
    ok(await client.post("/api/v1/email-addresses", headers=w["h"], json=support), 201)
    got = ok(await client.post(f"{D}/seed", headers=w["h"]), 201)
    assert got == {"created": 16, "deleted": 0}

    tickets = {r["subject"]: r for r in await rows(sm, w["ws"], _TICKETS)}
    assert len(tickets) == 16
    assert {r["source"] for r in tickets.values()} == {"demo"}

    # The 6 samples: new, untouched, and the rules ran on them.
    samples = [tickets[t.subject] for t in seeds.DEMO_SAMPLES]
    assert all(r["status"] == "open" and r["replies"] == 0 for r in samples)
    refund = tickets[seeds.DEMO_SAMPLES[0].subject]
    assert (refund["team"], refund["priority"]) == ("Billing", "high")  # "refund" rule
    assert tickets[seeds.DEMO_SAMPLES[3].subject]["team"] == "Tech"  # "crash" rule

    # The 5 past tickets: resolved by the Admin, rated, dated back, took their hours.
    for past in seeds.DEMO_PAST:
        r = tickets[past.ticket.subject]
        assert (r["status"], r["score"], r["replies"]) == ("resolved", past.score, 1)
        assert str(r["assignee_id"]) == w["me"]
        ago = r["now"] - r["resolved_at"]
        assert timedelta(days=past.days_ago) <= ago < timedelta(days=past.days_ago, minutes=1)
        took = r["resolved_at"] - r["created_at"]
        assert timedelta(hours=past.hours) <= took < timedelta(hours=past.hours, minutes=1)
        assert r["created_at"] < r["first_reply_at"] <= r["resolved_at"]
    assert await count(sm, w["ws"], "hd_csat") == 5

    # The 4 live ones: the Admin's, worked on; one waits on the customer.
    live = [tickets[t.ticket.subject] for t in seeds.DEMO_LIVE]
    assert all(str(r["assignee_id"]) == w["me"] for r in live)
    assert [r["status"] for r in live] == ["open", "open", "pending", "open"]
    assert [(r["replies"], r["notes"]) for r in live] == [(1, 0), (0, 1), (1, 0), (1, 1)]
    assert await count(sm, w["ws"], "hd_task") == 1
    minutes = await rows(sm, w["ws"], "SELECT sum(minutes) AS m FROM hd_time_log")
    assert minutes[0]["m"] == 35

    # The handover: waiting to be accepted, with the AI's reason.
    h = tickets[seeds.DEMO_HANDOVER.ticket.subject]
    assert h["accepted"] is False and h["handover"]["reason"] == seeds.DEMO_HANDOVER.reason

    assert await count(sm, w["ws"], "hd_email_out") == 0  # nobody is ever emailed
    assert await audit(sm, w["ws"]) == [("seed", {"created": 16})]


async def test_load_sample_tickets(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    assert ok(await client.post(f"{D}/samples", headers=w["h"]), 201) == {
        "created": 6,
        "deleted": 0,
    }
    tickets = await rows(sm, w["ws"], _TICKETS)
    assert sorted(r["subject"] for r in tickets) == sorted(t.subject for t in seeds.DEMO_SAMPLES)
    assert all(r["status"] == "open" and r["replies"] == 0 for r in tickets)
    assert await audit(sm, w["ws"]) == [("samples", {"created": 6})]


async def test_the_reset(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    other = await desk(client, sm)  # another workspace keeps its tickets
    ok(await client.post(f"{D}/samples", headers=other["h"]), 201)
    ok(await client.post(f"{D}/seed", headers=w["h"]), 201)
    ok(await client.post("/api/v1/tickets", headers=w["h"], json={
        "subject": "Made by hand", "description": "x",
        "contact": {"email": "a@example.org", "name": "A"}}), 201)  # fmt: skip

    got = ok(await client.post(f"{D}/reset", headers=w["h"]))
    assert got == {"created": 16, "deleted": 17}
    tickets = await rows(sm, w["ws"], _TICKETS)
    assert len(tickets) == 16 and "Made by hand" not in {r["subject"] for r in tickets}
    assert await count(sm, w["ws"], "hd_csat") == 5  # the old ratings went with their tickets
    assert await count(sm, w["ws"], "hd_task") == 1
    seeded = ("seed", {"created": 16})
    assert await audit(sm, w["ws"]) == [seeded, ("reset", {"deleted": 17}), seeded]
    assert len(await rows(sm, other["ws"], _TICKETS)) == 6


async def test_admin_only(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    neha = bearer(await sign_in(client, w["slug"], "neha@example.com"))
    for route in ("seed", "samples", "reset"):
        assert (await client.post(f"{D}/{route}", headers=neha)).status_code == 403
        assert (await client.post(f"{D}/{route}")).status_code == 401
    assert await count(sm, w["ws"], "hd_ticket") == 0


async def test_off_in_production(app_sessionmaker: Sm) -> None:
    from cedesk.main import create_app

    url = "postgresql+asyncpg://cedesk_app:x@db/cedesk"
    settings = Settings(env=Env.production, secret_key="p" * 40, sign_in_limit=100_000,
                        database_url=url)  # fmt: skip
    app = create_app(settings)
    app.state.sessionmaker = app_sessionmaker
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        w = await desk(c, app_sessionmaker)
        for route in ("seed", "samples", "reset"):
            r = await c.post(f"{D}/{route}", headers=w["h"])
            assert r.status_code == 403
            assert "production" in r.json()["error"]["message"]
        assert await count(app_sessionmaker, w["ws"], "hd_ticket") == 0
        assert await audit(app_sessionmaker, w["ws"]) == []
