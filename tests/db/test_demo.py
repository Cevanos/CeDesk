"""Demo and sample data (md D1-D3), with RLS on: what the seed makes (through the services,
so rules run), the past tickets' dates and ratings, the samples, the reset (only demo data,
only this workspace, and nothing lost when it fails half-way), no second seed, Admin only,
audited, never an email, webhook or Slack alert, and only on a server that turned it on
and is not production."""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import seeds
from cedesk.helpdesk.domain.tickets import Channel
from cedesk.helpdesk.service import apps, ticket_tools, tickets
from cedesk.helpdesk.service.csat import CsatHandler
from cedesk.helpdesk.service.delivery import deliver_due
from cedesk.helpdesk.service.slack_alerts import SlackAlertHandler
from cedesk.helpdesk.service.tickets import NewTicket
from cedesk.helpdesk.service.webhooks import WebhookFanout
from cedesk.main import create_app
from cedesk.settings import Env, Settings
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in
from tests.db.test_jira_slack import HOOK, SEALER, install_fakes
from tests.db.test_webhooks import install

pytestmark = pytest.mark.db

D = "/api/v1/demo"
T = "/api/v1/tickets"


def server(**settings: Any) -> Settings:
    """Settings that ignore the developer's .env, so "off by default" means it."""
    return Settings(_env_file=None, sign_in_limit=100_000, **settings)  # type: ignore[call-arg]


@pytest.fixture
async def client(app_sessionmaker: Sm) -> AsyncIterator[AsyncClient]:
    """A server that turned demo data on (CEDESK_DEMO_ENABLED=true)."""
    app = create_app(server(demo_enabled=True))
    app.state.sessionmaker = app_sessionmaker
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    slug = await new_workspace(sm, seed=True)  # the default teams, types and rules
    token = await sign_in(client, slug, "admin@example.com")
    neha = await add(client, token, "neha@example.com", "agent", "Neha")
    me = ok(await client.get("/api/v1/me", headers=bearer(token)))
    return {"slug": slug, "h": bearer(token), "token": token, "neha": neha,
            "me": me["member"]["id"], "ws": UUID(me["workspace"]["id"])}  # fmt: skip


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
    assert await count(sm, w["ws"], "hd_attachment") == 0  # and no files are stored
    emails = await rows(sm, w["ws"], "SELECT email FROM hd_contact")
    assert {r["email"] for r in emails} == seeds.DEMO_EMAILS
    assert all(e.endswith(".invalid") for e in seeds.DEMO_EMAILS)
    assert await audit(sm, w["ws"]) == [("seed", {"created": 16})]

    # A second seed is refused: the demo is already there.
    r = await client.post(f"{D}/seed", headers=w["h"])
    assert r.status_code == 409 and "use reset" in r.json()["error"]["message"]
    assert r.status_code == 409 and await count(sm, w["ws"], "hd_ticket") == 16


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

    again = await client.post(f"{D}/samples", headers=w["h"])
    assert again.status_code == 409 and "use reset" in again.json()["error"]["message"]
    assert (await client.post(f"{D}/seed", headers=w["h"])).status_code == 409
    assert await count(sm, w["ws"], "hd_ticket") == 6


async def by_hand(client: AsyncClient, w: dict[str, Any], email: str = "a@example.org") -> Any:
    return ok(await client.post(T, headers=w["h"], json={
        "subject": "Made by hand", "description": "x",
        "contact": {"email": email, "name": "A"}}), 201)  # fmt: skip


async def arrived(sm: Sm, ws: UUID, subject: str, **more: Any) -> None:
    """A ticket from a channel (email) or brought by an import (`source="zendesk"`)."""
    new = NewTicket(subject=subject, contact_email="b@example.org", contact_name="B",
                    description="x", **more)  # fmt: skip
    await tickets.create_from_channel(sm, ws, new)


async def demo_ids(sm: Sm, ws: UUID) -> set[UUID]:
    return {r["id"] for r in await rows(sm, ws, "SELECT id FROM hd_ticket WHERE source = 'demo'")}


