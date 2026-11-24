"""Before and after a migration (differentiator plan #4), with RLS on and a fake source.

- the pre-migration report says what imports (with the source's counts), which source
  groups match no team, what to set up in CeDesk and what can't move; it saves nothing and
  is for Admins only;
- the cutover checklist reads what CeDesk holds: a finished import with nothing missing
  still needs a team on every open imported ticket and a support address with verified
  forwarding before it is ready;
- a sync look that reopens, answers or resolves a ticket sends nothing and runs nothing
  (no email, no event, no notification, no rule): parallel running is safe.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient

from cedesk.helpdesk.domain.imports import Group, Message
from tests.db.helpers import Sm, ok
from tests.db.test_import_sync import Source, stop_all
from tests.db.test_imports import Fake, I, end_runs, fake_desk, im, sql, start, work

pytestmark = pytest.mark.db

__all__ = ["im"]  # the fixture, shared

AT = datetime(2025, 2, 1, 8, 0, tzinfo=UTC)


async def test_the_report_says_what_imports_what_needs_setup_and_what_cannot_move(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Fake(tickets=3)
    src.group_list = [Group("g1", "Billing"), Group("g2", "VIP desk")]
    w, conn = await fake_desk(im, sm, src)

    report = ok(await im.get(f"{I}/connections/{conn['id']}/report", headers=w["admin"]))
    items = {i["key"]: i for i in report["items"]}
    assert report["source"] == "zendesk"
    assert items["tickets"] == {
        "key": "tickets", "name": "Tickets", "status": "imports", "count": 3,
        "note": items["tickets"]["note"],
    }  # fmt: skip
    assert items["contacts"]["status"] == "imports" and items["articles"]["count"] == 0
    # Billing matches the team made in `desk`; VIP desk matches none.
    assert items["groups"]["status"] == "needs_setup" and items["groups"]["count"] == 1
    assert "VIP desk" in items["groups"]["note"] and "Billing" not in items["groups"]["note"]
    assert items["automation"]["status"] == "needs_setup"
    assert items["history"]["status"] == "cannot_move"
    assert report["totals"] == {"imports": 4, "needs_setup": 6, "cannot_move": 1}

    # Nothing was saved: no run, no ticket.
    assert await sql(sm, w["ws"], "SELECT 1 FROM hd_import_run") == []
    assert await sql(sm, w["ws"], "SELECT 1 FROM hd_ticket") == []
    # Admins only; a closed connection is refused.
    r = await im.get(f"{I}/connections/{conn['id']}/report", headers=w["asha"])
    assert r.status_code == 403
    closed = await im.post(f"{I}/connections/{conn['id']}/close", headers=w["admin"])
    assert closed.status_code == 204
    r = await im.get(f"{I}/connections/{conn['id']}/report", headers=w["admin"])
    assert r.status_code == 409


async def test_the_cutover_checklist_finds_what_blocks_go_live(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Fake(tickets=2)  # resolved in the source, team Billing
    src.tickets.append(Fake.ticket(2, status="open", group_id=None))  # open, no group
    w, conn = await fake_desk(im, sm, src)
    run_id = await start(im, w, conn, kinds=["tickets"], team_map={"g1": w["team"]})
    await work(im, sm)

    found = ok(await im.get(f"{I}/runs/{run_id}/cutover", headers=w["admin"]))
    checks = {c["key"]: c for c in found["checks"]}
    assert found["run_id"] == run_id and found["source"] == "zendesk"
    assert {k: c["status"] for k, c in checks.items()} == {
        "import_finished": "ok", "counts_match": "ok", "no_open_failures": "ok",
        "sync_fresh": "skipped", "agents_matched": "ok", "teams_set": "problem",
        "email_ready": "problem",
    }  # fmt: skip
    assert found["ready"] is False
    assert checks["teams_set"]["detail"].startswith("1 open imported ticket has no team")

    # Give the open ticket a team and verify a support address: ready.
    await sql(sm, w["ws"], "UPDATE hd_ticket SET team_id = :t WHERE team_id IS NULL", t=w["team"])
    await sql(
        sm, w["ws"],
        "INSERT INTO hd_email_address (workspace_id, address, name, forwarding_verified_at) "
        "VALUES (:w, 'help@example.org', 'Help', now())", w=w["ws"],
    )  # fmt: skip
    found = ok(await im.get(f"{I}/runs/{run_id}/cutover", headers=w["admin"]))
    assert found["ready"] is True
    assert {c["status"] for c in found["checks"]} == {"ok", "skipped"}

    # Admins only; an unknown run is 404.
    assert (await im.get(f"{I}/runs/{run_id}/cutover", headers=w["asha"])).status_code == 403
    r = await im.get(f"{I}/runs/00000000-0000-0000-0000-000000000000/cutover", headers=w["admin"])
    assert r.status_code == 404
    await end_runs(sm, w["ws"])


async def test_a_syncing_import_needs_a_recent_look(im: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w, conn = await fake_desk(im, sm, Source(tickets=1))
    started = ok(
        await im.post(f"{I}/connections/{conn['id']}/run", headers=w["admin"],
                      json={"kinds": ["tickets"], "sync": True}),
        202,
    )  # fmt: skip
    await work(im, sm)
    url = f"{I}/runs/{started['id']}/cutover"
    fresh = {c["key"]: c["status"] for c in ok(await im.get(url, headers=w["admin"]))["checks"]}
    assert fresh["sync_fresh"] == "ok"

    await sql(sm, w["ws"], "UPDATE hd_import_run SET last_sync_at = now() - interval '20 minutes'")
    stale = {c["key"]: c["status"] for c in ok(await im.get(url, headers=w["admin"]))["checks"]}
    assert stale["sync_fresh"] == "problem"
    await stop_all(sm, w["ws"])


async def test_parallel_running_sends_nothing_and_runs_nothing(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src = Source(tickets=1)
    src.tickets = [Fake.ticket(0, status="open")]
    w, conn = await fake_desk(im, sm, src)
    ok(
        await im.post(f"{I}/connections/{conn['id']}/run", headers=w["admin"],
                      json={"kinds": ["tickets"], "sync": True}),
        202,
    )  # fmt: skip
    await work(im, sm)

    # In the source: the customer wrote again, an agent answered, and the ticket was
    # resolved; a new ticket came in.
    later = datetime(2025, 2, 2, 8, 0, tzinfo=UTC)
    answered = Fake.ticket(
        0, status="solved", resolved_at=later,
        messages=[
            Message("customer", "Hello 0", AT, "m0"),
            Message("customer", "Any news?", later, "m0b"),
            Message("agent", "Refunded.", later, "m0c", author_email="asha@example.com"),
        ],
    )  # fmt: skip
    src.changed = [answered, Fake.ticket(5, status="open")]
    await sql(sm, w["ws"], "UPDATE hd_import_run SET next_run_at = now()")
    await work(im, sm)
    rows: list[dict[str, Any]] = [
        dict(r) for r in await sql(sm, w["ws"], "SELECT source_id, status FROM hd_ticket")
    ]
    assert sorted((r["source_id"], r["status"]) for r in rows) == [
        ("Z0", "resolved"), ("Z5", "open"),
    ]  # fmt: skip

    # Nothing went out and nothing ran. (Adding Asha in `desk` sends a security alert.)
    assert await sql(sm, w["ws"], "SELECT 1 FROM hd_email_out") == []
    assert await sql(sm, w["ws"], "SELECT 1 FROM hd_outbox WHERE event <> 'security.alert'") == []
    assert await sql(sm, w["ws"], "SELECT 1 FROM hd_notification") == []
    assert await sql(sm, w["ws"], "SELECT 1 FROM hd_message WHERE body LIKE 'Rule applied%'") == []
    await stop_all(sm, w["ws"])
