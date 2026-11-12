"""More metrics (md R3, R4, R5), the help-article votes report (R8) and CSV export of
every report (R7), with RLS on: exact numbers from a known set of tickets, the team
filter, who may see them, and the Governance link only when Cevanos is connected."""

import csv
import io
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text

from cedesk.db import workspace_session
from tests.db.helpers import Sm, add, bearer, ok, sign_in
from tests.db.test_reports import desk, ticket

pytestmark = pytest.mark.db

M = "/api/v1/reports/metrics"
A = "/api/v1/reports/articles"
TEAM = "/api/v1/reports/team"

H = timedelta(hours=1)

# name: (team, agent, channel, topic, sentiment, created ago, resolved ago, reopens,
#        replies from Neha)
TICKETS: dict[str, tuple[Any, ...]] = {
    "One": ("Billing", "Neha", "email", "Billing", "neutral", 5 * H, 3 * H, 0, 1),
    "Two": ("Billing", "Neha", "chat", "Refund / return", "negative", 6 * H, H, 0, 2),
    "Reopened": ("Shipping", "Ravi", "email", "Delivery", "neutral", 10 * H, 2 * H, 1, 1),
    "Back": ("Shipping", "Ravi", "portal", "Delivery", "negative", 30 * H, None, 1, 1),
    "Fresh": (None, None, "web", None, None, timedelta(minutes=20), None, 0, 0),
    "Waiting": ("Shipping", "Ravi", "email", "General question", "positive", 2 * H, None, 0, 0),
    "Day": ("Billing", "Neha", "api", "App problem", "neutral", 6 * H, None, 0, 0),
    "Zero": ("Billing", "Neha", "email", "Billing", "neutral", 4 * H, 3 * H, 0, 0),
    "Imported": ("Billing", "Neha", "email", "Billing", "neutral", 48 * H, H, 0, 1),
    "History": (None, "Neha", "chat", "General question", "positive", 3 * H, H, 0, 1),
    "Merged": ("Billing", "Neha", "email", "Billing", "neutral", 2 * H, None, 0, 1),
    "Old": ("Billing", "Neha", "email", "Billing", "neutral", 40 * 24 * H, 35 * 24 * H, 0, 1),
}
"""One: one reply, so one-touch. Two: two replies. Reopened: one reply but reopened once.
Back: reopened and open again. Zero: resolved with no reply. Imported (from Zendesk) and
Merged (into One) are left out. History: a Cevanos chat whose copied history has an
"agent" line with no CeDesk author, then one reply from Neha: still one-touch. Old: outside
the 30 days."""


