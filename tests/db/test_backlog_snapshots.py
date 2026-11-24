"""Daily backlog snapshots and GET /reports/backlog (feature-gap plan 3.1) through the real
API with RLS on: what a snapshot counts, that writing one twice is harmless, who may read
them, team scope, and the 3 year pruning."""

from datetime import timedelta

import pytest
from httpx import AsyncClient

from cedesk.helpdesk.service import backlog
from tests.db.helpers import Sm, ok
from tests.db.report_helpers import desk, now, place, sql, team_only_member, ticket

pytestmark = pytest.mark.db

B = "/api/v1/reports/backlog"
KOLKATA_DAY_AFTER = now().replace(hour=22, minute=30, second=0, microsecond=0)  # 04:00 next day IST


async def snapshots(sm: Sm, w: dict, **where: object) -> list:
    return await sql(
        sm, w["ws"],
        "SELECT day, kind, team_id, open, pending, breaching FROM hd_backlog_snapshot "
        "ORDER BY day, kind, team_id NULLS FIRST",
    )  # fmt: skip


async def test_a_pass_writes_the_total_each_team_and_no_team(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await ticket(client, w, "a", team="Billing")
    pending = await ticket(client, w, "b", team="Billing")
    await ticket(client, w, "c", team="Shipping")
    await ticket(client, w, "d")  # no team
    resolved = await ticket(client, w, "e", team="Billing")
    await place(app_sessionmaker, w, resolved["id"], resolved=now())
    await sql(app_sessionmaker, w["ws"], "UPDATE hd_ticket SET status = 'pending' WHERE id = :i",
              i=__import__("uuid").UUID(pending["id"]))  # fmt: skip

    assert await backlog.run_snapshots(app_sessionmaker, now=KOLKATA_DAY_AFTER) >= 1
    rows = await snapshots(app_sessionmaker, w)
    by = {(r["kind"], str(r["team_id"]) if r["team_id"] else None): r for r in rows}
    assert (by["total", None]["open"], by["total", None]["pending"]) == (3, 1)  # resolved left out
    assert (
        by["team", w["teams"]["Billing"]]["open"],
        by["team", w["teams"]["Billing"]]["pending"],
    ) == (1, 1)
    assert by["team", w["teams"]["Shipping"]]["open"] == 1
    assert by["no_team", None]["open"] == 1
    assert len({r["day"] for r in rows}) == 1


async def test_a_second_pass_changes_nothing_and_a_rewrite_keeps_one_row(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await ticket(client, w, "a", team="Billing")
    await backlog.capture_workspace(app_sessionmaker, w["ws"], KOLKATA_DAY_AFTER)
    before = await snapshots(app_sessionmaker, w)
    assert await backlog.capture_workspace(app_sessionmaker, w["ws"], KOLKATA_DAY_AFTER) is False
    assert await snapshots(app_sessionmaker, w) == before

    # The statement itself is an upsert: running it again for the same day updates the rows.
    await ticket(client, w, "b", team="Billing")
    from cedesk.db import workspace_session
    from cedesk.helpdesk.repo import backlog as repo

    day = before[0]["day"]
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await repo.capture(s, w["ws"], day, KOLKATA_DAY_AFTER)
    after = await snapshots(app_sessionmaker, w)
    assert len(after) == len(before)
    assert [r["open"] for r in after if r["kind"] == "total"] == [2]


async def test_merged_tickets_and_imported_breaches_are_left_out(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    keep = await ticket(client, w, "keep")
    dup = await ticket(client, w, "dup")
    ok(await client.post(f"/api/v1/tickets/{dup['id']}/merge", headers=w["admin"],
                         json={"into": keep["id"]}))  # fmt: skip
    imported = await ticket(client, w, "from zendesk")
    await place(app_sessionmaker, w, imported["id"], source="zendesk")
    await sql(
        app_sessionmaker, w["ws"], "UPDATE hd_ticket SET sla_due_at = now() - interval '2 days'"
    )  # all past due
    await backlog.capture_workspace(app_sessionmaker, w["ws"], KOLKATA_DAY_AFTER)
    total = next(r for r in await snapshots(app_sessionmaker, w) if r["kind"] == "total")
    assert (total["open"], total["breaching"]) == (
        2,
        1,
    )  # the merged one is out; the imported one never breaches


async def test_the_report_lists_days_with_a_snapshot_and_says_where_history_starts(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await ticket(client, w, "a", team="Billing")
    # Due well after the snapshot: on the real SLA clock it was already past due at 22:30
    # UTC on some runs, so `breaching` depended on the time of day (CI, 23 Nov).
    await sql(app_sessionmaker, w["ws"], "UPDATE hd_ticket SET sla_due_at = :at",
              at=KOLKATA_DAY_AFTER + timedelta(days=2))  # fmt: skip
    empty = ok(await client.get(B, headers=w["sam"]))
    assert (empty["days"], empty["first_day"]) == ([], None)

    await backlog.capture_workspace(app_sessionmaker, w["ws"], KOLKATA_DAY_AFTER)
    out = ok(await client.get(B, headers=w["sam"]))
    assert len(out["days"]) == 1
    day = out["days"][0]
    assert (day["open"], day["pending"], day["breaching"]) == (1, 0, 0)
    assert day["taken_at"] and out["first_day"] == day["day"] and out["timezone"]
    mine = ok(await client.get(B, headers=w["sam"], params={"team_id": w["teams"]["Billing"]}))
    assert mine["days"][0]["open"] == 1
    other = ok(await client.get(B, headers=w["sam"], params={"team_id": w["teams"]["Shipping"]}))
    assert other["days"][0]["open"] == 0
    assert (
        await client.get(
            B, headers=w["sam"], params={"team_id": "00000000-0000-4000-8000-000000000000"}
        )
    ).status_code == 404


async def test_only_report_roles_read_it_and_a_bad_period_is_refused(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    for who in ("neha", "lisa"):
        assert (await client.get(B, headers=w[who])).status_code == 403
    assert (await client.get(B, headers=w["admin"])).status_code == 200
    bad = {"from": "2026-01-01T00:00:00+00:00", "to": "2027-06-01T00:00:00+00:00"}
    assert (await client.get(B, headers=w["admin"], params=bad)).status_code == 422


async def test_a_team_only_role_reads_its_own_team_and_no_other(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await ticket(client, w, "a", team="Billing")
    await ticket(client, w, "b", team="Shipping")
    await ticket(client, w, "c", team="Shipping")
    await backlog.capture_workspace(app_sessionmaker, w["ws"], KOLKATA_DAY_AFTER)
    ravi = await team_only_member(client, w, "Billing")
    own = ok(await client.get(B, headers=ravi))
    assert own["team_id"] == w["teams"]["Billing"] and own["days"][0]["open"] == 1
    assert (
        await client.get(B, headers=ravi, params={"team_id": w["teams"]["Shipping"]})
    ).status_code == 404


async def test_one_workspace_never_reads_anothers_snapshots(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    a = await desk(client, app_sessionmaker)
    b = await desk(client, app_sessionmaker)
    await ticket(client, a, "mine")
    await backlog.capture_workspace(app_sessionmaker, a["ws"], KOLKATA_DAY_AFTER)
    await backlog.capture_workspace(app_sessionmaker, b["ws"], KOLKATA_DAY_AFTER)
    assert ok(await client.get(B, headers=a["admin"]))["days"][0]["open"] == 1
    assert ok(await client.get(B, headers=b["admin"]))["days"][0]["open"] == 0


async def test_the_csv_has_one_row_per_day_with_when_it_was_taken(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await ticket(client, w, "a")
    await backlog.capture_workspace(app_sessionmaker, w["ws"], KOLKATA_DAY_AFTER)
    r = await client.get(B, headers=w["admin"], params={"format": "csv"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    text = r.content.decode("utf-8-sig")
    assert "Day,Open,Pending,Breaching,Taken at" in text and "Backlog by day" in text


async def test_snapshots_older_than_three_years_are_pruned(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    old = (now() - timedelta(days=3 * 365 + 10)).date()
    recent = (now() - timedelta(days=10)).date()
    await sql(app_sessionmaker, w["ws"],
              "INSERT INTO hd_backlog_snapshot (workspace_id, day, kind, open, pending, breaching) "
              "VALUES (:ws, :old, 'total', 1, 0, 0), (:ws, :recent, 'total', 2, 0, 0)",
              ws=w["ws"], old=old, recent=recent)  # fmt: skip
    assert await backlog.prune_everywhere(app_sessionmaker) >= 1
    assert [r["day"] for r in await snapshots(app_sessionmaker, w)] == [recent]