async def test_the_reset(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    other = await desk(client, sm)  # another workspace keeps its tickets
    ok(await client.post(f"{D}/samples", headers=other["h"]), 201)
    ok(await client.post(f"{D}/seed", headers=w["h"]), 201)
    await by_hand(client, w, seeds.DEMO_SAMPLES[0].email)  # a real ticket, a demo customer
    await arrived(sm, w["ws"], "Imported", source="zendesk", source_id="77")
    await arrived(sm, w["ws"], "By email", source="email", channel=Channel.email)
    old = await demo_ids(sm, w["ws"])

    got = ok(await client.post(f"{D}/reset", headers=w["h"]))
    assert got == {"created": 16, "deleted": 16}
    made = await rows(sm, w["ws"], _TICKETS)
    assert len(made) == 19
    kept = {r["subject"] for r in made if r["source"] != "demo"}
    assert kept == {"Made by hand", "Imported", "By email"}  # only demo data is touched
    new = await demo_ids(sm, w["ws"])
    assert len(new) == 16 and not new & old
    assert await count(sm, w["ws"], "hd_csat") == 5  # the old ratings went with their tickets
    assert await count(sm, w["ws"], "hd_task") == 1
    assert await count(sm, w["ws"], "hd_contact") == 17  # the 16 demo customers and B
    seeded = ("seed", {"created": 16})
    assert await audit(sm, w["ws"]) == [seeded, ("reset", {"deleted": 16, "created": 16})]
    assert len(await rows(sm, other["ws"], _TICKETS)) == 6


async def test_a_failed_reset_loses_nothing(
    client: AsyncClient, app_sessionmaker: Sm, monkeypatch: pytest.MonkeyPatch
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    ok(await client.post(f"{D}/seed", headers=w["h"]), 201)
    await by_hand(client, w)
    ids = "SELECT id FROM hd_ticket ORDER BY id"
    before = await rows(sm, w["ws"], ids)

    async def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the database went away")

    monkeypatch.setattr(ticket_tools, "log_time", boom)  # half-way through the live tickets
    try:
        r = await client.post(f"{D}/reset", headers=w["h"])
        assert r.status_code == 500
    except RuntimeError:
        pass  # the test transport passes the error on
    assert await rows(sm, w["ws"], ids) == before  # the old demo is all there, nothing new
    assert await count(sm, w["ws"], "hd_csat") == 5
    assert await audit(sm, w["ws"]) == [("seed", {"created": 16})]

    # A seed that fails leaves nothing behind either, not even its customers.
    fresh = await desk(client, sm)
    await by_hand(client, fresh, seeds.DEMO_SAMPLES[1].email)  # this customer stays
    try:
        await client.post(f"{D}/seed", headers=fresh["h"])
    except RuntimeError:
        pass
    left = await rows(sm, fresh["ws"], "SELECT t.subject, c.email FROM hd_ticket t "
                                      "JOIN hd_contact c ON c.id = t.contact_id")  # fmt: skip
    assert [(r["subject"], r["email"]) for r in left] == [
        ("Made by hand", seeds.DEMO_SAMPLES[1].email)
    ]
    assert await count(sm, fresh["ws"], "hd_contact") == 1
    assert await audit(sm, fresh["ws"]) == []

    monkeypatch.undo()  # and once it works again, so does the reset
    assert ok(await client.post(f"{D}/reset", headers=w["h"])) == {"created": 16, "deleted": 16}


async def test_one_seed_at_a_time(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    got = await asyncio.gather(*(client.post(f"{D}/seed", headers=w["h"]) for _ in range(2)))
    assert sorted(r.status_code for r in got) == [201, 409]
    assert await count(sm, w["ws"], "hd_ticket") == 16


async def test_demo_data_never_leaves(client: AsyncClient, app_sessionmaker: Sm) -> None:
    """No webhook delivery, Slack alert, CSAT survey or reply email for a demo ticket,
    even when its customer had a real address."""
    sm = app_sessionmaker
    fake, post = install_fakes(client)
    install(client)  # webhook URLs are checked against a fake internet
    w = await desk(client, sm)
    support = {"address": f"support@{w['slug']}.test", "name": "Acme support"}
    ok(await client.post("/api/v1/email-addresses", headers=w["h"], json=support), 201)
    hook = {"url": "https://hooks.example.com/cedesk", "events": ["ticket.created",
            "ticket.updated", "ticket.resolved", "message.created"]}  # fmt: skip
    made = ok(await client.post("/api/v1/webhooks", headers=w["h"], json=hook), 201)
    ok(await client.put("/api/v1/apps/slack_alerts", headers=w["h"], json={"secret": HOOK}))
    ok(await client.post(f"{D}/seed", headers=w["h"]), 201)

    # A demo customer given a real address; an agent replies by email and resolves.
    sample = seeds.DEMO_SAMPLES[0]  # email channel, High: Slack would want it
    async with workspace_session(sm, w["ws"]) as s:
        await s.execute(text("UPDATE hd_contact SET email = 'priya@example.com' "
                             "WHERE email = :e"), {"e": sample.email})  # fmt: skip
    demo = {r["subject"]: r for r in await rows(sm, w["ws"], "SELECT id, subject FROM hd_ticket")}
    t = demo[sample.subject]
    reply = {"kind": "reply", "body": "Refund on its way."}
    ok(await client.post(f"{T}/{t['id']}/messages", headers=w["h"], json=reply), 201)
    ok(await client.post(f"{T}/{t['id']}/resolve", headers=w["h"]))

    slack = SlackAlertHandler(sm, SEALER, post, apps.slack_bound, app_url="https://app.test")
    survey = CsatHandler(sm, base_url="https://portal.test", fallback=None)
    await deliver_due(sm, [WebhookFanout(sm), slack, survey])
    assert await count(sm, w["ws"], "hd_webhook_delivery") == 0
    assert fake.slack() == [apps.SLACK_TEST_LINE]  # only the line from installing
    assert await count(sm, w["ws"], "hd_email_out") == 0
    assert await count(sm, w["ws"], "hd_csat_link") == 0

    # The same handlers do act on a real ticket (so the checks above mean something).
    real = await by_hand(client, w)
    ok(await client.patch(f"{T}/{real['id']}", headers=w["h"], json={"priority": "urgent"}))
    ok(await client.post(f"{T}/{real['id']}/resolve", headers=w["h"]))
    await deliver_due(sm, [WebhookFanout(sm), slack, survey])
    assert await count(sm, w["ws"], "hd_webhook_delivery") > 0
    assert len(fake.slack()) == 2
    assert await count(sm, w["ws"], "hd_email_out") == 1  # the survey
    # The webhook goes, with its queued deliveries: other tests' workers send what is due.
    gone = await client.delete(f"/api/v1/webhooks/{made['id']}", headers=w["h"])
    assert gone.status_code == 204
    assert await count(sm, w["ws"], "hd_webhook_delivery") == 0


async def test_admin_only(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    neha = bearer(await sign_in(client, w["slug"], "neha@example.com"))
    # A custom role with the `settings` right is still not an Admin.
    role = {"name": "Ops", "settings": True, "reports": True, "assign_any": True}
    made = ok(await client.post("/api/v1/roles", headers=w["h"], json=role), 201)
    ravi = await add(client, w["token"], "ravi@example.com", "agent")
    ok(await client.patch(f"/api/v1/members/{ravi}", headers=w["h"],
                          json={"role": "custom", "role_id": made["id"]}))  # fmt: skip
    ops = bearer(await sign_in(client, w["slug"], "ravi@example.com"))
    for route in ("seed", "samples", "reset"):
        for who in (neha, ops):
            assert (await client.post(f"{D}/{route}", headers=who)).status_code == 403
        assert (await client.post(f"{D}/{route}")).status_code == 401
    assert await count(sm, w["ws"], "hd_ticket") == 0


async def refused(settings: Settings, sm: Sm, says: str) -> None:
    app = create_app(settings)
    app.state.sessionmaker = sm
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        w = await desk(c, sm)
        for route in ("seed", "samples", "reset"):
            r = await c.post(f"{D}/{route}", headers=w["h"])
            assert r.status_code == 403
            assert says in r.json()["error"]["message"]
        assert await count(sm, w["ws"], "hd_ticket") == 0
        assert await audit(sm, w["ws"]) == []


async def test_off_unless_turned_on(app_sessionmaker: Sm, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CEDESK_DEMO_ENABLED", raising=False)
    assert server().demo_enabled is False
    await refused(server(), app_sessionmaker, "CEDESK_DEMO_ENABLED=true")
    await refused(server(env=Env.staging, demo_enabled=False, secret_key="s" * 40,
                         database_url="postgresql+asyncpg://cedesk_app:x@db/cedesk"),
                  app_sessionmaker, "CEDESK_DEMO_ENABLED=true")  # fmt: skip


async def test_off_in_production_even_when_turned_on(app_sessionmaker: Sm) -> None:
    url = "postgresql+asyncpg://cedesk_app:x@db/cedesk"
    settings = server(env=Env.production, demo_enabled=True, secret_key="p" * 40,
                      database_url=url)  # fmt: skip
    await refused(settings, app_sessionmaker, "production")
