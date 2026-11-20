"""The report engine and built-in reports (feature-gap plan 3.1, 3.2) through the real API
with RLS on: zero-filled time series in the workspace zone, groupings, what is left out,
refused definitions, the team scope, the timeout and the concurrency cap."""

from datetime import datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from cedesk.helpdesk.repo import report_engine as engine_repo
from cedesk.helpdesk.service import report_engine
from cedesk.helpdesk.service.errors import TooManyAttempts
from tests.db.helpers import Sm, ok
from tests.db.report_helpers import desk, now, place, sql, team_only_member, ticket

pytestmark = pytest.mark.db

RUN = "/api/v1/reports/run"
ZONE = ZoneInfo("Asia/Kolkata")  # the default when a workspace has no SLA calendar


def noon(days_ago: int) -> datetime:
    """Noon, `days_ago` local days back, in the workspace's zone."""
    day = datetime.now(ZONE).date() - timedelta(days=days_ago)
    return datetime.combine(day, time(12), tzinfo=ZONE)


def run(client: AsyncClient, w: dict, who: str = "sam", **body: Any) -> Any:
    return client.post(RUN, headers=w[who], json=body)


def by_label(result: dict) -> dict[str, Any]:
    return {g["label"]: g["value"] for g in result["groups"]}


async def made(client: AsyncClient, sm: Sm, w: dict, days_ago: int, **kw: Any) -> dict:
    t = await ticket(client, w, **kw)
    await place(sm, w, t["id"], created=noon(days_ago))
    return t


