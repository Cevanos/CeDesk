"""The live view (feature-gap plan 3.4) through the real API with RLS on: the counts, who has
waited longest, what is due on the SLA clock, snoozed and imported tickets, the team scope,
and the shared short-lived answer."""

import asyncio
from datetime import timedelta
from uuid import UUID

import pytest
from httpx import AsyncClient

from cedesk.helpdesk.service.live import LiveCache
from tests.db.helpers import Sm, ok
from tests.db.report_helpers import desk, now, place, sql, team_only_member, ticket

pytestmark = pytest.mark.db

L = "/api/v1/reports/live"


async def due(sm: Sm, w: dict, ticket_id: str, delta: timedelta) -> None:
    await sql(sm, w["ws"], "UPDATE hd_ticket SET sla_due_at = :at WHERE id = :id",
              at=now() + delta, id=UUID(ticket_id))  # fmt: skip


async def queue(client: AsyncClient, sm: Sm, w: dict) -> dict[str, dict]:
    """Billing: a (waiting 3 h, due in 20 min), b (replied, due in 5 h), c (waiting 1 h,
    past due), d (pending). Shipping: e (open, snoozed's twin), f (snoozed, past due).
    g: resolved. h: imported, past due."""
    t = {
        "a": await ticket(client, w, "a", team="Billing"),
        "b": await ticket(client, w, "b", team="Billing"),
        "c": await ticket(client, w, "c", team="Billing"),
        "d": await ticket(client, w, "d", team="Billing"),
        "e": await ticket(client, w, "e", team="Shipping"),
        "f": await ticket(client, w, "f", team="Shipping"),
        "g": await ticket(client, w, "g", team="Shipping"),
        "h": await ticket(client, w, "h"),
    }
    await place(sm, w, t["a"]["id"], created=now() - timedelta(hours=3))
    await place(sm, w, t["b"]["id"], created=now() - timedelta(hours=2), first_reply=now())
    await place(sm, w, t["c"]["id"], created=now() - timedelta(hours=1))
    await place(sm, w, t["g"]["id"], resolved=now())
    await place(sm, w, t["h"]["id"], source="zendesk")
    # Every open ticket's due time is set here: d and e kept the real SLA clock, which
    # follows business hours, so at some times of day they fell inside a window and the
    # counts changed (CI, 23 Nov: due_soon had 2 more).
    for key, delta in (("a", timedelta(minutes=20)), ("b", timedelta(hours=5)),
                       ("c", -timedelta(hours=1)), ("d", timedelta(days=3)),
                       ("e", timedelta(days=3)), ("f", -timedelta(days=1)),
                       ("h", -timedelta(days=3))):  # fmt: skip
        await due(sm, w, t[key]["id"], delta)
    await sql(sm, w["ws"], "UPDATE hd_ticket SET status = 'pending' WHERE id = :id",
              id=UUID(t["d"]["id"]))  # fmt: skip
    await sql(sm, w["ws"],
              "UPDATE hd_ticket SET status = 'pending', snoozed_until = now() + interval '1 day' "
              "WHERE id = :id", id=UUID(t["f"]["id"]))  # fmt: skip
    return t


