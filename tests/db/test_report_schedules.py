"""Scheduled report emails (feature-gap plan 3.5) through the real API and the worker pass,
with RLS on: who may be a recipient, who sees and changes a schedule, exactly-once sending
(two workers, a restart, an outage), the checks made again at every send, a failing schedule
never starving the others, and leaving."""

import asyncio
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from cedesk.helpdesk.service import report_schedules
from cedesk.helpdesk.service.password_reset import Sender
from tests.db.helpers import PASSWORD, Sm, add, bearer, ok, sign_in
from tests.db.report_helpers import desk, now, sql, team_only_member, ticket

pytestmark = pytest.mark.db

S = "/api/v1/report-schedules"
SENDER = Sender("reports@desk.test", "CeDesk reports")
BASE = "https://desk.example"


def body(w: dict, report_id: str | None = None, **extra: Any) -> dict:
    target = {"report_id": report_id} if report_id else {"preset": "created_by_team"}
    return {
        "name": "Weekly summary", "cadence": "weekly", "weekday": 1, "hour": 9,
        "recipient_ids": [w["ids"]["sam"]],
    } | target | extra  # fmt: skip


async def make(client: AsyncClient, w: dict, who: str = "admin", **extra: Any) -> dict:
    return ok(await client.post(S, headers=w[who], json=body(w, **extra)), 201)  # type: ignore[no-any-return]


async def due_now(sm: Sm, w: dict, schedule_id: str | None = None) -> None:
    where = "WHERE id = :id" if schedule_id else ""
    params = {"id": UUID(schedule_id)} if schedule_id else {}
    await sql(sm, w["ws"],
              f"UPDATE hd_report_schedule SET next_run_at = now() - interval '1 minute' {where}",  # noqa: S608
              **params)  # fmt: skip


async def work(
    sm: Sm,
    *,
    fallback: Sender | None = SENDER,
    at: Any = None,
    budget_ms: int = 600_000,
    only: list[Any] | None = None,
) -> int:
    return await report_schedules.run_scheduled_reports(
        sm, base_url=BASE, fallback=fallback, timeout_ms=10_000, budget_ms=budget_ms, now=at,
        workspace_ids=None if only is None else [UUID(str(ws)) for ws in only],
    )  # fmt: skip


def slow_reports(monkeypatch: pytest.MonkeyPatch, seconds: float) -> list[int]:
    """Every report run takes `seconds` longer; returns the per-query timeouts it was given."""
    real = report_schedules.report_engine.run_definition
    timeouts: list[int] = []

    async def slow(*args: Any, **kwargs: Any) -> Any:
        timeouts.append(kwargs["timeout_ms"])
        await asyncio.sleep(seconds)
        return await real(*args, **kwargs)

    monkeypatch.setattr(report_schedules.report_engine, "run_definition", slow)
    return timeouts


async def emails(sm: Sm, w: dict) -> list[Any]:
    return await sql(sm, w["ws"],
                     "SELECT to_addresses, subject, body_text, from_address FROM hd_email_out "
                     "WHERE ticket_id IS NULL ORDER BY created_at")  # fmt: skip


async def row(sm: Sm, w: dict, schedule_id: str) -> Any:
    found = await sql(sm, w["ws"], "SELECT * FROM hd_report_schedule WHERE id = :id",
                      id=UUID(schedule_id))  # fmt: skip
    return found[0]


async def second_supervisor(client: AsyncClient, w: dict) -> dict[str, str]:
    await add(
        client, (w["admin"]["Authorization"].split()[1]), "sue@example.com", "supervisor", "Sue"
    )
    return bearer(await sign_in(client, w["slug"], "sue@example.com", PASSWORD))


# making and seeing -------------------------------------------------------------------------


