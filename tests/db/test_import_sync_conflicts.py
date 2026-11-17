"""A sync look never undoes what CeDesk agents did, unless the source changed it (P1-10).

Before (audit C5b), every look set the status and priority from the source, so an agent's
change in CeDesk went back within 5 minutes during "sync until go-live". Now the source's
value is applied only when it changed since the source was last read (`hd_import_seen`);
when both changed, the source's new value wins and the system line says it replaced a
CeDesk change. Checked for status, priority, owner, team, tags, subject and custom fields,
with RLS on.
"""

from typing import Any

import pytest
from httpx import AsyncClient

from tests.db.helpers import Sm, ok
from tests.db.test_import_sync import Source, im, stop_all
from tests.db.test_imports import Fake, I, fake_desk, run_state, sql, work

pytestmark = pytest.mark.db

__all__ = ["im"]  # the fixture, shared

BASE: dict[str, Any] = {
    "status": "open", "priority": "low", "subject": "Card declined", "tags": ["billing"],
    "group_id": "g1", "assignee_email": None, "fields": {"Plan": "Basic", "Seats": 3},
}  # fmt: skip


async def syncing(im: AsyncClient, sm: Sm) -> tuple[Source, dict[str, Any], str]:
    src = Source()
    src.tickets = [Fake.ticket(0, **BASE)]
    w, conn = await fake_desk(im, sm, src)
    started = ok(await im.post(
        f"{I}/connections/{conn['id']}/run", headers=w["admin"],
        json={"kinds": ["tickets"], "sync": True, "team_map": {"g1": None, "g2": str(w["team"])}},
    ), 202)  # fmt: skip
    await work(im, sm)
    return src, w, started["id"]


async def look(im: AsyncClient, sm: Sm, w: dict[str, Any], src: Source, **now: Any) -> None:
    src.changed = [Fake.ticket(0, **{**BASE, **now})]
    await sql(sm, w["ws"], "UPDATE hd_import_run SET next_run_at = now()")
    await work(im, sm)


async def ticket(sm: Sm, ws: Any) -> Any:
    [row] = await sql(
        sm,
        ws,
        "SELECT status, priority, assignee_id, team_id, tags, subject, fields FROM hd_ticket",
    )
    return row


async def updated_lines(sm: Sm, ws: Any) -> list[str]:
    rows = await sql(
        sm, ws, "SELECT body FROM hd_message WHERE body LIKE 'Updated from%' ORDER BY created_at"
    )
    return [r["body"] for r in rows]


async def test_agent_edits_survive_looks_where_the_source_did_not_change(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src, w, run_id = await syncing(im, sm)
    await sql(
        sm, w["ws"],
        "UPDATE hd_ticket SET status = 'pending', priority = 'urgent', assignee_id = :a, "
        "team_id = :t, subject = 'Card declined (VIP)', tags = '{imported-zendesk,vip}', "
        "fields = fields || '{\"plan\": \"Gold\", \"note\": \"agent\"}'",
        a=w["asha_id"], t=w["team"],
    )  # fmt: skip
    before = await ticket(sm, w["ws"])

    await look(im, sm, w, src)  # the source shows exactly what it showed at the import
    await look(im, sm, w, src)

    assert await ticket(sm, w["ws"]) == before  # every agent edit is still there
    assert await updated_lines(sm, w["ws"]) == []
    assert (await run_state(im, w, run_id))["counts"]["sync"]["updated"] == 0
    await stop_all(sm, w["ws"])


async def test_what_the_source_changed_comes_over_and_nothing_else(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src, w, _ = await syncing(im, sm)
    # Agents in CeDesk: urgent, owned by Asha, an extra tag, a field of their own.
    await sql(
        sm, w["ws"],
        "UPDATE hd_ticket SET priority = 'urgent', assignee_id = :a, "
        "tags = tags || '{vip}', fields = fields || '{\"note\": \"agent\"}'",
        a=w["asha_id"],
    )  # fmt: skip

    # The source: solved, high priority, new subject, moved to g2, tag swapped, a field
    # changed and one removed.
    await look(
        im, sm, w, src, status="solved", priority="high", subject="Card declined twice",
        group_id="g2", tags=["refund"], fields={"Plan": "Pro"},
    )  # fmt: skip

    row = await ticket(sm, w["ws"])
    assert row["status"] == "resolved" and row["priority"] == "high"
    assert row["subject"] == "Card declined twice"
    assert row["assignee_id"] == w["asha_id"]  # the source's owner didn't change: Asha stays
    assert str(row["team_id"]) == str(w["team"])
    assert row["tags"] == ["imported-zendesk", "vip", "refund"]  # billing gone, vip kept
    assert row["fields"]["plan"] == "Pro" and row["fields"]["note"] == "agent"
    assert "seats" not in row["fields"]
    [line] = await updated_lines(sm, w["ws"])
    assert line == (
        "Updated from Zendesk: status resolved; priority high (replacing a CeDesk change); "
        "team g2; subject; tags added: refund; tags removed: billing; fields: plan, seats"
    )
    await stop_all(sm, w["ws"])


async def test_an_import_run_again_keeps_agent_edits_too(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    """The same rule for a second import of the same tickets (sync off)."""
    sm = app_sessionmaker
    src = Source()
    src.tickets = [Fake.ticket(0, **BASE)]
    w, conn = await fake_desk(im, sm, src)
    ok(await im.post(f"{I}/connections/{conn['id']}/run", headers=w["admin"],
                     json={"kinds": ["tickets"]}), 202)  # fmt: skip
    await work(im, sm)
    await sql(
        sm, w["ws"],
        "UPDATE hd_ticket SET status = 'resolved', resolved_at = now(), priority = 'urgent'",
    )  # fmt: skip

    conn2 = ok(await im.post(f"{I}/connections", headers=w["admin"], json={
        "source": "zendesk", "settings": {"subdomain": "acme"},
        "credentials": {"token": "tok-secret-1234"}}), 201)  # fmt: skip
    ok(await im.post(f"{I}/connections/{conn2['id']}/run", headers=w["admin"],
                     json={"kinds": ["tickets"]}), 202)  # fmt: skip
    await work(im, sm)
    row = await ticket(sm, w["ws"])
    assert (row["status"], row["priority"]) == ("resolved", "urgent")
    await stop_all(sm, w["ws"])