async def test_the_queue_right_now(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    t = await queue(client, app_sessionmaker, w)
    out = ok(await client.get(L, headers=w["sam"]))
    total = out["total"]
    # open: a b c e h; pending (not snoozed): d; snoozed: f; the resolved one is out.
    assert (total["open"], total["pending"], total["snoozed"]) == (5, 1, 1)
    assert total["breached"] == 1  # c only: h is imported, f is snoozed
    assert total["at_risk"] == 1  # a (20 min): b is 5 h away
    assert out["window_minutes"] == 60 and out["team_id"] is None
    teams = {x["name"]: x for x in out["teams"]}
    assert (teams["Billing"]["open"], teams["Billing"]["pending"]) == (3, 1)
    assert (teams["Shipping"]["open"], teams["Shipping"]["snoozed"]) == (1, 1)
    assert teams["No team"]["open"] == 1

    # Waiting for a first reply: open, not snoozed, no agent reply yet, oldest first.
    assert [x["id"] for x in out["waiting"][:2]] == [t["a"]["id"], t["c"]["id"]]
    assert out["waiting"][0]["waiting_minutes"] >= 179 and out["waiting"][0]["label"].startswith(
        "T-"
    )
    assert t["b"]["id"] not in {x["id"] for x in out["waiting"]}  # it has a reply
    assert t["h"]["id"] not in {x["id"] for x in out["waiting"]}  # imported: not ours
    assert t["f"]["id"] not in {x["id"] for x in out["waiting"]}  # snoozed
    assert [x["id"] for x in out["due_soon"]] == [t["a"]["id"]]
    assert 15 <= out["due_soon"][0]["minutes_left"] <= 20


async def test_a_wider_window_catches_more_and_a_team_narrows_it(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await queue(client, app_sessionmaker, w)
    wide = ok(await client.get(L, headers=w["sam"], params={"window_minutes": 360}))
    assert [x["id"] for x in wide["due_soon"]] == [t["a"]["id"], t["b"]["id"]]
    assert wide["total"]["at_risk"] == 2
    billing = ok(await client.get(L, headers=w["sam"], params={"team_id": w["teams"]["Billing"]}))
    assert billing["team_id"] == w["teams"]["Billing"] and billing["total"]["open"] == 3
    assert [x["name"] for x in billing["teams"]] == ["Billing"]
    for bad in (4, 1441):
        assert (
            await client.get(L, headers=w["sam"], params={"window_minutes": bad})
        ).status_code == 422
    missing = {"team_id": "00000000-0000-4000-8000-000000000000"}
    assert (await client.get(L, headers=w["sam"], params=missing)).status_code == 404


async def test_agents_are_counted_by_the_status_they_set(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    ok(await client.patch(f"/api/v1/agents/{w['ids']['ravi']}/routing", headers=w["admin"],
                          json={"status": "on_break"}))  # fmt: skip
    agents = ok(await client.get(L, headers=w["sam"]))["agents"]
    assert agents["on_break"] == 1 and agents["available"] >= 1
    assert sum(agents.values()) >= 3


async def test_only_report_roles_see_it(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    for who in ("neha", "lisa"):
        assert (await client.get(L, headers=w[who])).status_code == 403
    assert (await client.get(L, headers=w["admin"])).status_code == 200


async def test_a_team_only_role_sees_only_its_teams_queue(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    t = await queue(client, app_sessionmaker, w)
    ravi = await team_only_member(client, w, "Billing")
    out = ok(await client.get(L, headers=ravi))
    assert out["team_id"] == w["teams"]["Billing"] and out["total"]["open"] == 3
    assert {x["id"] for x in out["waiting"]} == {t["a"]["id"], t["c"]["id"]}
    asked = await client.get(L, headers=ravi, params={"team_id": w["teams"]["Shipping"]})
    assert asked.status_code == 404


async def test_many_viewers_share_one_answer_for_a_few_seconds(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await ticket(client, w, "first")
    first = ok(await client.get(L, headers=w["sam"]))
    await ticket(client, w, "second")
    again = ok(await client.get(L, headers=w["admin"]))
    assert again["generated_at"] == first["generated_at"] and again["total"]["open"] == 1
    other = ok(await client.get(L, headers=w["sam"], params={"window_minutes": 30}))
    assert other["total"]["open"] == 2  # another window is another answer


async def test_the_cache_builds_an_answer_once_even_when_asked_at_once() -> None:
    cache = LiveCache(timedelta(seconds=5))
    calls = 0

    async def make() -> object:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return object()

    answers = await asyncio.gather(*[cache.get("k", make) for _ in range(8)])  # type: ignore[arg-type]
    assert calls == 1 and len({id(a) for a in answers}) == 1
    short = LiveCache(timedelta(milliseconds=10))
    await short.get("k", make)  # type: ignore[arg-type]
    await asyncio.sleep(0.05)
    await short.get("k", make)  # type: ignore[arg-type]
    assert calls == 3


async def test_the_live_view_downloads_as_csv_and_workspaces_stay_apart(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    a = await desk(client, app_sessionmaker)
    b = await desk(client, app_sessionmaker)
    await ticket(client, a, "mine")
    r = await client.get(L, headers=a["sam"], params={"format": "csv"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    body = r.content.decode("utf-8-sig")
    assert "Queue now" in body and "Waiting for a first reply" in body and "Agents" in body
    assert ok(await client.get(L, headers=b["sam"]))["total"]["open"] == 0