async def test_a_schedule_names_its_report_timing_and_recipients(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    made = await make(client, w, name="  Weekly   summary ")
    assert made["name"] == "Weekly summary" and made["target"]["preset"] == "created_by_team"
    assert made["target"]["label"] == "Created by team"
    assert (made["cadence"], made["weekday"], made["hour"], made["range"]) == (
        "weekly",
        1,
        9,
        "last_7d",
    )
    assert made["timezone"] == "Asia/Kolkata" and made["enabled"] is True
    assert made["next_run_at"] and made["last_sent_at"] is None and made["last_error"] is None
    assert [r["name"] for r in made["recipients"]] == ["Sam"]
    assert (made["can_edit"], made["is_recipient"], made["owner_name"]) == (True, False, "Admin")


async def test_what_a_schedule_may_send_and_to_whom(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    saved = ok(
        await client.post(
            "/api/v1/reports/saved",
            headers=w["sam"],
            json={"name": "Mine", "definition": {"metric": "created"}},
        ),
        201,
    )
    for bad in (
        {"preset": "created_by_team", "report_id": saved["id"]},  # both
        {"preset": None},  # neither
        {"preset": "no_such_report"},
        {"preset": None, "report_id": saved["id"]},  # Sam's personal report: not Admin's to send
        {"cadence": "daily"},  # weekday with a daily cadence
        {"cadence": "weekly", "weekday": None},
        {"cadence": "monthly", "weekday": None, "day_of_month": 30},
        {"hour": 24},
        {"range": "last_year"},
        {"recipient_ids": []},
        {"recipient_ids": [w["ids"]["neha"]]},  # an agent: no reports right
        {"recipient_ids": [w["ids"]["lisa"]]},
        {"recipient_ids": ["00000000-0000-4000-8000-000000000000"]},  # not a member
        {"recipient_ids": [w["ids"]["sam"]] * 11},
    ):
        r = await client.post(S, headers=w["admin"], json=body(w) | bad)
        assert r.status_code == 422, bad
    assert (
        "Neha"
        in (
            await client.post(
                S, headers=w["admin"], json=body(w) | {"recipient_ids": [w["ids"]["neha"]]}
            )
        ).text
    )
    assert (await client.post(S, headers=w["neha"], json=body(w))).status_code == 403
    ok(
        await client.post(
            S,
            headers=w["admin"],
            json=body(w, report_id=None)
            | {"cadence": "monthly", "weekday": None, "day_of_month": 15, "range": "last_30d"},
        ),
        201,
    )


async def test_a_reader_with_fewer_teams_than_the_owner_is_refused(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await team_only_member(client, w, "Billing")  # Ravi: reports right, Billing only
    refused = await client.post(
        S, headers=w["admin"], json=body(w) | {"recipient_ids": [w["ids"]["ravi"]]}
    )
    assert (
        refused.status_code == 422
        and "Ravi" in refused.text
        and "at least as many teams" in refused.text
    )
    # The other way round is fine: a Billing-only owner may send to someone who sees everything.
    ravi = bearer(await sign_in(client, w["slug"], "ravi@example.com"))
    ok(await client.post(S, headers=ravi, json=body(w) | {"recipient_ids": [w["ids"]["sam"]]}), 201)


async def test_the_owner_recipients_and_admins_see_a_schedule_and_nobody_else(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    sue = await second_supervisor(client, w)
    mine = await make(client, w, "sam")  # Sam owns it, and also gets it
    assert [s["id"] for s in ok(await client.get(S, headers=w["sam"]))] == [mine["id"]]
    assert [s["id"] for s in ok(await client.get(S, headers=w["admin"]))] == [
        mine["id"]
    ]  # Admin sees all
    assert ok(await client.get(S, headers=sue)) == []
    assert (await client.get(f"{S}/{mine['id']}", headers=sue)).status_code == 404
    both = ok(
        await client.post(
            S,
            headers=w["admin"],
            json=body(w)
            | {"name": "For Sue", "recipient_ids": [w["ids"]["sam"], (await sue_id(client, sue))]},
        ),
        201,
    )
    got = ok(await client.get(f"{S}/{both['id']}", headers=sue))
    assert (got["is_recipient"], got["can_edit"]) == (True, False)
    assert (await client.get(S, headers=w["neha"])).status_code == 403


async def sue_id(client: AsyncClient, sue: dict) -> str:
    return ok(await client.get("/api/v1/me", headers=sue))["member"]["id"]  # type: ignore[no-any-return]


async def test_changing_a_schedule_and_who_may(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    sue = await second_supervisor(client, w)
    made = await make(client, w, "sam", cadence="weekly", range="last_30d")
    path = f"{S}/{made['id']}"
    assert (
        await client.patch(path, headers=sue, json={"name": "x"})
    ).status_code == 404  # not hers
    renamed = ok(await client.patch(path, headers=w["sam"], json={"name": "Renamed"}))
    assert renamed["name"] == "Renamed" and renamed["range"] == "last_30d"
    # A new cadence keeps the range that was chosen, and drops the weekday it no longer needs.
    monthly = ok(
        await client.patch(path, headers=w["sam"], json={"cadence": "monthly", "day_of_month": 3})
    )
    assert (monthly["cadence"], monthly["day_of_month"], monthly["weekday"], monthly["range"]) == (
        "monthly", 3, None, "last_30d",
    )  # fmt: skip
    assert (
        await client.patch(path, headers=w["sam"], json={"cadence": "monthly"})
    ).status_code in (200, 422)
    off = ok(await client.patch(path, headers=w["admin"], json={"enabled": False}))  # an Admin may
    assert off["enabled"] is False
    assert ok(await client.patch(path, headers=w["sam"], json={"enabled": True}))["enabled"] is True
    for bad in ({"hour": 25}, {"preset": "no_such"}, {"recipient_ids": [w["ids"]["neha"]]}, {}):
        assert (await client.patch(path, headers=w["sam"], json=bad)).status_code == 422, bad
    new_target = ok(await client.patch(path, headers=w["sam"], json={"preset": "csat_trend"}))
    assert new_target["target"]["preset"] == "csat_trend"


async def test_a_recipient_can_leave_and_nobody_else_can(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    sue = await second_supervisor(client, w)
    made = ok(
        await client.post(
            S,
            headers=w["admin"],
            json=body(w) | {"recipient_ids": [w["ids"]["sam"], await sue_id(client, sue)]},
        ),
        201,
    )
    leave = f"{S}/{made['id']}/leave"
    assert (
        await client.post(leave, headers=w["admin"])
    ).status_code == 404  # owner isn't a recipient
    assert (await client.post(leave, headers=w["sam"])).status_code == 204
    assert (await client.post(leave, headers=w["sam"])).status_code == 404  # already out
    assert [
        r["name"]
        for r in ok(await client.get(f"{S}/{made['id']}", headers=w["admin"]))["recipients"]
    ] == ["Sue"]
    assert (
        await client.get(f"{S}/{made['id']}", headers=w["sam"])
    ).status_code == 404  # no longer sees it


async def test_deleting_and_the_audit_trail(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    made = await make(client, w, "sam")
    ok(await client.patch(f"{S}/{made['id']}", headers=w["sam"], json={"hour": 10}))
    assert (await client.post(f"{S}/{made['id']}/leave", headers=w["sam"])).status_code == 204
    assert (await client.delete(f"{S}/{made['id']}", headers=w["sam"])).status_code == 204
    assert (await client.delete(f"{S}/{made['id']}", headers=w["sam"])).status_code == 404
    entries = ok(
        await client.get("/api/v1/audit", headers=w["admin"], params={"area": "report_schedules"})
    )
    assert sorted(e["action"] for e in entries["entries"]) == [
        "changed",
        "created",
        "deleted",
        "left",
    ]
    assert "sam@example.com" not in str(entries)  # ids and counts only


async def test_the_workspace_has_at_most_twenty_schedules_and_workspaces_stay_apart(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    other = await desk(client, app_sessionmaker)
    mine = await make(client, w)
    assert ok(await client.get(S, headers=other["admin"])) == []
    assert (await client.get(f"{S}/{mine['id']}", headers=other["admin"])).status_code == 404
    await sql(
        app_sessionmaker, w["ws"],
        "INSERT INTO hd_report_schedule (workspace_id, owner_id, name, preset, range_preset, "
        "cadence, hour, next_run_at) SELECT :ws, CAST(:o AS uuid), 's' || g, 'csat_trend', "
        "'last_7d', 'daily', 9, now() + interval '1 day' FROM generate_series(1, 19) g",
        ws=w["ws"], o=w["ids"]["admin"],
    )  # fmt: skip
    assert (await client.post(S, headers=w["admin"], json=body(w))).status_code == 422


# the worker --------------------------------------------------------------------------------


async def test_a_due_schedule_sends_one_email_each_and_moves_on(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    sue = await second_supervisor(client, w)
    await ticket(client, w, "x", team="Billing")
    made = ok(
        await client.post(
            S,
            headers=w["admin"],
            json=body(w) | {"recipient_ids": [w["ids"]["sam"], await sue_id(client, sue)]},
        ),
        201,
    )
    assert await work(app_sessionmaker) == 0  # not due yet
    await due_now(app_sessionmaker, w)
    assert await work(app_sessionmaker) == 2
    sent = await emails(app_sessionmaker, w)
    assert sorted(a for e in sent for a in e["to_addresses"]) == [
        "sam@example.com",
        "sue@example.com",
    ]
    first = sent[0]
    assert first["from_address"] == "reports@desk.test" and "Weekly summary" in first["subject"]
    for expected in (
        "Created by team",
        "Total: 1",
        "Billing: 1",
        f"{BASE}/reports",
        "Leave",
        "Asia/Kolkata",
    ):
        assert expected in first["body_text"]
    after = await row(app_sessionmaker, w, made["id"])
    assert after["last_sent_at"] is not None and after["last_error"] is None
    assert after["next_run_at"] > now()
    assert await work(app_sessionmaker) == 0  # a second pass finds nothing due


async def test_two_workers_at_once_send_a_schedule_only_once(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await make(client, w)
    await due_now(app_sessionmaker, w)
    counts = await asyncio.gather(
        work(app_sessionmaker), work(app_sessionmaker), work(app_sessionmaker)
    )
    assert sum(counts) == 1 and len(await emails(app_sessionmaker, w)) == 1


async def test_after_an_outage_it_sends_once_not_once_per_missed_week(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    made = await make(client, w)
    await sql(app_sessionmaker, w["ws"],
              "UPDATE hd_report_schedule SET next_run_at = now() - interval '40 days'")  # fmt: skip
    assert await work(app_sessionmaker) == 1
    assert await work(app_sessionmaker) == 0
    assert len(await emails(app_sessionmaker, w)) == 1
    assert (await row(app_sessionmaker, w, made["id"]))["next_run_at"] > now()


async def test_a_recipient_who_lost_the_right_is_skipped_and_none_left_says_so(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    sue = await second_supervisor(client, w)
    made = ok(
        await client.post(
            S,
            headers=w["admin"],
            json=body(w) | {"recipient_ids": [w["ids"]["sam"], await sue_id(client, sue)]},
        ),
        201,
    )
    ok(
        await client.patch(
            f"/api/v1/members/{w['ids']['sam']}", headers=w["admin"], json={"role": "agent"}
        )
    )
    await due_now(app_sessionmaker, w)
    assert await work(app_sessionmaker) == 1  # only Sue
    assert [a for e in await emails(app_sessionmaker, w) for a in e["to_addresses"]] == [
        "sue@example.com"
    ]
    ok(
        await client.patch(
            f"/api/v1/members/{await sue_id(client, sue)}",
            headers=w["admin"],
            json={"role": "agent"},
        )
    )
    await due_now(app_sessionmaker, w)
    assert await work(app_sessionmaker) == 0
    left = await row(app_sessionmaker, w, made["id"])
    assert (
        "No one left" in left["last_error"]
        and left["enabled"] is True
        and left["next_run_at"] > now()
    )


async def test_a_reader_narrower_than_the_owner_is_skipped_at_send_time(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    made = await make(client, w)  # Admin to Sam
    role = ok(
        await client.post(
            "/api/v1/roles",
            headers=w["admin"],
            json={
                "name": "Billing only",
                "scope": "team_only",
                "team_id": w["teams"]["Billing"],
                "reports": True,
            },
        ),
        201,
    )
    ok(await client.patch(f"/api/v1/members/{w['ids']['sam']}", headers=w["admin"],
                          json={"role": "custom", "role_id": role["id"]}))  # fmt: skip
    await due_now(app_sessionmaker, w)
    assert await work(app_sessionmaker) == 0
    assert "No one left" in (await row(app_sessionmaker, w, made["id"]))["last_error"]


async def test_an_owner_who_lost_the_right_switches_the_schedule_off(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    made = ok(
        await client.post(
            S, headers=w["sam"], json=body(w) | {"recipient_ids": [w["ids"]["admin"]]}
        ),
        201,
    )
    ok(
        await client.patch(
            f"/api/v1/members/{w['ids']['sam']}", headers=w["admin"], json={"role": "agent"}
        )
    )
    await due_now(app_sessionmaker, w)
    assert await work(app_sessionmaker) == 0
    off = await row(app_sessionmaker, w, made["id"])
    assert off["enabled"] is False and "reports right" in off["last_error"]
    assert await emails(app_sessionmaker, w) == []


async def test_without_a_sender_nothing_is_sent_and_the_schedule_says_why(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    made = await make(client, w)
    await due_now(app_sessionmaker, w)
    assert await work(app_sessionmaker, fallback=None) == 0
    stuck = await row(app_sessionmaker, w, made["id"])
    assert "No sender address" in stuck["last_error"] and stuck["next_run_at"] > now()
    shown = ok(await client.get(f"{S}/{made['id']}", headers=w["admin"]))
    assert "No sender address" in shown["last_error"]
    # The workspace's own default support address is used when there is one.
    ok(
        await client.post(
            "/api/v1/email-addresses",
            headers=w["admin"],
            json={"address": f"support@{w['slug']}.test", "name": "Support"},
        ),
        201,
    )
    await due_now(app_sessionmaker, w)
    assert await work(app_sessionmaker, fallback=None) == 1
    assert (await emails(app_sessionmaker, w))[0]["from_address"] == f"support@{w['slug']}.test"


async def test_one_failing_schedule_never_starves_the_others(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    saved = ok(
        await client.post(
            "/api/v1/reports/saved",
            headers=w["admin"],
            json={
                "name": "Doomed",
                "definition": {"metric": "created", "filters": {"team": w["teams"]["Billing"]}},
            },
        ),
        201,
    )
    doomed = await make(client, w, report_id=saved["id"], name="Doomed schedule")
    good = await make(client, w, name="Fine schedule")
    # Its team disappears from under it: the run raises "no such team".
    gone = '{"metric": "created", "filters": {"team": "00000000-0000-4000-8000-000000000000"}}'
    await sql(
        app_sessionmaker,
        w["ws"],
        "UPDATE hd_saved_report SET definition = CAST(:d AS jsonb)",
        d=gone,
    )
    move = (
        "UPDATE hd_report_schedule SET next_run_at = now() - CAST(:ago AS interval) WHERE id = :id"
    )
    await sql(app_sessionmaker, w["ws"], move, ago=timedelta(hours=2), id=UUID(doomed["id"]))
    await sql(app_sessionmaker, w["ws"], move, ago=timedelta(minutes=1), id=UUID(good["id"]))
    assert await work(app_sessionmaker) == 1  # the good one still went out, in the same pass
    bad = await row(app_sessionmaker, w, doomed["id"])
    assert "could not be run" in bad["last_error"] and bad["next_run_at"] > now()
    assert (await row(app_sessionmaker, w, good["id"]))["last_error"] is None
    assert await work(app_sessionmaker) == 0  # and the bad one isn't retried every minute


async def test_a_failure_after_the_run_marks_that_schedule_and_the_next_still_goes(
    client: AsyncClient, app_sessionmaker: Sm, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = await desk(client, app_sessionmaker)
    sue = await second_supervisor(client, w)
    doomed = await make(client, w, name="Doomed schedule",
                        recipient_ids=[w["ids"]["sam"], await sue_id(client, sue)])  # fmt: skip
    good = await make(client, w, name="Fine schedule")
    real = report_schedules.email_repo.enqueue
    calls = {"doomed": 0}

    async def enqueue(s: Any, *args: Any, **kwargs: Any) -> Any:
        if "Doomed" in kwargs["subject"]:
            calls["doomed"] += 1
            if calls["doomed"] == 2:  # the first email is queued, then the database errors
                await s.execute(text("SELECT 1 / 0"))
        return await real(s, *args, **kwargs)

    monkeypatch.setattr(report_schedules.email_repo, "enqueue", enqueue)
    move = (
        "UPDATE hd_report_schedule SET next_run_at = now() - CAST(:ago AS interval) WHERE id = :id"
    )
    await sql(app_sessionmaker, w["ws"], move, ago=timedelta(hours=2), id=UUID(doomed["id"]))
    await sql(app_sessionmaker, w["ws"], move, ago=timedelta(minutes=1), id=UUID(good["id"]))
    assert await work(app_sessionmaker, only=[w["ws"]]) == 1  # the next one still went out
    sent = await emails(app_sessionmaker, w)
    assert len(sent) == 1 and "Fine" in sent[0]["subject"]  # the doomed one's first email
    # was rolled back with the rest of its run: nobody got half a schedule
    bad = await row(app_sessionmaker, w, doomed["id"])
    assert bad["last_error"].startswith("Sending failed (")
    assert bad["next_run_at"] > now() and bad["last_sent_at"] is None and bad["enabled"]
    assert (await row(app_sessionmaker, w, good["id"]))["last_error"] is None
    assert await work(app_sessionmaker, only=[w["ws"]]) == 0  # not retried every minute
    assert calls["doomed"] == 2


async def test_a_pass_stops_claiming_when_its_time_budget_is_spent_and_loses_nothing(
    client: AsyncClient, app_sessionmaker: Sm, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = await desk(client, app_sessionmaker)
    made = [await make(client, w, name=f"Schedule {i}") for i in range(3)]
    await due_now(app_sessionmaker, w)
    timeouts = slow_reports(monkeypatch, 0.3)
    passes = [await work(app_sessionmaker, budget_ms=250, only=[w["ws"]]) for _ in range(4)]
    assert passes == [1, 1, 1, 0]  # one report per pass fits; the rest waited, still due
    assert len(await emails(app_sessionmaker, w)) == 3  # each once
    for schedule in made:
        assert (await row(app_sessionmaker, w, schedule["id"]))["last_sent_at"] is not None
    assert set(timeouts) == {10_000}  # each run is held to the timeout the worker passes


async def test_workspaces_take_turns_so_a_busy_one_cannot_starve_the_others(
    client: AsyncClient, app_sessionmaker: Sm, monkeypatch: pytest.MonkeyPatch
) -> None:
    busy = await desk(client, app_sessionmaker)
    quiet = await desk(client, app_sessionmaker)
    for i in range(3):
        await make(client, busy, name=f"Busy {i}")
    await make(client, quiet, name="Quiet")
    await due_now(app_sessionmaker, busy)
    await due_now(app_sessionmaker, quiet)
    slow_reports(monkeypatch, 0.3)
    # Room for two reports: one each, whichever workspace's turn comes first.
    assert await work(app_sessionmaker, budget_ms=500, only=[busy["ws"], quiet["ws"]]) == 2
    assert len(await emails(app_sessionmaker, busy)) == 1
    assert len(await emails(app_sessionmaker, quiet)) == 1
    # The busy workspace's other two are still due and go out next, each once.
    assert await work(app_sessionmaker, only=[busy["ws"], quiet["ws"]]) == 2
    assert len(await emails(app_sessionmaker, busy)) == 3


async def test_a_report_the_owner_can_no_longer_see_is_skipped(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    saved = ok(
        await client.post(
            "/api/v1/reports/saved",
            headers=w["sam"],
            json={"name": "Shared", "shared": True, "definition": {"metric": "created"}},
        ),
        201,
    )
    made = await make(client, w, "admin", report_id=saved["id"])
    await sql(
        app_sessionmaker, w["ws"], "UPDATE hd_saved_report SET shared = false"
    )  # as if unshared
    await due_now(app_sessionmaker, w)
    assert await work(app_sessionmaker) == 0
    assert "no longer available" in (await row(app_sessionmaker, w, made["id"]))["last_error"]


async def test_a_schedule_goes_with_its_saved_report(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    saved = ok(
        await client.post(
            "/api/v1/reports/saved",
            headers=w["admin"],
            json={"name": "Mine", "definition": {"metric": "created"}},
        ),
        201,
    )
    await make(client, w, report_id=saved["id"])
    assert len(ok(await client.get(S, headers=w["admin"]))) == 1
    assert (
        await client.delete(f"/api/v1/reports/saved/{saved['id']}", headers=w["admin"])
    ).status_code == 204
    assert ok(await client.get(S, headers=w["admin"])) == []


async def test_a_shared_report_another_members_schedule_sends_cannot_be_deleted(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    saved = ok(
        await client.post(
            "/api/v1/reports/saved",
            headers=w["sam"],
            json={"name": "Team numbers", "shared": True, "definition": {"metric": "created"}},
        ),
        201,
    )
    path = f"/api/v1/reports/saved/{saved['id']}"
    own = await make(client, w, "sam", report_id=saved["id"], name="Sam's own")
    theirs = await make(client, w, "admin", report_id=saved["id"], name="Admin's")
    refused = await client.delete(path, headers=w["sam"])
    assert refused.status_code == 409 and "Another member's scheduled report" in refused.text
    assert {s["id"] for s in ok(await client.get(S, headers=w["admin"]))} == {
        own["id"], theirs["id"],
    }  # fmt: skip
    assert (await client.delete(f"{S}/{theirs['id']}", headers=w["admin"])).status_code == 204
    # Only the owner's own schedule is left: it goes with the report, and that is audited.
    assert (await client.delete(path, headers=w["sam"])).status_code == 204
    assert ok(await client.get(S, headers=w["admin"])) == []
    entries = ok(
        await client.get("/api/v1/audit", headers=w["admin"], params={"area": "report_schedules"})
    )["entries"]
    gone = [e for e in entries if e["action"] == "deleted" and e["target_id"] == own["id"]]
    assert len(gone) == 1 and gone[0]["detail"] == {"report_id": saved["id"]}
    reports = ok(await client.get("/api/v1/audit", headers=w["admin"], params={"area": "reports"}))
    deleted = [e for e in reports["entries"] if e["action"] == "deleted"]
    assert deleted[0]["detail"]["schedules"] == 1


async def test_a_backlog_and_a_time_series_read_well_in_plain_text(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await ticket(client, w, "x")
    await make(client, w, name="Per day", preset="created_per_day", range="last_7d")
    await due_now(app_sessionmaker, w)
    assert await work(app_sessionmaker) == 1
    text = (await emails(app_sessionmaker, w))[0]["body_text"]
    assert "Total: 1" in text and "Tickets created" in text and text.count(": 0") >= 5