async def test_the_builder_lists_what_it_offers_to_report_roles_only(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    out = ok(await client.get("/api/v1/reports/options", headers=w["sam"]))
    assert {m["key"] for m in out["metrics"]} >= {"created", "open_now", "backlog", "reopen_rate"}
    assert "tag" in out["group_by"] and "last_30d" in ranges(out) and out["max_groups"] == 50
    assert any(p["key"] == "created_per_day" for p in out["presets"])
    for who in ("neha", "lisa"):
        assert (await client.get("/api/v1/reports/options", headers=w[who])).status_code == 403
        assert (await run(client, w, who, preset="created_per_day")).status_code == 403


def ranges(options: dict) -> list[str]:
    return options["ranges"]


async def test_tickets_created_per_day_are_zero_filled_in_the_workspace_zone(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    for days_ago in (1, 1, 3):
        await made(client, app_sessionmaker, w, days_ago)
    out = ok(await run(client, w, preset="created_per_day", range={"preset": "last_7d"}))
    days = [g["key"] for g in out["groups"]]
    assert len(days) == 7 and days == sorted(days) and out["timezone"] == "Asia/Kolkata"
    counts = {g["key"]: g["value"] for g in out["groups"]}
    assert counts[noon(1).date().isoformat()] == 2
    assert counts[noon(3).date().isoformat()] == 1
    assert counts[noon(2).date().isoformat()] == 0  # a quiet day is 0, not missing
    assert out["total"] == 3 and out["total_n"] == 3 and out["unit"] == "count"


async def test_weeks_start_on_monday_and_months_on_the_first(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await made(client, app_sessionmaker, w, 2)
    weeks = ok(await run(client, w, definition={"metric": "created", "group_by": "week"}))
    assert all(datetime.fromisoformat(g["key"]).weekday() == 0 for g in weeks["groups"])
    assert sum(g["value"] for g in weeks["groups"]) == 1
    months = ok(await run(client, w, definition={"metric": "created", "group_by": "month",
                                                  "range": {"preset": "last_90d"}}))  # fmt: skip
    assert all(g["key"].endswith("-01") for g in months["groups"])


async def test_priority_team_and_channel_groupings_and_filters(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    for kw in (
        {"priority": "high", "team": "Billing"}, {"priority": "high", "team": "Billing"},
        {"priority": "low", "team": "Shipping"}, {"priority": "low"},
    ):  # fmt: skip
        await made(client, app_sessionmaker, w, 2, **kw)
    prio = ok(await run(client, w, definition={"metric": "created", "group_by": "priority"}))
    assert by_label(prio) == {"high": 2, "low": 2}
    team = ok(await run(client, w, definition={"metric": "created", "group_by": "team"}))
    assert by_label(team) == {"Billing": 2, "Shipping": 1, "No team": 1}
    only_billing = ok(
        await run(client, w, definition={"metric": "created", "group_by": "priority",
                                         "filters": {"team": w["teams"]["Billing"]}})
    )  # fmt: skip
    assert by_label(only_billing) == {"high": 2}
    assert (
        ok(await run(client, w, definition={"metric": "created", "group_by": "channel"}))["groups"][
            0
        ]["label"]
        == "web"
    )
    # Biggest first.
    assert [g["n"] for g in team["groups"]] == sorted(
        (g["n"] for g in team["groups"]), reverse=True
    )


async def test_a_tag_grouping_counts_a_ticket_in_each_of_its_tags_and_says_so(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await made(client, app_sessionmaker, w, 2, tags=["vip", "refund"])
    await made(client, app_sessionmaker, w, 2, tags=["refund"])
    await made(client, app_sessionmaker, w, 2)
    out = ok(await run(client, w, definition={"metric": "created", "group_by": "tag"}))
    assert by_label(out) == {"refund": 2, "vip": 1, "No tags": 1}
    assert out["total"] == 3 and any("each of its tags" in n for n in out["notes"])
    filtered = ok(await run(client, w, definition={"metric": "created", "filters": {"tag": "vip"}}))
    assert filtered["total"] == 1


async def test_resolved_and_the_two_averages_and_what_they_leave_out(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    sm = app_sessionmaker
    for hours, reply_min in ((2, 30), (4, 90)):
        t = await ticket(client, w, "x")
        created = noon(3)
        await place(sm, w, t["id"], created=created, resolved=created + timedelta(hours=hours),
                    first_reply=created + timedelta(minutes=reply_min))  # fmt: skip
    foreign = await ticket(client, w, "from zendesk")  # counts, but its times are not ours
    created = noon(3)
    await place(sm, w, foreign["id"], created=created, resolved=created + timedelta(days=2),
                first_reply=created + timedelta(days=1), source="zendesk")  # fmt: skip
    merged_into = await ticket(client, w, "keep")
    dup = await ticket(client, w, "dup")
    ok(await client.post(f"/api/v1/tickets/{dup['id']}/merge", headers=w["admin"],
                         json={"into": merged_into["id"]}))  # fmt: skip
    await place(sm, w, dup["id"], created=noon(3))

    resolved = ok(await run(client, w, definition={"metric": "resolved"}))
    assert resolved["total"] == 3  # two ours and the imported one; the merged duplicate is out
    avg_resolution = ok(await run(client, w, definition={"metric": "avg_resolution"}))
    assert (avg_resolution["total"], avg_resolution["total_n"], avg_resolution["unit"]) == (
        180.0,
        2,
        "minutes",
    )
    avg_first = ok(await run(client, w, definition={"metric": "avg_first_response"}))
    assert (avg_first["total"], avg_first["total_n"]) == (60.0, 2)
    only_imported = ok(
        await run(client, w, definition={"metric": "created", "filters": {"imported": "only"}})
    )
    assert only_imported["total"] == 1
    no_imported = ok(
        await run(client, w, definition={"metric": "created", "filters": {"imported": "exclude"}})
    )
    assert no_imported["total"] == 3  # two ours and "keep"; the merged duplicate is out


async def test_merged_tickets_are_left_out_of_every_metric(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    keep = await ticket(client, w, "keep")
    dup = await ticket(client, w, "dup")
    ok(await client.post(f"/api/v1/tickets/{dup['id']}/merge", headers=w["admin"],
                         json={"into": keep["id"]}))  # fmt: skip
    assert ok(await run(client, w, definition={"metric": "created"}))["total"] == 1
    assert ok(await run(client, w, definition={"metric": "open_now"}))["total"] == 1


async def test_reopen_rate_csat_and_time_logged(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    sm = app_sessionmaker
    for reopened in (0, 0, 1, 1):
        t = await ticket(client, w, "x")
        await place(sm, w, t["id"], created=noon(3), resolved=noon(2), reopen_count=reopened)
        await sql(
            sm, w["ws"],
            "INSERT INTO hd_csat (workspace_id, ticket_id, contact_id, score, source, rated_at) "
            "SELECT workspace_id, id, contact_id, :score, 'portal', :at FROM hd_ticket "
            "WHERE id = :id",
            score=5 if reopened else 3, at=noon(1), id=UUID(t["id"]),
        )  # fmt: skip
        await sql(
            sm, w["ws"],
            "INSERT INTO hd_time_log (workspace_id, ticket_id, member_id, minutes, logged_at) "
            "VALUES (:ws, :t, :m, 15, :at)",
            ws=w["ws"], t=UUID(t["id"]), m=UUID(w["ids"]["neha"]), at=noon(1),
        )  # fmt: skip
    assert ok(await run(client, w, definition={"metric": "reopen_rate"}))["total"] == 50.0
    csat = ok(await run(client, w, definition={"metric": "csat_average"}))
    assert (csat["total"], csat["unit"]) == (4.0, "score")
    assert ok(await run(client, w, definition={"metric": "csat_count"}))["total"] == 4
    logged = ok(await run(client, w, definition={"metric": "time_logged", "group_by": "assignee"}))
    assert by_label(logged) == {"Neha": 60} and logged["unit"] == "minutes"


async def test_open_now_is_as_of_now_and_cannot_be_grouped_by_time(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await made(client, app_sessionmaker, w, 40, priority="urgent")  # old, still open
    done = await ticket(client, w, "done", priority="urgent")
    await place(app_sessionmaker, w, done["id"], resolved=now())
    out = ok(await run(client, w, definition={"metric": "open_now", "group_by": "priority"}))
    assert by_label(out) == {"urgent": 1} and out["from"] is None
    bad = await run(client, w, definition={"metric": "open_now", "group_by": "day"})
    assert bad.status_code == 422 and "open now" in bad.text


async def test_refused_definitions_and_requests(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    for body in (
        {"definition": {"metric": "created", "filters": {"status": "open"}}},
        {"definition": {"metric": "revenue"}},
        {"definition": {"metric": "created", "group_by": "x'; DROP TABLE hd_ticket; --"}},
        {"definition": {"metric": "created", "range": {"preset": "forever"}}},
        {"definition": {"metric": "created", "range": {"from": "2020-01-01T00:00:00Z",
                                                        "to": "2026-01-01T00:00:00Z"}}},
        {"preset": "created_per_day", "definition": {"metric": "created"}},
        {},
        {"definition": {"metric": "created"}, "range": {"preset": "last_7d"}},
    ):  # fmt: skip
        assert (await run(client, w, **body)).status_code == 422, body
    assert (await run(client, w, preset="no_such_report")).status_code == 404
    assert (
        ok(await client.get("/api/v1/tickets", headers=w["admin"]))["tickets"] is not None
    )  # still there


async def test_a_preset_can_be_run_over_another_range(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await made(client, app_sessionmaker, w, 20)
    week = ok(await run(client, w, preset="created_per_day", range={"preset": "last_7d"}))
    month = ok(await run(client, w, preset="created_per_day", range={"preset": "last_30d"}))
    assert (len(week["groups"]), len(month["groups"])) == (7, 30)
    assert (week["total"], month["total"]) == (0, 1)
    absolute = ok(await run(
        client, w, preset="created_per_day",
        range={"from": noon(5).isoformat(), "to": noon(0).isoformat()},
    ))  # fmt: skip
    assert len(absolute["groups"]) == 6


async def test_a_team_only_role_is_held_to_its_team_in_every_run(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    for team in ("Billing", "Shipping", "Shipping"):
        await made(client, app_sessionmaker, w, 2, team=team)
    ravi = await team_only_member(client, w, "Billing")
    own = ok(
        await client.post(
            RUN, headers=ravi, json={"definition": {"metric": "created", "group_by": "team"}}
        )
    )
    assert by_label(own) == {"Billing": 1}
    asked = await client.post(
        RUN,
        headers=ravi,
        json={"definition": {"metric": "created", "filters": {"team": w["teams"]["Shipping"]}}},
    )
    assert asked.status_code == 404
    assert (
        ok(
            await client.post(
                RUN,
                headers=ravi,
                json={
                    "definition": {"metric": "created", "filters": {"team": w["teams"]["Billing"]}}
                },
            )
        )["total"]
        == 1
    )
    for_all = ok(await run(client, w, definition={"metric": "created"}))
    assert for_all["total"] == 3
    unknown = await run(
        client,
        w,
        definition={
            "metric": "created",
            "filters": {"team": "00000000-0000-4000-8000-000000000000"},
        },
    )
    assert unknown.status_code == 404


async def test_a_workspace_never_counts_anothers_tickets(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    a = await desk(client, app_sessionmaker)
    b = await desk(client, app_sessionmaker)
    await made(client, app_sessionmaker, a, 2)
    await made(client, app_sessionmaker, a, 2)
    assert ok(await run(client, a, definition={"metric": "created"}))["total"] == 2
    assert ok(await run(client, b, definition={"metric": "created"}))["total"] == 0
    assert ok(await run(client, b, definition={"metric": "open_now"}))["total"] == 0


async def test_a_range_older_than_the_archive_age_says_it_may_be_low(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    long = ok(
        await run(client, w, definition={"metric": "created", "range": {"preset": "last_365d"}})
    )
    assert long["incomplete"] is True and long["complete_from"]
    assert any("archive" in n for n in long["notes"])
    short = ok(
        await run(client, w, definition={"metric": "created", "range": {"preset": "last_30d"}})
    )
    assert short["incomplete"] is False


async def test_a_run_over_the_time_limit_asks_to_narrow_it(
    client: AsyncClient, app_sessionmaker: Sm, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = await desk(client, app_sessionmaker)

    async def slow(session: Any, *args: Any, **kwargs: Any) -> list[Any]:
        await session.execute(text("SELECT pg_sleep(3)"))
        return []

    monkeypatch.setattr(engine_repo, "run", slow)
    definition = {"metric": "created"}
    from cedesk.helpdesk.domain import report_defs as defs

    with pytest.raises(report_engine.Invalid, match="took too long"):
        await report_engine.run_definition(
            app_sessionmaker, w["ws"], None, defs.parse(definition), timeout_ms=200
        )


async def test_only_so_many_reports_run_at_once(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    app = client._transport.app  # type: ignore[attr-defined]
    app.state.report_slots = report_engine.ReportSlots(1, wait_seconds=0.2)
    async with app.state.report_slots.acquire():
        busy = await run(client, w, definition={"metric": "created"})
        assert busy.status_code == 429 and busy.headers["retry-after"] == "5"
    assert (await run(client, w, definition={"metric": "created"})).status_code == 200

    slots = report_engine.ReportSlots(2, wait_seconds=0.1)
    async with slots.acquire(), slots.acquire():
        with pytest.raises(TooManyAttempts):
            async with slots.acquire():
                pass


async def test_a_run_downloads_as_csv(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    renamed = {"name": "=HYPERLINK(1)"}
    ok(
        await client.patch(
            f"/api/v1/teams/{w['teams']['Billing']}", headers=w["admin"], json=renamed
        )
    )
    await made(client, app_sessionmaker, w, 2, team="Billing")
    r = await client.post(
        RUN,
        headers=w["sam"],
        params={"format": "csv"},
        json={"definition": {"metric": "created", "group_by": "team"}},
    )
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    body = r.content.decode("utf-8-sig")
    assert "Tickets created" in body and "Groups" in body
    assert "'=HYPERLINK(1)" in body  # a spreadsheet formula stays text
    plain = await client.post(
        RUN, headers=w["sam"], params={"format": "csv"}, json={"definition": {"metric": "created"}}
    )
    assert "Groups" not in plain.content.decode("utf-8-sig")