async def setup(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    w = await desk(client, sm)
    t = {n: await ticket(client, w, n, spec[0], spec[1]) for n, spec in TICKETS.items()}
    now = datetime.now(UTC)
    neha = UUID(w["ids"]["Neha"])
    async with workspace_session(sm, w["ws"]) as s:
        for name, (_, _, channel, topic, mood, ago, resolved, reopens, replies) in TICKETS.items():
            created = now - ago
            await s.execute(text(
                "UPDATE hd_ticket SET created_at = :c, channel = :ch, topic = :topic, "
                "sentiment = :mood, reopen_count = :reopens, "
                "resolved_at = CAST(:r AS timestamptz), status = CASE WHEN CAST(:r AS "
                "timestamptz) IS NULL THEN 'open' ELSE 'resolved' END WHERE id = :id"),
                {"c": created, "ch": channel, "topic": topic, "mood": mood, "reopens": reopens,
                 "r": now - resolved if resolved else None, "id": t[name]})  # fmt: skip
            for i in range(replies):
                await s.execute(text(
                    "INSERT INTO hd_message (workspace_id, ticket_id, kind, body, "
                    "author_member_id, author_name, created_at) SELECT workspace_id, id, "
                    "'agent', 'On it', :neha, 'Neha', :at FROM hd_ticket WHERE id = :id"),
                    {"neha": neha, "at": created + (i + 1) * timedelta(minutes=5),
                     "id": t[name]})  # fmt: skip
        await s.execute(text(
            "INSERT INTO hd_message (workspace_id, ticket_id, kind, body, author_name, "
            "created_at) SELECT workspace_id, id, 'agent', 'From the chat', 'Cevanos agent', "
            "created_at FROM hd_ticket WHERE id = :id"), {"id": t["History"]})  # fmt: skip
        await s.execute(text("UPDATE hd_ticket SET status = 'pending' WHERE id = :id"),
                        {"id": t["Waiting"]})  # fmt: skip
        await s.execute(text("UPDATE hd_ticket SET source = 'zendesk', source_id = 'z-9' "
                             "WHERE id = :id"), {"id": t["Imported"]})  # fmt: skip
        await s.execute(text("UPDATE hd_ticket SET merged_into_id = :one WHERE id = :id"),
                        {"one": t["One"], "id": t["Merged"]})  # fmt: skip
        ids = {name: UUID(member) for name, member in w["ids"].items()}
        admin = (await s.execute(text("SELECT id FROM member WHERE role = 'admin'"))).scalar()
        for ticket_name, who, minutes, billable, ago in (
            ("One", ids["Neha"], 30, True, H), ("Two", ids["Neha"], 15, False, 2 * H),
            ("Reopened", ids["Ravi"], 45, True, H), ("Fresh", admin, 10, False, H),
            ("One", ids["Neha"], 60, True, 40 * 24 * H),  # outside the period
        ):  # fmt: skip
            await s.execute(text(
                "INSERT INTO hd_time_log (workspace_id, ticket_id, member_id, minutes, "
                "billable, logged_at) SELECT workspace_id, id, :who, :minutes, :billable, :at "
                "FROM hd_ticket WHERE id = :id"),
                {"who": who, "minutes": minutes, "billable": billable, "at": now - ago,
                 "id": t[ticket_name]})  # fmt: skip
        for ticket_name, who, due, done in (
            ("Day", ids["Neha"], now + H, None), ("Two", ids["Neha"], now - H, None),
            ("Waiting", ids["Neha"], None, None),  # a Shipping ticket: not in Billing's report
            ("Reopened", ids["Ravi"], now - H, now - H / 2),  # done: not counted
        ):  # fmt: skip
            await s.execute(text(
                "INSERT INTO hd_task (workspace_id, ticket_id, owner_id, text, due_at, "
                "done_at) SELECT workspace_id, id, :who, 'Check', :due, :done FROM hd_ticket "
                "WHERE id = :id"),
                {"who": who, "due": due, "done": done, "id": t[ticket_name]})  # fmt: skip
    return w | {"t": t}


def rows(report: dict[str, Any], key: str) -> list[tuple[Any, int, int]]:
    return [(r["label"], r["created"], r["open"]) for r in report[key]]


async def test_the_metrics_numbers(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await setup(client, app_sessionmaker)
    report = ok(await client.get(M, headers=w["h"]))
    assert report["summary"] == {
        "open": 4,  # Back, Fresh, Waiting, Day (Merged is left out)
        "one_touch": 2,  # One, History
        "one_touch_measured": 5,  # One, Two, Reopened, Zero, History
        "one_touch_percent": 40.0,
        "reopened": 2,  # Reopened, Back
        "reopen_measured": 6,  # One, Two, Reopened, Back, Zero, History
        "reopen_rate_percent": 33.3,
        "time_logged_minutes": 100,  # 30 + 15 + 45 + 10 (the old 60 is outside)
        "billable_minutes": 75,
    }
    assert [(b["bucket"], b["open"]) for b in report["backlog_by_age"]] == [
        ("under_1h", 1), ("1h_4h", 1), ("4h_24h", 1), ("over_1d", 1)]  # fmt: skip
    assert rows(report, "channels") == [
        ("email", 5, 1), ("web", 1, 1), ("portal", 1, 1), ("api", 1, 1), ("chat", 2, 0),
        ("phone", 0, 0)]  # fmt: skip
    assert rows(report, "topics") == [
        ("Refund / return", 1, 0), ("Billing", 3, 0), ("Delivery", 2, 1), ("App problem", 1, 1),
        ("General question", 2, 1), ("Not set", 1, 1)]  # fmt: skip
    assert rows(report, "sentiment") == [
        ("negative", 2, 1), ("neutral", 5, 1), ("positive", 2, 1), ("Not set", 1, 1)]  # fmt: skip
    assert report["topics"][-1]["value"] is None
    assert [
        (a["name"], a["logged_minutes"], a["billable_minutes"], a["open_tasks"], a["overdue_tasks"])
        for a in report["agents"]
    ] == [
        ("Admin", 10, 0, 0, 0),
        ("Neha", 45, 30, 3, 1),
        ("Ravi", 45, 45, 0, 0),
        ("Sam", 0, 0, 0, 0),
    ]  # fmt: skip  (Meena, a light agent, is not listed)
    assert report["governance_url"] is None  # Cevanos isn't connected


async def test_one_team(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await setup(client, app_sessionmaker)
    billing = ok(await client.get(M, headers=w["h"], params={"team_id": w["teams"]["Billing"]}))
    s = billing["summary"]
    assert (s["open"], s["one_touch"], s["one_touch_measured"], s["reopened"],
            s["reopen_measured"], s["reopen_rate_percent"]) == (1, 1, 3, 0, 3, 0.0)  # fmt: skip
    assert (s["time_logged_minutes"], s["billable_minutes"]) == (45, 30)
    # Neha's task on a Shipping ticket isn't counted here.
    assert [(a["name"], a["open_tasks"]) for a in billing["agents"]] == [("Neha", 2)]
    assert [b["open"] for b in billing["backlog_by_age"]] == [0, 0, 1, 0]  # Day


async def test_the_governance_link_needs_cevanos(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    assert ok(await client.get(M, headers=w["h"]))["governance_url"] is None
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(
            text(
                "INSERT INTO hd_cevanos_connection (workspace_id, api_key_sealed, "
                "api_key_hint, webhook_secret_sealed) VALUES (:ws, '\\x00', 'abcd', '\\x00')"
            ),
            {"ws": w["ws"]},
        )
    link = ok(await client.get(M, headers=w["h"]))["governance_url"]
    assert link == "https://cevanos.ai/dashboard/governance"


async def test_who_may_see_them(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    for who, code in (("neha", 403), ("meena", 403), ("sam", 200)):
        h = bearer(await sign_in(client, w["slug"], f"{who}@example.com"))
        for url in (M, A):
            assert (await client.get(url, headers=h)).status_code == code, (who, url)
    now = datetime.now(UTC)
    bad = {"from": now.isoformat(), "to": (now - timedelta(days=1)).isoformat()}
    for url in (M, A):
        assert (await client.get(url, headers=w["h"], params=bad)).status_code == 422
    r = await client.get(M, headers=w["h"], params={"team_id": str(UUID(int=7))})
    assert r.status_code == 404
    r = await client.get(M, headers=w["h"], params={"format": "xml"})
    assert r.status_code == 422


async def test_a_team_only_role_sees_its_own_team(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await setup(client, app_sessionmaker)
    admin = w["h"]
    made = ok(await client.post("/api/v1/roles", headers=admin, json={
        "name": "Shipping lead", "reports": True, "scope": "team_only",
        "team_id": w["teams"]["Shipping"]}), 201)  # fmt: skip
    rita = await add(client, admin["Authorization"].split()[1], "rita@example.com", "agent", "Rita")
    ok(await client.patch(f"/api/v1/members/{rita}", headers=admin,
                          json={"role": "custom", "role_id": made["id"]}))  # fmt: skip
    h = bearer(await sign_in(client, w["slug"], "rita@example.com"))
    mine = ok(await client.get(M, headers=h))
    assert mine["team_id"] == w["teams"]["Shipping"]
    assert mine["summary"]["open"] == 2  # Back, Waiting
    r = await client.get(M, headers=h, params={"team_id": w["teams"]["Billing"]})
    assert r.status_code == 404
    assert (await client.get(A, headers=h)).status_code == 200  # articles aren't per team


def sections(r: Response) -> list[list[str]]:
    assert r.headers["content-type"] == "text/csv; charset=utf-8"
    assert r.headers["content-disposition"].startswith('attachment; filename="cedesk-')
    body = r.content.decode("utf-8")
    assert body.startswith("﻿")
    return list(csv.reader(io.StringIO(body.removeprefix("﻿"))))


def table(found: list[list[str]], title: str) -> list[list[str]]:
    """The rows of one section, header first, up to the next blank row."""
    start = found.index([title]) + 1
    end = found.index([], start) if [] in found[start:] else len(found)
    return found[start:end]


async def test_every_report_as_csv(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await setup(client, app_sessionmaker)
    token = w["h"]["Authorization"].split()[1]
    await add(client, token, "evil@example.com", "agent", "=HYPERLINK(1)")

    metrics = sections(await client.get(M, headers=w["h"], params={"format": "csv"}))
    assert metrics[0] == ["CeDesk more metrics"]
    assert metrics[3] == ["Team", "All teams"]
    summary = {row[0]: row[1] for row in table(metrics, "Summary")[1:]}
    assert summary["One-touch resolution (%)"] == "40.0"
    assert summary["Reopen rate (%)"] == "33.3"
    assert table(metrics, "Backlog by age")[1:] == [
        ["Under 1 hour", "1"], ["1 to 4 hours", "1"], ["4 to 24 hours", "1"],
        ["Over 1 day", "1"]]  # fmt: skip
    assert ["Not set", "1", "1"] in table(metrics, "Tickets by topic")
    agents = table(metrics, "Handling time per agent")
    assert ["Neha", "agent", "45", "30", "3", "1"] in agents
    assert ["'=HYPERLINK(1)", "agent", "0", "0", "0", "0"] in agents  # stays text

    team = sections(await client.get(TEAM, headers=w["h"], params={"format": "csv"}))
    assert team[0] == ["CeDesk team report"]
    assert table(team, "CSAT by score")[0] == ["Score", "Ratings"]
    assert any(row[0] == "Billing" for row in table(team, "Teams")[1:])
    assert any(row[0] == "'=HYPERLINK(1)" for row in table(team, "Agents")[1:])

    billing = w["teams"]["Billing"]
    one = sections(
        await client.get(M, headers=w["h"], params={"format": "csv", "team_id": billing})
    )
    assert one[3] == ["Team", billing]

    articles = sections(await client.get(A, headers=w["h"], params={"format": "csv"}))
    assert articles[0] == ["CeDesk help-article votes"]
    assert table(articles, "Articles") == [
        ["Article", "Category", "Status", "Votes", "Helpful", "Not helpful", "Helpful (%)"]
    ]


async def vote(sm: Sm, ws: UUID, article: str, visitor: int, helpful: bool, ago: timedelta) -> None:
    async with workspace_session(sm, ws) as s:
        await s.execute(text(
            "INSERT INTO hd_article_vote (workspace_id, article_id, visitor_hash, helpful, "
            "voted_at) VALUES (:ws, :a, :v, :helpful, :at)"),
            {"ws": ws, "a": UUID(article), "v": visitor.to_bytes(4, "big"), "helpful": helpful,
             "at": datetime.now(UTC) - ago})  # fmt: skip


async def test_the_article_votes(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    h = w["h"]
    faq = ok(await client.post("/api/v1/article-categories", headers=h, json={"name": "FAQ"}), 201)
    made = {}
    articles = (
        ("Returns", faq["id"], "published"),
        ("=SUM(1)", None, "draft"),
        ("Unread", None, "published"),
    )
    for title, category, status in articles:
        body = {"title": title, "status": status, "category_id": category}
        made[title] = ok(await client.post("/api/v1/articles", headers=h, json=body), 201)["id"]
    ws, sm = w["ws"], app_sessionmaker
    for visitor, helpful in ((1, True), (2, True), (3, False)):
        await vote(sm, ws, made["Returns"], visitor, helpful, H)
    await vote(sm, ws, made["=SUM(1)"], 1, False, H)
    await vote(sm, ws, made["=SUM(1)"], 2, True, timedelta(days=40))  # outside the period

    report = ok(await client.get(A, headers=h))
    assert (report["votes"], report["helpful"], report["not_helpful"], report["helpful_percent"],
            report["articles_voted"], report["truncated"]) == (4, 2, 2, 50.0, 2, False)  # fmt: skip
    assert [
        (
            a["title"],
            a["category"],
            a["status"],
            a["votes"],
            a["helpful"],
            a["not_helpful"],
            a["helpful_percent"],
        )
        for a in report["articles"]
    ] == [
        ("Returns", "FAQ", "published", 3, 2, 1, 66.7),
        ("=SUM(1)", None, "draft", 1, 0, 1, 0.0),
    ]  # fmt: skip  ("Unread" has no votes)

    recent = ok(await client.get(A, headers=h, params={
        "from": (datetime.now(UTC) - 2 * H).isoformat()}))  # fmt: skip
    assert recent["votes"] == 4
    older = ok(await client.get(A, headers=h, params={
        "from": (datetime.now(UTC) - timedelta(days=50)).isoformat(),
        "to": (datetime.now(UTC) - timedelta(days=30)).isoformat()}))  # fmt: skip
    assert (older["votes"], older["helpful"]) == (1, 1)

    found = sections(await client.get(A, headers=h, params={"format": "csv"}))
    assert table(found, "Articles")[1:] == [
        ["Returns", "FAQ", "published", "3", "2", "1", "66.7"],
        ["'=SUM(1)", "", "draft", "1", "0", "1", "0.0"],
    ]
